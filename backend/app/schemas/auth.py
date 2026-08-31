from pydantic import BaseModel, EmailStr

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

# --- ADD THIS FOR CASHIERS ---
class CashierLoginSchema(BaseModel):
    username: str
    pin: str