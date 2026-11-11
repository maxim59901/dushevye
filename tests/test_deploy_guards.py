"""Nginx deny rules, backup privacy, and catalog.db kept out of a payload.

    .venv/bin/python tests/test_deploy_guards.py
"""
from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NGINX_CONF = ROOT / "deploy" / "nginx-dushevye.conf"
INSTALL = ROOT / "deploy" / "install-on-vds.sh"
BACKUP = ROOT / "deploy" / "dushevye-backup.sh"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _http(port: int, path: str) -> int:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


class DeployGuardTest(unittest.TestCase):
    def test_install_script_does_not_replace_catalog_or_sshd(self) -> None:
        text = INSTALL.read_text(encoding="utf-8")
        self.assertNotIn('cp -a "$BASE/data/catalog/catalog.db"', text)
        self.assertNotIn("sshd_config", text)
        self.assertNotIn("systemctl reload ssh", text)
        self.assertNotIn("systemctl restart ssh", text)
        self.assertIn("/etc/cron.d/dushevye", text)
        self.assertIn("dushevye-backup.sh", text)
        self.assertIn("crontab -r", text)
        subprocess.run(["bash", "-n", str(INSTALL)], check=True)
        subprocess.run(["bash", "-n", str(BACKUP)], check=True)
        nginx = NGINX_CONF.read_text(encoding="utf-8")
        self.assertNotRegex(nginx, r"(?m)^\s*alias\s")
        self.assertIn("deny all", nginx)
        self.assertIn("sqlite|bak|sql|db|env", nginx)

    def test_payload_with_catalog_db_is_refused(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("deploy_vds", ROOT / "deploy" / "deploy-vds.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        directory = Path(tempfile.mkdtemp(prefix="dushevye-payload-"))
        try:
            blocked = directory / "bad.tgz"
            with tarfile.open(blocked, "w:gz") as archive:
                payload = directory / "catalog.db"
                payload.write_bytes(b"not a live database")
                archive.add(payload, arcname="data/catalog/catalog.db")
            self.assertEqual(module.payload_catalog_db(blocked), "data/catalog/catalog.db")
            clean = directory / "ok.tgz"
            with tarfile.open(clean, "w:gz") as archive:
                photo = directory / "ok.jpg"
                photo.write_bytes(b"\xff\xd8\xff\xd9")
                archive.add(photo, arcname="public/media/parts/ok.jpg")
            self.assertIsNone(module.payload_catalog_db(clean))
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_backup_is_private_and_sqlite_consistent(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="dushevye-backup-"))
        site = directory / "site"
        dest = directory / "backups"
        data = site / "data"
        data.mkdir(parents=True)
        db_path = data / "app.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE marks (id INTEGER PRIMARY KEY, label TEXT)")
        conn.execute("INSERT INTO marks (label) VALUES ('committed')")
        conn.commit()
        conn.close()
        catalog = data / "catalog.db"
        catalog_conn = sqlite3.connect(catalog)
        catalog_conn.execute("CREATE TABLE parts (id INTEGER PRIMARY KEY, sku TEXT)")
        catalog_conn.execute("INSERT INTO parts (sku) VALUES ('H-1')")
        catalog_conn.commit()
        catalog_conn.close()
        env = os.environ.copy()
        env["DUSHEVYE_SITE"] = str(site)
        env["DUSHEVYE_BACKUP_DIR"] = str(dest)
        try:
            subprocess.run(["bash", str(BACKUP)], check=True, env=env, cwd=directory)
            self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o700)
            archives = list(dest.glob("dushevye-*.tgz"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(stat.S_IMODE(archives[0].stat().st_mode), 0o600)
            extract = directory / "out"
            extract.mkdir()
            with tarfile.open(archives[0], "r:gz") as archive:
                archive.extractall(extract)
            snap = sqlite3.connect(extract / "site" / "dushevye" / "data" / "app.db")
            labels = [row[0] for row in snap.execute("SELECT label FROM marks ORDER BY id")]
            snap.close()
            self.assertEqual(labels, ["committed"])
            live = sqlite3.connect(db_path)
            live.execute("INSERT INTO marks (label) VALUES ('after-backup')")
            live.commit()
            live.close()
            again = sqlite3.connect(extract / "site" / "dushevye" / "data" / "app.db")
            self.assertEqual(
                [row[0] for row in again.execute("SELECT label FROM marks")],
                ["committed"],
            )
            again.close()
            cat = sqlite3.connect(extract / "site" / "dushevye" / "data" / "catalog.db")
            self.assertEqual(cat.execute("SELECT sku FROM parts").fetchone()[0], "H-1")
            cat.close()
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_nginx_blocks_backups_and_serves_media(self) -> None:
        if shutil.which("nginx") is None:
            self.skipTest("nginx is not installed")
        root = Path(tempfile.mkdtemp(prefix="dushevye-nginx-"))
        public = root / "public"
        (public / "media" / "parts").mkdir(parents=True)
        (public / "lk").mkdir()
        (public / "index.html").write_text("<!doctype html><title>ok</title>", encoding="utf-8")
        (public / "cfg.html").write_text("<!doctype html><title>cfg</title>", encoding="utf-8")
        (public / "lk" / "login.html").write_text("<!doctype html><title>login</title>", encoding="utf-8")
        (public / "index.html.bak.20260101_120000").write_text("old", encoding="utf-8")
        (public / "notes.sql").write_text("select 1;", encoding="utf-8")
        (public / "notes.sqlite").write_text("sqlite", encoding="utf-8")
        (public / "secret.db").write_text("db", encoding="utf-8")
        (public / "secret.env").write_text("TOKEN=nope", encoding="utf-8")
        (public / "secret.env.local").write_text("TOKEN=nope", encoding="utf-8")
        (public / ".env").write_text("TOKEN=nope", encoding="utf-8")
        sql_dir = public / "dump.sql"
        sql_dir.mkdir()
        (sql_dir / "index.html").write_text("no", encoding="utf-8")
        photo = public / "media" / "parts" / "ok.jpg"
        photo.write_bytes(b"\xff\xd8\xff\xd9")
        (public / "media" / "parts" / "hidden.bak").write_text("no", encoding="utf-8")
        (public / "media" / ".secret.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        port = _free_port()
        prefix = root / "ngx"
        for name in ("body", "proxy", "fastcgi", "uwsgi", "scgi"):
            (prefix / name).mkdir(parents=True)
        server = NGINX_CONF.read_text(encoding="utf-8")
        server = server.replace("listen 80 default_server;", f"listen 127.0.0.1:{port};")
        server = server.replace("    listen [::]:80 default_server;\n", "")
        server = server.replace("root /var/www/dushevye/public;", f"root {public};")
        conf = root / "nginx.conf"
        conf.write_text(
            textwrap.dedent(
                f"""
                worker_processes 1;
                error_log {prefix}/error.log;
                pid {prefix}/nginx.pid;
                events {{ worker_connections 64; }}
                http {{
                    access_log {prefix}/access.log;
                    client_body_temp_path {prefix}/body;
                    proxy_temp_path {prefix}/proxy;
                    fastcgi_temp_path {prefix}/fastcgi;
                    uwsgi_temp_path {prefix}/uwsgi;
                    scgi_temp_path {prefix}/scgi;
                    {server}
                }}
                """
            ),
            encoding="utf-8",
        )
        proc = subprocess.Popen(
            ["nginx", "-c", str(conf), "-p", str(prefix), "-g", "daemon off;"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            import time

            for _ in range(50):
                if proc.poll() is not None:
                    err = proc.stderr.read().decode("utf-8", "replace")
                    self.fail(err or "nginx exited")
                try:
                    _http(port, "/index.html")
                    break
                except (urllib.error.URLError, ConnectionError):
                    time.sleep(0.05)
            else:
                self.fail("nginx did not accept connections")
            open_paths = ("/index.html", "/cfg.html", "/lk/login.html", "/media/parts/ok.jpg")
            for path in open_paths:
                self.assertEqual(_http(port, path), 200, path)
            closed = (
                "/index.html.bak.20260101_120000",
                "/notes.sql",
                "/notes.sqlite",
                "/secret.db",
                "/secret.env",
                "/secret.env.local",
                "/.env",
                "/dump.sql/",
                "/dump.sql/index.html",
                "/media/parts/hidden.bak",
                "/media/.secret.jpg",
            )
            for path in closed:
                self.assertEqual(_http(port, path), 403, path)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
