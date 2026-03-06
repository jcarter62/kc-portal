from sqlalchemy import Boolean, Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from database import Base
import datetime

class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True, index=True)
    value = Column(String)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    membership_number = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    first_name = Column(String)
    last_name = Column(String)
    phone_number = Column(String)
    image_url = Column(String)
    is_admin = Column(Boolean, default=False)
    position = Column(String, nullable=True)
    
    # Relationship to password
    password = relationship("UserPassword", back_populates="user", uselist=False, cascade="all, delete-orphan")

class UserPassword(Base):
    __tablename__ = "user_passwords"

    id = Column(Integer, primary_key=True, index=True)
    membership_number = Column(String, ForeignKey("users.membership_number"), unique=True, index=True)
    password_hash = Column(String)

    user = relationship("User", back_populates="password")

class PasswordReset(Base):
    __tablename__ = "password_resets"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    key = Column(String, unique=True, index=True)
    expires_at = Column(DateTime)
    used = Column(Boolean, default=False)

class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    slug = Column(String, unique=True, index=True)
    content = Column(Text)
    is_public = Column(Boolean, default=True)

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    description = Column(Text)
    has_color = Column(Boolean, default=False)
    has_size = Column(Boolean, default=False)
    has_text = Column(Boolean, default=False)
    custom_text_help = Column(String, nullable=True) # Text displayed above the custom text field
    price = Column(String, nullable=True) # Default price if no size-specific price

    images = relationship("ProductImage", back_populates="product", cascade="all, delete-orphan", order_by="ProductImage.display_order")
    colors = relationship("ProductColor", back_populates="product", cascade="all, delete-orphan")
    sizes = relationship("ProductSize", back_populates="product", cascade="all, delete-orphan")

class ProductColor(Base):
    __tablename__ = "product_colors"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    color = Column(String)

    product = relationship("Product", back_populates="colors")

class ProductSize(Base):
    __tablename__ = "product_sizes"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    size = Column(String)
    price = Column(String) # Storing as String to handle any currency formatting or ranges if needed, but usually Float is better. Given the prompt "price for each size, or one price for all sizes", I'll use String for simplicity in formatting.

    product = relationship("Product", back_populates="sizes")

class ProductImage(Base):
    __tablename__ = "product_images"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    image_url = Column(String)
    display_order = Column(Integer, default=0)

    product = relationship("Product", back_populates="images")

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="Pending") # Pending, Approved, Denied, Delivered, Completed
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    total_price = Column(String, nullable=True)
    payment_id = Column(String, nullable=True)
    payment_status = Column(String, default="Unpaid") # Unpaid, Paid, Pending, Refunded

    user = relationship("User")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    color = Column(String, nullable=True)
    size = Column(String, nullable=True)
    text = Column(String, nullable=True)
    quantity = Column(Integer, default=1)
    price = Column(String, nullable=True) # Price per item at order time

    order = relationship("Order", back_populates="items")
    product = relationship("Product")
