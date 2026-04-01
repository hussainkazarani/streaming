import threading
import queue
import logging
import asyncio

# Initialize module logger
logger = logging.getLogger(__name__)

# The queue where FastAPI drops incoming text requests
req_queue = queue.Queue()

# Engine instances
engine_instance = None
whisper_model = None
voice_encoder = None

def get_engine():
    return engine_instance

def get_whisper():
    return whisper_model

def get_voice_encoder():
    return voice_encoder

def init_worker_thread(main_loop: asyncio.AbstractEventLoop):
    """Spins up the isolated background thread for the PyTorch engine."""
    thread = threading.Thread(target=_model_worker, args=(main_loop,), daemon=True)
    thread.start()
    return thread

def _model_worker(main_loop: asyncio.AbstractEventLoop):
    """
    The infinite background loop. Initializes the engine once,
    then waits for incoming text chunks to process.
    Imports are deferred here so PyTorch is never loaded by the main web server thread.
    """
    logger.info("Starting AI Worker Thread...")

    try:
        # Deferred imports - keeps PyTorch out of the FastAPI thread
        from tts_engine.engine import StreamTTS
        from voice_cloning.manager import load_voices
        from web_api.storage import load_allvoices_file
        from streaming.config import VOICE_SEGMENTS

        global engine_instance, whisper_model, voice_encoder
        engine = StreamTTS()
        engine_instance = engine

        import whisper
        from resemblyzer import VoiceEncoder

        whisper_model = whisper.load_model("base")
        voice_encoder = VoiceEncoder()

        logger.info("Whisper and Resemblyzer loaded.")

        voices_data = load_allvoices_file()
        voices = load_voices(engine, voices_data)
        VOICE_SEGMENTS.update(voices)

        logger.info("AI Worker is fully loaded and listening for requests.")
    except Exception:
        logger.error("Failed to initialize AI Engine in worker thread", exc_info=True)
        return

    while True:
        # Block and wait for a new request from the web server
        req = req_queue.get()
        if req is None:
            break  # Poison pill — safely shuts down the thread

        chunks, speaker_id, voice_segment, abort_event, async_q = req

        try:
            # Generate audio chunks and route them back to the async event loop
            for (chunk_idx, item) in engine.generate_request(chunks, speaker_id, voice_segment, abort_event):
                main_loop.call_soon_threadsafe(async_q.put_nowait, (chunk_idx, item))

            # Signal that generation is completely finished
            main_loop.call_soon_threadsafe(async_q.put_nowait, (-1, "DONE"))

        except Exception:
            logger.error("Worker encountered an error during generation", exc_info=True)
            main_loop.call_soon_threadsafe(async_q.put_nowait, (-1, "DONE"))

def flush_queues():
    """Drains pending requests to prevent stale audio leaking if a user disconnects abruptly."""
    while not req_queue.empty():
        try:
            req_queue.get_nowait()
        except queue.Empty:
            break
