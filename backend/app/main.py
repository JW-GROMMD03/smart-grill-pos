import os
import json
from pathlib import Path
from contextlib import asynccontextmanager
from typing import List, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.api.v1 import auth, pos, admin
from app.core.redis import check_redis_connection, redis_client
from app.core.security import SecurityEngine

# ==========================================
# REAL-TIME WEBSOCKET MANAGER
# ==========================================
class ConnectionManager:
    def __init__(self):
        self.admin_connections: List[WebSocket] = []
        self.cashier_connections: Dict[str, WebSocket] = {}

    async def connect_admin(self, websocket: WebSocket):
        await websocket.accept()
        self.admin_connections.append(websocket)

    def disconnect_admin(self, websocket: WebSocket):
        if websocket in self.admin_connections:
            self.admin_connections.remove(websocket)

    async def connect_cashier(self, websocket: WebSocket, cashier_id: str):
        await websocket.accept()
        self.cashier_connections[cashier_id] = websocket

    def disconnect_cashier(self, cashier_id: str):
        if cashier_id in self.cashier_connections:
            del self.cashier_connections[cashier_id]

    async def broadcast_admin(self, message: dict):
        for connection in self.admin_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

    async def force_logout_cashier(self, cashier_id: str, reason: str):
        if cashier_id in self.cashier_connections:
            try:
                await self.cashier_connections[cashier_id].send_json({"action": "force_logout", "reason": reason})
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
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None
)

origins = [origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(pos.router, prefix="/api/v1/pos", tags=["Cashier Operations"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin Portal"])

@app.get("/health")
async def health():
    return {"status": "online"}

# ==========================================
# WEBSOCKET ENDPOINTS
# ==========================================
@app.websocket("/ws/admin")
async def websocket_admin(websocket: WebSocket, token: str = Query(...)):
    try:
        user = SecurityEngine.verify_token(token)
        if user.get("role") != "admin":
            raise ValueError("Unauthorized")
    except Exception:
        await websocket.close(code=1008)
        return

    await socket_manager.connect_admin(websocket)
    try:
        while True:
            await websocket.receive_text() # Keep connection alive
    except WebSocketDisconnect:
        socket_manager.disconnect_admin(websocket)

@app.websocket("/ws/cashier/{cashier_id}")
async def websocket_cashier(websocket: WebSocket, cashier_id: str, token: str = Query(...)):
    try:
        user = SecurityEngine.verify_token(token)
        if user.get("sub") != cashier_id:
            raise ValueError("Unauthorized")
    except Exception:
        await websocket.close(code=1008)
        return

    await socket_manager.connect_cashier(websocket, cashier_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        socket_manager.disconnect_cashier(cashier_id)

# --- SMART FOLDER DETECTION ---
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