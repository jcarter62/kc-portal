from fastapi import FastAPI, Request, Depends, Form, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, case
from starlette.middleware.sessions import SessionMiddleware
import models
from database import engine, get_db
import os
from dotenv import load_dotenv
import csv
from typing import Optional
import io
import codecs
from passlib.context import CryptContext
import shutil
import uuid
import smtplib
from email.message import EmailMessage
import secrets
from datetime import datetime, timedelta
import re
import app_logging
import pandas as pd
from utils import (
    MEDIA_FOLDER, 
    templates, 
    get_current_user, 
    get_settings_dict, 
    verify_password, 
    get_password_hash, 
    send_email, 
    render_template,
    format_phone_number
)

load_dotenv()

models.Base.metadata.create_all(bind=engine)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET_KEY") or os.getenv("SECRET_KEY", secrets.token_hex(32)))

# Middleware for logging and security
@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    # Restrict access to documentation in production to local IPs only
    if request.url.path.rstrip("/") in ["/docs", "/redoc"] or request.url.path == "/openapi.json":
        client_ip = request.headers.get("CF-Connecting-IP") or request.client.host
        
        # Simple check for local LAN IP ranges
        is_local = client_ip in ("127.0.0.1", "::1") or \
                   client_ip.startswith("192.168.") or \
                   client_ip.startswith("10.") or \
                   client_ip.startswith("172.")
        
        if not is_local and os.getenv("DEBUG", "false").lower() != "true":
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN, 
                content={"detail": "Access to documentation is restricted to local network."}
            )
            
    return await app_logging.log_requests(request, call_next)

# Mount static files if directory exists, otherwise create it
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")


# Helper to validate password strength
def is_password_strong(password: str) -> bool:
    if len(password) < 10:
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[A-Z]", password):
        return False
    return True


# Import and include admin and store routers
import admin
import store
app.include_router(admin.router)
app.include_router(store.router)

@app.get("/favicon.ico", include_in_schema=False)
@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
async def favicon():
    return FileResponse("static/kofc_r_emblem_rgb_pos.png")

@app.get("/robots.txt", include_in_schema=False)
async def robots():
    return FileResponse("robots.txt")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    page = db.query(models.Page).filter(models.Page.slug == "home").first()
    return render_template("home.html", {"request": request, "user": user, "page": page}, db)

@app.head("/", response_class=HTMLResponse)
async def head_home():
    home_head = "<html><head><title>Portal Head</title></head></html>"
    return home_head

@app.get("/about", response_class=HTMLResponse)
async def about(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    page = db.query(models.Page).filter(models.Page.slug == "about").first()
    return render_template("about.html", {"request": request, "user": user, "page": page}, db)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    return render_template("login.html", {"request": request}, db)

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    # Username can be membership number or email, case-insensitive
    user = db.query(models.User).filter(
        or_(
            func.lower(models.User.membership_number) == username.lower(),
            func.lower(models.User.email) == username.lower()
        )
    ).first()
    
    if not user:
        return render_template("login.html", {"request": request, "error": "Invalid credentials"}, db)
    
    # Check password
    user_password = db.query(models.UserPassword).filter(models.UserPassword.membership_number == user.membership_number).first()
    
    # If no password record exists, create one using membership number as default
    if not user_password:
        hashed_pwd = get_password_hash(user.membership_number)
        user_password = models.UserPassword(membership_number=user.membership_number, password_hash=hashed_pwd)
        db.add(user_password)
        db.commit()
        # Verify against the newly created default password
        if password != user.membership_number:
             return render_template("login.html", {"request": request, "error": "Invalid credentials"}, db)
    else:
        if not verify_password(password, user_password.password_hash):
            return render_template("login.html", {"request": request, "error": "Invalid credentials"}, db)

    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="user_id", value=str(user.id))
    return response

@app.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("user_id")
    return response

@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, db: Session = Depends(get_db)):
    return render_template("forgot_password.html", {"request": request}, db)

@app.post("/forgot-password")
async def forgot_password(
    request: Request,
    email: str = Form(...),
    membership_number: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(
        func.lower(models.User.email) == email.lower(),
        models.User.membership_number == membership_number
    ).first()

    if not user:
        return render_template("forgot_password.html", {"request": request, "error": "Invalid email or membership number."}, db)

    # Generate reset key
    reset_key = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=1)
    
    new_reset = models.PasswordReset(
        user_id=user.id,
        key=reset_key,
        expires_at=expires_at
    )
    db.add(new_reset)
    db.commit()

    # Send email
    try:
        msg = EmailMessage()
        msg.set_content(f"Click the link to reset your password: {request.url_for('reset_password_page')}?key={reset_key}")
        msg["Subject"] = "Password Reset Request"
        msg["From"] = os.getenv("SMTP_USER")
        msg["To"] = user.email

        with smtplib.SMTP(os.getenv("SMTP_HOST"), os.getenv("SMTP_PORT")) as server:
            server.starttls()
            server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"))
            server.send_message(msg)
        
        return render_template("forgot_password.html", {"request": request, "message": "Password reset link sent to your email."}, db)
    except Exception as e:
        print(f"Email sending failed: {e}")
        return render_template("forgot_password.html", {"request": request, "error": "Could not send reset email. Please contact an administrator."}, db)

@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, key: str, db: Session = Depends(get_db)):
    reset_request = db.query(models.PasswordReset).filter(
        models.PasswordReset.key == key,
        models.PasswordReset.used == False,
        models.PasswordReset.expires_at > datetime.utcnow()
    ).first()

    if not reset_request:
        return render_template("reset_password.html", {"request": request, "error": "Invalid or expired reset key."}, db)
    
    return render_template("reset_password.html", {"request": request, "key": key}, db)

@app.post("/reset-password")
async def reset_password(
    request: Request,
    key: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db)
):
    if new_password != confirm_password:
        return render_template("reset_password.html", {"request": request, "key": key, "error": "Passwords do not match."}, db)

    if not is_password_strong(new_password):
        return render_template("reset_password.html", {"request": request, "key": key, "error": "Password must be at least 10 characters long and contain both upper and lower case letters."}, db)

    reset_request = db.query(models.PasswordReset).filter(
        models.PasswordReset.key == key,
        models.PasswordReset.used == False,
        models.PasswordReset.expires_at > datetime.utcnow()
    ).first()

    if not reset_request:
        return render_template("reset_password.html", {"request": request, "error": "Invalid or expired reset key."}, db)

    user = db.query(models.User).filter(models.User.id == reset_request.user_id).first()
    if not user:
        return render_template("reset_password.html", {"request": request, "error": "User not found."}, db)

    user_password = db.query(models.UserPassword).filter(models.UserPassword.membership_number == user.membership_number).first()
    if not user_password:
        user_password = models.UserPassword(membership_number=user.membership_number)
        db.add(user_password)

    user_password.password_hash = get_password_hash(new_password)
    reset_request.used = True
    db.commit()

    return render_template("login.html", {"request": request, "message": "Password has been reset successfully. Please log in."}, db)

@app.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    return render_template("change_password.html", {"request": request, "user": user}, db)

@app.post("/change-password")
async def change_password(
    request: Request, 
    current_password: str = Form(...), 
    new_password: str = Form(...), 
    confirm_password: str = Form(...), 
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    if new_password != confirm_password:
        return render_template("change_password.html", {"request": request, "user": user, "error": "New passwords do not match"}, db)
    
    if not is_password_strong(new_password):
        return render_template("change_password.html", {"request": request, "user": user, "error": "Password must be at least 10 characters long and contain both upper and lower case letters."}, db)

    user_password = db.query(models.UserPassword).filter(models.UserPassword.membership_number == user.membership_number).first()
    
    # Verify current password
    if not user_password or not verify_password(current_password, user_password.password_hash):
        return render_template("change_password.html", {"request": request, "user": user, "error": "Incorrect current password"}, db)
    
    # Update password
    user_password.password_hash = get_password_hash(new_password)
    db.commit()
    
    return render_template("change_password.html", {"request": request, "user": user, "message": "Password changed successfully"}, db)

@app.get("/members", response_class=HTMLResponse)
async def members(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    members = db.query(models.User).all()
    # Sort members by last_name and then first_name
    members.sort(key=lambda x: (x.last_name, x.first_name))
    return render_template("members.html", {"request": request, "user": user, "members": members}, db)

@app.get("/contact-list", response_class=HTMLResponse)
async def contact_list(
    request: Request, 
    sort_by: str = "last_name", 
    positions_first: bool = False,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    query = db.query(models.User)
    
    order_by_clauses = []
    
    if positions_first:
        # Sort by position is not null/empty first
        order_by_clauses.append(case((or_(models.User.position == None, models.User.position == ""), 1), else_=0))
        order_by_clauses.append(models.User.position)

    if sort_by == "phone":
        order_by_clauses.append(models.User.phone_number)
    elif sort_by == "email":
        order_by_clauses.append(models.User.email)
    else: # default last_name
        order_by_clauses.append(models.User.last_name)
        order_by_clauses.append(models.User.first_name)
        
    members = query.order_by(*order_by_clauses).all()
    
    return render_template("contact_list.html", {
        "request": request, 
        "user": user, 
        "members": members,
        "sort_by": sort_by,
        "positions_first": positions_first,
        "current_time": datetime.now()
    }, db)

@app.get("/contact-list/download")
async def download_contact_list(
    sort_by: str = "last_name", 
    positions_first: bool = False,
    db: Session = Depends(get_db)
):
    query = db.query(models.User)
    order_by_clauses = []
    
    if positions_first:
        order_by_clauses.append(case((or_(models.User.position == None, models.User.position == ""), 1), else_=0))
        order_by_clauses.append(models.User.position)

    if sort_by == "phone":
        order_by_clauses.append(models.User.phone_number)
    elif sort_by == "email":
        order_by_clauses.append(models.User.email)
    else:
        order_by_clauses.append(models.User.last_name)
        order_by_clauses.append(models.User.first_name)
        
    members = query.order_by(*order_by_clauses).all()
    
    data = []
    for m in members:
        data.append({
            "First Name": m.first_name,
            "Last Name": m.last_name,
            "Email": m.email,
            "Phone": format_phone_number(m.phone_number),
            "Position": m.position or ""
        })
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Contacts')
    
    output.seek(0)
    
    headers = {
        'Content-Disposition': 'attachment; filename="contact_list.xlsx"'
    }
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.get("/calendar", response_class=HTMLResponse)
async def calendar(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)

    page = db.query(models.Page).filter(models.Page.slug == "calendar").first()
    return render_template("calendar.html", {"request": request, "user": user, "page": page}, db)

@app.get("/media/{file_path:path}")
async def get_media(file_path: str):
    # Prevent directory traversal
    safe_path = os.path.normpath(os.path.join(MEDIA_FOLDER, file_path))
    if not os.path.abspath(safe_path).startswith(os.path.abspath(MEDIA_FOLDER)):
        raise HTTPException(status_code=404, detail="File not found")
        
    if os.path.exists(safe_path) and os.path.isfile(safe_path):
        return FileResponse(safe_path)
    raise HTTPException(status_code=404, detail="File not found")

# Dynamic page route - MUST be last to avoid conflicts with other routes
@app.get("/{slug}", response_class=HTMLResponse)
async def view_page(request: Request, slug: str, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    page = db.query(models.Page).filter(models.Page.slug == slug).first()
    
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    
    if not page.is_public and not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        
    return render_template("home.html", {"request": request, "user": user, "page": page}, db)

# Initialize default pages and settings if not exist
@app.on_event("startup")
async def startup_event():
    db = next(get_db())
    # Create default pages
    pages = [
        {"title": "Home", "slug": "home", "content": "<h1>Welcome to Knights of Columbus</h1>", "is_public": True},
        {"title": "About", "slug": "about", "content": "<h1>About Us</h1><p>Org information goes here.</p>", "is_public": True},
        {"title": "Calendar", "slug": "calendar", "content": "<h1>Calendar</h1><p>Upcoming events.</p>", "is_public": False},
    ]
    for page_data in pages:
        if not db.query(models.Page).filter(models.Page.slug == page_data["slug"]).first():
            db.add(models.Page(**page_data))
    
    # Create default settings
    settings = [
        {"key": "council_name", "value": "My Council"},
        {"key": "council_number", "value": "1234"},
        {"key": "app_title", "value": "KC Portal"},
        {"key": "email_text", "value": "Welcome to our portal"},
        {"key": "order_notification_email", "value": "admin@example.com"},
        {"key": "product_images_folder", "value": "product_images"},
        {"key": "paypal_client_id", "value": ""},
        {"key": "paypal_enabled", "value": "false"},
        {"key": "paypal_mode", "value": "sandbox"},
        {"key": "manual_payment_instructions", "value": "Please pay via Check or Cash to the financial secretary."}
    ]
    for setting_data in settings:
        if not db.query(models.Setting).filter(models.Setting.key == setting_data["key"]).first():
            db.add(models.Setting(**setting_data))
    
    # Create a default admin user if no users exist
    if not db.query(models.User).first():
        admin_username = os.getenv("INIT_ADMIN_USER", "admin")
        admin_password = os.getenv("INIT_ADMIN_PASS", "Admin12345")
        
        admin_user = models.User(
            membership_number=admin_username,
            first_name="Admin",
            last_name="User",
            email="admin@example.com",
            is_admin=True
        )
        db.add(admin_user)
        
        # Create password for admin
        hashed_pwd = get_password_hash(admin_password)
        admin_pwd = models.UserPassword(membership_number=admin_username, password_hash=hashed_pwd)
        db.add(admin_pwd)
    
    # Initialize passwords for existing users if missing
    users = db.query(models.User).all()
    for user in users:
        if not user.membership_number:
            continue
            
        # Check if password record exists
        pwd_record = db.query(models.UserPassword).filter(models.UserPassword.membership_number == user.membership_number).first()
        if not pwd_record:
            hashed_pwd = get_password_hash(user.membership_number)
            new_pwd = models.UserPassword(membership_number=user.membership_number, password_hash=hashed_pwd)
            db.add(new_pwd)
    
    db.commit()
