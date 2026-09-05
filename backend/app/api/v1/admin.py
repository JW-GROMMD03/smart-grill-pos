import json
import random
import string
import uuid
import calendar
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from itsdangerous import URLSafeTimedSerializer
from app.core.redis import redis_client
from app.core.security import SecurityEngine
from app.core.supabase import supabase
from app.core.shift_engine import ShiftEngine
from app.core.config import settings

router = APIRouter()
serializer = URLSafeTimedSerializer(settings.JWT_SECRET)

# ==========================================
# SHIFT OVERLAP & PERMIT CONTROL
# ==========================================

class ShiftPermitRequest(BaseModel):
    permit_type: str  # "EXTENSION", "EARLY_START", "OVERLAP"
    permitted_shift: str  # "DAY", "NIGHT"
    action: str  # "GRANT", "REVOKE"

class ShiftForceRequest(BaseModel):
    shift: str  # "DAY", "NIGHT", "AUTO"

@router.get("/shift/status")
async def get_shift_status(admin=Depends(SecurityEngine.verify_token)):
    """Returns the current clock shift, system active shift, permits, and forced overrides."""
    (clock_shift, clock_bdate), _, in_grace = ShiftEngine.get_shift_context()
    eff_shift, eff_bdate, is_overridden = await ShiftEngine.get_effective_shift_context()
    
    active_shift_id = await redis_client.get("system:active_shift")
    if isinstance(active_shift_id, bytes):
        active_shift_id = active_shift_id.decode('utf-8')

    permit_raw = await redis_client.get("system:shift_permit")
    active_permit = json.loads(permit_raw.decode('utf-8') if isinstance(permit_raw, bytes) else permit_raw) if permit_raw else None

    override_raw = await redis_client.get("system:shift_override")
    active_override = json.loads(override_raw.decode('utf-8') if isinstance(override_raw, bytes) else override_raw) if override_raw else None

    return {
        "clock_shift": clock_shift,
        "clock_bdate": clock_bdate,
        "effective_shift": eff_shift,
        "effective_bdate": eff_bdate,
        "in_grace_period": in_grace,
        "active_shift_id": active_shift_id,
        "permit": active_permit,
        "override": active_override,
        "is_overridden": is_overridden
    }

@router.post("/shift/permit")
async def manage_shift_permit(payload: ShiftPermitRequest, request: Request, admin=Depends(SecurityEngine.verify_token)):
    """Grants or revokes dynamic shift permits (Early Day Start, Extension, or Overlap)."""
    admin_id = admin.get("sub")
    action = payload.action.upper()

    if action == "GRANT":
        permit_data = {
            "status": "ACTIVE",
            "permit_type": payload.permit_type.upper(),
            "permitted_shift": payload.permitted_shift.upper(),
            "granted_by": admin_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await redis_client.set("system:shift_permit", json.dumps(permit_data))
        
        if payload.permit_type.upper() == "EARLY_START" and payload.permitted_shift.upper() == "DAY":
            tz = timezone(timedelta(hours=3))
            now_dt = datetime.now(tz)
            curr_id = f"{now_dt.date()}-DAY"
            await redis_client.set("system:active_shift", curr_id)

        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.broadcast_admin({"action": "shift_update", "permit": permit_data})

        return {"status": "success", "message": f"Shift permit ({payload.permit_type}) successfully granted.", "permit": permit_data}

    elif action == "REVOKE":
        await redis_client.delete("system:shift_permit")
        
        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.broadcast_admin({"action": "shift_update", "permit": None})

        return {"status": "success", "message": "Shift permit revoked. Regular time enforcement restored."}

    raise HTTPException(status_code=400, detail="Invalid permit action.")

@router.post("/shift/force")
async def force_shift_mode(payload: ShiftForceRequest, request: Request, admin=Depends(SecurityEngine.verify_token)):
    """Manually locks system operation into DAY or NIGHT shift, or resets to AUTO."""
    target_shift = payload.shift.upper()

    if target_shift in ["DAY", "NIGHT"]:
        override_data = {
            "shift": target_shift,
            "mode": "FORCED",
            "set_by": admin.get("sub"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await redis_client.set("system:shift_override", json.dumps(override_data))
        
        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.broadcast_admin({"action": "shift_update", "override": override_data})

        return {"status": "success", "message": f"System manually locked to {target_shift} shift."}

    elif target_shift in ["AUTO", "RESET"]:
        await redis_client.delete("system:shift_override")
        
        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.broadcast_admin({"action": "shift_update", "override": None})

        return {"status": "success", "message": "System shift schedule restored to automatic time tracking."}

    raise HTTPException(status_code=400, detail="Invalid shift force mode.")

# ==========================================
# ADMIN PROFILE MANAGEMENT
# ==========================================

class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None

@router.get("/profile")
async def get_admin_profile(admin=Depends(SecurityEngine.verify_token)):
    admin_id = admin.get("sub")
    cache_key = f"cache:admin_profile:{admin_id}"
    
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            if isinstance(cached, bytes):
                cached = cached.decode('utf-8')
            return json.loads(cached)
    except Exception:
        pass 
        
    try:
        res = supabase.table("cashiers").select("id, full_name, username").eq("id", admin_id).execute()
        profile_data = res.data[0] if res.data else {"full_name": admin.get("username", "Admin"), "username": settings.SMTP_EMAIL}
        
        await redis_client.setex(cache_key, 3600, json.dumps(profile_data))
        return profile_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch profile: {str(e)}")

@router.put("/profile")
async def update_admin_profile(data: ProfileUpdate, admin=Depends(SecurityEngine.verify_token)):
    admin_id = admin.get("sub")
    updates = {}
    
    if data.full_name:
        updates["full_name"] = data.full_name
    if data.password:
        updates["pin_hash"] = SecurityEngine.hash_password(data.password)
        
    if updates:
        try:
            res = supabase.table("cashiers").update(updates).eq("id", admin_id).execute()
            
            # Check if Supabase actually updated any row
            if not res.data:
                raise HTTPException(status_code=400, detail="Profile update failed: User record not found or update blocked by database policies.")
                
            await redis_client.delete(f"cache:admin_profile:{admin_id}")
            await SecurityEngine.log_event("SECURITY", admin_id, updates.get("full_name", "Admin"), "Updated profile credentials")
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=f"Failed to update profile: {str(e)}")
            
    return {"status": "success", "message": "Profile updated successfully."}

# ==========================================
# MASTER VAULT SECURE RESET PIPELINE
# ==========================================

class VaultResetInit(BaseModel):
    admin_email: str

class VerifyQuestionsRequest(BaseModel):
    token: str
    a1: str
    a2: str
    a3: str

class FinalResetRequest(BaseModel):
    token: str
    new_password: str

@router.post("/vault/initiate-reset")
async def terminal_initiate_reset(payload: VaultResetInit, request: Request):
    """Triggered exclusively via terminal CLI script with master key authentication."""
    master_header = request.headers.get("x-master-key")
    master_secret = getattr(settings, "MASTER_SECRET_KEY", "smart-grill-super-master-key")
    if master_header != master_secret:
        raise HTTPException(status_code=403, detail="Unauthorized terminal command.")

    token = serializer.dumps(payload.admin_email, salt="vault-reset-salt")
    reset_link = f"https://smartgrillpos.com/master-vault.html?token={token}"

    # Dispatch email directly via SMTP
    msg = EmailMessage()
    msg.set_content(f"Level 4 Vault Reset requested from Terminal.\n\nAccess your secure portal to answer validation questions:\n{reset_link}\n\nLink expires in 15 minutes.")
    msg['Subject'] = "CRITICAL: Admin Vault Password Reset Request"
    msg['From'] = settings.SMTP_EMAIL
    msg['To'] = payload.admin_email

    try:
        with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_EMAIL, settings.SMTP_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to dispatch email: {str(e)}")

    return {
        "status": "success", 
        "message": "Encrypted reset link successfully emailed to admin.", 
        "secure_token": token,
        "reset_link": reset_link
    }

@router.post("/vault/verify-questions")
async def verify_security_questions(payload: VerifyQuestionsRequest):
    try:
        email = serializer.loads(payload.token, salt="vault-reset-salt", max_age=900)
    except Exception:
        raise HTTPException(status_code=400, detail="Token expired or invalid.")

    res = supabase.table("cashiers").select("*").eq("username", email).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Admin identity not found.")
    
    admin_record = res.data[0]

    match_1 = SecurityEngine.verify_password(payload.a1.strip().lower(), admin_record.get("sec_a1_hash", ""))
    match_2 = SecurityEngine.verify_password(payload.a2.strip().lower(), admin_record.get("sec_a2_hash", ""))
    match_3 = SecurityEngine.verify_password(payload.a3.strip().lower(), admin_record.get("sec_a3_hash", ""))

    if not (match_1 and match_2 and match_3):
        raise HTTPException(status_code=401, detail="Incorrect security answers.")

    auth_token = serializer.dumps(email, salt="vault-authorized-salt")
    return {"status": "success", "auth_token": auth_token}

@router.post("/vault/execute-reset")
async def execute_vault_reset(payload: FinalResetRequest):
    try:
        email = serializer.loads(payload.token, salt="vault-authorized-salt", max_age=300)
    except Exception:
        raise HTTPException(status_code=400, detail="Authorization session expired.")

    new_hash = SecurityEngine.hash_password(payload.new_password)
    res = supabase.table("cashiers").update({"pin_hash": new_hash}).eq("username", email).execute()
    
    if not res.data:
        raise HTTPException(status_code=400, detail="Password rotation failed or user not found.")

    return {"status": "success", "message": "Admin password successfully rotated through secure vault pipeline."}

# ==========================================
# MENU MANAGEMENT
# ==========================================

class MenuItemCreate(BaseModel):
    name: str
    category: str
    price: float
    sub_category: Optional[str] = None

class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = None
    sub_category: Optional[str] = None

@router.get("/menu")
async def get_menu(admin=Depends(SecurityEngine.verify_token)):
    try:
        cached = await redis_client.get("cache:menu_v4")
        if cached:
            if isinstance(cached, bytes):
                cached = cached.decode('utf-8')
            return json.loads(cached)
    except Exception:
        pass
    
    try:
        res = supabase.table("menu_items").select("*").order("category").execute()
        menu_data = res.data or []
        await redis_client.setex("cache:menu_v4", 86400, json.dumps(menu_data))
        return menu_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database fetch failed for menu: {str(e)}")

@router.post("/menu")
async def add_menu_item(
    payload: MenuItemCreate,
    request: Request,
    admin=Depends(SecurityEngine.verify_token)
):
    item_id = str(uuid.uuid4())
    db_payload = {
        "id": item_id, 
        "name": payload.name, 
        "category": payload.category, 
        "price": payload.price, 
        "is_active": True
    }
    if payload.category.lower() == "meat cuts" and payload.sub_category:
        db_payload["sub_category"] = payload.sub_category.lower()

    try:
        supabase.table("menu_items").insert(db_payload).execute()
        await redis_client.delete("cache:menu_v4") 
        
        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.broadcast_cashier({"action": "menu_refresh"})
            
        await SecurityEngine.log_event("MENU", admin.get("sub"), admin.get("username"), f"Added {payload.name} ({payload.category})")
        return {"status": "success", "message": "Item added successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add item: {str(e)}")

@router.put("/menu/{item_id}")
async def update_menu_item(
    item_id: str, 
    payload: MenuItemUpdate, 
    request: Request,
    admin=Depends(SecurityEngine.verify_token)
):
    """Enhanced feature: Updates menu item properties (price, name, category, or sub_category) dynamically."""
    update_data = {}
    if payload.name is not None:
        update_data["name"] = payload.name
    if payload.category is not None:
        update_data["category"] = payload.category
    if payload.price is not None:
        update_data["price"] = payload.price
    if payload.sub_category is not None:
        update_data["sub_category"] = payload.sub_category.lower()

    if not update_data:
        raise HTTPException(status_code=400, detail="No valid update fields provided.")

    try:
        supabase.table("menu_items").update(update_data).eq("id", item_id).execute()
        await redis_client.delete("cache:menu_v4")
        
        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.broadcast_cashier({"action": "menu_refresh"})
            
        return {"status": "success", "message": "Menu item updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update item: {str(e)}")

@router.patch("/menu/{item_id}/toggle")
async def toggle_menu_item(item_id: str, is_active: bool, request: Request, admin=Depends(SecurityEngine.verify_token)):
    try:
        supabase.table("menu_items").update({"is_active": is_active}).eq("id", item_id).execute()
        await redis_client.delete("cache:menu_v4")
        
        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.broadcast_cashier({"action": "menu_refresh"})
            
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to toggle item: {str(e)}")

@router.delete("/menu/{item_id}")
async def delete_menu_item(item_id: str, request: Request, admin=Depends(SecurityEngine.verify_token)):
    try:
        supabase.table("menu_items").delete().eq("id", item_id).execute()
        await redis_client.delete("cache:menu_v4")
        
        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.broadcast_cashier({"action": "menu_refresh"})
            
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete item: {str(e)}")

# ==========================================
# USER MANAGEMENT & ACCESS CONTROL
# ==========================================

class UserBlockRequest(BaseModel):
    status: str 
    duration_days: Optional[Union[int, str]] = None 
    reason: Optional[str] = "Please contact manager for clarification."

@router.get("/users")
async def get_all_users(admin=Depends(SecurityEngine.verify_token)):
    try:
        res = supabase.table("cashiers").select("id, full_name, username, assigned_shift, status, blocked_until, block_reason").execute()
        users = res.data or []
        
        active_users = [
            u for u in users 
            if str(u.get("status") or "").strip().upper() != "DELETED"
        ]
        
        return active_users
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch users: {str(e)}")

@router.patch("/users/{user_id}/status")
async def update_user_status(user_id: str, payload: UserBlockRequest, request: Request, admin=Depends(SecurityEngine.verify_token)):
    try:
        blocked_until = None
        
        if payload.status.upper() == 'BLOCKED' and payload.duration_days:
            try:
                days = int(payload.duration_days)
                if days > 0:
                    blocked_until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
            except (ValueError, TypeError):
                pass 
        
        update_data = {
            "status": payload.status.upper(),
            "block_reason": payload.reason if payload.status.upper() != 'ACTIVE' else None,
            "blocked_until": blocked_until if payload.status.upper() == 'BLOCKED' else None
        }

        supabase.table("cashiers").update(update_data).eq("id", user_id).execute()
        await redis_client.delete(f"session:{user_id}")

        if payload.status.upper() == 'BLOCKED':
            if hasattr(request.app.state, 'sockets'):
                await request.app.state.sockets.force_logout_cashier(user_id, payload.reason)

        return {"status": "success", "message": f"User status updated to {payload.status.upper()}."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update user status: {str(e)}")

@router.delete("/users/{user_id}")
async def delete_user_account(user_id: str, request: Request, admin=Depends(SecurityEngine.verify_token)):
    try:
        supabase.table("cashiers").update({"status": "DELETED"}).eq("id", user_id).execute()
        await redis_client.delete(f"session:{user_id}")
        
        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.force_logout_cashier(user_id, "Your account has been permanently removed by the Admin.")
            
        return {"status": "success", "message": "User account permanently deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete user: {str(e)}")

# ==========================================
# EXPENSE TRACKER & DEDUCTION PANEL
# ==========================================

class ExpenseCreate(BaseModel):
    description: str
    amount: float
    payment_type: str 
    cash_amount: Optional[float] = 0.0
    mpesa_amount: Optional[float] = 0.0
    business_date: Optional[str] = None
    shift: Optional[str] = "Day"

@router.post("/expenses")
async def create_admin_expense(payload: ExpenseCreate, request: Request, admin=Depends(SecurityEngine.verify_token)):
    admin_id = admin.get("sub")
    if not payload.business_date:
        _, payload.business_date = ShiftEngine.calculate_current_shift()
        
    db_payload = {
        "description": payload.description,
        "amount": payload.amount,
        "payment_type": payload.payment_type.upper(),
        "cash_amount": payload.cash_amount if payload.payment_type.upper() == 'PARTIAL' else (payload.amount if payload.payment_type.upper() == 'CASH' else 0.0),
        "mpesa_amount": payload.mpesa_amount if payload.payment_type.upper() == 'PARTIAL' else (payload.amount if payload.payment_type.upper() == 'MPESA' else 0.0),
        "recorded_by": admin_id,
        "business_date": payload.business_date,
        "shift": payload.shift
    }

    try:
        supabase.table("expenses").insert(db_payload).execute()
        for key in await redis_client.keys("dashboard:analytics:*"):
            await redis_client.delete(key)
        for key in await redis_client.keys("smartgrill:deep_bi:*"):
            await redis_client.delete(key)
            
        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.broadcast_admin({"action": "refresh_sales"})
            
        return {"status": "success", "message": "Expense recorded successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to record expense: {str(e)}")

@router.get("/expenses/filtered")
async def get_filtered_expenses(
    month: Optional[str] = Query(default=None),
    date: Optional[str] = Query(default=None), 
    shift: Optional[str] = Query(default=None),
    admin=Depends(SecurityEngine.verify_token)
):
    try:
        query = supabase.table("expenses").select("*")
        
        if date:
            query = query.eq("business_date", date)
        elif month:
            y, m = map(int, month.split("-"))
            last_day = calendar.monthrange(y, m)[1]
            query = query.gte("business_date", f"{month}-01").lte("business_date", f"{month}-{last_day:02d}")
        
        if shift and shift != "All":
            query = query.eq("shift", shift)
            
        res = query.order("created_at", desc=True).execute()
        expenses = res.data or []

        cashier_ids = list(set(e.get("recorded_by") for e in expenses if e.get("recorded_by")))
        cashier_map = {}
        if cashier_ids:
            c_res = supabase.table("cashiers").select("id, full_name").in_("id", cashier_ids).execute()
            cashier_map = {c["id"]: c["full_name"] for c in (c_res.data or [])}

        total_exp = sum(float(e.get("amount", 0)) for e in expenses)
        cash_exp = sum(float(e.get("cash_amount", 0)) for e in expenses)
        mpesa_exp = sum(float(e.get("mpesa_amount", 0)) for e in expenses)

        cashier_breakdown = {}
        for e in expenses:
            c_name = cashier_map.get(e.get("recorded_by"), "Admin / System")
            e["cashiers"] = {"full_name": c_name}
            cashier_breakdown[c_name] = cashier_breakdown.get(c_name, 0) + float(e.get("amount", 0))

        daily_totals = {}
        for e in expenses:
            d = e.get("business_date")
            daily_totals[d] = daily_totals.get(d, 0) + float(e.get("amount", 0))

        highest_day = max(daily_totals, key=daily_totals.get) if daily_totals else None

        return {
            "total_expenses": total_exp,
            "cash_expenses": cash_exp,
            "mpesa_expenses": mpesa_exp,
            "highest_expense_day": highest_day,
            "cashier_breakdown": cashier_breakdown,
            "daily_breakdown": daily_totals,
            "expenses": expenses
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch expense analytics: {str(e)}")

# ==========================================
# COMPREHENSIVE RECORDS & DEEP MONTHLY BI
# ==========================================

@router.get("/records/comprehensive")
async def get_comprehensive_records(
    date: Optional[str] = Query(default=None),
    month: Optional[str] = Query(default=None),
    year: Optional[str] = Query(default=None),
    shift: Optional[str] = Query(default=None),
    admin=Depends(SecurityEngine.verify_token)
):
    try:
        sales_query = supabase.table("sales").select("*, cashiers(full_name, assigned_shift)")
        exp_query = supabase.table("expenses").select("*") 

        if date:
            sales_query = sales_query.eq("business_date", date)
            exp_query = exp_query.eq("business_date", date)
        elif month:
            y, m = map(int, month.split("-"))
            last_day = calendar.monthrange(y, m)[1]
            sales_query = sales_query.gte("business_date", f"{month}-01").lte("business_date", f"{month}-{last_day:02d}")
            exp_query = exp_query.gte("business_date", f"{month}-01").lte("business_date", f"{month}-{last_day:02d}")
        elif year:
            sales_query = sales_query.gte("business_date", f"{year}-01-01").lte("business_date", f"{year}-12-31")
            exp_query = exp_query.gte("business_date", f"{year}-01-01").lte("business_date", f"{year}-12-31")

        sales_res = sales_query.order("created_at", desc=True).execute()
        exp_res = exp_query.order("created_at", desc=True).execute()

        sales = sales_res.data or []
        expenses = exp_res.data or []

        exp_cashier_ids = list(set(e.get("recorded_by") for e in expenses if e.get("recorded_by")))
        exp_cashier_map = {}
        if exp_cashier_ids:
            c_res = supabase.table("cashiers").select("id, full_name").in_("id", exp_cashier_ids).execute()
            exp_cashier_map = {c["id"]: c["full_name"] for c in (c_res.data or [])}
            
        for e in expenses:
            c_name = exp_cashier_map.get(e.get("recorded_by"), "Admin / System")
            e["cashiers"] = {"full_name": c_name}

        if shift and shift != "All":
            target_shift = shift.strip().lower()
            sales = [
                s for s in sales 
                if str(s.get("shift") or s.get("cashiers", {}).get("assigned_shift") or "").strip().lower() == target_shift
            ]
            expenses = [
                e for e in expenses 
                if str(e.get("shift") or "").strip().lower() == target_shift
            ]

        total_sales = sum(float(s.get("total_amount", 0)) for s in sales)
        total_expenses = sum(float(e.get("amount", 0)) for e in expenses)
        net_profit = total_sales - total_expenses

        return {
            "summary": {
                "total_sales": total_sales,
                "total_expenses": total_expenses,
                "net_profit": net_profit,
                "total_transactions": len(sales),
                "total_expense_records": len(expenses)
            },
            "sales": sales,
            "expenses": expenses
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch comprehensive records: {str(e)}")

# ==========================================
# LIVE SALES & CASH AT HAND (CACHE-ASIDE)
# ==========================================

@router.get("/sales/live")
async def get_live_sales(
    business_date: str = Query(default=None),
    admin=Depends(SecurityEngine.verify_token)
):
    if not business_date:
        _, business_date = ShiftEngine.calculate_current_shift()

    cache_key = f"dashboard:analytics:{business_date}"
    
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            if isinstance(cached, bytes):
                cached = cached.decode('utf-8')
            return json.loads(cached)
    except Exception:
        pass 

    try:
        sales_res = supabase.table("sales").select("*").eq("business_date", business_date).order("created_at", desc=True).execute()
        sales = sales_res.data or []

        sale_ids = [s["id"] for s in sales]
        items_map = {}
        if sale_ids:
            items_res = supabase.table("sale_items").select("sale_id, quantity, item_name").in_("sale_id", sale_ids).execute()
            for it in (items_res.data or []):
                items_map.setdefault(it["sale_id"], []).append(f"{it['quantity']}x {it['item_name']}")

        cashier_ids = list(set(s.get("cashier_id") for s in sales if s.get("cashier_id")))
        if cashier_ids:
            cashiers_res = supabase.table("cashiers").select("id, full_name, assigned_shift").in_("id", cashier_ids).execute()
            cashier_map = {c["id"]: c for c in (cashiers_res.data or [])}
            
            for s in sales:
                c_info = cashier_map.get(s.get("cashier_id"))
                s["cashiers"] = c_info if c_info else {"full_name": "Unknown", "assigned_shift": s.get("shift") or "N/A"}
                s["item_summary"] = ", ".join(items_map.get(s["id"], []))
        else:
            for s in sales:
                s["cashiers"] = {"full_name": "Unknown", "assigned_shift": s.get("shift") or "N/A"}
                s["item_summary"] = ", ".join(items_map.get(s["id"], []))

        exp_res = supabase.table("expenses").select("*").eq("business_date", business_date).execute()
        expenses = exp_res.data or []
        
        cash_sales = sum(float(s.get("cash_amount") or 0) for s in sales)
        mpesa_sales = sum(float(s.get("mpesa_amount") or 0) for s in sales)
        cash_expenses = sum(float(e.get("cash_amount") or e.get("amount") or 0) for e in expenses if str(e.get("payment_type") or "").upper() in ["CASH", "PARTIAL"])
        
        cash_at_hand = cash_sales - cash_expenses

        payload = {
            "business_date": business_date,
            "cash_at_hand": cash_at_hand,
            "cash_sales": cash_sales,
            "mpesa_sales": mpesa_sales,
            "total_sales": cash_sales + mpesa_sales,
            "total_expenses": sum(float(e.get("amount", 0)) for e in expenses),
            "recent_transactions": sales[:100] 
        }
        
        await redis_client.setex(cache_key, 60, json.dumps(payload)) 
        return payload
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database fetch failed for live sales: {str(e)}")

# ==========================================
# DEEP BUSINESS INTELLIGENCE (CACHE-ASIDE)
# ==========================================

@router.get("/analytics/deep")
async def get_deep_analytics(
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
    admin=Depends(SecurityEngine.verify_token)
):
    if not start_date or not end_date:
        _, current_date = ShiftEngine.calculate_current_shift()
        start_date = start_date or current_date
        end_date = end_date or current_date
        
    cache_key = f"smartgrill:deep_bi:{start_date}:{end_date}"
    
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            if isinstance(cached, bytes):
                cached = cached.decode('utf-8')
            return json.loads(cached)
    except Exception:
        pass 

    try:
        sales_res = supabase.table("sales").select("id, payment_method, cash_amount, mpesa_amount, total_amount").gte("business_date", start_date).lte("business_date", end_date).execute()
        sales_map = {s["id"]: s for s in (sales_res.data or [])}

        items_res = supabase.table("sale_items").select("*").gte("created_at", f"{start_date}T00:00:00").lte("created_at", f"{end_date}T23:59:59").execute()
        items = items_res.data or []

        meat = {
            "beef": {"1/4": 0.0, "1/2": 0.0, "1kg": 0.0, "total_kg": 0.0, "revenue": 0.0, "cash": 0.0, "mpesa": 0.0},
            "mbuzi": {"1/4": 0.0, "1/2": 0.0, "1kg": 0.0, "total_kg": 0.0, "revenue": 0.0, "cash": 0.0, "mpesa": 0.0},
            "chicken": {"1/4": 0.0, "1/2": 0.0, "1kg": 0.0, "total_kg": 0.0, "revenue": 0.0, "cash": 0.0, "mpesa": 0.0}
        }
        fish = {"prices": {}, "total_revenue": 0.0, "cash": 0.0, "mpesa": 0.0}
        greens = {}
        sides = {
            "kachumbari": {"qty": 0.0, "revenue": 0.0},
            "wet_fry": {"qty": 0.0, "revenue": 0.0},
            "chips_regular": {"qty": 0.0, "revenue": 0.0},
            "chips_masala": {"qty": 0.0, "revenue": 0.0}
        }
        drinks = {"pepsi": {"qty": 0.0, "rev": 0.0}, "coke": {"qty": 0.0, "rev": 0.0}, "water": {"qty": 0.0, "rev": 0.0}}

        for item in items:
            name = str(item.get("item_name") or "").lower()
            cat = str(item.get("category") or "").lower() 
            sub_cat = str(item.get("sub_category") or "").lower()
            qty = float(item.get("quantity") or 0)
            total = float(item.get("total") or item.get("subtotal") or 0)
            unit_price = float(item.get("unit_price") or (total / qty if qty > 0 else 0))
            
            parent_sale = sales_map.get(item.get("sale_id"), {})
            payment_method = str(parent_sale.get("payment_method") or "CASH").upper()
            
            item_cash = 0.0
            item_mpesa = 0.0
            
            if payment_method == "PARTIAL":
                sale_total = float(parent_sale.get("total_amount") or 1)
                if sale_total > 0:
                    cash_ratio = float(parent_sale.get("cash_amount") or 0) / sale_total
                    mpesa_ratio = float(parent_sale.get("mpesa_amount") or 0) / sale_total
                    item_cash = total * cash_ratio
                    item_mpesa = total * mpesa_ratio
            elif payment_method == "MPESA":
                item_mpesa = total
            else:
                item_cash = total
            
            if cat in ["meat cuts", "meat"] or sub_cat in ["beef", "mbuzi", "chicken"] or any(m in name for m in ["beef", "mbuzi", "chicken"]):
                target = sub_cat if sub_cat in meat else next((m for m in ["beef", "mbuzi", "chicken"] if m in name), "beef")
                
                weight_added = 0.0
                if target == "beef" or target == "chicken":
                    if unit_price <= 275:
                        meat[target]["1/4"] += qty
                        weight_added = 0.25 * qty
                    elif unit_price <= 600:
                        meat[target]["1/2"] += qty
                        weight_added = 0.5 * qty
                    else:
                        meat[target]["1kg"] += qty
                        weight_added = 1.0 * qty
                elif target == "mbuzi":
                    if unit_price <= 350:
                        meat[target]["1/4"] += qty
                        weight_added = 0.25 * qty
                    elif unit_price <= 750:
                        meat[target]["1/2"] += qty
                        weight_added = 0.5 * qty
                    else:
                        meat[target]["1kg"] += qty
                        weight_added = 1.0 * qty
                
                meat[target]["total_kg"] += weight_added
                meat[target]["revenue"] += total
                meat[target]["cash"] += item_cash
                meat[target]["mpesa"] += item_mpesa

            elif "tilapia" in cat or "tilapia" in name:
                price = str(item.get("price") or 0)
                if price not in fish["prices"]:
                    fish["prices"][price] = {"amount": 0.0, "revenue": 0.0}
                fish["prices"][price]["amount"] += qty
                fish["prices"][price]["revenue"] += total
                fish["total_revenue"] += total
                fish["cash"] += item_cash
                fish["mpesa"] += item_mpesa

            elif "greens" in cat:
                if name not in greens:
                    greens[name] = {"qty": 0.0, "revenue": 0.0}
                greens[name]["qty"] += qty
                greens[name]["revenue"] += total

            if "kachumbari" in name:
                sides["kachumbari"]["qty"] += qty
                sides["kachumbari"]["revenue"] += total
            elif "wet fry" in name or "wetfry" in name:
                sides["wet_fry"]["qty"] += qty
                sides["wet_fry"]["revenue"] += total
            elif "chips" in cat or "chips" in name:
                if "masala" in name:
                    sides["chips_masala"]["qty"] += qty
                    sides["chips_masala"]["revenue"] += total
                else:
                    sides["chips_regular"]["qty"] += qty
                    sides["chips_regular"]["revenue"] += total

            if "pepsi" in name:
                drinks["pepsi"]["qty"] += qty
                drinks["pepsi"]["rev"] += total
            elif "coke" in name or "coca" in name:
                drinks["coke"]["qty"] += qty
                drinks["coke"]["rev"] += total
            elif "water" in name:
                drinks["water"]["qty"] += qty
                drinks["water"]["rev"] += total

        for m_key in meat:
            meat[m_key]["total_kg"] = float(meat[m_key]["total_kg"])
            meat[m_key]["revenue"] = round(float(meat[m_key]["revenue"]), 2)
            meat[m_key]["cash"] = round(float(meat[m_key]["cash"]), 2)
            meat[m_key]["mpesa"] = round(float(meat[m_key]["mpesa"]), 2)

        payload = {
            "meat": meat,
            "fish": fish,
            "greens": greens,
            "sides": sides,
            "drinks": drinks
        }

        await redis_client.setex(cache_key, 60, json.dumps(payload))
        return payload

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Deep analytics fetch failed: {str(e)}")

# ==========================================
# CASHIER & SMART SCANNER LOGIC
# ==========================================

@router.post("/cashiers/register")
async def register_cashier(
    full_name: str, username: str, pin: str, assigned_shift: str,
    admin=Depends(SecurityEngine.verify_token)
):
    try:
        pin_hash = SecurityEngine.hash_password(pin)
        supabase.table("cashiers").insert({
            "full_name": full_name, 
            "username": username, 
            "pin_hash": pin_hash, 
            "assigned_shift": assigned_shift,
            "status": "ACTIVE"
        }).execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to register cashier: {str(e)}")

@router.post("/deletion/request")
async def request_deletion_code(sale_id: str, item_id: str, cashier_id: str):
    token = ''.join(random.choices(string.digits, k=6))
    try:
        supabase.table("deletion_requests").insert({
            "sale_id": sale_id,
            "item_id": item_id,
            "cashier_id": cashier_id,
            "token": token,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        }).execute()
        
        return {
            "status": "pending_approval",
            "token": token,
            "qr_payload": f"smartgrill://approve-delete?token={token}&item_id={item_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Deletion request failed: {str(e)}")

class DeletionAuth(BaseModel):
    token: str

@router.post("/deletion/authorize")
async def authorize_deletion(payload: DeletionAuth, request: Request, admin=Depends(SecurityEngine.verify_token)):
    token = payload.token.strip() 
    cache_key = f"code_delete:{token}" if token.isdigit() else f"qr_delete:{token}"
    
    try:
        data = await redis_client.get(cache_key)
        if not data:
            raise HTTPException(status_code=400, detail="Invalid or expired deletion token.")
            
        if isinstance(data, bytes):
            data = data.decode('utf-8')

        req = json.loads(data)
        if req.get("status") == "approved":
            raise HTTPException(status_code=400, detail="This deletion was already authorized.")
            
        target_id = req.get("target_id")
        is_valid_uuid = True
        try:
            uuid.UUID(str(target_id))
        except (ValueError, TypeError, AttributeError):
            is_valid_uuid = False
            
        if is_valid_uuid:
            supabase.table("sale_items").delete().eq("id", target_id).execute()
            supabase.table("sale_items").delete().eq("sale_id", target_id).execute()
            supabase.table("sales").delete().eq("id", target_id).execute()
        
        qr_keys = await redis_client.keys("qr_delete:*")
        code_keys = await redis_client.keys("code_delete:*")
        all_keys = qr_keys + code_keys
        for k in all_keys:
            if isinstance(k, bytes): 
                k = k.decode('utf-8')
            k_data = await redis_client.get(k)
            if k_data:
                if isinstance(k_data, bytes):
                    k_data = k_data.decode('utf-8')
                k_req = json.loads(k_data)
                if str(k_req.get("target_id")) == str(target_id):
                    k_req["status"] = "approved"
                    await redis_client.setex(k, 60, json.dumps(k_req)) 
        
        for key in await redis_client.keys("dashboard:analytics:*"):
            await redis_client.delete(key)
        for key in await redis_client.keys("smartgrill:*"):
            await redis_client.delete(key)
            
        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.broadcast_admin({"action": "refresh_sales"})
            
        return {"status": "success", "message": "Item successfully deleted. The POS has been updated."}
        
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=f"Authorization execution failed: {str(e)}")