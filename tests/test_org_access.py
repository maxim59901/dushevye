"""Demo mode, suspend, and server-side glass estimate.

    .venv/bin/python tests/test_org_access.py
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_IMPORT_DIR = Path(tempfile.mkdtemp(prefix="dushevye-access-import-"))
os.environ["DUSHEVYE_DB"] = str(_IMPORT_DIR / "app.db")
os.environ["DUSHEVYE_CATALOG_DB"] = str(_IMPORT_DIR / "catalog.db")
os.environ["DUSHEVYE_SESSION_SECRET"] = "test-only-secret"
os.environ.pop("DUSHEVYE_SERVE_PUBLIC", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.org_access import main as org_main  # noqa: E402
from app.quotes import GLASS_RATES, area_sqm  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSWORD = "test-pass-123"
PASSWORD_HASH = hash_password(PASSWORD)

CATALOG_SCHEMA = """
CREATE TABLE part_groups (
  id TEXT PRIMARY KEY,
  title_ru TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE parts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id TEXT NOT NULL REFERENCES part_groups(id),
  source TEXT NOT NULL,
  sku TEXT NOT NULL,
  name TEXT NOT NULL,
  price REAL,
  currency TEXT NOT NULL DEFAULT 'RUB',
  url TEXT,
  in_stock INTEGER,
  glass_mm TEXT,
  image_url TEXT,
  material TEXT,
  finish TEXT,
  params_json TEXT,
  raw_json TEXT
);
"""


def build_catalog(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(CATALOG_SCHEMA)
    conn.execute("INSERT INTO part_groups (id, title_ru, sort) VALUES ('hinges', 'Петли', 1)")
    conn.execute(
        """INSERT INTO parts (group_id, source, sku, name, price, currency)
           VALUES ('hinges', 'titan', 'H-810', 'Петля 8-10', 100, 'RUB')"""
    )
    conn.commit()
    conn.close()


def file_sig(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OrgAccessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="dushevye-access-"))
        self.db_path = self.dir / "app.db"
        self.catalog_path = self.dir / "catalog.db"
        os.environ["DUSHEVYE_DB"] = str(self.db_path)
        os.environ["DUSHEVYE_CATALOG_DB"] = str(self.catalog_path)
        import app.main as main

        main.CATALOG_DB = self.catalog_path
        build_catalog(self.catalog_path)
        self.catalog_before = file_sig(self.catalog_path)
        init_db()
        init_db()
        self.ids = self._seed()
        self.admin = TestClient(app)
        self.owner = TestClient(app)
        self.other = TestClient(app)
        self.admin.__enter__()
        self.owner.__enter__()
        self.other.__enter__()

    def tearDown(self) -> None:
        self.admin.__exit__(None, None, None)
        self.owner.__exit__(None, None, None)
        self.other.__exit__(None, None, None)
        self.assertEqual(file_sig(self.catalog_path), self.catalog_before)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _seed(self) -> dict[str, int]:
        conn = connect()
        try:
            org = conn.execute(
                "INSERT INTO organizations (name, slug) VALUES ('Душевые', 'default')"
            )
            org_id = int(org.lastrowid)
            other = conn.execute(
                "INSERT INTO organizations (name, slug) VALUES ('Чужие', 'other')"
            )
            other_id = int(other.lastrowid)
            ids = {"org": org_id, "other": other_id}
            for email, role, name, target in (
                ("admin@example.com", "admin", "Максим", org_id),
                ("owner@example.com", "owner", "Александр", org_id),
                ("manager@example.com", "manager", "Менеджер", org_id),
                ("outsider@example.com", "admin", "Чужой админ", other_id),
                ("other-owner@example.com", "owner", "Чужой хозяин", other_id),
            ):
                cur = conn.execute(
                    """INSERT INTO users (org_id, email, name, role, password_hash)
                       VALUES (?,?,?,?,?)""",
                    (target, email, name, role, PASSWORD_HASH),
                )
                ids[email] = int(cur.lastrowid)
            conn.commit()
            self.owner_hash = conn.execute(
                "SELECT password_hash FROM users WHERE email='owner@example.com'"
            ).fetchone()["password_hash"]
            access_rows = conn.execute("SELECT COUNT(*) AS c FROM org_access").fetchone()["c"]
            self.assertEqual(access_rows, 0)
            return ids
        finally:
            conn.close()

    def _login(self, client: TestClient, email: str) -> str:
        token = client.get("/api/auth/csrf").json()["csrfToken"]
        res = client.post(
            "/api/auth/login",
            json={"email": email, "password": PASSWORD},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["user"]["org_access"]["access_mode"], "full")
        self.assertFalse(res.json()["user"]["org_access"]["suspended"])
        return token

    def _password_hash(self) -> str:
        conn = connect()
        try:
            return conn.execute(
                "SELECT password_hash FROM users WHERE email='owner@example.com'"
            ).fetchone()["password_hash"]
        finally:
            conn.close()

    def _role(self, email: str) -> str:
        conn = connect()
        try:
            return conn.execute(
                "SELECT role FROM users WHERE email=?", (email,)
            ).fetchone()["role"]
        finally:
            conn.close()

    def test_page_has_no_glass_rate_table(self) -> None:
        text = (ROOT / "public" / "cfg.html").read_text(encoding="utf-8")
        for needle in ("GLASS_RATES", "areaSqm", "perSqm", "kindMult", "typeFactor", "0.55"):
            self.assertNotIn(needle, text)

    def test_estimate_hides_rate_table(self) -> None:
        token = self._login(self.owner, "owner@example.com")
        res = self.owner.post(
            "/api/config/estimate",
            json={
                "template_id": "corner_two",
                "width_mm": 900,
                "depth_mm": 900,
                "height_mm": 2000,
                "glass_thick_mm": 8,
                "glass_kind": "clear",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["ok"])
        area = area_sqm("corner", 900, 2000, 900)
        unit = (
            GLASS_RATES["per_sqm"][8]
            * GLASS_RATES["kind_mult"]["clear"]
            * GLASS_RATES["type_factor"]["corner"]
        )
        self.assertEqual(body["glass_area_m2"], 3.6)
        self.assertEqual(body["glass_unit_price"], unit)
        self.assertAlmostEqual(body["glass_sum"], area * unit, places=2)
        self.assertEqual(body["glass_sum"], 16934.4)
        raw = res.text
        for secret_key in ("per_sqm", "kind_mult", "type_factor", "GLASS_RATES", "perSqm"):
            self.assertNotIn(secret_key, raw)

    def test_admin_keeps_access_when_owner_is_limited(self) -> None:
        admin_token = self._login(self.admin, "admin@example.com")
        owner_token = self._login(self.owner, "owner@example.com")
        other_token = self._login(self.other, "other-owner@example.com")
        switched = self.admin.patch(
            "/api/org/access",
            json={"access_mode": "demo", "suspended": True, "org_id": self.ids["other"]},
            headers={"X-CSRF-Token": admin_token},
        )
        self.assertEqual(switched.status_code, 200, switched.text)
        self.assertEqual(switched.json()["org_id"], self.ids["org"])
        self.assertEqual(switched.json()["access_mode"], "demo")
        self.assertTrue(switched.json()["suspended"])

        users = self.admin.get("/api/users")
        self.assertEqual(users.status_code, 200, users.text)
        catalog = self.admin.get("/api/catalog?limit=1000")
        self.assertEqual(catalog.status_code, 200, catalog.text)
        self.assertTrue(catalog.json()["show_price"])
        self.assertEqual(catalog.json()["items"][0]["price"], 100)

        self.assertEqual(self.owner.get("/api/users").status_code, 403)
        invite = self.owner.post(
            "/api/users",
            json={"email": "new@example.com", "role": "admin", "name": "Новый"},
            headers={"X-CSRF-Token": owner_token},
        )
        self.assertEqual(invite.status_code, 403, invite.text)
        escalate = self.owner.patch(
            f"/api/users/{self.ids['owner@example.com']}/role",
            json={"role": "admin"},
            headers={"X-CSRF-Token": owner_token},
        )
        self.assertEqual(escalate.status_code, 403, escalate.text)
        self.assertEqual(self._role("owner@example.com"), "owner")
        self.assertEqual(self._password_hash(), self.owner_hash)

        quote = self.owner.post(
            "/api/quotes",
            json={
                "template_id": "corner_two",
                "width_mm": 900,
                "depth_mm": 900,
                "height_mm": 2000,
                "glass_thick_mm": 8,
                "glass_kind": "clear",
                "items": [{"id": 1, "qty": 2}],
            },
            headers={"X-CSRF-Token": owner_token},
        )
        # Suspended org rejects the quote before demo export rules.
        self.assertEqual(quote.status_code, 403, quote.text)
        self.assertIn("приостановлен", quote.json()["detail"])

        # Lift only the suspension; demo restrictions stay for the owner.
        resumed = self.admin.patch(
            "/api/org/access",
            json={"suspended": False},
            headers={"X-CSRF-Token": admin_token},
        )
        self.assertEqual(resumed.status_code, 200, resumed.text)
        quote = self.owner.post(
            "/api/quotes",
            json={
                "template_id": "corner_two",
                "width_mm": 900,
                "depth_mm": 900,
                "height_mm": 2000,
                "glass_thick_mm": 8,
                "glass_kind": "clear",
                "items": [{"id": 1, "qty": 2}],
            },
            headers={"X-CSRF-Token": owner_token},
        )
        self.assertEqual(quote.status_code, 201, quote.text)
        quote_id = quote.json()["quote"]["id"]
        for path in (
            f"/api/quotes/{quote_id}/pdf",
            f"/api/quotes/{quote_id}/pdf?download=1",
            "/api/catalog?limit=1000",
            "/api/catalog?limit=40&offset=40",
            "/api/catalog?limit=40&format=csv",
            "/api/quotes?download=1",
            "/api/catalog/compat/rules",
        ):
            blocked = self.owner.get(path)
            self.assertEqual(blocked.status_code, 403, path + " " + blocked.text)
        page = self.owner.get("/api/catalog?limit=40")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertFalse(page.json()["show_price"])
        self.assertIsNone(page.json()["items"][0]["price"])
        one = self.owner.get("/api/catalog/1")
        self.assertEqual(one.status_code, 200, one.text)
        self.assertIsNone(one.json()["item"]["price"])

        admin_pdf = self.admin.get(f"/api/quotes/{quote_id}/pdf?download=1")
        self.assertNotEqual(admin_pdf.status_code, 403, admin_pdf.text)
        self.assertNotIn("демонстрационном", admin_pdf.text)

        outsider_users = self.other.get("/api/users")
        self.assertEqual(outsider_users.status_code, 200, outsider_users.text)
        outsider_catalog = self.other.get("/api/catalog?limit=1000")
        self.assertEqual(outsider_catalog.status_code, 200, outsider_catalog.text)
        self.assertTrue(outsider_catalog.json()["show_price"])
        denied = self.other.patch(
            "/api/org/access",
            json={"access_mode": "full", "suspended": False},
            headers={"X-CSRF-Token": other_token},
        )
        self.assertEqual(denied.status_code, 403, denied.text)

        audit = self.admin.get("/api/audit?limit=50")
        self.assertEqual(audit.status_code, 200, audit.text)
        modes = [row for row in audit.json()["items"] if row["action"] == "org_access_mode"]
        suspends = [row for row in audit.json()["items"] if row["action"] == "org_suspend"]
        self.assertTrue(modes)
        self.assertTrue(suspends)
        self.assertEqual(modes[0]["actor_id"], self.ids["admin@example.com"])
        self.assertTrue(modes[0]["created_at"])
        other_audit = self.other.get("/api/audit?limit=50")
        self.assertEqual(other_audit.status_code, 200, other_audit.text)
        self.assertFalse(
            any(row["action"] in ("org_access_mode", "org_suspend") for row in other_audit.json()["items"])
        )
        self.assertEqual(self._password_hash(), self.owner_hash)

    def test_suspended_session_then_resume(self) -> None:
        owner_token = self._login(self.owner, "owner@example.com")
        admin_token = self._login(self.admin, "admin@example.com")
        self.assertEqual(self.owner.get("/api/projects").status_code, 200)
        self.assertEqual(self._password_hash(), self.owner_hash)
        suspended = self.admin.patch(
            "/api/org/access",
            json={"suspended": True},
            headers={"X-CSRF-Token": admin_token},
        )
        self.assertEqual(suspended.status_code, 200, suspended.text)
        blocked = self.owner.get("/api/projects")
        self.assertEqual(blocked.status_code, 403, blocked.text)
        me = self.owner.get("/api/auth/me")
        self.assertEqual(me.status_code, 200, me.text)
        self.assertTrue(me.json()["user"]["org_access"]["suspended"])
        self.assertEqual(me.json()["user"]["role"], "owner")
        changed = self.owner.post(
            "/api/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "another-pass-123"},
            headers={"X-CSRF-Token": owner_token},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(self.owner.get("/api/health").status_code, 200)
        self.assertEqual(self.owner.get("/api/templates").status_code, 200)
        chat = self.owner.post("/api/vitrine/chat", json={"message": "что такое КП"})
        self.assertEqual(chat.status_code, 200, chat.text)
        self.assertEqual(self.admin.get("/api/quotes").status_code, 200)
        self.admin.patch(
            "/api/org/access",
            json={"suspended": False},
            headers={"X-CSRF-Token": admin_token},
        )
        self.assertEqual(self.owner.get("/api/projects").status_code, 200)
        conn = connect()
        try:
            rows = conn.execute(
                """SELECT actor_id, action, created_at FROM audit_log
                   WHERE org_id=? AND action='org_suspend' ORDER BY id""",
                (self.ids["org"],),
            ).fetchall()
        finally:
            conn.close()
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0]["actor_id"], self.ids["admin@example.com"])
        self.assertTrue(rows[0]["created_at"])
        self.assertTrue(rows[1]["created_at"])

    def test_cli_refuses_until_identity_matches(self) -> None:
        argv = [
            "--db",
            str(self.db_path),
            "enable-demo",
            "--org-id",
            str(self.ids["org"]),
            "--confirm-slug",
            "default",
            "--confirm-owner-email",
            "owner@example.com",
            "--actor-email",
            "admin@example.com",
            "--ack-admins",
            "1",
        ]
        self.assertEqual(org_main(argv), 0)
        conn = connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM org_access").fetchone()["c"], 0)
        finally:
            conn.close()
        self.assertEqual(org_main(argv + ["--apply"]), 0)
        conn = connect()
        try:
            row = conn.execute("SELECT access_mode, suspended FROM org_access").fetchone()
            self.assertEqual(row["access_mode"], "demo")
            self.assertEqual(row["suspended"], 0)
            other = conn.execute(
                "SELECT 1 FROM org_access WHERE org_id=?", (self.ids["other"],)
            ).fetchone()
            self.assertIsNone(other)
            role = conn.execute(
                "SELECT role, password_hash FROM users WHERE email='owner@example.com'"
            ).fetchone()
            self.assertEqual(role["role"], "owner")
            self.assertEqual(role["password_hash"], self.owner_hash)
        finally:
            conn.close()
        bad = [
            "--db",
            str(self.db_path),
            "enable-demo",
            "--org-id",
            str(self.ids["org"]),
            "--confirm-slug",
            "other",
            "--confirm-owner-email",
            "owner@example.com",
            "--actor-email",
            "admin@example.com",
            "--ack-admins",
            "1",
            "--apply",
        ]
        with self.assertRaises(SystemExit):
            org_main(bad)
        bad_ack = bad.copy()
        bad_ack[bad_ack.index("--ack-admins") + 1] = "9"
        bad_ack[bad_ack.index("--confirm-slug") + 1] = "default"
        with self.assertRaises(SystemExit):
            org_main(bad_ack)


if __name__ == "__main__":
    unittest.main()
