from pydantic import BaseModel, Field
from typing import List, Optional

class OrderItemSchema(BaseModel):
    item_name: str
    category: str
    unit_price: float = Field(gt=0)
    quantity: int = Field(gt=0)
    subtotal: float = Field(gt=0)

class CreateOrderSchema(BaseModel):
    cashier_id: str
    payment_method: str
    cash_amount: Optional[float] = 0.0
    mpesa_amount: Optional[float] = 0.0
    total_amount: float
    items: List[OrderItemSchema]

class ExpenseSchema(BaseModel):
    description: str
    amount: float = Field(gt=0, le=1000, description="Cashier expenses cannot exceed 1000 KSh")
    payment_type: str

class QRDeleteRequestSchema(BaseModel):
    target_id: str
    cashier_id: str

class VerifyQRDeleteSchema(BaseModel):
    qr_token: Optional[str] = None
    short_code: Optional[str] = None
    admin_secret_key: str