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

# Context processor to inject common variables into templates
# Note: We need access to the templates object. We'll assume it's passed or imported.
from utils import (
    templates, 
    render_template, 
    MEDIA_FOLDER, 
    get_password_hash, 
    send_email,
    get_current_user,
    get_settings_dict
)

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

@router.get("/products", response_class=HTMLResponse)
async def list_products(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    products = db.query(models.Product).all()
    return render_template("admin_products.html", {"request": request, "user": user, "products": products}, db)

@router.get("/products/new", response_class=HTMLResponse)
async def new_product(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return render_template("edit_product.html", {"request": request, "user": user, "product": None}, db)

@router.post("/products/new")
async def create_product(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    has_color: bool = Form(False),
    has_size: bool = Form(False),
    has_text: bool = Form(False),
    custom_text_help: Optional[str] = Form(None),
    price: Optional[str] = Form(None),
    available_colors: list[str] = Form([]),
    available_sizes: list[str] = Form([]),
    size_prices: list[str] = Form([]),
    images: list[UploadFile] = File([]),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    product = models.Product(
        name=name,
        description=description,
        has_color=has_color,
        has_size=has_size,
        has_text=has_text,
        custom_text_help=custom_text_help,
        price=price
    )
    db.add(product)
    db.flush()
    
    if has_color:
        for color_name in available_colors:
            if color_name.strip():
                db_color = models.ProductColor(product_id=product.id, color=color_name.strip())
                db.add(db_color)
    
    if has_size:
        for i, size_name in enumerate(available_sizes):
            if size_name.strip():
                price_val = size_prices[i] if i < len(size_prices) else ""
                db_size = models.ProductSize(product_id=product.id, size=size_name.strip(), price=price_val)
                db.add(db_size)
    
    db.commit()
    db.refresh(product)
    
    # Handle image uploads
    if images and images[0].filename:
        settings_dict = get_settings_dict(db)
        img_folder_name = settings_dict.get("product_images_folder", "product_images")
        
        safe_name = "".join(c for c in product.name if c.isalnum() or c in ('-', '_')).strip().replace(" ", "_")
        if not safe_name: safe_name = f"product_{product.id}"
        
        product_folder = os.path.join(MEDIA_FOLDER, img_folder_name, safe_name)
        if not os.path.exists(product_folder):
            os.makedirs(product_folder)
            
        for i, image in enumerate(images):
            if not image.filename: continue
            
            file_extension = os.path.splitext(image.filename)[1]
            safe_filename = "".join(c for c in image.filename if c.isalnum() or c in ('.', '-', '_')).strip().replace(" ", "_")
            file_path = os.path.join(product_folder, safe_filename)
            
            if os.path.exists(file_path):
                file_path = os.path.join(product_folder, f"{i}_{safe_filename}")

            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(image.file, buffer)
            
            # Save to DB
            rel_path = os.path.relpath(file_path, MEDIA_FOLDER)
            db_img = models.ProductImage(
                product_id=product.id,
                image_url=rel_path,
                display_order=i
            )
            db.add(db_img)
        db.commit()
        
    return RedirectResponse(url="/admin/products", status_code=status.HTTP_303_SEE_OTHER)

@router.get("/products/edit/{product_id}", response_class=HTMLResponse)
async def edit_product_page(request: Request, product_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
        
    return render_template("edit_product.html", {"request": request, "user": user, "product": product}, db)

@router.post("/products/edit/{product_id}")
async def update_product(
    request: Request,
    product_id: int,
    name: str = Form(...),
    description: str = Form(""),
    has_color: bool = Form(False),
    has_size: bool = Form(False),
    has_text: bool = Form(False),
    custom_text_help: Optional[str] = Form(None),
    price: Optional[str] = Form(None),
    available_colors: list[str] = Form([]),
    available_sizes: list[str] = Form([]),
    size_prices: list[str] = Form([]),
    images: list[UploadFile] = File([]),
    deleted_images: str = Form(""),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    product.name = name
    product.description = description
    product.has_color = has_color
    product.has_size = has_size
    product.has_text = has_text
    product.custom_text_help = custom_text_help
    product.price = price

    # Update colors
    db.query(models.ProductColor).filter(models.ProductColor.product_id == product_id).delete()
    if has_color:
        for color_name in available_colors:
            if color_name.strip():
                db_color = models.ProductColor(product_id=product.id, color=color_name.strip())
                db.add(db_color)
    
    # Update sizes
    db.query(models.ProductSize).filter(models.ProductSize.product_id == product_id).delete()
    if has_size:
        for i, size_name in enumerate(available_sizes):
            if size_name.strip():
                price_val = size_prices[i] if i < len(size_prices) else ""
                db_size = models.ProductSize(product_id=product.id, size=size_name.strip(), price=price_val)
                db.add(db_size)
    
    # Handle deleted images
    if deleted_images:
        image_ids = [int(i) for i in deleted_images.split(",") if i.strip()]
        for img_id in image_ids:
            img = db.query(models.ProductImage).filter(models.ProductImage.id == img_id, models.ProductImage.product_id == product_id).first()
            if img:
                # Also delete from filesystem
                full_path = os.path.join(MEDIA_FOLDER, img.image_url)
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    try:
                        os.remove(full_path)
                    except Exception as e:
                        print(f"Error deleting file {full_path}: {e}")
                db.delete(img)
    
    # Handle image uploads if any
    if images and images[0].filename:
        settings_dict = get_settings_dict(db)
        img_folder_name = settings_dict.get("product_images_folder", "product_images")
        
        safe_name = "".join(c for c in product.name if c.isalnum() or c in ('-', '_')).strip().replace(" ", "_")
        if not safe_name: safe_name = f"product_{product.id}"
        
        product_folder = os.path.join(MEDIA_FOLDER, img_folder_name, safe_name)
        if not os.path.exists(product_folder):
            os.makedirs(product_folder)
            
        # Get current max order
        current_images = db.query(models.ProductImage).filter(models.ProductImage.product_id == product.id).all()
        max_order = max([img.display_order for img in current_images]) if current_images else -1
        
        for i, image in enumerate(images):
            if not image.filename: continue
            
            file_extension = os.path.splitext(image.filename)[1]
            safe_filename = "".join(c for c in image.filename if c.isalnum() or c in ('.', '-', '_')).strip().replace(" ", "_")
            file_path = os.path.join(product_folder, safe_filename)
            
            if os.path.exists(file_path):
                file_path = os.path.join(product_folder, f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{safe_filename}")

            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(image.file, buffer)
            
            rel_path = os.path.relpath(file_path, MEDIA_FOLDER)
            db_img = models.ProductImage(
                product_id=product.id,
                image_url=rel_path,
                display_order=max_order + i + 1
            )
            db.add(db_img)
            
    db.commit()
    return RedirectResponse(url="/admin/products", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/products/delete/{product_id}")
async def delete_product(request: Request, product_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if product:
        # Get info for folder deletion before DB delete
        settings_dict = get_settings_dict(db)
        img_folder_name = settings_dict.get("product_images_folder", "product_images")
        safe_name = "".join(c for c in product.name if c.isalnum() or c in ('-', '_')).strip().replace(" ", "_")
        
        db.delete(product)
        db.commit()
        
        # Delete folder
        if safe_name:
            product_folder = os.path.join(MEDIA_FOLDER, img_folder_name, safe_name)
            if os.path.exists(product_folder) and os.path.isdir(product_folder):
                try:
                    shutil.rmtree(product_folder)
                except Exception as e:
                    print(f"Error deleting folder {product_folder}: {e}")
        
    return RedirectResponse(url="/admin/products", status_code=status.HTTP_303_SEE_OTHER)

@router.get("/orders", response_class=HTMLResponse)
async def list_orders(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    orders = db.query(models.Order).order_by(models.Order.created_at.desc()).all()
    return render_template("admin_orders.html", {"request": request, "user": user, "orders": orders}, db)

@router.post("/orders/{order_id}/approve")
async def approve_order(request: Request, order_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order:
        order.status = "Approved"
        db.commit()
        
        # Email member
        if order.user and order.user.email:
            items_text = ""
            for item in order.items:
                p_name = item.product.name if item.product else "Deleted Product"
                items_text += f"- {p_name} (Qty: {item.quantity})"
                if item.color: items_text += f", Color: {item.color}"
                if item.size: items_text += f", Size: {item.size}"
                if item.text: items_text += f", Text: {item.text}"
                items_text += f"\n"
            
            content = f"Your order has been approved.\n\nItems:\n{items_text}\nTotal Amount: {order.total_price or 'N/A'}\n"
            send_email(order.user.email, "Order Approved", content)
            
    return RedirectResponse(url="/admin/orders", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/orders/{order_id}/deny")
async def deny_order(request: Request, order_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order:
        order.status = "Denied"
        db.commit()
        
        # Email member
        if order.user and order.user.email:
            items_text = ""
            for item in order.items:
                p_name = item.product.name if item.product else "Deleted Product"
                items_text += f"- {p_name} (Qty: {item.quantity})"
                if item.color: items_text += f", Color: {item.color}"
                if item.size: items_text += f", Size: {item.size}"
                if item.text: items_text += f", Text: {item.text}"
                items_text += f"\n"
            
            content = f"Your order for the following items has been denied:\n\n{items_text}"
            send_email(order.user.email, "Order Denied", content)
            
    return RedirectResponse(url="/admin/orders", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/orders/{order_id}/complete")
async def complete_order(request: Request, order_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order:
        order.status = "Delivered"
        order.completed_at = datetime.utcnow()
        db.commit()
        
    return RedirectResponse(url="/admin/orders", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/orders/{order_id}/mark-paid")
async def mark_order_paid(request: Request, order_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order:
        order.payment_status = "Paid"
        db.commit()
        
    return RedirectResponse(url="/admin/orders", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/orders/{order_id}/delete")
async def delete_order(request: Request, order_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order:
        db.delete(order)
        db.commit()
        
    return RedirectResponse(url="/admin/orders", status_code=status.HTTP_303_SEE_OTHER)
