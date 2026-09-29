# shift_engine.py
from datetime import datetime, timedelta
import pytz
import json
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
        
        # Day Shift Strict: 08:00 to 19:59 (Grace period up to 20:30)
        if 8 <= hour < 20:
            current_shift = "DAY"
            current_bdate = str(current_date)
            prev_shift = "NIGHT"
            prev_bdate = str(prev_date)
            in_grace = (hour == 8 and minute <= 15)
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
    async def get_effective_shift_context(branch_id: str, now: datetime = None) -> tuple[str, str, bool]:
        """Fetches current active shift considering branch overrides and admin permits."""
        override_raw = await redis_client.get(f"system:shift_override:{branch_id}")
        if override_raw:
            if isinstance(override_raw, bytes):
                override_raw = override_raw.decode('utf-8')
            override_data = json.loads(override_raw)
            forced_shift = str(override_data.get("shift", "")).strip().upper()
            if forced_shift in ["DAY", "NIGHT"]:
                tz = pytz.timezone('Africa/Nairobi')
                now_dt = now or datetime.now(tz)
                bdate = str(now_dt.date())
                if forced_shift == "NIGHT" and now_dt.hour < 8:
                    bdate = str(now_dt.date() - timedelta(days=1))
                return forced_shift, bdate, True

        (curr_shift, curr_bdate), _, _ = ShiftEngine.get_shift_context(now)
        return curr_shift, curr_bdate, False

    @staticmethod
    def calculate_current_shift() -> tuple[str, str]:
        (curr_shift, curr_bdate), _, _ = ShiftEngine.get_shift_context()
        return curr_shift, curr_bdate

    @staticmethod
    async def validate_shift_access(cashier_id: str, assigned_shift: str, branch_id: str, background_tasks=None, report_func=None) -> tuple[str, str]:
        safe_assigned = str(assigned_shift).strip().upper() if assigned_shift else "NONE"
        if safe_assigned in ["DAY SHIFT", "DAY_SHIFT"]:
            safe_assigned = "DAY"
        elif safe_assigned in ["NIGHT SHIFT", "NIGHT_SHIFT"]:
            safe_assigned = "NIGHT"

        override_raw = await redis_client.get(f"system:shift_override:{branch_id}")
        if override_raw:
            if isinstance(override_raw, bytes):
                override_raw = override_raw.decode('utf-8')
            override_data = json.loads(override_raw)
            forced_shift = str(override_data.get("shift", "")).strip().upper()
            if forced_shift in ["DAY", "NIGHT"]:
                if safe_assigned == forced_shift:
                    tz = pytz.timezone('Africa/Nairobi')
                    now_dt = datetime.now(tz)
                    bdate = str(now_dt.date())
                    if forced_shift == "NIGHT" and now_dt.hour < 8:
                        bdate = str(now_dt.date() - timedelta(days=1))
                    return forced_shift, bdate
                else:
                    raise HTTPException(status_code=403, detail=f"Shift locked to {forced_shift} for branch {branch_id}.")

        permit_raw = await redis_client.get(f"system:shift_permit:{branch_id}")
        active_permit = None
        if permit_raw:
            if isinstance(permit_raw, bytes):
                permit_raw = permit_raw.decode('utf-8')
            active_permit = json.loads(permit_raw)

        (curr_shift, curr_bdate), (prev_shift, prev_bdate), in_grace = ShiftEngine.get_shift_context()
        curr_id = f"{branch_id}-{curr_bdate}-{curr_shift}"
        prev_id = f"{branch_id}-{prev_bdate}-{prev_shift}"

        if active_permit and active_permit.get("status") == "ACTIVE":
            permitted_shift = str(active_permit.get("permitted_shift", "")).strip().upper()
            permit_type = str(active_permit.get("permit_type", "OVERLAP")).strip().upper()
            if permit_type == "OVERLAP" or safe_assigned == permitted_shift or safe_assigned == curr_shift:
                eff_shift = safe_assigned if safe_assigned in ["DAY", "NIGHT"] else curr_shift
                eff_bdate = curr_bdate if eff_shift == curr_shift else (prev_bdate if eff_shift == prev_shift else curr_bdate)
                return eff_shift, eff_bdate

        active_shift_id = await redis_client.get(f"system:active_shift:{branch_id}")
        if isinstance(active_shift_id, bytes):
            active_shift_id = active_shift_id.decode('utf-8')

        if in_grace:
            if not active_shift_id or active_shift_id == prev_id:
                if safe_assigned == prev_shift:
                    return prev_shift, prev_bdate
                elif safe_assigned == curr_shift:
                    await redis_client.set(f"system:active_shift:{branch_id}", curr_id)
                    if report_func and background_tasks and active_shift_id == prev_id:
                        background_tasks.add_task(report_func, prev_shift, prev_bdate, branch_id)
                    return curr_shift, curr_bdate
            else:
                if safe_assigned == prev_shift:
                    raise HTTPException(status_code=403, detail="Shift locked out. New shift active.")
                elif safe_assigned == curr_shift:
                    return curr_shift, curr_bdate
        else:
            if active_shift_id != curr_id:
                await redis_client.set(f"system:active_shift:{branch_id}", curr_id)
                if report_func and background_tasks and active_shift_id == prev_id:
                    background_tasks.add_task(report_func, prev_shift, prev_bdate, branch_id)
                    
            if safe_assigned != curr_shift:
                raise HTTPException(status_code=403, detail=f"Shift locked. System operating under {curr_shift} shift.")
            return curr_shift, curr_bdate
            
        raise HTTPException(status_code=403, detail="Shift validation failed.")