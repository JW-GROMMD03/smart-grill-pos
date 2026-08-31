import os
import asyncio
from dotenv import load_dotenv
from app.core.supabase import supabase

# Load secure variables from .env
load_dotenv()

async def create_super_admin():
    email = os.getenv("SUPERUSER_EMAIL")
    password = os.getenv("SUPERUSER_PASSWORD")
    
    if not email or not password:
        print("❌ Error: Superuser credentials not found in .env file.")
        return

    print(f"🚀 Initializing Superuser creation for {email}...")
    
    try:
        # 1. Create user in Supabase Auth
        auth_res = supabase.auth.sign_up({
            "email": email,
            "password": password,
        })
        
        user = auth_res.user
        if not user:
            print("❌ Failed to create user in Auth system.")
            return

        # 2. Force the role to 'admin' in your profiles table
        supabase.table("profiles").upsert({
            "id": user.id,
            "full_name": "Smart Grill Executive",
            "role": "admin"
        }).execute()

        print("✅ Superuser successfully created and granted Admin privileges!")
        
    except Exception as e:
        print(f"⚠️ Error creating superuser: {e}")

if __name__ == "__main__":
    asyncio.run(create_super_admin())