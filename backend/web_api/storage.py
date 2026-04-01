import os
import json

USERS_FILE = os.path.join("data", "users.json")
VOICES_FILE = os.path.join("data", "voices.json")
USER_VOICES_DIR = os.path.join("data", "user_voices")

os.makedirs(USER_VOICES_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(data):
    with open(USERS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_allvoices_file():
    if not os.path.exists(VOICES_FILE):
        return []
    with open(VOICES_FILE, "r") as f:
        return json.load(f)

def save_allvoices_file(data):
    with open(VOICES_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_user_voice_file(email):
    safe = email.replace("@", "_").replace(".", "_")
    return os.path.join(USER_VOICES_DIR, f"{safe}.json")

def load_user_voices(email):
    path = get_user_voice_file(email)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)

def save_user_voices(email, voices):
    path = get_user_voice_file(email)
    with open(path, "w") as f:
        json.dump(voices, f, indent=2)