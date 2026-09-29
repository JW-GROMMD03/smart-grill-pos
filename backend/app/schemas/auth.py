from pydantic import BaseModel, EmailStr
from typing import Optional

class LoginSchema(BaseModel):
    email: EmailStr
    password: str

class OTPVerifySchema(BaseModel):
    email: EmailStr
    otp: str

class VaultResetSchema(BaseModel):
    new_password: str

class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    token: str
    branch: Optional[str] = None

# --- UPDATED FOR CASHIERS WITH BRANCH SUPPORT ---
class CashierLoginSchema(BaseModel):
    username: str
    pin: str
    branch: str
    branch: Optional[str] = "Smartgrill"