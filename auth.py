import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr

import database

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("JWT_SECRET_KEY is missing. Please add it to your .env file.")

ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))  # default: 7 days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# auto_error=False is what makes guest mode possible: requests with no token
# (or a bad one) still reach the endpoint, just with current_user = None.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# =========================
# Schemas
# =========================

class UserCreate(BaseModel):
    email: EmailStr
    name: str
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: Optional[str] = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# =========================
# Password + token helpers
# =========================

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


# =========================
# Register / login
# =========================

def register_user(payload: UserCreate) -> Token:
    existing = database.get_user_by_email(payload.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists."
        )

    hashed = hash_password(payload.password)
    user = database.create_user(payload.email, payload.name, hashed)

    token = create_access_token({"sub": user["id"]})
    return Token(
        access_token=token,
        user=UserOut(id=user["id"], email=user["email"], name=user["name"])
    )


def login_user(payload: UserLogin) -> Token:
    user = database.get_user_by_email(payload.email)

    if not user or not verify_password(payload.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password."
        )

    token = create_access_token({"sub": user["id"]})
    return Token(
        access_token=token,
        user=UserOut(id=user["id"], email=user["email"], name=user["name"])
    )


# =========================
# Dependencies
# =========================

async def get_current_user_optional(token: Optional[str] = Depends(oauth2_scheme)) -> Optional[UserOut]:
    """Returns the logged-in user if a valid token is present, otherwise None.
    Use this on endpoints that should work for guests too (e.g. /api/travel)."""
    if not token:
        return None

    payload = decode_token(token)
    if not payload:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    user = database.get_user_by_id(user_id)
    if not user:
        return None

    return UserOut(id=user["id"], email=user["email"], name=user["name"])


async def get_current_user_required(
    user: Optional[UserOut] = Depends(get_current_user_optional)
) -> UserOut:
    """Use this on endpoints that must be authenticated (e.g. /api/trips)."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
