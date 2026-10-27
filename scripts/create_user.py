#!/usr/bin/env python3
"""Create an admin or verifier account directly in the database (these roles cannot self-register).
Usage: python scripts/create_user.py admin@example.com 'A-Strong-Passw0rd' admin"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.db import SessionLocal, User, init_db
from app.security import hash_password, check_password_strength

email, pw, role = sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "verifier"
assert role in ("admin", "verifier", "owner")
check_password_strength(pw)
init_db()
with SessionLocal() as db:
    db.add(User(email=email.lower(), pw_hash=hash_password(pw), role=role)); db.commit()
print("created", email, role)
