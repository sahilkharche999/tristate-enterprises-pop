"""Pydantic schemas for authentication requests and responses."""
from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    email: str
    name: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=8)


class UserLogin(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    created_at: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
