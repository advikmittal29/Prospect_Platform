"""
Standalone SMTP test — verifies the handoff email path works before a real handoff.
Reads SMTP settings from .env and sends one test email to the manager address.
Run:  python test_email.py
"""
import os
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

# Adjust these names if your .env uses different variable names (check with:
#   findstr /I "SMTP MAIL EMAIL" .env
host     = os.getenv("SMTP_HOST", "smtp.gmail.com")
port     = int(os.getenv("SMTP_PORT", "587"))
username = os.getenv("SMTP_USERNAME")
password = os.getenv("SMTP_PASSWORD")
from_addr = os.getenv("SMTP_FROM_EMAIL", username)
to_addr   = os.getenv("REPLY_HANDOFF_MANAGER_EMAIL")

print(f"Host: {host}:{port}")
print(f"From: {from_addr}")
print(f"To:   {to_addr}")
print(f"User: {username}")
print(f"Pass: {'(set, ' + str(len(password)) + ' chars)' if password else '(MISSING!)'}")
print("-" * 40)

if not all([host, port, username, password, from_addr, to_addr]):
    print("ERROR: One or more required values are missing above. Fix .env and retry.")
    raise SystemExit(1)

msg = MIMEText(
    "This is a test of the gNxt handoff email path.\n\n"
    "If you're reading this, SMTP sending works and real handoff "
    "notifications will reach this inbox."
)
msg["Subject"] = "[gNxt] Handoff email test"
msg["From"] = from_addr
msg["To"] = to_addr

try:
    print("Connecting...")
    with smtplib.SMTP(host, port, timeout=20) as server:
        server.starttls()
        print("Logging in...")
        server.login(username, password)
        print("Sending...")
        server.sendmail(from_addr, [to_addr], msg.as_string())
    print("-" * 40)
    print(f"SUCCESS — test email sent to {to_addr}. Check that inbox (and spam).")
except Exception as exc:
    print("-" * 40)
    print(f"FAILED: {type(exc).__name__}: {exc}")
    print("\nCommon causes:")
    print(" - Using your normal Gmail password instead of a 16-char App Password")
    print(" - 2-Step Verification not enabled on the Gmail account")
    print(" - Wrong SMTP variable names in .env (run: findstr /I \"SMTP MAIL EMAIL\" .env)")