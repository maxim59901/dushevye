"""Entity versions: before/after snapshots and who can read them.

Run from the repo root:

    .venv/bin/pip install -r app/requirements.txt httpx
    .venv/bin/python tests/test_versions.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_IMPORT_DIR = Path(tempfile.mkdtemp(prefix="dushevye-versions-import-"))
os.environ["DUSHEVYE_DB"] = str(_IMPORT_DIR / "app.db")
os.environ["DUSHEVYE_CATALOG_DB"] = str(_IMPORT_DIR / "catalog.db")
os.environ["DUSHEVYE_SESSION_SECRET"] = "test-only-secret"
os.environ.pop("DUSHEVYE_SERVE_PUBLIC", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect, init_db  # noqa: E402
from app.main import app  # noqa: E402
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
    conn.execute(
        "INSERT INTO part_groups (id, title_ru, sort) VALUES ('hinges', 'Петли', 1)"
    )
    conn.execute(
        """INSERT INTO parts (group_id, source, sku, name, price, currency)
           VALUES ('hinges', 'titan', 'H-1', 'Петля хром', 100, 'RUB')"""
    )
    conn.commit()
    conn.close()


class VersionsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="dushevye-versions-"))
        os.environ["DUSHEVYE_DB"] = str(self.dir / "app.db")
        os.environ["DUSHEVYE_CATALOG_DB"] = str(self.dir / "catalog.db")
        build_catalog(self.dir / "catalog.db")
        init_db()
        init_db()
        self.ids = self._seed()
        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()
        self.token = ""

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)
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
            users = {}
            for email, role, name in (
                ("owner@example.com", "owner", "Хозяин"),
                ("manager@example.com", "manager", "Менеджер"),
                ("other-manager@example.com", "manager", "Другой"),
                ("accountant@example.com", "accountant", "Бухгалтер"),
                ("outsider@example.com", "admin", "Чужая организация"),
            ):
                target_org = other_id if email.startswith("outsider") else org_id
                cur = conn.execute(
                    """INSERT INTO users (org_id, email, name, role, password_hash)
                       VALUES (?,?,?,?,?)""",
                    (target_org, email, name, role, PASSWORD_HASH),
                )
                users[email] = int(cur.lastrowid)
            conn.commit()
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_versions'"
            ).fetchone()
            self.assertIsNotNone(table)
            return {"org_id": org_id, **users}
        finally:
            conn.close()

    def login(self, email: str) -> None:
        token = self.client.get("/api/auth/csrf").json()["csrfToken"]
        res = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": PASSWORD},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.token = token

    def logout(self) -> None:
        self.client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": self.token})

    def send(self, method: str, path: str, body: dict | None = None):
        headers = {"X-CSRF-Token": self.token} if self.token else {}
        return self.client.request(method, path, json=body, headers=headers)

    def versions(self, **params):
        return self.client.get("/api/versions", params=params)

    def test_status_diff_and_visibility(self) -> None:
        anon = self.client.get("/api/versions")
        self.assertEqual(anon.status_code, 401)

        self.login("manager@example.com")
        created = self.send("POST", "/api/projects", {"customer_name": "Анна", "customer_phone": "1"})
        self.assertEqual(created.status_code, 201, created.text)
        project_id = created.json()["project"]["id"]

        same = self.send("PATCH", f"/api/projects/{project_id}/status", {"status": "created"})
        self.assertEqual(same.status_code, 200, same.text)
        moved = self.send("PATCH", f"/api/projects/{project_id}/status", {"status": "estimate"})
        self.assertEqual(moved.status_code, 200, moved.text)
        again = self.send("PATCH", f"/api/projects/{project_id}/status", {"status": "estimate"})
        self.assertEqual(again.status_code, 200, again.text)

        own = self.versions(entity_type="project", entity_id=project_id)
        self.assertEqual(own.status_code, 200, own.text)
        payload = own.json()
        self.assertFalse(payload["can_see_org"])
        status_rows = [row for row in payload["items"] if row["action"] == "project_status_change"]
        self.assertEqual(len(status_rows), 1)
        self.assertEqual(status_rows[0]["before"]["status"], "created")
        self.assertEqual(status_rows[0]["after"]["status"], "estimate")
        self.assertIn("Создан", status_rows[0]["summary"])
        self.assertIn("Просчёт", status_rows[0]["summary"])
        self.assertEqual(status_rows[0]["actor_id"], self.ids["manager@example.com"])
        creates = [row for row in payload["items"] if row["action"] == "project_create"]
        self.assertEqual(len(creates), 1)
        self.assertIsNone(creates[0]["before"])
        self.assertEqual(creates[0]["after"]["status"], "created")
        self.assertEqual(creates[0]["after"]["customer_name"], "Анна")

        bad_type = self.versions(entity_type="login")
        self.assertEqual(bad_type.status_code, 400)
        bad_id = self.versions(entity_id=project_id)
        self.assertEqual(bad_id.status_code, 400)
        self.logout()

        self.login("owner@example.com")
        further = self.send("PATCH", f"/api/projects/{project_id}/status", {"status": "in_work"})
        self.assertEqual(further.status_code, 200, further.text)
        role = self.send(
            "PATCH",
            f"/api/users/{self.ids['accountant@example.com']}/role",
            {"role": "measurer"},
        )
        self.assertEqual(role.status_code, 200, role.text)
        owner_rows = self.versions(entity_type="project", entity_id=project_id).json()["items"]
        self.assertTrue(self.versions().json()["can_see_org"])
        actors = {row["actor_id"] for row in owner_rows}
        self.assertEqual(
            actors,
            {self.ids["manager@example.com"], self.ids["owner@example.com"]},
        )
        owner_status = next(row for row in owner_rows if row["after"]["status"] == "in_work")
        self.assertEqual(owner_status["before"]["status"], "estimate")
        self.assertEqual(owner_status["actor_id"], self.ids["owner@example.com"])
        role_rows = self.versions(entity_type="user", entity_id=self.ids["accountant@example.com"]).json()["items"]
        self.assertEqual(role_rows[0]["before"]["role"], "accountant")
        self.assertEqual(role_rows[0]["after"]["role"], "measurer")
        self.assertNotIn("password", str(role_rows[0]["before"]) + str(role_rows[0]["after"]))

        audit = self.client.get("/api/audit?limit=20")
        self.assertEqual(audit.status_code, 200, audit.text)
        audit_items = audit.json()["items"]
        self.assertTrue(any(row["action"] == "project_status_change" for row in audit_items))
        self.assertTrue(any(row["action"] == "change_role" and "->" in (row["detail"] or "") for row in audit_items))
        self.assertNotIn("before", audit_items[0])
        self.logout()

        self.login("manager@example.com")
        hidden = self.versions(entity_type="project", entity_id=project_id).json()["items"]
        self.assertTrue(hidden)
        self.assertTrue(all(row["actor_id"] == self.ids["manager@example.com"] for row in hidden))
        self.assertFalse(any(row["after"]["status"] == "in_work" for row in hidden))
        recent = self.versions().json()["items"]
        self.assertTrue(all(row["actor_id"] == self.ids["manager@example.com"] for row in recent))
        foreign_role = self.versions(
            entity_type="user", entity_id=self.ids["accountant@example.com"]
        ).json()["items"]
        self.assertEqual(foreign_role, [])
        self.logout()

        self.login("other-manager@example.com")
        other = self.versions(entity_type="project", entity_id=project_id).json()["items"]
        self.assertEqual(other, [])
        self.logout()

        self.login("outsider@example.com")
        outside = self.versions().json()["items"]
        self.assertEqual(outside, [])
        self.logout()

    def test_quote_stock_and_quote_link(self) -> None:
        self.login("manager@example.com")
        project = self.send("POST", "/api/projects", {"customer_name": "Борис"})
        project_id = project.json()["project"]["id"]
        quote = self.send(
            "POST",
            "/api/quotes",
            {
                "fence_type": "straight",
                "width_mm": 900,
                "height_mm": 2000,
                "glass_thick_mm": 8,
                "glass_kind": "clear",
                "items": [{"id": 1, "qty": 2}],
            },
        )
        self.assertEqual(quote.status_code, 201, quote.text)
        quote_id = quote.json()["quote"]["id"]
        linked = self.send("PATCH", f"/api/projects/{project_id}/quote", {"quote_id": quote_id})
        self.assertEqual(linked.status_code, 200, linked.text)

        quote_rows = self.versions(entity_type="quote", entity_id=quote_id).json()["items"]
        self.assertEqual(len(quote_rows), 1)
        self.assertEqual(quote_rows[0]["action"], "quote_create")
        self.assertIsNone(quote_rows[0]["before"])
        self.assertEqual(quote_rows[0]["after"]["fence_type"], "straight")
        self.assertEqual(quote_rows[0]["after"]["width_mm"], 900)
        self.assertEqual(quote_rows[0]["after"]["hardware_count"], 1)
        self.assertNotIn("price", quote_rows[0]["after"])
        self.assertNotIn("total", quote_rows[0]["after"])
        self.assertNotIn("hardware_sum", quote_rows[0]["after"])

        link_rows = [
            row
            for row in self.versions(entity_type="project", entity_id=project_id).json()["items"]
            if row["action"] == "project_quote_link"
        ]
        self.assertEqual(len(link_rows), 1)
        self.assertIsNone(link_rows[0]["before"]["quote_id"])
        self.assertEqual(link_rows[0]["after"]["quote_id"], quote_id)
        self.logout()

        self.login("owner@example.com")
        receipt = self.send("POST", "/api/stock/in", {"part_id": 1, "qty": 10, "min_qty": 2})
        self.assertEqual(receipt.status_code, 200, receipt.text)
        self.logout()

        self.login("manager@example.com")
        denied = self.versions(entity_type="stock", entity_id=1).json()["items"]
        self.assertEqual(denied, [])
        reserved = self.send(
            "POST",
            "/api/stock/reserve",
            {"part_id": 1, "qty": 2, "project_id": project_id},
        )
        self.assertEqual(reserved.status_code, 200, reserved.text)
        stock_rows = self.versions(entity_type="stock", entity_id=1).json()["items"]
        self.assertEqual(len(stock_rows), 1)
        self.assertEqual(stock_rows[0]["action"], "stock_reserve")
        self.assertEqual(stock_rows[0]["before"]["qty_on_hand"], 10)
        self.assertEqual(stock_rows[0]["before"]["qty_reserved"], 0)
        self.assertEqual(stock_rows[0]["before"]["min_qty"], 2)
        self.assertEqual(stock_rows[0]["after"]["qty_on_hand"], 10)
        self.assertEqual(stock_rows[0]["after"]["qty_reserved"], 2)
        self.assertEqual(stock_rows[0]["after"]["min_qty"], 2)
        failed = self.send("POST", "/api/stock/out", {"part_id": 1, "qty": 100})
        self.assertEqual(failed.status_code, 403, failed.text)
        self.assertEqual(len(self.versions(entity_type="stock", entity_id=1).json()["items"]), 1)
        seen = self.versions().json()["items"]
        self.assertTrue(all(row["actor_id"] == self.ids["manager@example.com"] for row in seen))
        self.assertTrue(any(row["entity_type"] == "quote" for row in seen))
        self.logout()

        self.login("owner@example.com")
        owner_stock = self.versions(entity_type="stock", entity_id=1).json()["items"]
        actions = [row["action"] for row in owner_stock]
        self.assertEqual(actions, ["stock_reserve", "stock_in"])
        first = owner_stock[1]
        self.assertIsNone(first["before"])
        self.assertEqual(first["after"]["qty_on_hand"], 10)
        self.assertEqual(first["after"]["min_qty"], 2)
        self.assertEqual(first["after"]["qty_reserved"], 0)
        owner_all = self.versions().json()["items"]
        actors = {row["actor_id"] for row in owner_all}
        self.assertIn(self.ids["manager@example.com"], actors)
        self.assertIn(self.ids["owner@example.com"], actors)
        quote_seen = self.versions(entity_type="quote", entity_id=quote_id).json()["items"]
        self.assertEqual(quote_seen[0]["actor_id"], self.ids["manager@example.com"])
        audit_actions = {row["action"] for row in self.client.get("/api/audit?limit=50").json()["items"]}
        self.assertIn("quote_create", audit_actions)
        self.assertIn("stock_in", audit_actions)
        self.assertIn("stock_reserve", audit_actions)


if __name__ == "__main__":
    unittest.main()
