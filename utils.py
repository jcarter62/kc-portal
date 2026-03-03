import os
import smtplib
from email.message import EmailMessage
from datetime import datetime
import re
from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from passlib.context import CryptContext
import models
from dotenv import load_dotenv

load_dotenv()

# Media configuration
MEDIA_FOLDER = os.getenv("MEDIA_FOLDER")
if not MEDIA_FOLDER:
    MEDIA_FOLDER = os.path.join(os.getcwd(), "media")

if not os.path.exists(MEDIA_FOLDER):
    os.makedirs(MEDIA_FOLDER)

templates = Jinja2Templates(directory="templates")
templates.env.globals['now'] = datetime.utcnow

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def format_phone_number(phone: str) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits[0] == "1":
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return phone

templates.env.filters["format_phone"] = format_phone_number

def get_current_user(request: Request, db: Session):
    user_id = request.cookies.get("user_id")
    if user_id:
        try:
            return db.query(models.User).filter(models.User.id == int(user_id)).first()
        except (ValueError, TypeError):
            return None
    return None

def get_settings_dict(db: Session):
    try:
        settings = db.query(models.Setting).all()
        return {s.key: s.value for s in settings}
    except Exception:
        return {}

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def send_email(to_email: str, subject: str, content: str):
    if not to_email:
        return False
    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    
    if not all([smtp_host, smtp_user, smtp_pass]):
        print("Email not sent: SMTP configuration missing.")
        return False

    try:
        msg = EmailMessage()
        msg.set_content(content)
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = to_email

        port_str = os.getenv("SMTP_PORT", "587")
        try:
            port = int(port_str)
        except ValueError:
            port = 587

        with smtplib.SMTP(smtp_host, port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Email sending failed: {e}")
        return False

def render_template(template_name: str, context: dict, db: Session):
    try:
        app_settings = get_settings_dict(db)
        context.update(app_settings)
    except Exception:
        pass
    return templates.TemplateResponse(template_name, context)
