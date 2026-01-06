from fastapi import APIRouter, Request, Depends, Form, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
import models
from database import get_db
import os
import csv
import io
import shutil
from datetime import datetime
from typing import Optional

router = APIRouter(prefix="/admin")

# Helper to get current user from session (duplicated from main.py or should be in a shared utils)
def get_current_user(request: Request, db: Session):
    user_id = request.cookies.get("user_id")
    if user_id:
        return db.query(models.User).filter(models.User.id == int(user_id)).first()
    return None

# Helper to get global settings
def get_settings_dict(db: Session):
    settings = db.query(models.Setting).all()
    return {s.key: s.value for s in settings}

# Context processor to inject common variables into templates
# Note: We need access to the templates object. We'll assume it's passed or imported.
from main import templates, render_template, MEDIA_FOLDER, get_password_hash

@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return render_template("import.html", {"request": request, "user": user}, db)

@router.post("/import")
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

@router.get("/pages", response_class=HTMLResponse)
async def list_pages(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    pages = db.query(models.Page).all()
    return render_template("admin_pages.html", {"request": request, "user": user, "pages": pages}, db)

@router.get("/pages/new", response_class=HTMLResponse)
async def new_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return render_template("edit_page.html", {"request": request, "user": user, "page": None}, db)

@router.post("/pages/new")
async def create_page(request: Request, title: str = Form(...), slug: str = Form(...), content: str = Form(...), is_public: bool = Form(False), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    new_page = models.Page(title=title, slug=slug, content=content, is_public=is_public)
    db.add(new_page)
    db.commit()
    return RedirectResponse(url="/admin/pages", status_code=status.HTTP_303_SEE_OTHER)

@router.get("/pages/{page_id}/edit", response_class=HTMLResponse)
async def edit_page(request: Request, page_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    page = db.query(models.Page).filter(models.Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    
    return render_template("edit_page.html", {"request": request, "user": user, "page": page}, db)

@router.get("/api/pages/{page_id}")
async def get_page_content(request: Request, page_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    page = db.query(models.Page).filter(models.Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    
    return JSONResponse(content={"content": page.content})

@router.post("/pages/{page_id}/edit")
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

@router.post("/pages/{page_id}/delete")
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

@router.get("/users", response_class=HTMLResponse)
async def list_users(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    users = db.query(models.User).all()
    return render_template("admin_users.html", {"request": request, "user": user, "users": users}, db)

@router.get("/users/new", response_class=HTMLResponse)
async def new_user(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return render_template("edit_user.html", {"request": request, "user": user, "edit_user": None}, db)

@router.post("/users/new")
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

@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    edit_user = db.query(models.User).filter(models.User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return render_template("edit_user.html", {"request": request, "user": user, "edit_user": edit_user}, db)

@router.post("/users/{user_id}/edit")
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

@router.post("/users/{user_id}/delete")
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

@router.get("/settings", response_class=HTMLResponse)
async def list_settings(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    settings = db.query(models.Setting).all()
    return render_template("admin_settings.html", {"request": request, "user": user, "settings": settings}, db)

@router.post("/settings")
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

@router.post("/upload-image")
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
