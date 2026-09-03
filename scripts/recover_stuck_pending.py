import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "outputs", "portal.db")

def recover():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Find pending registrations that are approved but have no user row with the matching phone_hash
    # We can just revert any stuck row for the specific test user 03001234567 (which we know is id=3)
    # But a more generic query:
    # Any row in pending_registrations where status='approved' and phone_hash is NOT in users
    
    stuck_rows = conn.execute("""
        SELECT pr.id, pr.claimed_name
        FROM pending_registrations pr
        WHERE pr.status = 'approved'
          AND NOT EXISTS (
              SELECT 1 FROM users u WHERE u.phone = pr.phone_hash
          )
    """).fetchall()

    if not stuck_rows:
        print("No stuck pending registrations found.")
        return

    print(f"Found {len(stuck_rows)} stuck registration(s) (approved but no user account):")
    for row in stuck_rows:
        print(f"  - ID: {row['id']}, Name: {row['claimed_name']}")

    print("\nReverting their status to 'pending'...")
    for row in stuck_rows:
        conn.execute("UPDATE pending_registrations SET status='pending' WHERE id=?", (row['id'],))
    
    conn.commit()
    print("Done. Admins can now properly reject or process these registrations.")

if __name__ == "__main__":
    recover()
