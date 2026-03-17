"""Authentication endpoints: signup, login, refresh, logout, me."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .dependencies import get_current_user
from .models import TokenResponse, UserCreate, UserLogin, UserResponse
from .utils import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from ..ai_implementation.db import get_session, User
from ..config import settings as app_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

REFRESH_COOKIE = "refresh_token"
COOKIE_MAX_AGE = app_settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60  # seconds


def _set_refresh_cookie(response: Response, token: str):
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=app_settings.COOKIE_SECURE,
        samesite="lax",
        max_age=COOKIE_MAX_AGE,
        path="/auth",
    )


@router.post("/signup", status_code=201)
async def signup(body: UserCreate, session: Session = Depends(get_session)):
    hashed = await run_in_threadpool(hash_password, body.password)
    user = User(
        email=body.email.lower().strip(),
        name=body.name.strip(),
        hashed_password=hashed,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Email already registered")

    logger.info(f"New user registered: {body.email}")
    return {"message": "Account created successfully"}


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin, response: Response, session: Session = Depends(get_session)):
    user = session.execute(
        select(User).where(User.email == body.email.lower().strip())
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    valid = await run_in_threadpool(verify_password, body.password, user.hashed_password)
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user_resp = UserResponse(id=user.id, email=user.email, name=user.name, created_at=user.created_at)
    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)
    _set_refresh_cookie(response, refresh)

    logger.info(f"User logged in: {user.email}")
    return TokenResponse(access_token=access, user=user_resp)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: Request, response: Response, session: Session = Depends(get_session)):
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")

    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    user_resp = UserResponse(id=user.id, email=user.email, name=user.name, created_at=user.created_at)
    new_access = create_access_token(user.id)
    new_refresh = create_refresh_token(user.id)
    _set_refresh_cookie(response, new_refresh)

    return TokenResponse(access_token=new_access, user=user_resp)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(REFRESH_COOKIE, path="/auth")
    return {"message": "Logged out"}


@router.get("/me", response_model=UserResponse)
async def me(current_user: dict = Depends(get_current_user)):
    return UserResponse(**current_user)
