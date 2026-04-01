import os
import logging
import torch
import torchaudio
import difflib
import numpy as np
from streaming.config import SAMPLE_RATE, CONSENT_TEXT

# Initialize the module-specific logger
logger = logging.getLogger(__name__)

class Segment:
    """
    Data structure representing a distinct voice cloning segment.
    Holds the text transcript, speaker ID, raw audio tensor, and pre-tokenized tensors.
    """
    def __init__(self, text: str, speaker: int, audio: torch.Tensor = None, sample_rate: int = SAMPLE_RATE):
        self.text = text
        self.speaker = speaker
        self.audio = audio
        self.sample_rate = sample_rate
        self.audio_tokens = None

def get_voice_path(filename: str) -> str:
    """Safely resolves the absolute path to a voice file relative to this script."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(current_dir, "voices", filename)

def load_reference_audio(path: str, sample_rate: int = SAMPLE_RATE) -> torch.Tensor:
    """
    Loads a .wav file, converts stereo to mono if necessary, 
    and resamples the audio to match the engine's target sample rate.
    """
    if not os.path.exists(path):
        logger.error("Reference audio not found: %s", path)
        raise FileNotFoundError(f"Reference audio not found: {path}")
    
    try:
        wav, sr = torchaudio.load(path)
        # Downmix stereo to mono by averaging channels
        if wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        wav = wav.squeeze(0)
        
        return torchaudio.functional.resample(wav, orig_freq=sr, new_freq=sample_rate)
    except Exception as e:
        logger.error(f"Failed to load or resample audio at {path}", exc_info=True)
        raise

def load_voices(engine, voices_data) -> dict:
    """
    Loads voices from metadata and RETURNS a dict of name -> Segment.
    No global state mutation here.
    """

    logger.info("Loading voices...")

    voices = {}

    for v in voices_data:
        try:
            name = v["name"]

            if name in voices:
                logger.debug(f"Voice already loaded, skipping: {name}")
                continue

            path = v["path"]
            text = v["text"]
            speaker_id = v["speaker_id"]

            audio = load_reference_audio(path, SAMPLE_RATE)

            seg = Segment(
                text=text,
                speaker=speaker_id,
                audio=audio
            )

            seg.audio_tokens = tokenize_audio(engine, audio)
            
            voices[name] = seg

            logger.debug(f"Successfully loaded and tokenized voice: {name}")

        except Exception:
            logger.error(f"Failed to load voice '{v.get('name')}'", exc_info=True)

    logger.info(f"Finished loading voices. Total: {len(voices)}")

    return voices

def tokenize_audio(engine, audio: torch.Tensor) -> tuple:
    """Tokenizes a reference audio tensor for use in a voice segment."""
    with torch.no_grad():
        audio_gpu = audio.to(engine.device)

        tokens = engine.audio_tokenizer.encode(
            audio_gpu.unsqueeze(0).unsqueeze(0)
        )[0]

        tokens = tokens[:engine._num_codebooks, :]

        eos = torch.zeros(tokens.size(0), 1, device=engine.device)
        tokens = torch.cat([tokens, eos], dim=1)

        T = tokens.size(1)
        width = engine._num_codebooks + 1

        frame = torch.zeros(T, width, dtype=torch.long, device=engine.device)
        mask  = torch.zeros(T, width, dtype=torch.bool, device=engine.device)

        frame[:, :engine._num_codebooks] = tokens.transpose(0, 1)
        mask[:, :engine._num_codebooks]  = True

        return (frame.unsqueeze(0), mask.unsqueeze(0))
    
def verify_consent(whisper_model, voice_encoder, consent_path, reference_path, threshold=0.75):
    """
    Verifies that the consent audio matches the expected text (via Whisper)
    and that the speaker matches the reference audio (via Resemblyzer).
    """
    # 1. Transcribe and check text accuracy
    result = whisper_model.transcribe(consent_path)
    transcript = result["text"].strip()
    ratio = difflib.SequenceMatcher(None, transcript.lower(), CONSENT_TEXT.lower()).ratio()
    
    logger.debug("Consent transcription ratio: %.2f — '%s'", ratio, transcript)
    
    if ratio < 0.85:
        return False, f"Please read the consent phrase more clearly (accuracy: {int(ratio*100)}%)"

    # 2. Compare speaker embeddings
    consent_wav   = load_reference_audio(consent_path).numpy()
    reference_wav = load_reference_audio(reference_path).numpy()

    consent_embed   = voice_encoder.embed_utterance(consent_wav)
    reference_embed = voice_encoder.embed_utterance(reference_wav)

    similarity = float(np.dot(consent_embed, reference_embed) / (
        np.linalg.norm(consent_embed) * np.linalg.norm(reference_embed)
    ))

    logger.debug("Voice similarity score: %.2f", similarity)

    if similarity < threshold:
        return False, f"Voice does not match reference audio (similarity: {int(similarity*100)}%)"

    return True, "Verified"
