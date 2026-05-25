import os
import sqlite3

def check_file(path):
    if os.path.exists(path):
        size = os.path.getsize(path)
        print(f"File {path}: Size = {size} bytes")
        try:
            conn = sqlite3.connect(path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cursor.fetchall()]
            print(f"  Tables: {tables}")
            if 'accounts' in tables:
                cursor.execute("SELECT COUNT(*) FROM accounts")
                count = cursor.fetchone()[0]
                print(f"  Accounts count: {count}")
                cursor.execute("SELECT status, COUNT(*) FROM accounts GROUP BY status")
                print(f"    Status: {cursor.fetchall()}")
            if 'registration_tasks' in tables:
                cursor.execute("SELECT COUNT(*) FROM registration_tasks")
                count = cursor.fetchone()[0]
                print(f"  Registration tasks count: {count}")
            conn.close()
        except Exception as e:
            print(f"  Error: {e}")
    else:
        print(f"File {path} does not exist.")

print("--- Current Database ---")
check_file("data/database.db")

print("\n--- Backup Database ---")
check_file("data/database.db.bak")
