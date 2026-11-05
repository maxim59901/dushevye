"""Template configurator: valid draft, size error, incompatible hardware.

Run from the repo root:

    .venv/bin/pip install -r app/requirements.txt httpx
    .venv/bin/python tests/test_config_rules.py
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

_IMPORT_DIR = Path(tempfile.mkdtemp(prefix="dushevye-rules-import-"))
os.environ["DUSHEVYE_DB"] = str(_IMPORT_DIR / "app.db")
os.environ["DUSHEVYE_CATALOG_DB"] = str(_IMPORT_DIR / "catalog.db")
os.environ["DUSHEVYE_SESSION_SECRET"] = "test-only-secret"
os.environ.pop("DUSHEVYE_SERVE_PUBLIC", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.config_rules import rule_ids  # noqa: E402
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
            ("sliding", "Раздвижные системы", 4),
        ],
    )
    conn.executemany(
        """INSERT INTO parts (group_id, source, sku, name, price, currency)
           VALUES (?,?,?,?,?, 'RUB')""",
        [
            ("hinges", "titan", "H-810", "Петля стекло-стена 8-10мм", 100),
            ("hinges", "titan", "H-46", "Петля стекло-стекло 4-6мм", 80),
            ("sliding", "titan", "SL-1", "Раздвижная система", 500),
        ],
    )
    conn.commit()
    conn.close()


def catalog_sig(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ConfigRulesApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="dushevye-rules-"))
        os.environ["DUSHEVYE_DB"] = str(self.dir / "app.db")
        os.environ["DUSHEVYE_CATALOG_DB"] = str(self.dir / "catalog.db")
        build_catalog(self.dir / "catalog.db")
        self.catalog_before = catalog_sig(self.dir / "catalog.db")
        init_db()
        self._seed()
        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()
        self.token = ""

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)
        self.assertEqual(catalog_sig(self.dir / "catalog.db"), self.catalog_before)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _seed(self) -> None:
        conn = connect()
        try:
            org = conn.execute(
                "INSERT INTO organizations (name, slug) VALUES ('Душевые', 'default')"
            )
            org_id = int(org.lastrowid)
            conn.execute(
                """INSERT INTO users (org_id, email, name, role, password_hash)
                   VALUES (?,?,?,?,?)""",
                (org_id, "manager@example.com", "Менеджер", "manager", PASSWORD_HASH),
            )
            conn.commit()
        finally:
            conn.close()

    def login(self) -> None:
        token = self.client.get("/api/auth/csrf").json()["csrfToken"]
        res = self.client.post(
            "/api/auth/login",
            json={"email": "manager@example.com", "password": PASSWORD},
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

    def test_templates_and_rule_catalog(self) -> None:
        anon = self.client.get("/api/templates")
        self.assertEqual(anon.status_code, 200, anon.text)
        items = anon.json()["items"]
        self.assertEqual(
            [row["id"] for row in items],
            ["corner_two", "niche_straight", "ushape", "trapezoid"],
        )
        by_id = {row["id"]: row for row in items}
        self.assertFalse(by_id["corner_two"]["angle_ne_90"])
        self.assertTrue(by_id["trapezoid"]["angle_ne_90"])
        self.assertEqual(by_id["corner_two"]["glass_thickness_mm"], [8, 10])
        self.assertIn("width_mm", [dim["id"] for dim in by_id["ushape"]["dimensions"]])
        self.assertTrue(any(slot["kind"] == "door" for slot in by_id["niche_straight"]["slots"]))
        self.assertGreaterEqual(len(rule_ids()), 10)
        self.assertEqual(len(set(rule_ids())), len(rule_ids()))

    def test_validate_ok(self) -> None:
        self.login()
        res = self.send(
            "POST",
            "/api/config/validate",
            self.corner(part_ids=[1, 1]),
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["errors"], [])
        self.assertEqual(data["template_id"], "corner_two")
        self.assertTrue(any(row["code"] == "clamps_count" for row in data["warnings"]))
        self.assertTrue(any("коннектор" in row["message"] for row in data["warnings"]))

    def test_validate_size_error(self) -> None:
        self.login()
        res = self.send(
            "POST",
            "/api/config/validate",
            self.corner(width_mm=500),
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertFalse(data["ok"])
        width = next(row for row in data["errors"] if row["code"] == "width_mm")
        self.assertIn("500", width["message"])
        self.assertIn("700", width["message"])
        self.assertIn("вне допуска", width["message"])

    def test_validate_incompatible_hardware(self) -> None:
        self.login()
        res = self.send(
            "POST",
            "/api/config/validate",
            self.corner(part_ids=[2, 2]),
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertFalse(data["ok"])
        clash = next(row for row in data["errors"] if row["code"] == "hinge_4_6")
        self.assertIn("4–6", clash["message"])
        self.assertIn("8 мм", clash["message"])
        self.assertNotIn("hinges_count", [row["code"] for row in data["errors"]])

    def test_quote_blocks_errors_and_saves_valid_bom(self) -> None:
        self.login()
        blocked = self.send(
            "POST",
            "/api/quotes",
            self.corner(width_mm=500, items=[{"id": 1, "qty": 2}]),
        )
        self.assertEqual(blocked.status_code, 400, blocked.text)
        detail = blocked.json()["detail"]
        self.assertFalse(detail["ok"])
        self.assertTrue(any(row["code"] == "width_mm" for row in detail["errors"]))
        self.assertEqual(self.client.get("/api/quotes").json()["items"], [])

        clash = self.send(
            "POST",
            "/api/quotes",
            self.corner(items=[{"id": 2, "qty": 2}]),
        )
        self.assertEqual(clash.status_code, 400, clash.text)
        self.assertTrue(
            any(row["code"] == "hinge_4_6" for row in clash.json()["detail"]["errors"])
        )
        self.assertEqual(self.client.get("/api/quotes").json()["items"], [])

        bare = self.client.post("/api/config/validate", json=self.corner())
        self.assertEqual(bare.status_code, 403, bare.text)

        saved = self.send(
            "POST",
            "/api/quotes",
            self.corner(items=[{"id": 1, "qty": 2}]),
        )
        self.assertEqual(saved.status_code, 201, saved.text)
        quote = saved.json()["quote"]
        self.assertEqual(quote["template_id"], "corner_two")
        self.assertEqual(quote["fence_type"], "corner")
        self.assertEqual(quote["fence_label"], "Угловая (две стенки)")
        self.assertEqual(quote["glass_area_m2"], 3.6)
        self.assertEqual(quote["glass_sum"], 16934.4)
        self.assertEqual(quote["total"], 16934.4)
        hardware = [line for line in quote["lines"] if line["kind"] == "hardware"]
        self.assertEqual(len(hardware), 1)
        self.assertEqual(hardware[0]["sku"], "H-810")
        self.assertEqual(hardware[0]["qty"], 2)
        self.assertTrue(saved.json()["validation"]["ok"])

        pdf = self.client.get(f"/api/quotes/{quote['id']}/pdf")
        if pdf.status_code == 200:
            self.assertTrue(pdf.content.startswith(b"%PDF"))
            self.assertEqual(pdf.headers["content-type"], "application/pdf")
        else:
            self.assertEqual(pdf.status_code, 503)

    def test_legacy_quote_without_template(self) -> None:
        self.login()
        res = self.send(
            "POST",
            "/api/quotes",
            {
                "fence_type": "straight",
                "width_mm": 900,
                "height_mm": 2000,
                "glass_thick_mm": 8,
                "glass_kind": "clear",
                "items": [{"id": 1, "qty": 1}],
            },
        )
        self.assertEqual(res.status_code, 201, res.text)
        quote = res.json()["quote"]
        self.assertIsNone(quote["template_id"])
        self.assertEqual(quote["fence_type"], "straight")
        self.assertEqual(quote["glass_area_m2"], 1.8)
        self.assertEqual(quote["glass_sum"], 7560.0)
        self.assertNotIn("validation", res.json())

    def test_angle_and_pair(self) -> None:
        self.login()
        right = self.send(
            "POST",
            "/api/config/validate",
            {
                "template_id": "trapezoid",
                "width_mm": 900,
                "depth_mm": 900,
                "height_mm": 2000,
                "angle_deg": 90,
                "glass_thick_mm": 8,
                "glass_kind": "clear",
            },
        )
        self.assertEqual(right.status_code, 200, right.text)
        self.assertFalse(right.json()["ok"])
        self.assertTrue(any(row["code"] == "angle_deg" for row in right.json()["errors"]))
        self.assertIn("90", next(row["message"] for row in right.json()["errors"] if row["code"] == "angle_deg"))

        pair = self.send(
            "POST",
            "/api/config/validate",
            self.corner(part_ids=[1, 1, 3]),
        )
        self.assertEqual(pair.status_code, 200, pair.text)
        codes = [row["code"] for row in pair.json()["errors"]]
        self.assertIn("sliding_with_hinges", codes)
        self.assertIn("sliding_template", codes)


if __name__ == "__main__":
    unittest.main()
