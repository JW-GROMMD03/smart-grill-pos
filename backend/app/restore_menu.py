import sys
from pathlib import Path

# Ensure app module can be imported
sys.path.append(str(Path(__file__).resolve().parent))

from app.database import SessionLocal
# Adjust the import path below if your Menu model name or location differs
from app.models.menu import MenuItem 
import uuid

default_menu_items = [
    # Meat Cuts
    {"name": "Beef Quarter", "category": "MEAT CUTS", "sub_category": "beef", "price": 200, "is_active": True},
    {"name": "Beef Half", "category": "MEAT CUTS", "sub_category": "beef", "price": 400, "is_active": True},
    {"name": "Beef Full (1 KG)", "category": "MEAT CUTS", "sub_category": "beef", "price": 800, "is_active": True},
    {"name": "Mbuzi Quarter", "category": "MEAT CUTS", "sub_category": "mbuzi", "price": 250, "is_active": True},
    {"name": "Mbuzi Half", "category": "MEAT CUTS", "sub_category": "mbuzi", "price": 500, "is_active": True},
    {"name": "Mbuzi Full (1 KG)", "category": "MEAT CUTS", "sub_category": "mbuzi", "price": 1000, "is_active": True},
    {"name": "Chicken Quarter", "category": "MEAT CUTS", "sub_category": "chicken", "price": 300, "is_active": True},
    {"name": "Chicken Half", "category": "MEAT CUTS", "sub_category": "chicken", "price": 600, "is_active": True},
    {"name": "Chicken Full (1 KG)", "category": "MEAT CUTS", "sub_category": "chicken", "price": 1200, "is_active": True},

    # Tilapia Variations
    {"name": "Tilapia (Small)", "category": "TILAPIA VARIATIONS", "sub_category": None, "price": 450, "is_active": True},
    {"name": "Tilapia (Medium)", "category": "TILAPIA VARIATIONS", "sub_category": None, "price": 600, "is_active": True},
    {"name": "Tilapia (Large)", "category": "TILAPIA VARIATIONS", "sub_category": None, "price": 850, "is_active": True},

    # Wet Fry
    {"name": "Beef Wet Fry", "category": "WETFRY", "sub_category": None, "price": 350, "is_active": True},
    {"name": "Mbuzi Wet Fry", "category": "WETFRY", "sub_category": None, "price": 400, "is_active": True},
    {"name": "Chicken Wet Fry", "category": "WETFRY", "sub_category": None, "price": 450, "is_active": True},

    # Greens & Kachumbari
    {"name": "Kachumbari", "category": "GREENS & KACHUMBARI", "sub_category": None, "price": 50, "is_active": True},
    {"name": "Managu", "category": "GREENS & KACHUMBARI", "sub_category": None, "price": 100, "is_active": True},
    {"name": "Cabbage", "category": "GREENS & KACHUMBARI", "sub_category": None, "price": 80, "is_active": True},

    # Chips & Packaging
    {"name": "Regular Chips", "category": "CHIPS & PACKAGING", "sub_category": None, "price": 150, "is_active": True},
    {"name": "Masala Chips", "category": "CHIPS & PACKAGING", "sub_category": None, "price": 200, "is_active": True},
    {"name": "Takeaway Box", "category": "CHIPS & PACKAGING", "sub_category": None, "price": 30, "is_active": True},

    # Drinks & Water
    {"name": "Coca-Cola (500ml)", "category": "DRINKS & WATER", "sub_category": None, "price": 100, "is_active": True},
    {"name": "Pepsi (500ml)", "category": "DRINKS & WATER", "sub_category": None, "price": 100, "is_active": True},
    {"name": "Drinking Water (1L)", "category": "DRINKS & WATER", "sub_category": None, "price": 50, "is_active": True}
]

def restore_catalog():
    db = SessionLocal()
    try:
        print("Restoring default menu catalog...")
        for item_data in default_menu_items:
            existing = db.query(MenuItem).filter(MenuItem.name == item_data["name"]).first()
            if not existing:
                new_item = MenuItem(
                    id=str(uuid.uuid4()),
                    name=item_data["name"],
                    category=item_data["category"],
                    sub_category=item_data["sub_category"],
                    price=item_data["price"],
                    is_active=item_data["is_active"]
                )
                db.add(new_item)
        db.commit()
        print("Menu catalog successfully restored!")
    except Exception as e:
        db.rollback()
        print(f"Error restoring catalog: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    restore_catalog()