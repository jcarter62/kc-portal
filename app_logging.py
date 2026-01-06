import logging
import time
from fastapi import Request
import models
from database import SessionLocal
import os

# Configure logging
LOG_FILE = os.getenv("LOG_FILE", "app.log")

# Create a custom logger
logger = logging.getLogger("kc-portal")
logger.setLevel(logging.INFO)

# Create handlers
c_handler = logging.StreamHandler()
f_handler = logging.FileHandler(LOG_FILE)
c_handler.setLevel(logging.INFO)
f_handler.setLevel(logging.INFO)

# Create formatters and add it to handlers
log_format = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
c_handler.setFormatter(log_format)
f_handler.setFormatter(log_format)

# Add handlers to the logger
logger.addHandler(c_handler)
logger.addHandler(f_handler)

# Disable uvicorn access logging to avoid duplicate/standard logs
logging.getLogger("uvicorn.access").disabled = True

def get_client_type(user_agent: str) -> str:
    if not user_agent:
        return "Unknown"
    
    ua = user_agent.lower()
    # Check for mobile first as they often contain "like Mac OS X" or "Linux"
    if "iphone" in ua or "ipad" in ua:
        return "iOS"
    if "android" in ua:
        return "Android"
    if "windows" in ua:
        return "Windows"
    if "macintosh" in ua or "mac os" in ua:
        return "Mac"
    if "linux" in ua:
        return "Linux"
    
    return "Other"

async def log_requests(request: Request, call_next):
    client_ip = request.headers.get("CF-Connecting-IP") or request.client.host
    user_agent = request.headers.get("User-Agent", "")
    client_type = get_client_type(user_agent)
    
    # Get user info for logging
    user_id = request.cookies.get("user_id")
    user_info = "Anonymous"
    if user_id:
        try:
            db = SessionLocal()
            user = db.query(models.User).filter(models.User.id == int(user_id)).first()
            if user:
                user_info = f"{user.first_name or ''} {user.last_name or ''}".strip() or f"User ID: {user_id}"
            else:
                user_info = f"User ID: {user_id} (Not Found)"
            db.close()
        except Exception:
            user_info = f"User ID: {user_id} (Error)"
    
    start_time = time.time()
    response = await call_next(request)
    process_time = (time.time() - start_time) * 1000
    
    logger.info(
        f"IP: {client_ip} ({client_type}) - {user_info} - {request.method} {request.url.path} - "
        f"Status: {response.status_code} - Completed in {process_time:.2f}ms"
    )
    return response
