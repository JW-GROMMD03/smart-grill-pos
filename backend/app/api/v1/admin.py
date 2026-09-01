import json
import random
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from app.core.redis import redis_client
from app.core.security import SecurityEngine
from app.core.supabase import supabase
from app.core.shift_engine import ShiftEngine

router = APIRouter()

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
        profile_data = res.data[0] if res.data else {"full_name": admin.get("username", "Admin"), "username": "admin@smartgrill.co.ke"}
        
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
            supabase.table("cashiers").update(updates).eq("id", admin_id).execute()
            await redis_client.delete(f"cache:admin_profile:{admin_id}")
            await SecurityEngine.log_event("SECURITY", admin_id, updates.get("full_name", "Admin"), "Updated profile credentials")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update profile: {str(e)}")
            
    return {"status": "success", "message": "Profile updated successfully."}

# ==========================================
# MENU MANAGEMENT
# ==========================================

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
    name: str, 
    category: str, 
    price: float, 
    sub_category: Optional[str] = None,
    admin=Depends(SecurityEngine.verify_token)
):
    item_id = str(uuid.uuid4())
    payload = {
        "id": item_id, 
        "name": name, 
        "category": category, 
        "price": price, 
        "is_active": True
    }
    if category.lower() == "meat" and sub_category:
        payload["sub_category"] = sub_category.lower()

    try:
        supabase.table("menu_items").insert(payload).execute()
        await redis_client.delete("cache:menu_v4") 
        await SecurityEngine.log_event("MENU", admin.get("sub"), admin.get("username"), f"Added {name} ({category})")
        return {"status": "success", "message": "Item added successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add item: {str(e)}")

@router.put("/menu/{item_id}")
async def update_menu_item(item_id: str, price: float, admin=Depends(SecurityEngine.verify_token)):
    try:
        supabase.table("menu_items").update({"price": price}).eq("id", item_id).execute()
        await redis_client.delete("cache:menu_v4")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update item: {str(e)}")

@router.patch("/menu/{item_id}/toggle")
async def toggle_menu_item(item_id: str, is_active: bool, admin=Depends(SecurityEngine.verify_token)):
    try:
        supabase.table("menu_items").update({"is_active": is_active}).eq("id", item_id).execute()
        await redis_client.delete("cache:menu_v4")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to toggle item: {str(e)}")

@router.delete("/menu/{item_id}")
async def delete_menu_item(item_id: str, admin=Depends(SecurityEngine.verify_token)):
    try:
        supabase.table("menu_items").delete().eq("id", item_id).execute()
        await redis_client.delete("cache:menu_v4")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete item: {str(e)}")

# ==========================================
# USER MANAGEMENT & ACCESS CONTROL
# ==========================================

@router.get("/users")
async def get_all_users(admin=Depends(SecurityEngine.verify_token)):
    try:
        res = supabase.table("cashiers").select("id, full_name, username, assigned_shift, status, blocked_until, block_reason").execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch users: {str(e)}")

class UserBlockRequest(BaseModel):
    status: str 
    duration_days: Optional[int] = None 
    reason: Optional[str] = "Please contact manager for clarification."

@router.patch("/users/{user_id}/status")
async def update_user_status(user_id: str, payload: UserBlockRequest, request: Request, admin=Depends(SecurityEngine.verify_token)):
    try:
        blocked_until = None
        if payload.status.upper() == 'BLOCKED' and payload.duration_days:
            blocked_until = (datetime.now(timezone.utc) + timedelta(days=payload.duration_days)).isoformat()
        
        update_data = {
            "status": payload.status.upper(),
            "block_reason": payload.reason if payload.status.upper() != 'ACTIVE' else None,
            "blocked_until": blocked_until if payload.status.upper() == 'BLOCKED' else None
        }

        supabase.table("cashiers").update(update_data).eq("id", user_id).execute()
        await redis_client.delete(f"session:{user_id}")

        # LIVE SYNC: Instantly terminate active cashier session
        if payload.status.upper() == 'BLOCKED':
            if hasattr(request.app.state, 'sockets'):
                await request.app.state.sockets.force_logout_cashier(user_id, payload.reason)

        return {"status": "success", "message": f"User status updated to {payload.status.upper()}."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update user status: {str(e)}")

@router.delete("/users/{user_id}")
async def delete_user_account(user_id: str, request: Request, admin=Depends(SecurityEngine.verify_token)):
    try:
        supabase.table("cashiers").delete().eq("id", user_id).execute()
        await redis_client.delete(f"session:{user_id}")
        
        # LIVE SYNC: Instantly terminate active cashier session on deletion
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
            
        # LIVE SYNC: Push updates to Admin Dashboard
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
        query = supabase.table("expenses").select("*, cashiers(full_name)")
        if date:
            query = query.eq("business_date", date)
        elif month:
            query = query.gte("business_date", f"{month}-01").lte("business_date", f"{month}-31")
        
        if shift and shift != "All":
            query = query.eq("shift", shift)
            
        res = query.order("created_at", desc=True).execute()
        expenses = res.data or []

        total_exp = sum(float(e.get("amount", 0)) for e in expenses)
        cash_exp = sum(float(e.get("cash_amount", 0)) for e in expenses)
        mpesa_exp = sum(float(e.get("mpesa_amount", 0)) for e in expenses)

        cashier_breakdown = {}
        for e in expenses:
            c_name = e.get("cashiers", {}).get("full_name") if e.get("cashiers") else "Admin / System"
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
        exp_query = supabase.table("expenses").select("*, cashiers(full_name)")

        if date:
            sales_query = sales_query.eq("business_date", date)
            exp_query = exp_query.eq("business_date", date)
        elif month:
            sales_query = sales_query.gte("business_date", f"{month}-01").lte("business_date", f"{month}-31")
            exp_query = exp_query.gte("business_date", f"{month}-01").lte("business_date", f"{month}-31")
        elif year:
            sales_query = sales_query.gte("business_date", f"{year}-01-01").lte("business_date", f"{year}-12-31")
            exp_query = exp_query.gte("business_date", f"{year}-01-01").lte("business_date", f"{year}-12-31")

        sales_res = sales_query.order("created_at", desc=True).execute()
        exp_res = exp_query.order("created_at", desc=True).execute()

        sales = sales_res.data or []
        expenses = exp_res.data or []

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
        
        # INCREASED CACHE EXPIRY TO 60 SECONDS
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

        # INCREASED CACHE EXPIRY TO 60 SECONDS
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
            "assigned_shift": assigned_shift
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
                k_req = json.loads(k_data)
                if str(k_req.get("target_id")) == str(target_id):
                    k_req["status"] = "approved"
                    await redis_client.setex(k, 60, json.dumps(k_req)) 
        
        for key in await redis_client.keys("dashboard:analytics:*"):
            await redis_client.delete(key)
        for key in await redis_client.keys("smartgrill:*"):
            await redis_client.delete(key)
            
        # LIVE SYNC: Push updates to Admin Dashboard after successful deletion
        if hasattr(request.app.state, 'sockets'):
            await request.app.state.sockets.broadcast_admin({"action": "refresh_sales"})
            
        return {"status": "success", "message": "Item successfully deleted. The POS has been updated."}
        
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=f"Authorization execution failed: {str(e)}")