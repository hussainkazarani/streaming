import random
import string
import smtplib
import time
import logging
from email.mime.text import MIMEText
from fastapi import Request
from streaming.config import EMAIL, APP_PASSWORD

logger = logging.getLogger(__name__)

OTP_STORE = {}          # email -> otp
TOKEN_STORE = {}        # token -> email
LAST_OTP_REQUESTS = {}  # email -> [timestamps]

def generate_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=32))

def get_user(request: Request):
    token = request.headers.get("Authorization")
    return TOKEN_STORE.get(token)

def send_email(to_email, otp):
    msg = MIMEText(f"Your OTP is: {otp}")
    msg['Subject'] = 'Your OTP Code'
    msg['From'] = EMAIL
    msg['To'] = to_email

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(EMAIL, APP_PASSWORD)
        server.send_message(msg)