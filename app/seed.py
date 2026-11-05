#!/usr/bin/env python3
"""Seed default org + Maxim admin. Writes initial password to a secure file once."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# allow running as script: python -m app.seed or python seed.py from app/
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "app"

from app.db import connect, init_db, db_path
from app.security import generate_password, hash_password


DEFAULT_ADMIN_EMAIL = os.environ.get("DUSHEVYE_ADMIN_EMAIL", "maxim59901@gmail.com")
DEFAULT_ADMIN_NAME = os.environ.get("DUSHEVYE_ADMIN_NAME", "Максим")
ORG_NAME = os.environ.get("DUSHEVYE_ORG_NAME", "Душевые / Станок")
ORG_SLUG = os.environ.get("DUSHEVYE_ORG_SLUG", "default")
PASSWORD_FILE = Path(os.environ.get("DUSHEVYE_ADMIN_PASS_FILE", "/root/dushevye-initial-admin.txt"))


def seed(force_password: bool = False) -> int:
    init_db()
    conn = connect()
    try:
        org = conn.execute("SELECT id FROM organizations WHERE slug=?", (ORG_SLUG,)).fetchone()
        if not org:
            cur = conn.execute(
                "INSERT INTO organizations (name, slug) VALUES (?,?)", (ORG_NAME, ORG_SLUG)
            )
            org_id = cur.lastrowid
            print(f"created org id={org_id} slug={ORG_SLUG}")
        else:
            org_id = org["id"]
            print(f"org exists id={org_id}")

        user = conn.execute(
            "SELECT id FROM users WHERE org_id=? AND lower(email)=?",
            (org_id, DEFAULT_ADMIN_EMAIL.lower()),
        ).fetchone()

        if user and not force_password:
            print(f"admin already exists id={user['id']} email={DEFAULT_ADMIN_EMAIL}")
            return 0

        temp = generate_password(14)
        ph = hash_password(temp)
        if user:
            conn.execute(
                "UPDATE users SET password_hash=?, must_change_password=1, role='admin', name=?, updated_at=datetime('now') WHERE id=?",
                (ph, DEFAULT_ADMIN_NAME, user["id"]),
            )
            uid = user["id"]
            print(f"reset admin password id={uid}")
        else:
            cur = conn.execute(
                """INSERT INTO users (org_id, email, name, role, password_hash, must_change_password)
                   VALUES (?,?,?,?,?,1)""",
                (org_id, DEFAULT_ADMIN_EMAIL.lower(), DEFAULT_ADMIN_NAME, "admin", ph),
            )
            uid = cur.lastrowid
            print(f"created admin id={uid} email={DEFAULT_ADMIN_EMAIL}")

        conn.execute(
            "INSERT INTO audit_log (org_id, actor_id, action, target_type, target_id, detail) VALUES (?,?,?,?,?,?)",
            (org_id, uid, "seed_admin", "user", uid, DEFAULT_ADMIN_EMAIL),
        )
        conn.commit()

        PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        PASSWORD_FILE.write_text(
            f"email={DEFAULT_ADMIN_EMAIL}\npassword={temp}\nrole=admin\n"
            f"login=http://178.212.15.231/lk/login.html\n"
            f"note=Смените пароль после первого входа. Файл можно удалить.\n",
            encoding="utf-8",
        )
        try:
            os.chmod(PASSWORD_FILE, 0o600)
        except OSError:
            pass
        print(f"initial password written to {PASSWORD_FILE} (chmod 600)")
        # also print once to stdout for operator (parent agent report)
        print(f"INITIAL_ADMIN_PASSWORD={temp}")
        return 0
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--force-password", action="store_true", help="Reset Maxim password even if user exists")
    p.add_argument("--db", default=None, help="Override DUSHEVYE_DB path")
    args = p.parse_args()
    if args.db:
        os.environ["DUSHEVYE_DB"] = args.db
    raise SystemExit(seed(force_password=args.force_password))


if __name__ == "__main__":
    main()
