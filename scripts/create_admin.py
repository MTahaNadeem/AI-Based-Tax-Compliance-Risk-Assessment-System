import os
import sys
import uuid
import getpass
from datetime import datetime, timezone

# Ensure we can import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.portal_db import init_db, get_conn
from app.portal_auth import hash_password

def main():
    print("TaxNet Graph — Create Admin/Official Account")
    print("-" * 45)
    
    # Initialize DB (creates table if not exists)
    init_db()
    
    username = input("Username: ").strip()
    if not username:
        print("Error: Username cannot be empty.")
        return
        
    full_name = input("Full Name: ").strip()
    if not full_name:
        print("Error: Full Name cannot be empty.")
        return
        
    role = input("Role [auditor/admin] (default: auditor): ").strip().lower()
    if not role:
        role = "auditor"
    if role not in ("auditor", "admin"):
        print("Error: Role must be 'auditor' or 'admin'.")
        return
        
    password = getpass.getpass("Password: ")
    if len(password) < 8:
        print("Error: Password must be at least 8 characters.")
        return
        
    confirm = getpass.getpass("Confirm Password: ")
    if password != confirm:
        print("Error: Passwords do not match.")
        return

    admin_uuid = str(uuid.uuid4())
    pw_hash = hash_password(password)
    created_at = datetime.now(timezone.utc).isoformat()
    
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO admins (uuid, username, full_name, role, password_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (admin_uuid, username, full_name, role, pw_hash, created_at)
        )
        conn.commit()
        print(f"\nSuccess! Created {role} account for '{username}'.")
    except Exception as e:
        print(f"\nFailed to create account: {e}")

if __name__ == "__main__":
    main()
