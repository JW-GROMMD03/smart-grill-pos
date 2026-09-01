from datetime import datetime, time, timedelta
import pytz
import json
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
    async def get_effective_shift_context(now: datetime = None) -> tuple[str, str, bool]:
        """Fetches the current active shift considering Admin Forced Overrides and Permits."""
        # 1. Check Admin Force Override
        override_raw = await redis_client.get("system:shift_override")
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
        """Read-only fetch for the current baseline shift (used for fetching sales/expenses)."""
        (curr_shift, curr_bdate), _, _ = ShiftEngine.get_shift_context()
        return curr_shift, curr_bdate

    @staticmethod
    async def validate_shift_access(cashier_id: str, assigned_shift: str, background_tasks=None, report_func=None) -> tuple[str, str]:
        """Enforces grace periods, dynamic shift permits, manual admin overrides, and lockouts."""
        safe_assigned = str(assigned_shift).strip().upper() if assigned_shift else "NONE"
        if safe_assigned in ["DAY SHIFT", "DAY_SHIFT"]:
            safe_assigned = "DAY"
        elif safe_assigned in ["NIGHT SHIFT", "NIGHT_SHIFT"]:
            safe_assigned = "NIGHT"

        # 1. CHECK ADMIN MANUAL OVERRIDE (FORCED DAY / FORCED NIGHT)
        override_raw = await redis_client.get("system:shift_override")
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
                    raise HTTPException(
                        status_code=403, 
                        detail=f"Shift locked. Admin has manually enforced {forced_shift} shift operations."
                    )

        # 2. CHECK ADMIN ACTIVE OVERLAP / EXTENSION PERMIT
        permit_raw = await redis_client.get("system:shift_permit")
        active_permit = None
        if permit_raw:
            if isinstance(permit_raw, bytes):
                permit_raw = permit_raw.decode('utf-8')
            active_permit = json.loads(permit_raw)

        (curr_shift, curr_bdate), (prev_shift, prev_bdate), in_grace = ShiftEngine.get_shift_context()
        curr_id = f"{curr_bdate}-{curr_shift}"
        prev_id = f"{prev_bdate}-{prev_shift}"

        # If Admin granted a permit (Extension, Early Start, or Overlap)
        if active_permit and active_permit.get("status") == "ACTIVE":
            permitted_shift = str(active_permit.get("permitted_shift", "")).strip().upper()
            permit_type = str(active_permit.get("permit_type", "OVERLAP")).strip().upper()

            # Grant access if overlap is active OR if this specific cashier shift is permitted
            if permit_type == "OVERLAP" or safe_assigned == permitted_shift or safe_assigned == curr_shift:
                eff_shift = safe_assigned if safe_assigned in ["DAY", "NIGHT"] else curr_shift
                eff_bdate = curr_bdate if eff_shift == curr_shift else (prev_bdate if eff_shift == prev_shift else curr_bdate)
                return eff_shift, eff_bdate

        # 3. STANDARD TIME-BASED & GRACE PERIOD VALIDATION
        active_shift_id = await redis_client.get("system:active_shift")
        if isinstance(active_shift_id, bytes):
            active_shift_id = active_shift_id.decode('utf-8')

        if in_grace:
            if not active_shift_id or active_shift_id == prev_id:
                if safe_assigned == prev_shift:
                    return prev_shift, prev_bdate
                elif safe_assigned == curr_shift:
                    await redis_client.set("system:active_shift", curr_id)
                    if report_func and background_tasks and active_shift_id == prev_id:
                        background_tasks.add_task(report_func, prev_shift, prev_bdate)
                    return curr_shift, curr_bdate
                else:
                    raise HTTPException(status_code=403, detail="Invalid shift assignment.")
            else:
                if safe_assigned == prev_shift:
                    raise HTTPException(status_code=403, detail="Shift locked out. The new shift has already taken over.")
                elif safe_assigned == curr_shift:
                    return curr_shift, curr_bdate
        else:
            if active_shift_id != curr_id:
                await redis_client.set("system:active_shift", curr_id)
                if report_func and background_tasks and active_shift_id == prev_id:
                    background_tasks.add_task(report_func, prev_shift, prev_bdate)
                    
            if safe_assigned != curr_shift:
                raise HTTPException(status_code=403, detail=f"Shift locked. System is operating under {curr_shift} shift.")
            return curr_shift, curr_bdate
            
        raise HTTPException(status_code=403, detail="Shift validation failed.")