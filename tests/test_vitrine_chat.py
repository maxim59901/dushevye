"""Storefront assistant: template hint, rate limit, guest lead.

Run from the repo root:

    .venv/bin/pip install -r app/requirements.txt httpx
    .venv/bin/python tests/test_vitrine_chat.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_IMPORT_DIR = Path(tempfile.mkdtemp(prefix="dushevye-vitrine-import-"))
os.environ["DUSHEVYE_DB"] = str(_IMPORT_DIR / "app.db")
os.environ["DUSHEVYE_CATALOG_DB"] = str(_IMPORT_DIR / "catalog.db")
os.environ["DUSHEVYE_SESSION_SECRET"] = "test-only-secret"
os.environ["DUSHEVYE_VITRINE_LLM"] = "0"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("DUSHEVYE_SERVE_PUBLIC", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import reset_public_rate_limits  # noqa: E402
from app.vitrine import reply_is_public_safe  # noqa: E402

BANNED = ("whatsapp", "wa.me", "instagram", "facebook")


class VitrineChatTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_public_rate_limits()
        os.environ["DUSHEVYE_VITRINE_LLM"] = "0"
        os.environ.pop("OPENAI_API_KEY", None)
        self.dir = Path(tempfile.mkdtemp(prefix="dushevye-vitrine-"))
        os.environ["DUSHEVYE_DB"] = str(self.dir / "app.db")
        init_db()
        self._seed_org()
        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)
        reset_public_rate_limits()
        os.environ["DUSHEVYE_VITRINE_LLM"] = "0"
        os.environ.pop("OPENAI_API_KEY", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _seed_org(self) -> None:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO organizations (name, slug) VALUES ('Душевые', 'default')"
            )
            conn.commit()
        finally:
            conn.close()

    def _chat(self, message: str, **extra):
        body = {"message": message}
        body.update(extra)
        return self.client.post("/api/vitrine/chat", json=body)

    def test_corner_900_suggests_template_and_configurator(self) -> None:
        for message in ("угловая 900×900", "угловая 900x900", "угловая 900х900"):
            with self.subTest(message=message):
                reset_public_rate_limits()
                res = self._chat(message)
                self.assertEqual(res.status_code, 200, res.text)
                data = res.json()
                self.assertEqual(data["source"], "rules")
                self.assertEqual(data["template"]["id"], "corner_two")
                self.assertEqual(data["template"]["name"], "Угловая (две стенки)")
                self.assertEqual(data["dims"], {"width_mm": 900, "depth_mm": 900})
                self.assertEqual(
                    data["template"]["href"],
                    "/cfg.html?template=corner_two&width_mm=900&depth_mm=900",
                )
                self.assertIn("Угловая", data["reply"])
                self.assertIn("конфигуратор", data["reply"].casefold())
                labels = {item["id"]: item["label"] for item in data["actions"]}
                self.assertEqual(labels["configurator"], "Открыть конфигуратор")
                self.assertEqual(labels["lead"], "Оставить заявку")
                self.assertEqual(labels["demo"], "Демо ЛК")
                self.assertEqual(
                    next(item["href"] for item in data["actions"] if item["id"] == "demo"),
                    "/lk/login.html",
                )
                self._assert_public_safe(data)
                self.assertNotIn("dushevye_session", res.headers.get("set-cookie") or "")

    def test_history_keeps_type_and_script_is_not_echoed(self) -> None:
        res = self._chat(
            "900×900 <script>alert(1)</script>",
            history=[
                {"role": "user", "content": "угловая"},
                {"role": "assistant", "content": "Какой размер?"},
            ],
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertEqual(data["template"]["id"], "corner_two")
        self.assertEqual(data["dims"]["width_mm"], 900)
        self.assertNotIn("<script>", data["reply"])
        self.assertNotIn("alert", data["reply"])

    def test_niche_width_and_height(self) -> None:
        res = self._chat("ниша 800×2000")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertEqual(data["template"]["id"], "niche_straight")
        self.assertEqual(data["dims"], {"width_mm": 800, "height_mm": 2000})
        self.assertIn("width_mm=800", data["template"]["href"])
        self.assertIn("height_mm=2000", data["template"]["href"])

    def test_price_question_has_no_amount(self) -> None:
        res = self._chat("сколько стоит угловая 900×900")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertEqual(data["template"]["id"], "corner_two")
        self._assert_public_safe(data)
        for key in ("price", "unit_price", "hardware_sum", "total", "glass_sum"):
            self.assertNotIn(key, data)

    def test_faq_kp_and_demo(self) -> None:
        kp = self._chat("Что такое КП?").json()
        self.assertIn("коммерческ", kp["reply"].casefold())
        self.assertIsNone(kp["template"])
        self.assertTrue(any(item["id"] == "configurator" for item in kp["actions"]))
        demo = self._chat("как попасть в демо лк").json()
        self.assertIn("/lk/login.html", demo["reply"])
        self.assertTrue(
            any(item["id"] == "demo" and item["href"] == "/lk/login.html" for item in demo["actions"])
        )

    def test_chat_rate_limit_is_per_real_ip(self) -> None:
        headers = {"X-Real-IP": "203.0.113.10", "X-Forwarded-For": "198.51.100.9"}
        for index in range(10):
            res = self.client.post(
                "/api/vitrine/chat",
                json={"message": "привет"},
                headers={**headers, "X-Forwarded-For": f"198.51.100.{index}"},
            )
            self.assertEqual(res.status_code, 200, res.text)
        blocked = self.client.post(
            "/api/vitrine/chat",
            json={"message": "привет"},
            headers=headers,
        )
        self.assertEqual(blocked.status_code, 429, blocked.text)
        self.assertEqual(blocked.headers.get("retry-after"), "60")
        self.assertIn("минуту", blocked.json()["detail"])
        other = self.client.post(
            "/api/vitrine/chat",
            json={"message": "привет"},
            headers={"X-Real-IP": "203.0.113.11"},
        )
        self.assertEqual(other.status_code, 200, other.text)

    def test_lead_is_stored_with_vitrine_status(self) -> None:
        res = self.client.post(
            "/api/vitrine/leads",
            json={
                "name": "Анна",
                "phone": "+7 (999) 123-45-67",
                "comment": "угловая 900×900",
                "template_id": "corner_two",
                "excerpt": "Гость: угловая 900×900",
                "status": "Архив",
            },
        )
        self.assertEqual(res.status_code, 201, res.text)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "заявка с витрины")
        self.assertNotIn("phone", body)
        conn = connect()
        try:
            row = conn.execute("SELECT * FROM vitrine_leads WHERE id=?", (body["id"],)).fetchone()
            self.assertEqual(row["name"], "Анна")
            self.assertEqual(row["phone"], "+7 (999) 123-45-67")
            self.assertEqual(row["comment"], "угловая 900×900")
            self.assertEqual(row["status"], "заявка с витрины")
            self.assertEqual(row["template_id"], "corner_two")
            self.assertGreater(row["org_id"], 0)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"],
                0,
            )
            audit = conn.execute(
                "SELECT action, target_id, actor_id FROM audit_log WHERE action='vitrine_lead'"
            ).fetchone()
            self.assertEqual(audit["target_id"], body["id"])
            self.assertIsNone(audit["actor_id"])
        finally:
            conn.close()

    def test_lead_validation_does_not_insert(self) -> None:
        res = self.client.post(
            "/api/vitrine/leads",
            json={"name": "Анна", "phone": "123", "comment": "нет"},
        )
        self.assertEqual(res.status_code, 400, res.text)
        unknown = self.client.post(
            "/api/vitrine/leads",
            json={"name": "Анна", "phone": "+79991234567", "template_id": "nope"},
        )
        self.assertEqual(unknown.status_code, 400, unknown.text)
        conn = connect()
        try:
            count = conn.execute("SELECT COUNT(*) AS n FROM vitrine_leads").fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(count, 0)

    def test_lead_rate_limit_stops_the_eleventh_insert(self) -> None:
        headers = {"X-Real-IP": "203.0.113.40"}
        payload = {
            "name": "Борис",
            "phone": "89991234567",
            "comment": "ниша",
        }
        for _ in range(10):
            res = self.client.post("/api/vitrine/leads", json=payload, headers=headers)
            self.assertEqual(res.status_code, 201, res.text)
        blocked = self.client.post("/api/vitrine/leads", json=payload, headers=headers)
        self.assertEqual(blocked.status_code, 429, blocked.text)
        conn = connect()
        try:
            count = conn.execute("SELECT COUNT(*) AS n FROM vitrine_leads").fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(count, 10)

    def test_llm_price_falls_back_to_rules(self) -> None:
        os.environ["DUSHEVYE_VITRINE_LLM"] = "1"
        os.environ["OPENAI_API_KEY"] = "sk-test"
        try:
            with patch("app.vitrine.complete_openai", return_value="Итого 15000 руб. Напишите в whatsapp."):
                res = self._chat("угловая 900×900")
            data = res.json()
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(data["source"], "rules")
            self.assertNotIn("руб", data["reply"].casefold())
            self.assertEqual(data["template"]["id"], "corner_two")
            with patch(
                "app.vitrine.complete_openai",
                return_value="Шаблон угловая: ширина 900 мм и глубина 900 мм. Откройте конфигуратор.",
            ):
                polished = self._chat("угловая 900×900")
            polished_data = polished.json()
            self.assertEqual(polished_data["source"], "llm")
            self.assertEqual(polished_data["template"]["id"], "corner_two")
            self.assertEqual(polished_data["dims"]["width_mm"], 900)
            self._assert_public_safe(polished_data)
        finally:
            os.environ["DUSHEVYE_VITRITE_LLM"] = "0"
            os.environ.pop("OPENAI_API_KEY", None)

    def test_unsafe_reply_filter(self) -> None:
        self.assertFalse(reply_is_public_safe("Итого 12000 руб."))
        self.assertFalse(reply_is_public_safe("Напишите в WhatsApp"))
        self.assertFalse(reply_is_public_safe("Смотрите https://example.com"))
        self.assertTrue(reply_is_public_safe("Откройте конфигуратор и укажите высоту."))

    def test_widget_files_have_no_meta_messengers(self) -> None:
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "vitrine-chat.js").read_text(encoding="utf-8")
        cfg = (ROOT / "public" / "cfg.html").read_text(encoding="utf-8")
        self.assertIn('src="vitrine-chat.js"', html)
        self.assertIn("Оставить заявку", script)
        self.assertIn("Демо ЛК", script)
        self.assertIn("/api/vitrine/chat", script)
        self.assertIn("/api/vitrine/leads", script)
        self.assertIn("applyVitrinePreset", cfg)
        folded = (html + script).casefold()
        for banned in BANNED:
            self.assertNotIn(banned, script.casefold())
            self.assertNotIn(banned, folded.replace("мессенджеров meta", ""))

    def _assert_public_safe(self, data: dict) -> None:
        blob = str(data).casefold()
        self.assertNotIn("₽", str(data))
        self.assertNotIn("руб", blob)
        for banned in BANNED:
            self.assertNotIn(banned, blob)


if __name__ == "__main__":
    unittest.main()
