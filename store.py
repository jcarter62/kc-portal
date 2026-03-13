from fastapi import APIRouter, Request, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
import models
from database import get_db
from typing import Optional
from utils import (
    templates, 
    get_current_user, 
    get_settings_dict, 
    send_email, 
    render_template
)

router = APIRouter(prefix="/store")

@router.get("/")
async def store_root():
    return RedirectResponse(url="/store/order-products", status_code=status.HTTP_302_FOUND)

@router.get("/order-products", response_class=HTMLResponse)
async def order_products_page(request: Request, message: Optional[str] = None, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    products = db.query(models.Product).all()
    return render_template("order_products.html", {"request": request, "user": user, "products": products, "message": message}, db)

@router.get("/product/{product_id}", response_class=HTMLResponse)
async def product_detail(request: Request, product_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
        
    return render_template("product_detail.html", {"request": request, "user": user, "product": product}, db)

@router.post("/add-to-cart")
async def add_to_cart(
    request: Request,
    product_id: int = Form(...),
    quantity: int = Form(1),
    color: Optional[str] = Form(None),
    size: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Determine price
    price_str = product.price or "0"
    if size:
        size_obj = db.query(models.ProductSize).filter(
            models.ProductSize.product_id == product_id, 
            models.ProductSize.size == size
        ).first()
        if size_obj and size_obj.price:
            price_str = size_obj.price
            
    # Add to session cart
    cart = request.session.get("cart", [])
    cart.append({
        "product_id": product_id,
        "product_name": product.name,
        "quantity": quantity,
        "color": color,
        "size": size,
        "text": text,
        "price": price_str
    })
    request.session["cart"] = cart
    
    return RedirectResponse(url="/store/cart", status_code=status.HTTP_303_SEE_OTHER)

@router.get("/cart", response_class=HTMLResponse)
async def view_cart(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    cart = request.session.get("cart", [])
    total_val = 0.0
    
    # Process cart items for display and calculate total
    display_cart = []
    for item in cart:
        # Try to parse price for total calculation
        try:
            p_clean = item["price"].replace('$', '').replace(',', '').strip()
            p_float = float(p_clean)
            subtotal_val = p_float * item["quantity"]
            total_val += subtotal_val
            subtotal_str = f"${subtotal_val:,.2f}"
        except (ValueError, AttributeError, TypeError):
            subtotal_str = "N/A"
            
        display_cart.append({
            **item,
            "subtotal": subtotal_str
        })
            
    return render_template("cart.html", {
        "request": request, 
        "user": user, 
        "cart": display_cart, 
        "total": f"${total_val:,.2f}",
        "total_val": total_val
    }, db)

@router.get("/cart/remove/{index}")
async def remove_from_cart(request: Request, index: int):
    cart = request.session.get("cart", [])
    if 0 <= index < len(cart):
        cart.pop(index)
        request.session["cart"] = cart
    return RedirectResponse(url="/store/cart", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/checkout")
async def checkout(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    cart = request.session.get("cart", [])
    if not cart:
        return RedirectResponse(url="/store/order-products", status_code=status.HTTP_303_SEE_OTHER)
        
    total_val = 0.0
    for item in cart:
        try:
            p_clean = item["price"].replace('$', '').replace(',', '').strip()
            total_val += float(p_clean) * item["quantity"]
        except (ValueError, AttributeError, TypeError):
            pass
    
    order = models.Order(user_id=user.id, total_price=f"${total_val:,.2f}")
    db.add(order)
    db.flush()
    
    for item in cart:
        order_item = models.OrderItem(
            order_id=order.id,
            product_id=item["product_id"],
            color=item["color"],
            size=item["size"],
            text=item["text"],
            quantity=item["quantity"],
            price=item["price"]
        )
        db.add(order_item)
    
    db.commit()
    
    # Send email notifications
    settings_dict = get_settings_dict(db)
    admin_email = settings_dict.get("order_notification_email")
    
    subject = f"New Product Order - {user.first_name} {user.last_name}"
    items_text = ""
    for item in cart:
        items_text += f"- {item['product_name']} (Qty: {item['quantity']})"
        if item['color']: items_text += f", Color: {item['color']}"
        if item['size']: items_text += f", Size: {item['size']}"
        if item['text']: items_text += f", Text: {item['text']}"
        items_text += f", Price: {item['price']}\n"
        
    content = f"New order has been placed by {user.first_name} {user.last_name} ({user.membership_number}).\n\nItems:\n{items_text}\nTotal Amount: ${total_val:,.2f}\n"
    
    if admin_email:
        send_email(admin_email, subject, content)
    
    if user.email:
        send_email(user.email, "Order Confirmation", f"Thank you for your order. Your order is currently pending approval.\n\nDetails:\n{content}")
    
    # Clear cart
    request.session["cart"] = []
    
    return render_template("order_products.html", {
        "request": request, 
        "user": user, 
        "message": "Your order has been placed successfully and is pending approval.", 
        "products": db.query(models.Product).all()
    }, db)


@router.get("/my-orders", response_class=HTMLResponse)
async def my_orders(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    orders = db.query(models.Order).filter(models.Order.user_id == user.id).order_by(models.Order.created_at.desc()).all()
    return render_template("my_orders.html", {"request": request, "user": user, "orders": orders}, db)
