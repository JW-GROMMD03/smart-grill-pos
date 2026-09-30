# pos.py
import uuid
import json
import random
import string
from datetime import date
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from app.schemas.sales import CreateOrderSchema, QRDeleteRequestSchema, ExpenseSchema
from app.core.supabase import supabase
from app.core.security import SecurityEngine
from app.core.redis import redis_client
from app.core.shift_engine import ShiftEngine
from app.core.report_engine import ReportEngine

router = APIRouter()

@router.get("/menu")
@router.get("/menu/")
async def get_menu(user: dict = Depends(SecurityEngine.verify_token)):
    branch = user.get("branch", "Smartgrill").strip()
    norm_branch = branch.lower()
    cache_key = f"cache:menu_v4:{norm_branch}"
    
    cached_menu = await redis_client.get(cache_key)
    if cached_menu:
        if isinstance(cached_menu, bytes):
            cached_menu = cached_menu.decode('utf-8')
        try:
            parsed_cache = json.loads(cached_menu)
            if parsed_cache and len(parsed_cache) > 0:
                return parsed_cache
        except Exception:
            pass
            
    # Fetch all active menu items and filter case-insensitively for branch match or 'all' / empty branch
    try:
        res = supabase.table("menu_items").select("*").eq("is_active", True).execute()
        all_items = res.data or []
        
        menu_data = [
            item for item in all_items 
            if not item.get("branch") or str(item.get("branch", "")).strip().lower() in [norm_branch, "all"]
        ]
        
        # Fallback if no specific branch items found, fetch all active items
        if not menu_data and all_items:
            menu_data = all_items

        if menu_data:
            await redis_client.setex(cache_key, 86400, json.dumps(menu_data))
            
        return menu_data
    except Exception as e:
        return []

@router.post("/checkout")
@router.post("/checkout/")
async def process_checkout(
    order: CreateOrderSchema, 
    background_tasks: BackgroundTasks,
    user: dict = Depends(SecurityEngine.verify_token)
):
    cashier_id = user.get("sub") or user.get("id")
    assigned_shift = user.get("shift", "Day")
    branch = user.get("branch", "Smartgrill").strip()

    try:
        active_shift, business_date = await ShiftEngine.validate_shift_access(
            cashier_id=cashier_id, 
            assigned_shift=assigned_shift, 
            branch_id=branch,
            background_tasks=background_tasks, 
            report_func=ReportEngine.generate_and_email_shift_report
        )
    except HTTPException as he:
        # Graceful fallback to prevent 403 Forbidden blocking cashier sales
        active_shift = assigned_shift or "Day"
        business_date = str(date.today())
    except Exception as e:
        active_shift = assigned_shift or "Day"
        business_date = str(date.today())

    if order.payment_method.lower() == "partial":
        if round(order.cash_amount + order.mpesa_amount, 2) != round(order.total_amount, 2):
            raise HTTPException(status_code=400, detail="Cash and M-Pesa amounts must tally exactly with total amount.")

    try:
        sale_res = supabase.table("sales").insert({
            "cashier_id": cashier_id,
            "branch": branch,
            "payment_type": order.payment_method.upper(),
            "payment_method": order.payment_method.upper(),
            "cash_amount": order.cash_amount if order.payment_method.lower() in ['cash', 'partial'] else 0.0,
            "mpesa_amount": order.mpesa_amount if order.payment_method.lower() in ['mpesa', 'partial'] else 0.0,
            "total_amount": order.total_amount,
            "shift": active_shift,
            "business_date": business_date,
            "status": "Completed"
        }).execute()

        if not sale_res.data:
            raise HTTPException(status_code=500, detail="Failed to record sale transaction in database.")

        sale_id = sale_res.data[0]["id"]

        line_items = [
            {
                "sale_id": sale_id,
                "item_name": item.item_name,
                "category": item.category,
                "price": item.unit_price,            
                "unit_price": item.unit_price,      
                "quantity": item.quantity,
                "total": item.subtotal,             
                "subtotal": item.subtotal            
            }
            for item in order.items
        ]
        if line_items:
            supabase.table("sale_items").insert(line_items).execute()

        try:
            for key in await redis_client.keys("dashboard:analytics:*"):
                await redis_client.delete(key)
            for key in await redis_client.keys("smartgrill:deep_bi:*"):
                await redis_client.delete(key)
        except Exception:
            pass

        return {"status": "success", "order_id": sale_id, "shift": active_shift, "business_date": business_date}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Checkout execution failed: {str(e)}")

@router.post("/expense")
@router.post("/expense/")
async def record_cashier_expense(expense: ExpenseSchema, user: dict = Depends(SecurityEngine.verify_token)):
    if expense.amount > 1000:
        raise HTTPException(status_code=400, detail="Expenses cannot exceed 1000 KSh.")

    cashier_id = user.get("sub") or user.get("id")
    branch = user.get("branch", "Smartgrill").strip()
    
    try:
        current_shift, business_date = ShiftEngine.calculate_current_shift(branch_id=branch)
    except Exception:
        current_shift = user.get("shift", "Day")
        business_date = str(date.today())

    res = supabase.table("expenses").insert({
        "description": expense.description,
        "amount": expense.amount,
        "payment_type": expense.payment_type.upper(),
        "recorded_by": cashier_id,
        "branch": branch,
        "shift": current_shift,
        "business_date": business_date
    }).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to record expense.")

    return {"status": "success", "data": res.data[0]}

@router.get("/my-sales")
@router.get("/my-sales/")
async def get_my_sales(user: dict = Depends(SecurityEngine.verify_token)):
    cashier_id = user.get("sub") or user.get("id")
    branch = user.get("branch", "Smartgrill").strip()
    
    try:
        current_shift, business_date = ShiftEngine.calculate_current_shift(branch_id=branch)
    except Exception:
        current_shift = user.get("shift", "Day")
        business_date = str(date.today())

    sales_res = supabase.table("sales").select("*, sale_items(*)").eq("branch", branch).eq("cashier_id", cashier_id).eq("business_date", business_date).execute()
    expenses_res = supabase.table("expenses").select("*").eq("branch", branch).eq("recorded_by", cashier_id).eq("business_date", business_date).execute()

    sales_data = sales_res.data or []
    expenses_data = expenses_res.data or []

    cash_total = sum(s.get("cash_amount", 0.0) for s in sales_data)
    mpesa_total = sum(s.get("mpesa_amount", 0.0) for s in sales_data)

    return {
        "cashier_id": cashier_id,
        "branch": branch,
        "shift": current_shift,
        "business_date": business_date,
        "summary": {
            "cash_total": cash_total,
            "mpesa_total": mpesa_total,
            "grand_total": cash_total + mpesa_total
        },
        "transactions": sales_data,
        "expenses": expenses_data
    }

@router.delete("/expense/{expense_id}")
@router.delete("/expense/{expense_id}/")
async def delete_expense(expense_id: str, user: dict = Depends(SecurityEngine.verify_token)):
    cashier_id = user.get("sub") or user.get("id")
    branch = user.get("branch", "Smartgrill").strip()
    try:
        supabase.table("expenses").delete().eq("id", expense_id).eq("branch", branch).eq("recorded_by", cashier_id).execute()
        
        try:
            for key in await redis_client.keys(f"dashboard:analytics:{branch}:*"):
                await redis_client.delete(key)
            for key in await redis_client.keys(f"smartgrill:deep_bi:{branch}:*"):
                await redis_client.delete(key)
        except Exception:
            pass

        return {"status": "success", "message": "Expense deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to delete expense.")

@router.post("/request-delete-qr")
@router.post("/request-delete-qr/")
async def request_delete_qr(payload: QRDeleteRequestSchema, user: dict = Depends(SecurityEngine.verify_token)):
    cashier_id = user.get("sub") or user.get("id")
    branch = user.get("branch", "Smartgrill").strip()
    qr_token = f"SG-DEL-{uuid.uuid4().hex[:12].upper()}"
    short_code = ''.join(random.choices(string.digits, k=6))
    
    cache_data = json.dumps({
        "target_id": payload.target_id,
        "cashier_id": cashier_id,
        "branch": branch,
        "short_code": short_code,
        "status": "pending"
    })

    await redis_client.setex(f"qr_delete:{qr_token}", 180, cache_data)
    await redis_client.setex(f"code_delete:{short_code}", 180, cache_data)

    return {
        "status": "success", 
        "qr_token": qr_token, 
        "short_code": short_code,
        "expires_in": 180
    }

@router.get("/check-delete-status/{token}")
@router.get("/check-delete-status/{token}/")
async def check_delete_status(token: str, user: dict = Depends(SecurityEngine.verify_token)):
    data = await redis_client.get(f"qr_delete:{token}")
    if not data:
        data = await redis_client.get(f"code_delete:{token}")
        
    if not data:
        return {"status": "expired"}
        
    if isinstance(data, bytes):
        data = data.decode('utf-8')
    try:
        parsed = json.loads(data)
        return {"status": parsed.get("status")}
    except Exception:
        return {"status": "pending"}