import smtplib
import random
import json
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from fastapi import APIRouter, HTTPException, Request, Header

from app.schemas.auth import LoginSchema, UserResponse, OTPVerifySchema, VaultResetSchema, CashierLoginSchema
from app.core.supabase import supabase
from app.core.security import SecurityEngine
from app.core.redis import redis_client
from app.core.config import settings
from app.core.shift_engine import ShiftEngine

router = APIRouter()

def send_otp_email(receiver_email: str, otp: str):
    msg = EmailMessage()
    msg.set_content(f"Your Smart Grill Executive Access Code is: {otp}\n\nThis code expires in 5 minutes.")
    msg['Subject'] = 'Smart Grill POS | Verification Code'
    msg['From'] = settings.SMTP_USERNAME
    msg['To'] = receiver_email

    try:
        with smtplib.SMTP_SSL(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        print(f"SMTP Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to dispatch security email.")

@router.post("/login")
async def login(credentials: LoginSchema, request: Request):
    client_identifier = f"{request.client.host}:{credentials.email}"
    await SecurityEngine.check_rate_limit(client_identifier)

    try:
        auth_res = supabase.auth.sign_in_with_password({
            "email": credentials.email,
            "password": credentials.password
        })
        
        user = auth_res.user
        if not user:
            raise ValueError("Invalid user object")

        profile_res = supabase.table("profiles").select("role, full_name").eq("id", user.id).execute()
        user_data = profile_res.data[0] if profile_res.data else {}

        if user_data.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Executive clearance required.")

        await SecurityEngine.reset_attempts(client_identifier)

        otp = str(random.randint(100000, 999999))
        token = SecurityEngine.create_access_token({
            "sub": user.id, "email": user.email, "role": "admin"
        })

        payload = {
            "id": user.id, "email": user.email, 
            "full_name": user_data.get("full_name", "Admin"), 
            "role": "admin", "token": token
        }
        
        await redis_client.setex(f"otp:{credentials.email}:{otp}", 300, json.dumps(payload))
        send_otp_email(credentials.email, otp)

        return {"status": "otp_sent", "message": "Verification code dispatched to terminal."}

    except Exception as e:
        await SecurityEngine.record_failed_attempt(client_identifier)
        if "Invalid login credentials" in str(e):
            raise HTTPException(status_code=401, detail="Invalid executive credentials.")
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/verify-otp", response_model=UserResponse)
async def verify_otp(payload: OTPVerifySchema):
    cache_key = f"otp:{payload.email}:{payload.otp}"
    cached_data = await redis_client.get(cache_key)
    
    if not cached_data:
        raise HTTPException(status_code=401, detail="Invalid or expired security code.")
    
    if isinstance(cached_data, bytes):
        cached_data = cached_data.decode('utf-8')

    await redis_client.delete(cache_key)
    return json.loads(cached_data)

@router.post("/master-reset")
async def master_reset(payload: VaultResetSchema, x_master_key: str = Header(None)):
    if x_master_key != settings.MASTER_KEY:
        raise HTTPException(status_code=403, detail="CRITICAL: Invalid Master Key.")

    lockout_key = "vault:admin_reset_lock"
    if await redis_client.get(lockout_key):
        raise HTTPException(status_code=429, detail="Reset prohibited. A password modification occurred within the last 30 days.")

    try:
        supabase.auth.admin.update_user_by_id(
            uid=(supabase.table("profiles").select("id").eq("role", "admin").single().execute().data["id"]),
            attributes={"password": payload.new_password}
        )
        
        await redis_client.setex(lockout_key, 2592000, "locked")
        return {"status": "success", "message": "Admin clearance updated. Vault sealed for 30 days."}
    except Exception:
        raise HTTPException(status_code=500, detail="Database mutation failed.")

@router.post("/cashier-login")
async def cashier_login(credentials: CashierLoginSchema, request: Request):
    client_identifier = f"{request.client.host}:{credentials.username}"
    await SecurityEngine.check_rate_limit(client_identifier)

    try:
        res = supabase.table("cashiers").select("*").eq("username", credentials.username).execute()
        if not res.data:
            raise ValueError("Invalid credentials")
        
        cashier = res.data[0]
        
        # 1. STRICT ACCESS CONTROL: Check Blocked Status
        if cashier.get("status") == "BLOCKED":
            reason = cashier.get("block_reason", "Account suspended. Contact Admin.")
            raise HTTPException(status_code=403, detail=f"ACCESS DENIED: {reason}")

        # 2. DYNAMIC ACCESS CONTROL: Validate Current Shift Access via ShiftEngine
        assigned_shift = str(cashier.get("assigned_shift", "Day")).strip().upper()
        if assigned_shift in ["DAY SHIFT", "DAY_SHIFT"]:
            assigned_shift = "DAY"
        elif assigned_shift in ["NIGHT SHIFT", "NIGHT_SHIFT"]:
            assigned_shift = "NIGHT"

        # Validate shift access; throws 403 if locked out
        await ShiftEngine.validate_shift_access(cashier["id"], assigned_shift)

        if not SecurityEngine.verify_password(credentials.pin, cashier["pin_hash"]):
            raise ValueError("Invalid credentials")

        await SecurityEngine.reset_attempts(client_identifier)

        token = SecurityEngine.create_access_token({
            "sub": cashier["id"],
            "username": cashier["username"],
            "role": "cashier",
            "shift": cashier.get("assigned_shift", "Day")
        })

        return {
            "id": cashier["id"],
            "username": cashier["username"],
            "full_name": cashier["full_name"],
            "role": "cashier",
            "token": token
        }
    except ValueError:
        await SecurityEngine.record_failed_attempt(client_identifier)
        raise HTTPException(status_code=401, detail="Invalid username or PIN.")
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection error: {str(e)}")