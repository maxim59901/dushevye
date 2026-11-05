"""Сборка каталога проекта: JSON по группам + SQLite seed."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Iterable, List

from parsers.base import Item
from parsers.classify import GROUP_LABELS, GROUPS


SCHEMA = """
CREATE TABLE IF NOT EXISTS part_groups (
  id TEXT PRIMARY KEY,
  title_ru TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS parts (
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
  raw_json TEXT,
  UNIQUE(source, sku, url)
);
CREATE INDEX IF NOT EXISTS idx_parts_group ON parts(group_id);
CREATE INDEX IF NOT EXISTS idx_parts_source ON parts(source);
CREATE INDEX IF NOT EXISTS idx_parts_name ON parts(name);
"""


def item_dict(it: Item) -> dict:
    params = {}
    if it.params_json:
        try:
            params = json.loads(it.params_json) or {}
        except Exception:
            params = {}
    return {
        "source": it.source,
        "sku": it.sku,
        "name": it.name,
        "price": it.price,
        "currency": it.currency,
        "url": it.url,
        "in_stock": it.in_stock,
        "glass_mm": it.glass_mm,
        "image_url": it.image_url,
        "material": it.material,
        "finish": it.finish,
        "params": params,
    }


def build_grouped(items: Iterable[Item]) -> dict:
    by_group: dict[str, list] = {gid: [] for gid, _ in GROUPS}
    for it in items:
        g = it.group if it.group in by_group else "other"
        by_group[g].append(item_dict(it))
    return {
        "groups": [
            {
                "id": gid,
                "title_ru": title,
                "count": len(by_group[gid]),
                "items": by_group[gid],
            }
            for gid, title in GROUPS
        ],
        "total": sum(len(v) for v in by_group.values()),
    }


def write_json(path: Path, items: List[Item]) -> dict:
    data = build_grouped(items)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def write_sqlite(path: Path, items: List[Item]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA)
        for i, (gid, title) in enumerate(GROUPS):
            conn.execute(
                "INSERT INTO part_groups (id, title_ru, sort) VALUES (?,?,?)",
                (gid, title, i),
            )
        for it in items:
            g = it.group if it.group in GROUP_LABELS else "other"
            conn.execute(
                """INSERT OR REPLACE INTO parts
                   (group_id, source, sku, name, price, currency, url, in_stock,
                    glass_mm, image_url, material, finish, params_json, raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    g,
                    it.source,
                    it.sku,
                    it.name,
                    it.price,
                    it.currency,
                    it.url,
                    None if it.in_stock is None else int(bool(it.in_stock)),
                    it.glass_mm,
                    it.image_url,
                    it.material,
                    it.finish,
                    it.params_json,
                    it.raw_json,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def group_counts(items: List[Item]) -> Counter:
    c: Counter = Counter()
    for it in items:
        c[it.group if it.group in GROUP_LABELS else "other"] += 1
    return c
