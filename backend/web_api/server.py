import os
import sys
import warnings

# Suppress harmless PyTorch/torchaudio warnings before any other imports
warnings.filterwarnings("ignore", category=UserWarning)

# Add backend/ to path so sibling packages (streaming, tts_engine, voice_cloning) are importable.
# server.py lives in backend/web_api/, so one level up is backend/, two levels up is project root.
_backend_dir  = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_project_root = os.path.abspath(os.path.join(_backend_dir, '..'))
sys.path.insert(0, _backend_dir)

import threading
import asyncio
import logging
import psutil
import platform
import torch
from fastapi import Form, UploadFile, File
from voice_cloning.manager import Segment, load_reference_audio
from streaming.worker import get_engine
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from streaming.utils import split_text
from streaming.worker import req_queue, flush_queues, init_worker_thread, get_whisper, get_voice_encoder
from streaming.config import VOICE_SEGMENTS, USERS
from web_api.auth import OTP_STORE, TOKEN_STORE, LAST_OTP_REQUESTS, get_user, send_email
from storage import load_users, save_users, load_allvoices_file, save_allvoices_file, load_user_voices, save_user_voices
from web_api.logger import setup_logging, log_request
from web_api.auth import generate_token
from voice_cloning.manager import verify_consent

import random
import time

setup_logging()
logger = logging.getLogger(__name__)

# performed 1 optimization (priority process on admin privileges)
# Elevate process priority to ensure the web server isn't starved by PyTorch
try:
    p = psutil.Process(os.getpid())
    p.nice(psutil.HIGH_PRIORITY_CLASS if platform.system() == "Windows" else -10)
except Exception as e:
    logger.warning(f"Could not elevate process priority: {e}")

app = FastAPI(title="Stream TTS API")

# Global state to prevent multiple simultaneous users from crashing the GPU VRAM
current_active_user = None
abort_event = threading.Event()

@app.on_event("startup")
async def startup_event():
    """Starts the PyTorch worker thread when the FastAPI server boots up."""
    logger.info("FastAPI server starting up...")

    # load users into memory
    users = load_users()
    USERS.update(users)

    # preload token -> email mapping
    for email, token in USERS.items():
        TOKEN_STORE[token] = email
    
    main_loop = asyncio.get_running_loop()
    init_worker_thread(main_loop)

@app.get("/")
async def root():
    return RedirectResponse("/login/login.html")

@app.get("/favicon.ico")
async def favicon():
    return FileResponse(os.path.join(_project_root, "assets", "favicon.ico"))

@app.get("/api/ready")
async def api_ready():
    from streaming.worker import is_ready
    return {"ready": is_ready()}

@app.post("/auth/request-otp")
async def request_otp(data: dict):
    email = data["email"]
    now = time.time()

    # get existing timestamps
    timestamps = LAST_OTP_REQUESTS.get(email, [])

    # keep only last 60 seconds
    timestamps = [t for t in timestamps if now - t < 60]

    if len(timestamps) >= 3:
        return {
            "success": False,
            "error": "Too many OTP requests. Try again later."
        }

    # add current request
    timestamps.append(now)
    LAST_OTP_REQUESTS[email] = timestamps

    otp = str(random.randint(100000, 999999))

    OTP_STORE[email] = {
        "otp": otp,
        "expiry": time.time() + 300  # 5 minutes
    }

    send_email(email, otp)

    logger.debug("OTP generated for %s", email)

    return {"success": True}

@app.post("/auth/verify-otp")
async def verify_otp(data: dict):
    email = data["email"]
    otp   = data["otp"]

    record = OTP_STORE.get(email)

    if not record:
        return {"success": False, "error": "No OTP found"}

    # expiry check
    if time.time() > record["expiry"]:
        return {"success": False, "error": "OTP expired"}

    # actual OTP check
    if otp != record["otp"]:
        return {"success": False, "error": "Wrong OTP"}

    del OTP_STORE[email]

    if email in USERS:
        token = USERS[email]
    else:
        token = generate_token()
        USERS[email] = token
        save_users(USERS)

    TOKEN_STORE[token] = email

    return {"success": True, "token": token}

@app.get("/api/voices")
async def list_voices(request: Request):
    """Returns the pre-loaded voices for the frontend UI."""
    user = get_user(request)

    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    user_voice_names = load_user_voices(user)
    logger.info("User: %s, user_voices: %s, VOICE_SEGMENTS keys: %s", user, user_voice_names, list(VOICE_SEGMENTS.keys()))
    
    return JSONResponse([
        {"name": name, "speaker_id": VOICE_SEGMENTS[name].speaker}
        for name in user_voice_names if name in VOICE_SEGMENTS
    ])

@app.post("/api/voices/upload")
async def upload_voice(
    request: Request,
    name: str = Form(...),
    transcript: str = Form(...),
    file: UploadFile = File(...),
    verify_file: UploadFile = File(..., alias="verifyFile"),
):
    user = get_user(request)

    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    log_request(user, "upload", voice=name, text=transcript)

    if not file.filename.endswith('.wav'):
        return JSONResponse({"error": "Only .wav files accepted"}, status_code=400)

    if name in VOICE_SEGMENTS:
        return JSONResponse({"error": f"Voice '{name}' already exists"}, status_code=400)

    engine = get_engine()
    if engine is None:
        return JSONResponse({"error": "Engine not ready yet"}, status_code=503)

    # SAVE FILE PERMANENTLY
    upload_dir = os.path.join(_project_root, "backend", "voice_cloning", "voices")
    os.makedirs(upload_dir, exist_ok=True)

    file_path = os.path.join(upload_dir, f"{name}.wav")

    with open(file_path, "wb") as f:
        f.write(await file.read())

    # VERIFY CONSENT
    consent_path = os.path.join(upload_dir, f"{name}_consent.wav")
    with open(consent_path, "wb") as f:
        f.write(await verify_file.read())

    whisper_m = get_whisper()
    encoder   = get_voice_encoder()

    ok, reason = verify_consent(whisper_m, encoder, consent_path, file_path)
    os.remove(consent_path)

    if not ok:
        os.remove(file_path)
        return JSONResponse({"error": reason}, status_code=400)

    try:
        # BUILD SEGMENT
        audio = load_reference_audio(file_path)
        seg = Segment(text=transcript, speaker=len(VOICE_SEGMENTS), audio=audio)

        from voice_cloning.manager import tokenize_audio
        seg.audio_tokens = tokenize_audio(engine, audio)

        # STORE IN MEMORY
        VOICE_SEGMENTS[name] = seg

        # SAVE TO GLOBAL FILE
        data = load_allvoices_file()

        data.append({
            "name": name,
            "path": file_path,
            "text": transcript,
            "speaker_id": seg.speaker
        })

        save_allvoices_file(data)

        # LINK TO USER
        voices = load_user_voices(user)

        if name not in voices:
            voices.append(name)
            save_user_voices(user, voices)

        logger.info("Voice '%s' uploaded successfully.", name)

        return JSONResponse({
            "success": True,
            "name": name,
            "speaker_id": seg.speaker
        })

    except Exception as e:
        logger.error("Failed to upload voice '%s'", name, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)

@app.websocket("/api/{voice_name}")
async def voice_websocket(websocket: WebSocket, voice_name: str):
    """
    Handles real-time WebSocket connections.
    Receives text, pushes to the AI worker queue, and streams back audio bytes.
    """
    token = websocket.query_params.get("token")
    user = TOKEN_STORE.get(token)

    if token not in TOKEN_STORE:
        await websocket.close(code=1008)
        return

    global current_active_user

    # 1. Validate requested voice
    if voice_name not in VOICE_SEGMENTS:
        logger.warning("Connection rejected: Voice '%s' not found.", voice_name)
        return await websocket.close(code=1008)
    
    # check user owns voice
    user_voices = load_user_voices(user)

    if voice_name not in user_voices:
        await websocket.close(code=1008)
        return

    # 2. Enforce single-user lock
    if current_active_user is not None:
        logger.warning("Connection rejected: Server is currently busy.")
        return await websocket.close(code=1013)

    await websocket.accept()
    current_active_user = websocket
    voice_segment = VOICE_SEGMENTS[voice_name]
    speaker_id    = voice_segment.speaker

    abort_event.clear()
    flush_queues()
    logger.info("WebSocket connected. Streaming voice: %s", voice_name)

    try:
        while True:
            # Wait for incoming text from the browser
            data   = await websocket.receive_text()
            log_request(user, "generate", voice=voice_name, text=data)
            chunks = split_text(data)

            if not chunks:
                continue

            # Create a dedicated async queue for this specific streaming request
            async_q: asyncio.Queue = asyncio.Queue()

            # Pass the job to the PyTorch background thread
            req_queue.put((chunks, speaker_id, voice_segment, abort_event, async_q))

            first_packet = False

            while True:
                # Wait for generated audio chunks from the worker thread
                chunk_idx, item = await async_q.get()

                if abort_event.is_set() or item == "DONE":
                    break

                if item == "EOS":
                    # One chunk finished — continue to next
                    continue

                if not first_packet:
                    logger.debug("First audio packet routed through WebSocket.")
                    first_packet = True

                # Stream the raw audio bytes directly to the browser
                await websocket.send_bytes(item)

            if not abort_event.is_set():
                await websocket.send_text("END_OF_AUDIO")

    except WebSocketDisconnect:
        logger.info("Client disconnected normally.")
    except Exception:
        logger.error("WebSocket encountered an error", exc_info=True)
    finally:
        # Trigger abort to stop the PyTorch generator immediately
        abort_event.set()
        flush_queues()
        current_active_user = None
        logger.info("Connection closed. GPU lock released.")

@app.delete("/api/voices/{name}")
async def delete_voice(name: str, request: Request):
    user = get_user(request)

    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # Check user owns this voice
    user_voices = load_user_voices(user)
    if name not in user_voices:
        return JSONResponse({"error": "Voice not found"}, status_code=404)
    
    log_request(user, "delete", voice=name)

    # Remove from user's list
    user_voices.remove(name)
    save_user_voices(user, user_voices)

    # Check if any other user still has this voice
    all_users = load_users()
    still_in_use = False

    for email in all_users:
        if email == user:
            continue
        other_voices = load_user_voices(email)
        if name in other_voices:
            still_in_use = True
            break

    # If no one else uses it, delete globally
    if not still_in_use:
        # Remove from memory
        if name in VOICE_SEGMENTS:
            del VOICE_SEGMENTS[name]

        # Get path BEFORE deleting from file
        all_voices = load_allvoices_file()
        original = next((v for v in all_voices if v["name"] == name), None)

        # Remove from voices.json
        filtered = [v for v in all_voices if v["name"] != name]
        save_allvoices_file(filtered)

        # Delete the actual wav file using path from original data
        if original and os.path.exists(original["path"]):
            os.remove(original["path"])

        logger.info("Voice '%s' fully deleted.", name)
    else:
        logger.info("Voice '%s' unlinked from user but still in use by others.", name)

    return JSONResponse({"success": True})

app.mount("/", StaticFiles(directory=os.path.join(_project_root, "frontend"), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        ws_ping_interval=None,
        ws_ping_timeout=None,
    )