-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Profiles Table
CREATE TABLE IF NOT EXISTS public.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  full_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('cashier', 'admin')) DEFAULT 'cashier',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Inventory / Meat Items Table
CREATE TABLE IF NOT EXISTS public.inventory (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  item_name TEXT NOT NULL UNIQUE,
  price_per_kg NUMERIC(10, 2) NOT NULL CHECK (price_per_kg > 0),
  stock_kg NUMERIC(10, 3) NOT NULL DEFAULT 0.000 CHECK (stock_kg >= 0),
  is_active BOOLEAN DEFAULT TRUE,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Orders Table
CREATE TABLE IF NOT EXISTS public.orders (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cashier_id UUID NOT NULL REFERENCES public.profiles(id),
  total_amount NUMERIC(10, 2) NOT NULL,
  payment_method TEXT NOT NULL CHECK (payment_method IN ('cash', 'mpesa', 'card')),
  status TEXT NOT NULL CHECK (status IN ('completed', 'cancelled')) DEFAULT 'completed',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Order Items Table
CREATE TABLE IF NOT EXISTS public.order_items (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  order_id UUID NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
  item_id UUID NOT NULL REFERENCES public.inventory(id),
  weight_kg NUMERIC(10, 3) NOT NULL CHECK (weight_kg > 0),
  unit_price NUMERIC(10, 2) NOT NULL,
  subtotal NUMERIC(10, 2) NOT NULL
);

-- Performance Indexing for High-Speed DB Operations
CREATE INDEX IF NOT EXISTS idx_inventory_active ON public.inventory(is_active);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON public.orders(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON public.order_items(order_id);

-- Enable RLS
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public profiles read" ON public.profiles FOR SELECT USING (true);
CREATE POLICY "Authenticated inventory read" ON public.inventory FOR SELECT USING (auth.role() = 'authenticated');
CREATE POLICY "Cashier insert orders" ON public.orders FOR INSERT WITH CHECK (auth.role() = 'authenticated');