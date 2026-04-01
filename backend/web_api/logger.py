import os
import logging
from datetime import datetime

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

def log_request(email, action, voice=None, text=None):
    os.makedirs(os.path.join("data", "logs"), exist_ok=True)
    filename = os.path.join("data", "logs", f"{email}.txt")
    with open(filename, "a") as f:
        f.write(
            f"[{datetime.now()}] action={action} voice={voice} text={text}\n"
        )