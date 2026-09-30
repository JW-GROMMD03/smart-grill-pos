import os
import json
import base64
from pathlib import Path
from contextlib import asynccontextmanager
from typing import List, Dict, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.api.v1 import auth, pos, admin
from app.core.redis import check_redis_connection, redis_client
from app.core.security import SecurityEngine

# =============================================================
# REAL-TIME WEBSOCKET MANAGER FOR MULTI-BRANCH ENTERPRISE CONNECTIONS 
# =============================================================
class ConnectionManager:
    """
    Manages active WebSocket connections for admins and cashiers across
    multiple business locations (branches).
    """
    def __init__(self):
        # Admin sockets mapped to branch or 'All'
        self.admin_connections: List[Dict[str, any]] = []
        # Cashier sockets mapped by cashier_id with metadata (branch, etc.)
        self.cashier_connections: Dict[str, Dict[str, any]] = {}

    async def connect_admin(self, websocket: WebSocket, branch: str = "All"):
        await websocket.accept()
        self.admin_connections.append({"websocket": websocket, "branch": branch})

    def disconnect_admin(self, websocket: WebSocket):
        self.admin_connections = [
            conn for conn in self.admin_connections if conn["websocket"] != websocket
        ]

    async def connect_cashier(self, websocket: WebSocket, cashier_id: str, branch: str = "Smartgrill"):
        await websocket.accept()
        self.cashier_connections[cashier_id] = {
            "websocket": websocket,
            "branch": branch
        }

    def disconnect_cashier(self, cashier_id: str):
        if cashier_id in self.cashier_connections:
            del self.cashier_connections[cashier_id]

    # Broadcast updates to admins listening to all branches or a specific branch
    async def broadcast_admin(self, message: dict, branch: Optional[str] = None):
        for conn in list(self.admin_connections):
            admin_branch = conn["branch"]
            if branch is None or admin_branch == "All" or admin_branch == branch:
                try:
                    await conn["websocket"].send_json(message)
                except Exception:
                    pass

    # Push payload directly to all connected cashiers or filter by branch
    async def broadcast_cashier(self, message: dict, branch: Optional[str] = None):
        for conn in list(self.cashier_connections.values()):
            if branch is None or conn["branch"] == branch:
                try:
                    await conn["websocket"].send_json(message)
                except Exception:
                    pass

    async def send_to_cashier(self, cashier_id: str, message: dict):
        """Send a direct WebSocket message to a specific cashier."""
        if cashier_id in self.cashier_connections:
            try:
                await self.cashier_connections[cashier_id]["websocket"].send_json(message)
            except Exception:
                pass

    async def force_logout_cashier(self, cashier_id: str, reason: str):
        if cashier_id in self.cashier_connections:
            try:
                await self.cashier_connections[cashier_id]["websocket"].send_json({
                    "action": "force_logout",
                    "reason": reason
                })
            except Exception:
                pass

socket_manager = ConnectionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await check_redis_connection()
    app.state.sockets = socket_manager  # Inject socket manager into app state
    yield
    await redis_client.aclose()

app = FastAPI(
    title="Smart Grill POS",
    version="1.2.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None
)

# Parse existing settings if any
origins = [origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",") if origin.strip()]

# Explicitly inject custom domains, backend paths, and local testing URLs
origins.extend([
    "https://smartgrillpos.com",
    "https://www.smartgrillpos.com",
    "https://smart-grill-backend.onrender.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000"
])

# Remove any duplicates
origins = list(set(origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)

# Include core sub-routers
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(pos.router, prefix="/api/v1/pos", tags=["Cashier Operations"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin Portal"])

@app.get("/health")
async def health():
    return {
        "status": "online",
        "version": app.version,
        "active_admin_websockets": len(socket_manager.admin_connections),
        "active_cashier_websockets": len(socket_manager.cashier_connections)
    }


# =============================================================
# WEBSOCKET ENDPOINTS
# =============================================================
def decode_token_safe(token: str) -> dict:
    """Safely decodes JWT passed in query string to bypass missing header 403 errors."""
    try:
        payload = token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception:
        return {}

@app.websocket("/ws/admin")
async def websocket_admin(
    websocket: WebSocket,
    token: str = Query(...),
    branch: str = Query("All")
):
    user = decode_token_safe(token)
    if user.get("role") != "admin":
        await websocket.close(code=1008)
        return

    await socket_manager.connect_admin(websocket, branch=branch)
    try:
        while True:
            await websocket.receive_text()  # Keep connection alive
    except WebSocketDisconnect:
        socket_manager.disconnect_admin(websocket)

@app.websocket("/ws/cashier/{cashier_id}")
async def websocket_cashier(
    websocket: WebSocket,
    cashier_id: str,
    token: str = Query(...),
    branch: str = Query("Smartgrill")
):
    user = decode_token_safe(token)
    if user.get("sub") != cashier_id and user.get("id") != cashier_id:
        await websocket.close(code=1008)
        return

    # Use branch from JWT payload if present, otherwise fallback to query string
    cashier_branch = user.get("branch", branch)

    await socket_manager.connect_cashier(websocket, cashier_id, branch=cashier_branch)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        socket_manager.disconnect_cashier(cashier_id)

# --- SMART FOLDER DETECTION & STATIC FRONTEND MOUNTING ---
BASE_DIR = Path(__file__).resolve().parent.parent

if (BASE_DIR / "frontend").exists():
    FRONTEND_DIR = BASE_DIR / "frontend"
elif (BASE_DIR.parent / "frontend").exists():
    FRONTEND_DIR = BASE_DIR.parent / "frontend"
else:
    print("⚠️ WARNING: Could not locate the 'frontend' directory.")
    FRONTEND_DIR = None

if FRONTEND_DIR:
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")