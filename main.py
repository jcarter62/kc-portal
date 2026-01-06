from fastapi import FastAPI, Request, Depends, Form, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
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

load_dotenv()

models.Base.metadata.create_all(bind=engine)

app = FastAPI()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Mount static files if directory exists, otherwise create it
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Media configuration
MEDIA_FOLDER = os.getenv("MEDIA_FOLDER")
if not MEDIA_FOLDER:
    # Fallback if not set in .env
    MEDIA_FOLDER = os.path.join(os.getcwd(), "media")

if not os.path.exists(MEDIA_FOLDER):
    os.makedirs(MEDIA_FOLDER)

templates = Jinja2Templates(directory="templates")
templates.env.globals['now'] = datetime.utcnow

# Helper to get current user from session
def get_current_user(request: Request, db: Session):
    user_id = request.cookies.get("user_id")
    if user_id:
        return db.query(models.User).filter(models.User.id == int(user_id)).first()
    return None

# Helper to get global settings
def get_settings_dict(db: Session):
    settings = db.query(models.Setting).all()
    return {s.key: s.value for s in settings}

# Helper to verify password
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

# Helper to get password hash
def get_password_hash(password):
    return pwd_context.hash(password)

# Helper to validate password strength
def is_password_strong(password: str) -> bool:
    if len(password) < 10:
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[A-Z]", password):
        return False
    return True

# Context processor to inject common variables into templates
def render_template(template_name: str, context: dict, db: Session):
    # Add settings to context
    try:
        app_settings = get_settings_dict(db)
        context.update(app_settings)
    except Exception:
        # If getting settings fails (e.g. due to rollback), just proceed without them
        pass
    return templates.TemplateResponse(template_name, context)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    page = db.query(models.Page).filter(models.Page.slug == "home").first()
    return render_template("home.html", {"request": request, "user": user, "page": page}, db)

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

@app.get("/calendar", response_class=HTMLResponse)
async def calendar(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)

    page = db.query(models.Page).filter(models.Page.slug == "calendar").first()
    return render_template("calendar.html", {"request": request, "user": user, "page": page}, db)

@app.get("/admin/import", response_class=HTMLResponse)
async def import_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return render_template("import.html", {"request": request, "user": user}, db)

@app.post("/admin/import")
async def import_users(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    try:
        content = await file.read()
        # Handle potential BOM in CSV files (common in Excel exports)
        decoded_content = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(decoded_content))
        
        count = 0
        for row in reader:
            membership_number = row.get("Membership Number")
            if not membership_number:
                continue
            
            # Check if user exists by membership number
            existing_user = db.query(models.User).filter(models.User.membership_number == membership_number).first()
            if existing_user:
                continue

            # Handle empty email strings which cause unique constraint violations
            email = row.get("Primary Email")
            if not email or email.strip() == "":
                email = None
            
            # If email is present, check if it's already taken by another user
            if email:
                existing_email = db.query(models.User).filter(models.User.email == email).first()
                if existing_email:
                    # Skip this user or handle as needed (e.g. log warning)
                    continue

            new_user = models.User(
                membership_number=membership_number,
                first_name=row.get("First Name"),
                last_name=row.get("Last Name"),
                email=email,
                phone_number=row.get("Cell Phone") or row.get("Residence Phone"),
                is_admin=False # Default to false
            )
            db.add(new_user)
            
            # Create default password (membership number)
            hashed_pwd = get_password_hash(membership_number)
            new_pwd = models.UserPassword(membership_number=membership_number, password_hash=hashed_pwd)
            db.add(new_pwd)
            
            count += 1
        db.commit()
        return render_template("import.html", {"request": request, "user": user, "message": f"Import successful. {count} users added."}, db)
    except Exception as e:
        db.rollback() # Rollback the transaction on error
        return render_template("import.html", {"request": request, "user": user, "error": str(e)}, db)

@app.get("/admin/pages", response_class=HTMLResponse)
async def list_pages(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    pages = db.query(models.Page).all()
    return render_template("admin_pages.html", {"request": request, "user": user, "pages": pages}, db)

@app.get("/admin/pages/new", response_class=HTMLResponse)
async def new_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return render_template("edit_page.html", {"request": request, "user": user, "page": None}, db)

@app.post("/admin/pages/new")
async def create_page(request: Request, title: str = Form(...), slug: str = Form(...), content: str = Form(...), is_public: bool = Form(False), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    new_page = models.Page(title=title, slug=slug, content=content, is_public=is_public)
    db.add(new_page)
    db.commit()
    return RedirectResponse(url="/admin/pages", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/admin/pages/{page_id}/edit", response_class=HTMLResponse)
async def edit_page(request: Request, page_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    page = db.query(models.Page).filter(models.Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    
    return render_template("edit_page.html", {"request": request, "user": user, "page": page}, db)

@app.get("/admin/api/pages/{page_id}")
async def get_page_content(request: Request, page_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    page = db.query(models.Page).filter(models.Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    
    return JSONResponse(content={"content": page.content})

@app.post("/admin/pages/{page_id}/edit")
async def update_page(
    request: Request, 
    page_id: int, 
    title: str = Form(...), 
    slug: str = Form(...), 
    content: str = Form(...), 
    is_public: bool = Form(False), 
    deleted_images: str = Form(""),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    page = db.query(models.Page).filter(models.Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    
    page.title = title
    page.slug = slug
    page.content = content
    page.is_public = is_public
    db.commit()

    # Handle image deletion after commit
    if deleted_images:
        image_paths_to_delete = deleted_images.split(',')
        for image_path in image_paths_to_delete:
            if not image_path:
                continue
            
            # Security check
            full_path = os.path.normpath(os.path.join(MEDIA_FOLDER, image_path))
            if os.path.abspath(full_path).startswith(os.path.abspath(MEDIA_FOLDER)):
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    try:
                        os.remove(full_path)
                    except OSError as e:
                        print(f"Error deleting file {full_path}: {e}")

    return RedirectResponse(url="/admin/pages", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/admin/pages/{page_id}/delete")
async def delete_page(request: Request, page_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    page = db.query(models.Page).filter(models.Page.id == page_id).first()
    if page:
        page_slug = page.slug
        db.delete(page)
        db.commit()

        # Also delete the associated media folder
        if page_slug:
            slug_folder = os.path.join(MEDIA_FOLDER, page_slug)
            # Security check
            safe_path = os.path.normpath(slug_folder)
            if os.path.abspath(safe_path).startswith(os.path.abspath(MEDIA_FOLDER)) and os.path.isdir(safe_path):
                try:
                    shutil.rmtree(safe_path)
                except OSError as e:
                    print(f"Error deleting folder {safe_path}: {e}")

    return RedirectResponse(url="/admin/pages", status_code=status.HTTP_303_SEE_OTHER)

# User Management Routes

@app.get("/admin/users", response_class=HTMLResponse)
async def list_users(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    users = db.query(models.User).all()
    return render_template("admin_users.html", {"request": request, "user": user, "users": users}, db)

@app.get("/admin/users/new", response_class=HTMLResponse)
async def new_user(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return render_template("edit_user.html", {"request": request, "user": user, "edit_user": None}, db)

@app.post("/admin/users/new")
async def create_user(
    request: Request, 
    membership_number: str = Form(...), 
    first_name: str = Form(None), 
    last_name: str = Form(None), 
    email: str = Form(None), 
    phone_number: str = Form(None), 
    position: str = Form(None),
    is_admin: bool = Form(False), 
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    # Check for existing membership number
    if db.query(models.User).filter(models.User.membership_number == membership_number).first():
        return render_template("edit_user.html", {"request": request, "user": user, "edit_user": None, "error": "Membership number already exists"}, db)
    
    # Check for existing email if provided
    if email and email.strip() == "":
        email = None
    if email and db.query(models.User).filter(models.User.email == email).first():
        return render_template("edit_user.html", {"request": request, "user": user, "edit_user": None, "error": "Email already exists"}, db)

    new_user = models.User(
        membership_number=membership_number,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone_number=phone_number,
        position=position,
        is_admin=is_admin
    )
    db.add(new_user)
    
    # Create default password (membership number)
    hashed_pwd = get_password_hash(membership_number)
    new_pwd = models.UserPassword(membership_number=membership_number, password_hash=hashed_pwd)
    db.add(new_pwd)
    
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    edit_user = db.query(models.User).filter(models.User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return render_template("edit_user.html", {"request": request, "user": user, "edit_user": edit_user}, db)

@app.post("/admin/users/{user_id}/edit")
async def update_user(
    request: Request, 
    user_id: int, 
    membership_number: str = Form(...), 
    first_name: str = Form(None), 
    last_name: str = Form(None), 
    email: str = Form(None), 
    phone_number: str = Form(None), 
    position: str = Form(None),
    is_admin: bool = Form(False), 
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    edit_user = db.query(models.User).filter(models.User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check for existing membership number (exclude current user)
    existing_mem = db.query(models.User).filter(models.User.membership_number == membership_number).first()
    if existing_mem and existing_mem.id != user_id:
        return render_template("edit_user.html", {"request": request, "user": user, "edit_user": edit_user, "error": "Membership number already exists"}, db)
    
    # Check for existing email if provided (exclude current user)
    if email and email.strip() == "":
        email = None
    if email:
        existing_email = db.query(models.User).filter(models.User.email == email).first()
        if existing_email and existing_email.id != user_id:
            return render_template("edit_user.html", {"request": request, "user": user, "edit_user": edit_user, "error": "Email already exists"}, db)

    # If membership number changes, we need to update the password record too
    old_membership_number = edit_user.membership_number
    
    edit_user.membership_number = membership_number
    edit_user.first_name = first_name
    edit_user.last_name = last_name
    edit_user.email = email
    edit_user.phone_number = phone_number

    if position.lower() == 'none':
        position = None

    edit_user.position = position
    edit_user.is_admin = is_admin
    
    if old_membership_number != membership_number:
        pwd_record = db.query(models.UserPassword).filter(models.UserPassword.membership_number == old_membership_number).first()
        if pwd_record:
            pwd_record.membership_number = membership_number
    
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/admin/users/{user_id}/delete")
async def delete_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    # Prevent deleting yourself
    if user.id == user_id:
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)

    del_user = db.query(models.User).filter(models.User.id == user_id).first()
    if del_user:
        db.delete(del_user)
        db.commit()
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)

# Settings Management Routes

@app.get("/admin/settings", response_class=HTMLResponse)
async def list_settings(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    settings = db.query(models.Setting).all()
    return render_template("admin_settings.html", {"request": request, "user": user, "settings": settings}, db)

@app.post("/admin/settings")
async def update_settings(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    form_data = await request.form()
    
    # Update existing settings
    for key, value in form_data.items():
        if key == "new_key" or key == "new_value":
            continue
        
        setting = db.query(models.Setting).filter(models.Setting.key == key).first()
        if setting:
            setting.value = value
    
    # Add new setting if provided
    new_key = form_data.get("new_key")
    new_value = form_data.get("new_value")
    if new_key and new_value:
        # Check if key already exists
        if not db.query(models.Setting).filter(models.Setting.key == new_key).first():
            new_setting = models.Setting(key=new_key, value=new_value)
            db.add(new_setting)
            
    db.commit()
    return RedirectResponse(url="/admin/settings", status_code=status.HTTP_303_SEE_OTHER)

# Media Routes
@app.post("/admin/upload-image")
async def upload_image(request: Request, image: UploadFile = File(...), slug: str = Form(...), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Sanitize slug
    safe_slug = slug.strip()
    if not safe_slug:
        safe_slug = "general"
    
    # Ensure slug is safe for directory name (remove path separators)
    safe_slug = "".join(c for c in safe_slug if c.isalnum() or c in ('-', '_'))
    
    # Create directory
    upload_dir = os.path.join(MEDIA_FOLDER, safe_slug)
    if not os.path.exists(upload_dir):
        os.makedirs(upload_dir)
    
    # Sanitize filename
    original_filename = image.filename
    safe_filename = original_filename.replace(" ", "_")
    # Basic path traversal protection for filename
    safe_filename = os.path.basename(safe_filename)
    
    file_path = os.path.join(upload_dir, safe_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(image.file, buffer)
        
    # Return the URL to the image
    return {"url": f"/media/{safe_slug}/{safe_filename}"}

@app.get("/media/{file_path:path}")
async def get_media(file_path: str):
    # Prevent directory traversal
    safe_path = os.path.normpath(os.path.join(MEDIA_FOLDER, file_path))
    if not safe_path.startswith(os.path.abspath(MEDIA_FOLDER)):
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
