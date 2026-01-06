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
