import json
from datetime import datetime, date
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.redis import redis_client

CACHE_TTL = 300  # 5 minutes cache expiration

class SmartGrillAnalyticsService:

    @staticmethod
    async def get_dashboard_analytics(db: AsyncSession, start_date: str, end_date: str) -> Dict[str, Any]:
        cache_key = f"smartgrill:analytics:{start_date}:{end_date}"
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)

        # Dynamic SQL query aggregating live transactions and shift metrics
        query = text("""
            SELECT 
                COALESCE(SUM(total_amount), 0) AS total_revenue,
                COUNT(id) AS total_transactions,
                COALESCE(SUM(CASE WHEN EXTRACT(HOUR FROM created_at) BETWEEN 6 AND 17 THEN total_amount ELSE 0 END), 0) AS day_shift_revenue,
                COALESCE(SUM(CASE WHEN EXTRACT(HOUR FROM created_at) NOT BETWEEN 6 AND 17 THEN total_amount ELSE 0 END), 0) AS night_shift_revenue
            FROM transactions
            WHERE created_at::date BETWEEN :start_date AND :end_date
        """)
        result = await db.execute(query, {"start_date": start_date, "end_date": end_date})
        metrics = result.mappings().first()

        # Dynamic Category Breakdown
        cat_query = text("""
            SELECT c.name as category, COALESCE(SUM(ti.price * ti.quantity), 0) as revenue
            FROM transaction_items ti
            JOIN categories c ON ti.category_id = c.id
            JOIN transactions t ON ti.transaction_id = t.id
            WHERE t.created_at::date BETWEEN :start_date AND :end_date
            GROUP BY c.name
            ORDER BY revenue DESC
        """)
        cat_result = await db.execute(cat_query, {"start_date": start_date, "end_date": end_date})
        categories = cat_result.mappings().all()

        data = {
            "total_revenue": float(metrics["total_revenue"]),
            "total_transactions": metrics["total_transactions"],
            "day_shift_revenue": float(metrics["day_shift_revenue"]),
            "night_shift_revenue": float(metrics["night_shift_revenue"]),
            "categories": [{"category": row["category"], "revenue": float(row["revenue"])} for row in categories]
        }

        await redis_client.setex(cache_key, CACHE_TTL, json.dumps(data))
        return data

    @staticmethod
    async def get_greens_monitor(db: AsyncSession, start_date: str, end_date: str) -> Dict[str, Any]:
        cache_key = f"smartgrill:greens:{start_date}:{end_date}"
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)

        query = text("""
            SELECT 
                i.name as vegetable,
                SUM(ti.quantity) as plates_sold,
                SUM(ti.price * ti.quantity) as revenue,
                AVG(ti.price) as avg_price,
                COUNT(DISTINCT ti.transaction_id) as transactions
            FROM transaction_items ti
            JOIN items i ON ti.item_id = i.id
            JOIN categories c ON i.category_id = c.id
            JOIN transactions t ON ti.transaction_id = t.id
            WHERE LOWER(c.name) = 'greens' AND t.created_at::date BETWEEN :start_date AND :end_date
            GROUP BY i.name
        """)
        result = await db.execute(query, {"start_date": start_date, "end_date": end_date})
        greens = result.mappings().all()

        total_rev = sum(float(r["revenue"]) for r in greens)
        total_plates = sum(int(r["plates_sold"]) for r in greens)

        data = {
            "total_greens_revenue": total_rev,
            "total_plates_sold": total_plates,
            "variety_count": len(greens),
            "vegetables": [
                {
                    "vegetable": r["vegetable"],
                    "plates_sold": int(r["plates_sold"]),
                    "revenue": float(r["revenue"]),
                    "avg_price": float(r["avg_price"]),
                    "transactions": int(r["transactions"]),
                    "status": "Active"
                } for r in greens
            ]
        }

        await redis_client.setex(cache_key, CACHE_TTL, json.dumps(data))
        return data