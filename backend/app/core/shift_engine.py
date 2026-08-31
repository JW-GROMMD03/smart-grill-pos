from datetime import datetime, time, timedelta
import pytz
import redis.asyncio as redis
from fastapi import HTTPException
from app.core.redis import redis_client

class ShiftEngine:
    @staticmethod
    def get_shift_context(now: datetime = None):
        if not now:
            tz = pytz.timezone('Africa/Nairobi')
            now = datetime.now(tz)
            
        hour = now.hour
        minute = now.minute
        current_date = now.date()
        prev_date = current_date - timedelta(days=1)
        
        # Day Shift Strict: 08:00 to 19:59 (Grace to 20:30)
        if 8 <= hour < 20:
            current_shift = "DAY"
            current_bdate = str(current_date)
            prev_shift = "NIGHT"
            prev_bdate = str(prev_date)
            in_grace = (hour == 8 and minute <= 15)
        # Night Shift Strict: 20:00 to 07:59 (Grace to 08:15)
        else:
            if hour >= 20:
                current_shift = "NIGHT"
                current_bdate = str(current_date)
                prev_shift = "DAY"
                prev_bdate = str(current_date)
                in_grace = (hour == 20 and minute <= 30)
            else: # 00:00 to 07:59
                current_shift = "NIGHT"
                current_bdate = str(prev_date)
                prev_shift = "DAY"
                prev_bdate = str(prev_date)
                in_grace = False
                
        return (current_shift, current_bdate), (prev_shift, prev_bdate), in_grace

    @staticmethod
    def calculate_current_shift() -> tuple[str, str]:
        """Read-only fetch for the current baseline shift (used for fetching sales/expenses)."""
        (curr_shift, curr_bdate), _, _ = ShiftEngine.get_shift_context()
        return curr_shift, curr_bdate

    @staticmethod
    async def validate_shift_access(cashier_id: str, assigned_shift: str, background_tasks=None, report_func=None) -> tuple[str, str]:
        """Enforces grace periods, lockouts, and triggers background PDF reports on transition."""
        safe_assigned = str(assigned_shift).strip().upper() if assigned_shift else "NONE"
        (curr_shift, curr_bdate), (prev_shift, prev_bdate), in_grace = ShiftEngine.get_shift_context()
        
        curr_id = f"{curr_bdate}-{curr_shift}"
        prev_id = f"{prev_bdate}-{prev_shift}"
        
        # Check current active system shift
        active_shift_id = await redis_client.get("system:active_shift")
        if isinstance(active_shift_id, bytes):
            active_shift_id = active_shift_id.decode('utf-8')

        if in_grace:
            # System hasn't transitioned yet
            if not active_shift_id or active_shift_id == prev_id:
                if safe_assigned == prev_shift:
                    # Allow old shift to finish up
                    return prev_shift, prev_bdate
                elif safe_assigned == curr_shift:
                    # NEW shift claims the register. Lock out old shift & trigger PDF report.
                    await redis_client.set("system:active_shift", curr_id)
                    if report_func and background_tasks and active_shift_id == prev_id:
                        background_tasks.add_task(report_func, prev_shift, prev_bdate)
                    return curr_shift, curr_bdate
                else:
                    raise HTTPException(status_code=403, detail="Invalid shift assignment.")
            else:
                # System HAS transitioned. Old shift is locked out.
                if safe_assigned == prev_shift:
                    raise HTTPException(status_code=403, detail="Shift locked out. The new shift has already taken over.")
                elif safe_assigned == curr_shift:
                    return curr_shift, curr_bdate
        else:
            # Outside grace period, enforce strictly current shift.
            if active_shift_id != curr_id:
                await redis_client.set("system:active_shift", curr_id)
                if report_func and background_tasks and active_shift_id == prev_id:
                    background_tasks.add_task(report_func, prev_shift, prev_bdate)
                    
            if safe_assigned != curr_shift:
                raise HTTPException(status_code=403, detail=f"Shift locked. System is operating under {curr_shift} shift.")
            return curr_shift, curr_bdate
            
        raise HTTPException(status_code=403, detail="Shift validation failed.")