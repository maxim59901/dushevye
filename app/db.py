"""SQLite helpers for Dushevye auth."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("DUSHEVYE_DB", "/var/www/dushevye/data/app.db"))

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS organizations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL REFERENCES organizations(id),
  email TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(org_id, email)
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL REFERENCES organizations(id),
  actor_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL,
  target_type TEXT,
  target_id INTEGER,
  detail TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_users_org ON users(org_id);
CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log(org_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id);

CREATE TABLE IF NOT EXISTS quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL REFERENCES organizations(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'draft',
  fence_type TEXT NOT NULL,
  width_mm INTEGER NOT NULL,
  height_mm INTEGER NOT NULL,
  depth_mm INTEGER,
  template_id TEXT,
  depth2_mm INTEGER,
  angle_deg INTEGER,
  door_count INTEGER,
  glass_thick_mm INTEGER NOT NULL,
  glass_kind TEXT NOT NULL,
  glass_area_m2 REAL NOT NULL,
  glass_unit_price REAL NOT NULL,
  glass_sum REAL NOT NULL,
  hardware_sum REAL NOT NULL DEFAULT 0,
  total REAL NOT NULL,
  currency TEXT NOT NULL DEFAULT 'RUB',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quote_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
  line_no INTEGER NOT NULL,
  kind TEXT NOT NULL,
  part_id INTEGER,
  sku TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL,
  qty REAL NOT NULL,
  unit TEXT NOT NULL,
  unit_price REAL,
  line_sum REAL,
  source TEXT NOT NULL DEFAULT '',
  group_title TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_quotes_org_user ON quotes(org_id, user_id);
CREATE INDEX IF NOT EXISTS idx_quote_items_quote ON quote_items(quote_id);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL REFERENCES organizations(id),
  customer_name TEXT NOT NULL,
  customer_phone TEXT NOT NULL DEFAULT '',
  customer_address TEXT NOT NULL DEFAULT '',
  responsible_user_id INTEGER REFERENCES users(id),
  created_by INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'created',
  quote_id INTEGER REFERENCES quotes(id) ON DELETE SET NULL,
  amount REAL,
  currency TEXT NOT NULL DEFAULT 'RUB',
  due_date TEXT,
  closed_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_projects_org_status ON projects(org_id, status);
CREATE INDEX IF NOT EXISTS idx_projects_responsible ON projects(org_id, responsible_user_id);
CREATE INDEX IF NOT EXISTS idx_projects_created_by ON projects(org_id, created_by);
CREATE INDEX IF NOT EXISTS idx_projects_quote ON projects(quote_id);

CREATE TABLE IF NOT EXISTS stock_balances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL REFERENCES organizations(id),
  part_id INTEGER NOT NULL,
  sku TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  qty_on_hand REAL NOT NULL DEFAULT 0,
  qty_reserved REAL NOT NULL DEFAULT 0,
  min_qty REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(org_id, part_id),
  CHECK (
    qty_on_hand >= 0
    AND qty_reserved >= 0
    AND min_qty >= 0
    AND qty_reserved <= qty_on_hand + 0.0000001
  )
);

CREATE TABLE IF NOT EXISTS stock_movements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL REFERENCES organizations(id),
  part_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  qty REAL NOT NULL,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  actor_id INTEGER REFERENCES users(id),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK (kind IN ('in', 'out', 'reserve', 'release')),
  CHECK (qty > 0)
);

CREATE INDEX IF NOT EXISTS idx_stock_balances_org ON stock_balances(org_id);
CREATE INDEX IF NOT EXISTS idx_stock_movements_org_part ON stock_movements(org_id, part_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_movements_project ON stock_movements(project_id);
CREATE INDEX IF NOT EXISTS idx_stock_movements_actor ON stock_movements(org_id, actor_id);

CREATE TABLE IF NOT EXISTS entity_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL REFERENCES organizations(id),
  entity_type TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  actor_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL,
  before_json TEXT,
  after_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_versions_entity
  ON entity_versions(org_id, entity_type, entity_id, id);
CREATE INDEX IF NOT EXISTS idx_versions_actor
  ON entity_versions(org_id, actor_id, id);

CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_compat_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER REFERENCES organizations(id),
  origin TEXT NOT NULL DEFAULT 'custom',
  effect TEXT NOT NULL,
  group_id TEXT NOT NULL DEFAULT '',
  group_name TEXT NOT NULL DEFAULT '',
  part_id INTEGER,
  name_like TEXT NOT NULL DEFAULT '',
  glass_mm INTEGER,
  template_id TEXT NOT NULL DEFAULT '',
  template_kind TEXT NOT NULL DEFAULT 'any',
  reason TEXT NOT NULL,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK (origin IN ('seed', 'custom')),
  CHECK (effect IN ('allow', 'deny')),
  CHECK (template_kind IN ('any', 'door', 'fixed')),
  CHECK (length(trim(reason)) > 0),
  CHECK (
    length(trim(group_id)) > 0
    OR length(trim(group_name)) > 0
    OR part_id IS NOT NULL
    OR length(trim(name_like)) > 0
  )
);

CREATE INDEX IF NOT EXISTS idx_compat_rules_org ON catalog_compat_rules(org_id);

CREATE TABLE IF NOT EXISTS vitrine_leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  phone TEXT NOT NULL,
  comment TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'заявка с витрины',
  template_id TEXT NOT NULL DEFAULT '',
  excerpt TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_vitrine_leads_org ON vitrine_leads(org_id, created_at);
"""


def db_path() -> Path:
    return Path(os.environ.get("DUSHEVYE_DB", str(DEFAULT_DB)))


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_quotes(conn: sqlite3.Connection) -> None:
    """Add template columns on databases created before the template seed."""
    rows = conn.execute("PRAGMA table_info(quotes)").fetchall()
    if not rows:
        return
    have = {row[1] for row in rows}
    for name, ddl in (
        ("template_id", "TEXT"),
        ("depth2_mm", "INTEGER"),
        ("angle_deg", "INTEGER"),
        ("door_count", "INTEGER"),
    ):
        if name not in have:
            conn.execute(f"ALTER TABLE quotes ADD COLUMN {name} {ddl}")


def init_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate_quotes(conn)
        from .catalog_compat import seed_catalog_compat

        seed_catalog_compat(conn)
        conn.commit()


@contextmanager
def get_db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
