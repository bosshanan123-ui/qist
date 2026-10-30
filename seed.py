"""Run once locally after schema.sql:  python seed.py
Creates the first Admin and one Verification Officer. Change the passwords afterwards."""
import os
from dotenv import load_dotenv
from supabase import create_client
from werkzeug.security import generate_password_hash

load_dotenv()
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
users = [
    ("Shop Admin", "admin@qist.pk", "0300-0000001", "00000-0000001-1", "Admin@12345", "admin"),
    ("Field Officer", "officer@qist.pk", "0300-0000002", "00000-0000002-2", "Officer@12345", "officer"),
]
for name, email, phone, cnic, pw, role in users:
    exists = sb.table("users").select("id").eq("email", email).execute().data
    if exists:
        print("exists:", email); continue
    sb.table("users").insert({"name": name, "email": email, "phone": phone, "cnic": cnic,
                              "password_hash": generate_password_hash(pw), "role": role}).execute()
    print("created:", email, "/", pw)
