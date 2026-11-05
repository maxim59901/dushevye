"""Warehouse balances: receipt, reserve, deficit sort, roles, catalog stays read-only.

Run from the repo root:

    .venv/bin/pip install -r app/requirements.txt httpx
    .venv/bin/python tests/test_stock.py
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

_IMPORT_DIR = Path(tempfile.mkdtemp(prefix="dushevye-stock-import-"))
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
    conn.executemany(
        "INSERT INTO part_groups (id, title_ru, sort) VALUES (?,?,?)",
        [
            ("hinges", "Петли", 1),
            ("handles", "Ручки", 2),
            ("seals", "Уплотнители", 3),
        ],
    )
    conn.executemany(
        """INSERT INTO parts (group_id, source, sku, name, price, currency)
           VALUES (?,?,?,?,?, 'RUB')""",
        [
            ("hinges", "titan", "H-1", "Петля хром", 100),
            ("handles", "pauli", "R-2", "Ручка чёрная", 200),
            ("seals", "titan", "U-3", "Уплотнитель", 50),
            ("hinges", "arabella", "H-4", "Петля маленькая", 80),
        ],
    )
    conn.commit()
    conn.close()


def catalog_sig(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StockApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="dushevye-stock-"))
        os.environ["DUSHEVYE_DB"] = str(self.dir / "app.db")
        os.environ["DUSHEVYE_CATALOG_DB"] = str(self.dir / "catalog.db")
        build_catalog(self.dir / "catalog.db")
        self.catalog_before = catalog_sig(self.dir / "catalog.db")
        init_db()
        self.ids = self._seed()
        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()
        self.token = ""

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)
        self.assertEqual(catalog_sig(self.dir / "catalog.db"), self.catalog_before)
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
                ("admin@example.com", "admin", "Админ"),
                ("owner@example.com", "owner", "Хозяин"),
                ("supply@example.com", "supply", "Снабжение"),
                ("manager@example.com", "manager", "Менеджер"),
                ("other-manager@example.com", "manager", "Чужой менеджер"),
                ("accountant@example.com", "accountant", "Бухгалтер"),
                ("installer@example.com", "installer", "Монтажник"),
                ("measurer@example.com", "measurer", "Замерщик"),
                ("production@example.com", "production", "Цех"),
                ("outsider@example.com", "admin", "Чужая организация"),
            ):
                target_org = other_id if email == "outsider@example.com" else org_id
                cur = conn.execute(
                    """INSERT INTO users (org_id, email, name, role, password_hash)
                       VALUES (?,?,?,?,?)""",
                    (target_org, email, name, role, PASSWORD_HASH),
                )
                users[email] = int(cur.lastrowid)
            mine = conn.execute(
                """INSERT INTO projects (
                     org_id, customer_name, responsible_user_id, created_by, status
                   ) VALUES (?,?,?,?, 'created')""",
                (org_id, "Иван", users["manager@example.com"], users["manager@example.com"]),
            )
            theirs = conn.execute(
                """INSERT INTO projects (
                     org_id, customer_name, responsible_user_id, created_by, status
                   ) VALUES (?,?,?,?, 'created')""",
                (
                    org_id,
                    "Пётр",
                    users["other-manager@example.com"],
                    users["other-manager@example.com"],
                ),
            )
            install = conn.execute(
                """INSERT INTO projects (
                     org_id, customer_name, responsible_user_id, created_by, status
                   ) VALUES (?,?,?,?, 'in_work')""",
                (org_id, "Монтаж", users["installer@example.com"], users["admin@example.com"]),
            )
            conn.commit()
            return {
                "org_id": org_id,
                "mine": int(mine.lastrowid),
                "theirs": int(theirs.lastrowid),
                "install": int(install.lastrowid),
                **users,
            }
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

    def move(self, kind: str, body: dict, *, token: str | None = None):
        headers = {}
        if token is not None:
            headers["X-CSRF-Token"] = token
        elif self.token:
            headers["X-CSRF-Token"] = self.token
        return self.client.post(f"/api/stock/{kind}", json=body, headers=headers)

    def test_receipt_reserve_sort_and_roles(self) -> None:
        self.login("admin@example.com")
        denied = self.client.post("/api/stock/in", json={"part_id": 1, "qty": 1})
        self.assertEqual(denied.status_code, 403)

        missing = self.move("in", {"part_id": 999, "qty": 1})
        self.assertEqual(missing.status_code, 404)

        first = self.move("in", {"part_id": 1, "qty": 10, "min_qty": 2, "note": "коробка"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["item"]["qty_on_hand"], 10)
        self.assertEqual(first.json()["item"]["qty_free"], 10)
        self.assertEqual(first.json()["item"]["sku"], "H-1")
        self.assertNotIn("price", first.json()["item"])

        again = self.move("in", {"part_id": 1, "qty": 1.25})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["item"]["qty_on_hand"], 11.25)

        self.move("in", {"part_id": 2, "qty": 5, "min_qty": 3})
        self.move("in", {"part_id": 3, "qty": 8, "min_qty": 1})
        self.move("in", {"part_id": 4, "qty": 4, "min_qty": 0})

        reserved = self.move(
            "reserve",
            {"part_id": 2, "qty": 4, "project_id": self.ids["mine"]},
        )
        self.assertEqual(reserved.status_code, 200, reserved.text)
        item = reserved.json()["item"]
        self.assertEqual(item["qty_reserved"], 4)
        self.assertEqual(item["qty_free"], 1)
        self.assertEqual(item["deficit"], 2)
        self.assertTrue(item["below_min"])
        self.assertEqual(item["reservations"][0]["project_id"], self.ids["mine"])
        self.assertEqual(item["reservations"][0]["customer_name"], "Иван")

        self.move("reserve", {"part_id": 3, "qty": 1, "project_id": self.ids["install"]})

        too_much = self.move("out", {"part_id": 2, "qty": 2})
        self.assertEqual(too_much.status_code, 400, too_much.text)
        over_reserve = self.move("reserve", {"part_id": 2, "qty": 2, "project_id": self.ids["mine"]})
        self.assertEqual(over_reserve.status_code, 400, over_reserve.text)
        wrong_release = self.move("release", {"part_id": 2, "qty": 1})
        self.assertEqual(wrong_release.status_code, 400, wrong_release.text)

        listed = self.client.get("/api/stock")
        self.assertEqual(listed.status_code, 200, listed.text)
        payload = listed.json()
        self.assertEqual(payload["scope"], "full")
        self.assertTrue(payload["can_in"])
        self.assertEqual(payload["below_min"], 1)
        self.assertEqual([row["part_id"] for row in payload["items"]], [2, 3, 4, 1])
        self.assertEqual(payload["items"][0]["deficit"], 2)
        self.assertEqual(payload["items"][1]["qty_reserved"], 1)
        self.assertEqual(payload["items"][2]["qty_on_hand"], 4)
        self.assertGreater(payload["items"][3]["qty_on_hand"], payload["items"][2]["qty_on_hand"])

        hinges = self.client.get("/api/stock", params={"group": "hinges"})
        self.assertEqual([row["part_id"] for row in hinges.json()["items"]], [4, 1])
        pauli = self.client.get("/api/stock", params={"manufacturer": "pauli"})
        self.assertEqual([row["part_id"] for row in pauli.json()["items"]], [2])
        found = self.client.get("/api/stock", params={"q": "РУЧ"})
        self.assertEqual([row["part_id"] for row in found.json()["items"]], [2])

        spent = self.move("out", {"part_id": 2, "qty": 1, "project_id": self.ids["mine"]})
        self.assertEqual(spent.status_code, 200, spent.text)
        self.assertEqual(spent.json()["item"]["qty_on_hand"], 4)
        self.assertEqual(spent.json()["item"]["qty_reserved"], 4)
        self.assertEqual(spent.json()["item"]["qty_free"], 0)

        released = self.move(
            "release",
            {"part_id": 2, "qty": 1, "project_id": self.ids["mine"]},
        )
        self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(released.json()["item"]["qty_reserved"], 3)
        self.assertEqual(released.json()["item"]["qty_free"], 1)

        bare = self.move("reserve", {"part_id": 4, "qty": 1})
        self.assertEqual(bare.status_code, 200, bare.text)
        bare_off = self.move("release", {"part_id": 4, "qty": 1})
        self.assertEqual(bare_off.status_code, 200, bare_off.text)

        audit = self.client.get("/api/audit?limit=20").json()["items"]
        actions = {row["action"] for row in audit}
        self.assertTrue({"stock_in", "stock_out", "stock_reserve", "stock_release"} <= actions)
        reserve_row = next(row for row in audit if row["action"] == "stock_reserve" and "R-2" in (row["detail"] or ""))
        self.assertIn(f"project={self.ids['mine']}", reserve_row["detail"])
        self.assertEqual(reserve_row["target_type"], "stock")
        self.assertEqual(reserve_row["target_id"], 2)

        self.client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": self.token})

        self.login("manager@example.com")
        blocked_in = self.move("in", {"part_id": 1, "qty": 1})
        self.assertEqual(blocked_in.status_code, 403)
        no_project = self.move("reserve", {"part_id": 1, "qty": 1})
        self.assertEqual(no_project.status_code, 400)
        foreign = self.move(
            "reserve",
            {"part_id": 1, "qty": 1, "project_id": self.ids["theirs"]},
        )
        self.assertEqual(foreign.status_code, 404)
        own = self.move(
            "reserve",
            {"part_id": 1, "qty": 2, "project_id": self.ids["mine"]},
        )
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.json()["item"]["qty_reserved"], 2)
        manager_view = self.client.get("/api/stock").json()
        self.assertEqual(manager_view["scope"], "manager")
        self.assertFalse(manager_view["can_in"])
        self.assertTrue(manager_view["can_reserve"])
        hinge = next(row for row in manager_view["items"] if row["part_id"] == 2)
        self.assertEqual(hinge["qty_reserved"], 3)
        self.assertEqual(hinge["reservations"][0]["customer_name"], "Иван")
        self.assertNotIn("Пётр", str(hinge["reservations"]))
        self.assertEqual([p["id"] for p in manager_view["projects"]], [self.ids["mine"]])
        self.client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": self.token})

        self.login("accountant@example.com")
        viewed = self.client.get("/api/stock")
        self.assertEqual(viewed.status_code, 200)
        self.assertEqual(viewed.json()["scope"], "view")
        self.assertFalse(viewed.json()["can_reserve"])
        self.assertGreaterEqual(viewed.json()["total"], 4)
        self.assertEqual(self.move("out", {"part_id": 1, "qty": 1}).status_code, 403)
        self.client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": self.token})

        self.login("installer@example.com")
        own_only = self.client.get("/api/stock")
        self.assertEqual(own_only.status_code, 200, own_only.text)
        body = own_only.json()
        self.assertEqual(body["scope"], "own_reserves")
        self.assertEqual(body["items"], [])
        self.assertEqual(len(body["reserves"]), 1)
        self.assertEqual(body["reserves"][0]["project_id"], self.ids["install"])
        self.assertEqual(body["reserves"][0]["qty_reserved"], 1)
        self.assertEqual(body["reserves"][0]["sku"], "U-3")
        self._assert_no_balances(body)
        self.assertEqual(self.move("reserve", {"part_id": 3, "qty": 1, "project_id": self.ids["install"]}).status_code, 403)
        self.client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": self.token})

        self.login("production@example.com")
        empty = self.client.get("/api/stock").json()
        self.assertEqual(empty["reserves"], [])
        self.assertEqual(empty["items"], [])
        self._assert_no_balances(empty)
        self.client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": self.token})

        self.login("measurer@example.com")
        self.assertEqual(self.client.get("/api/stock").json()["scope"], "own_reserves")
        self.client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": self.token})

        self.login("supply@example.com")
        supply = self.client.get("/api/stock").json()
        self.assertEqual(supply["scope"], "full")
        self.assertTrue(supply["can_out"])
        project_ids = {row["id"] for row in supply["projects"]}
        self.assertIn(self.ids["mine"], project_ids)
        self.assertIn(self.ids["theirs"], project_ids)
        self.client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": self.token})

        self.login("owner@example.com")
        self.assertEqual(self.client.get("/api/stock").json()["scope"], "full")
        self.client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": self.token})

        self.login("outsider@example.com")
        foreign_org = self.client.get("/api/stock").json()
        self.assertEqual(foreign_org["items"], [])
        self.assertEqual(foreign_org["total"], 0)

        anon = TestClient(app)
        anon.__enter__()
        try:
            self.assertEqual(anon.get("/api/stock").status_code, 401)
        finally:
            anon.__exit__(None, None, None)

    def _assert_no_balances(self, body: dict) -> None:
        def walk(node) -> None:
            if isinstance(node, dict):
                self.assertNotIn("qty_on_hand", node)
                self.assertNotIn("min_qty", node)
                self.assertNotIn("qty_free", node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(body)


if __name__ == "__main__":
    unittest.main()
