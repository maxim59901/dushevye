"""Catalog compatibility: thickness filter, denied hinge, rule CRUD by role.

Run from the repo root:

    .venv/bin/pip install -r app/requirements.txt httpx
    .venv/bin/python tests/test_catalog_compat.py
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

_IMPORT_DIR = Path(tempfile.mkdtemp(prefix="dushevye-compat-import-"))
os.environ["DUSHEVYE_DB"] = str(_IMPORT_DIR / "app.db")
os.environ["DUSHEVYE_CATALOG_DB"] = str(_IMPORT_DIR / "catalog.db")
os.environ["DUSHEVYE_SESSION_SECRET"] = "test-only-secret"
os.environ.pop("DUSHEVYE_SERVE_PUBLIC", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.catalog_compat import SEED_RULES, deny_reason, make_context, opening_kind  # noqa: E402
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
            ("connectors", "Коннекторы", 3),
            ("seals", "Уплотнители", 4),
            ("sliding", "Раздвижные системы", 5),
        ],
    )
    conn.executemany(
        """INSERT INTO parts (group_id, source, sku, name, price, currency, glass_mm)
           VALUES (?,?,?,?,?, 'RUB', ?)""",
        [
            ("hinges", "titan", "H-810", "Петля только 8/10", 100, "8-10"),
            ("hinges", "titan", "H-46", "Петля стекло-стекло 4-6 мм", 80, "4-6"),
            ("hinges", "titan", "H-10", "Петля только 10", 90, "10"),
            ("handles", "titan", "KN-1", "Кноб круглый", 40, None),
            ("connectors", "titan", "C-810", "Зажим стена 8-10мм", 50, "8-10"),
            ("seals", "titan", "S-8", "Уплотнитель 8 мм", 20, "8"),
            ("sliding", "titan", "SL-1", "Раздвижная система", 500, None),
        ],
    )
    conn.commit()
    conn.close()


def catalog_sig(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CatalogCompatApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="dushevye-compat-"))
        os.environ["DUSHEVYE_DB"] = str(self.dir / "app.db")
        os.environ["DUSHEVYE_CATALOG_DB"] = str(self.dir / "catalog.db")
        build_catalog(self.dir / "catalog.db")
        self.catalog_before = catalog_sig(self.dir / "catalog.db")
        init_db()
        self._seed_users()
        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()
        self.token = ""

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)
        self.assertEqual(catalog_sig(self.dir / "catalog.db"), self.catalog_before)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _seed_users(self) -> None:
        conn = connect()
        try:
            org = conn.execute(
                "INSERT INTO organizations (name, slug) VALUES ('Душевые', 'default')"
            )
            org_id = int(org.lastrowid)
            for email, name, role in (
                ("manager@example.com", "Менеджер", "manager"),
                ("admin@example.com", "Админ", "admin"),
                ("owner@example.com", "Хозяин", "owner"),
            ):
                conn.execute(
                    """INSERT INTO users (org_id, email, name, role, password_hash)
                       VALUES (?,?,?,?,?)""",
                    (org_id, email, name, role, PASSWORD_HASH),
                )
            conn.commit()
        finally:
            conn.close()

    def login(self, email: str) -> None:
        if self.token:
            self.client.post(
                "/api/auth/logout",
                json={},
                headers={"X-CSRF-Token": self.token},
            )
        token = self.client.get("/api/auth/csrf").json()["csrfToken"]
        res = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": PASSWORD},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.token = token

    def send(self, method: str, path: str, body: dict | None = None, *, csrf: bool = True):
        headers = {"X-CSRF-Token": self.token} if csrf and self.token else {}
        return self.client.request(method, path, json=body, headers=headers)

    def corner(self, **extra) -> dict:
        body = {
            "template_id": "corner_two",
            "width_mm": 900,
            "depth_mm": 900,
            "height_mm": 2000,
            "glass_thick_mm": 8,
            "glass_kind": "clear",
        }
        body.update(extra)
        return body

    def denied_ids(self, glass_mm: int, template_id: str = "corner_two") -> set[int]:
        res = self.client.get(
            "/api/catalog/compat",
            params={"glass_mm": glass_mm, "template_id": template_id},
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertEqual(data["glass_mm"], glass_mm)
        self.assertGreaterEqual(data["rule_count"], 15)
        self.assertLessEqual(data["rule_count"], 40)
        return {int(row["part_id"]) for row in data["parts"] if row["effect"] == "deny"}

    def test_seed_size_and_openings(self) -> None:
        self.assertGreaterEqual(len(SEED_RULES), 15)
        self.assertLessEqual(len(SEED_RULES), 40)
        self.assertEqual(opening_kind("niche_straight"), "door")
        self.assertEqual(opening_kind("corner_two"), "door")
        self.assertEqual(opening_kind("ushape"), "door")
        self.assertEqual(opening_kind(""), "any")
        init_db()
        self.login("admin@example.com")
        first = self.client.get("/api/catalog/compat/rules")
        second_count = len(first.json()["items"])
        init_db()
        again = self.client.get("/api/catalog/compat/rules")
        self.assertEqual(len(again.json()["items"]), second_count)
        self.assertTrue(all(row["origin"] == "seed" for row in again.json()["items"]))

    def test_thickness_filter_and_hinge_810(self) -> None:
        self.login("manager@example.com")
        anon = self.client.get("/api/catalog/compat", params={"glass_mm": 6})
        # session cookie is set, so this is the manager. A fresh client is checked below.
        at6 = self.denied_ids(6)
        self.assertIn(1, at6)
        self.assertNotIn(2, at6)
        self.assertIn(5, at6)
        reason6 = next(
            row["reason"]
            for row in self.client.get("/api/catalog/compat", params={"glass_mm": 6, "template_id": "corner_two"}).json()["parts"]
            if row["part_id"] == 1
        )
        self.assertIn("6", reason6)
        self.assertTrue("8" in reason6 or "10" in reason6)

        at8 = self.denied_ids(8)
        self.assertNotIn(1, at8)
        self.assertIn(2, at8)
        self.assertIn(3, at8)

        at10 = self.denied_ids(10)
        self.assertNotIn(1, at10)
        self.assertNotIn(3, at10)
        self.assertIn(2, at10)

        corner = self.client.get(
            "/api/catalog/compat", params={"glass_mm": 8, "template_id": "corner_two"}
        ).json()
        sliding = next(row for row in corner["groups"] if row["group_id"] == "sliding")
        self.assertEqual(sliding["effect"], "deny")
        self.assertIn(7, self.denied_ids(8, "corner_two"))
        niche = self.client.get(
            "/api/catalog/compat", params={"glass_mm": 8, "template_id": "niche_straight"}
        ).json()
        sliding_niche = next(row for row in niche["groups"] if row["group_id"] == "sliding")
        self.assertEqual(sliding_niche["effect"], "allow")
        self.assertNotIn(7, {int(row["part_id"]) for row in niche["parts"]})
        self.assertEqual(anon.status_code, 200, anon.text)

        fresh = TestClient(app)
        bare = fresh.get("/api/catalog/compat", params={"glass_mm": 6})
        self.assertEqual(bare.status_code, 401, bare.text)

        checked = self.send(
            "POST",
            "/api/config/validate",
            self.corner(glass_thick_mm=6, part_ids=[1]),
        )
        self.assertEqual(checked.status_code, 200, checked.text)
        data = checked.json()
        self.assertFalse(data["ok"])
        hinge = next(row for row in data["errors"] if row["code"] in ("hinge_8_10", "catalog_compat"))
        self.assertIn("6", hinge["message"])
        self.assertTrue("8" in hinge["message"] or "10" in hinge["message"])
        self.assertIn("8/10", hinge["message"])

    def test_fixed_panel_denies_hinge_without_new_template(self) -> None:
        self.login("admin@example.com")
        rules = self.client.get("/api/catalog/compat/rules").json()["items"]
        part = {"id": 1, "group_id": "hinges", "sku": "H-810", "name": "Петля только 8/10", "glass_mm": "8-10"}
        reason = deny_reason(part, rules, make_context(8, "", opening="fixed"))
        self.assertIsNotNone(reason)
        self.assertIn("глух", reason or "")
        door = deny_reason(part, rules, make_context(8, "corner_two"))
        self.assertIsNone(door)

    def test_rule_crud_by_role(self) -> None:
        self.login("manager@example.com")
        listed = self.client.get("/api/catalog/compat/rules")
        self.assertEqual(listed.status_code, 403, listed.text)
        created = self.send(
            "POST",
            "/api/catalog/compat/rules",
            {"effect": "deny", "group_id": "handles", "part_id": 4, "glass_mm": 8, "reason": "Нельзя"},
        )
        self.assertEqual(created.status_code, 403, created.text)
        removed = self.send("DELETE", "/api/catalog/compat/rules/1")
        self.assertEqual(removed.status_code, 403, removed.text)
        self.assertIn(1, self.denied_ids(6))

        self.login("admin@example.com")
        no_csrf = self.send(
            "POST",
            "/api/catalog/compat/rules",
            {
                "effect": "deny",
                "group_id": "handles",
                "part_id": 4,
                "glass_mm": 8,
                "reason": "Кноб не ставим на это стекло.",
            },
            csrf=False,
        )
        self.assertEqual(no_csrf.status_code, 403, no_csrf.text)

        made = self.send(
            "POST",
            "/api/catalog/compat/rules",
            {
                "effect": "deny",
                "group_id": "handles",
                "part_id": 4,
                "glass_mm": 8,
                "reason": "Кноб не ставим на это стекло.",
            },
        )
        self.assertEqual(made.status_code, 201, made.text)
        rule = made.json()["rule"]
        self.assertEqual(rule["origin"], "custom")
        self.assertEqual(rule["part_id"], 4)
        self.assertEqual(rule["group_name"], "Ручки")
        rows = self.client.get("/api/catalog/compat/rules").json()["items"]
        self.assertTrue(any(row["id"] == rule["id"] and row["origin"] == "custom" for row in rows))
        self.assertTrue(any(row["origin"] == "seed" for row in rows))
        self.assertIn(4, self.denied_ids(8))
        self.assertNotIn(4, self.denied_ids(10))

        blocked = self.send("POST", "/api/config/validate", self.corner(part_ids=[4]))
        self.assertEqual(blocked.status_code, 200, blocked.text)
        self.assertFalse(blocked.json()["ok"])
        clash = next(row for row in blocked.json()["errors"] if row["code"] == "catalog_compat")
        self.assertIn("Кноб", clash["message"])
        self.assertIn("Кноб не ставим", clash["message"])

        saved_blocked = self.send(
            "POST",
            "/api/quotes",
            self.corner(items=[{"id": 4, "qty": 1}]),
        )
        self.assertEqual(saved_blocked.status_code, 400, saved_blocked.text)
        self.assertEqual(self.client.get("/api/quotes").json()["items"], [])

        missing = self.send(
            "POST",
            "/api/catalog/compat/rules",
            {"effect": "deny", "part_id": 9999, "reason": "нет такой"},
        )
        self.assertEqual(missing.status_code, 400, missing.text)

        self.login("owner@example.com")
        owner_list = self.client.get("/api/catalog/compat/rules")
        self.assertEqual(owner_list.status_code, 200, owner_list.text)
        self.assertTrue(any(row["id"] == rule["id"] for row in owner_list.json()["items"]))

        self.login("manager@example.com")
        still = self.send("DELETE", f"/api/catalog/compat/rules/{rule['id']}")
        self.assertEqual(still.status_code, 403, still.text)

        self.login("admin@example.com")
        deleted = self.send("DELETE", f"/api/catalog/compat/rules/{rule['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        left = self.client.get("/api/catalog/compat/rules").json()["items"]
        self.assertFalse(any(row["id"] == rule["id"] for row in left))
        self.assertNotIn(4, self.denied_ids(8))
        clear = self.send("POST", "/api/config/validate", self.corner(part_ids=[4, 4]))
        self.assertEqual(clear.status_code, 200, clear.text)
        self.assertTrue(clear.json()["ok"], clear.text)

        bom = self.send("POST", "/api/quotes", self.corner(items=[{"id": 1, "qty": 2}]))
        self.assertEqual(bom.status_code, 201, bom.text)
        quote = bom.json()["quote"]
        self.assertEqual(quote["glass_sum"], 16934.4)
        hardware = [line for line in quote["lines"] if line["kind"] == "hardware"]
        self.assertEqual(len(hardware), 1)
        self.assertEqual(hardware[0]["sku"], "H-810")
        self.assertEqual(hardware[0]["qty"], 2)
        self.assertEqual(hardware[0]["unit_price"], 100)
        self.assertEqual(hardware[0]["line_sum"], 200)


if __name__ == "__main__":
    unittest.main()
