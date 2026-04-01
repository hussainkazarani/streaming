import os
from dotenv import load_dotenv

load_dotenv()

# Environment and Model Setup
HF_TOKEN = os.getenv("HF_TOKEN")
MODEL_PATH = os.getenv("MODEL_PATH", "sesame/csm-1b")
DEVICE = os.getenv("DEVICE", "cuda")

# Generation Parameters
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "24000"))
MAX_MS = int(os.getenv("MAX_MS", "60000"))
FIRST_CHUNK_FRAMES = int(os.getenv("FIRST_CHUNK_FRAMES", "20"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.8"))
TOPK = int(os.getenv("TOPK", "50"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "20"))

# Security
EMAIL = os.getenv("EMAIL")
APP_PASSWORD = os.getenv("APP_PASSWORD")
CONSENT_TEXT = os.getenv("CONSENT_TEXT", "I consent to my voice being cloned and used in AI-generated audio on this platform.")

VOICE_SEGMENTS = {}  # name -> Segment
USERS = {}  # email -> token