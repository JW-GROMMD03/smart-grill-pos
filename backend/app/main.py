# app/main.py
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.core.config import settings
from app.api.v1 import auth, pos, admin
from app.core.redis import check_redis_connection, redis_client

@asynccontextmanager
async def lifespan(app: FastAPI):
    await check_redis_connection()
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

# ==========================================
# FIX: Fully Permissive CORS Middleware 
# ==========================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # Explicitly allows POST, PUT, DELETE, and OPTIONS
    allow_headers=["*"], # Explicitly allows all headers (Authorization, etc.)
)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(pos.router, prefix="/api/v1/pos", tags=["Cashier Operations"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin Portal"])

@app.get("/health")
async def health():
    return {"status": "online"}

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