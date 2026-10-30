-- Qist Installment Management System: run this whole file in Supabase -> SQL Editor
create extension if not exists pgcrypto;

create table users (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  email text unique not null,
  phone text,
  cnic text unique,
  password_hash text not null,
  role text not null default 'customer' check (role in ('admin','officer','customer')),
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table products (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  category text not null default 'General',
  description text,
  cash_price integer not null check (cash_price > 0),
  stock integer not null default 0 check (stock >= 0),
  image_url text,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table plans (
  months integer primary key check (months > 0),
  markup_pct numeric(5,2) not null check (markup_pct >= 0)
);

create table applications (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references users(id) on delete restrict,
  product_id uuid not null references products(id) on delete restrict,
  months integer not null references plans(months),
  total_price integer not null,
  down_payment integer not null,
  monthly_amount integer not null,
  address text not null,
  guarantor_name text not null,
  guarantor_cnic text not null,
  guarantor_phone text not null,
  guarantor_address text not null,
  cnic_doc text,
  bill_doc text,
  status text not null default 'submitted'
    check (status in ('submitted','needs_info','verified','rejected','active','completed')),
  officer_id uuid references users(id),
  officer_notes text,
  admin_notes text,
  start_date date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table installments (
  id uuid primary key default gen_random_uuid(),
  application_id uuid not null references applications(id) on delete restrict,
  seq integer not null,
  due_date date not null,
  amount integer not null,
  status text not null default 'pending' check (status in ('pending','paid')),
  paid_on date,
  fine_charged integer not null default 0
);
create index on installments (application_id);
create index on installments (status, due_date);

create table payments (
  id uuid primary key default gen_random_uuid(),
  application_id uuid not null references applications(id) on delete restrict,
  installment_id uuid references installments(id) on delete restrict,
  kind text not null default 'installment' check (kind in ('down','installment')),
  amount integer not null,
  method text not null check (method in ('cash','jazzcash','easypaisa','bank')),
  reference text,
  status text not null default 'pending' check (status in ('pending','confirmed','rejected')),
  txn_id text unique not null,
  recorded_by uuid references users(id),
  created_at timestamptz not null default now()
);

create table audit_log (
  id bigint generated always as identity primary key,
  actor_id uuid,
  action text not null,
  detail text,
  created_at timestamptz not null default now()
);

-- Lock every table: only the server (service_role key) can touch data.
alter table users enable row level security;
alter table products enable row level security;
alter table plans enable row level security;
alter table applications enable row level security;
alter table installments enable row level security;
alter table payments enable row level security;
alter table audit_log enable row level security;

-- Private bucket for CNIC / utility bill uploads
insert into storage.buckets (id, name, public) values ('documents','documents', false)
on conflict (id) do nothing;

-- Default plans (admin can change markup later)
insert into plans (months, markup_pct) values (3, 8), (6, 15), (12, 28);

-- Sample products
insert into products (name, category, description, cash_price, stock) values
 ('Haier 1.5 Ton Inverter AC', 'Home appliances', 'Energy-saving inverter air conditioner', 145000, 6),
 ('Honda CD 70 (2025)', 'Bikes', 'Fuel-efficient commuter bike', 168000, 4),
 ('Dawlance 14 cu ft Refrigerator', 'Home appliances', 'Frost-free double door', 98000, 8),
 ('Samsung 43" Smart LED TV', 'Electronics', 'Full HD smart television', 72000, 10),
 ('Orient 8kg Washing Machine', 'Home appliances', 'Top-load automatic', 54000, 5),
 ('Infinix Hot 40 Pro', 'Mobiles', '8GB / 256GB smartphone', 46000, 12);
