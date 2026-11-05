"""Org stock on top of catalog part ids.

Balances and movements live in app.db. catalog.db is opened read-only:
a receipt never writes supplier prices, names, or stock flags.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any, Optional
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from .projects import can_write_project
from .quotes import catalog_db_path

FULL_ROLES = frozenset({"admin", "owner", "supply"})
RESERVE_ROLES = frozenset({"admin", "owner", "supply", "manager"})
OWN_RESERVE_ROLES = frozenset({"measurer", "installer", "production"})

MOVE_KINDS = frozenset({"in", "out", "reserve", "release"})
AUDIT_ACTION = {
    "in": "stock_in",
    "out": "stock_out",
    "reserve": "stock_reserve",
    "release": "stock_release",
}


class StockMoveIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    part_id: int
    qty: float
    project_id: Optional[int] = None
    note: str = ""
    min_qty: Optional[float] = None


def stock_caps(user) -> dict[str, Any]:
    role = user["role"]
    if role in FULL_ROLES:
        scope = "full"
    elif role == "accountant":
        scope = "view"
    elif role == "manager":
        scope = "manager"
    elif role in OWN_RESERVE_ROLES:
        scope = "own_reserves"
    else:
        scope = "none"
    return {
        "scope": scope,
        "can_in": role in FULL_ROLES,
        "can_out": role in FULL_ROLES,
        "can_reserve": role in RESERVE_ROLES,
        "can_set_min": role in FULL_ROLES,
    }


def list_stock(
    conn,
    user,
    *,
    group: Optional[str],
    source: Optional[str],
    q: Optional[str],
    limit: int,
) -> dict[str, Any]:
    caps = stock_caps(user)
    if caps["scope"] == "none":
        raise HTTPException(status_code=403, detail="Склад недоступен для этой роли")
    if caps["scope"] == "own_reserves":
        reserves = _own_reserves(conn, user)
        return {
            **caps,
            "items": [],
            "reserves": reserves,
            "total": len(reserves),
            "below_min": 0,
            "groups": [],
            "sources": [],
            "projects": [],
        }

    group_id = _clean_filter(group, 80, "Некорректная группа")
    source_id = _clean_filter(source, 80, "Некорректный производитель")
    if source_id:
        source_id = source_id.lower()
    query = _clean_filter(q, 200, "Поиск не длиннее 200 символов")
    if group_id and not catalog_db_path().exists():
        raise HTTPException(status_code=503, detail="Каталог ещё не загружен")

    balances = conn.execute(
        "SELECT * FROM stock_balances WHERE org_id=? ORDER BY part_id",
        (user["org_id"],),
    ).fetchall()
    parts = fetch_catalog_parts([int(row["part_id"]) for row in balances])
    reservations = _reservation_lines(conn, user)
    items = []
    for row in balances:
        item = present_balance(row, parts.get(int(row["part_id"])))
        item["reservations"] = reservations.get(int(row["part_id"]), [])
        items.append(item)

    groups, sources = _facets(items)
    filtered = [item for item in items if _matches(item, group_id, source_id, query)]
    filtered.sort(key=_sort_key)
    below = sum(1 for item in filtered if item["below_min"])
    return {
        **caps,
        "items": filtered[:limit],
        "reserves": [],
        "total": len(filtered),
        "below_min": below,
        "groups": groups,
        "sources": sources,
        "projects": _project_choices(conn, user) if caps["can_reserve"] else [],
    }


def apply_movement(conn, user, kind: str, body: StockMoveIn) -> tuple[dict[str, Any], int, str]:
    """Apply one stock operation inside the caller's transaction.

    The first statement is BEGIN IMMEDIATE so the balance check and the
    movement insert commit together.
    """
    if kind not in MOVE_KINDS:
        raise HTTPException(status_code=400, detail="Неизвестная операция")
    caps = stock_caps(user)
    if kind in ("in", "out") and not caps["can_in"]:
        raise HTTPException(
            status_code=403,
            detail="Приход и расход доступны админу, хозяину и снабжению",
        )
    if kind in ("reserve", "release") and not caps["can_reserve"]:
        raise HTTPException(
            status_code=403,
            detail="Резерв доступен админу, хозяину, снабжению и менеджеру",
        )
    if body.part_id < 1:
        raise HTTPException(status_code=400, detail="Некорректная позиция каталога")
    part = fetch_catalog_part(body.part_id)
    qty = clean_qty(body.qty)
    note = clean_note(body.note)
    new_min = None
    if kind == "in" and body.min_qty is not None:
        if not caps["can_set_min"]:
            raise HTTPException(
                status_code=403,
                detail="Минимальный остаток задаёт админ, хозяин или снабжение",
            )
        new_min = clean_min(body.min_qty)

    conn.execute("BEGIN IMMEDIATE")
    project_id = _assert_project(
        conn,
        user,
        body.project_id,
        required=kind in ("reserve", "release") and user["role"] == "manager",
    )
    row = conn.execute(
        "SELECT * FROM stock_balances WHERE org_id=? AND part_id=?",
        (user["org_id"], body.part_id),
    ).fetchone()
    before = _balance_snap(row) if row is not None else None
    on_hand = round_qty(row["qty_on_hand"]) if row else 0.0
    reserved = round_qty(row["qty_reserved"]) if row else 0.0
    min_qty = round_qty(row["min_qty"]) if row else 0.0
    if new_min is not None:
        min_qty = new_min

    if kind == "in":
        on_hand = round_qty(on_hand + qty)
    elif kind == "out":
        _require_row(row)
        free = round_qty(on_hand - reserved)
        if qty > free:
            raise HTTPException(
                status_code=400,
                detail="Нельзя списать больше свободного остатка. Зарезервированное сначала снимите с резерва.",
            )
        on_hand = round_qty(on_hand - qty)
    elif kind == "reserve":
        _require_row(row)
        free = round_qty(on_hand - reserved)
        if qty > free:
            raise HTTPException(status_code=400, detail="Свободного остатка не хватает для резерва")
        reserved = round_qty(reserved + qty)
    else:
        _require_row(row)
        bucket = round_qty(_reserved_bucket(conn, user["org_id"], body.part_id, project_id))
        if qty > bucket or qty > reserved:
            if project_id is None:
                raise HTTPException(status_code=400, detail="Нет такого резерва без проекта")
            raise HTTPException(
                status_code=400,
                detail="Нельзя снять больше, чем зарезервировано под этот проект",
            )
        reserved = round_qty(reserved - qty)

    sku = str(part["sku"] or "")
    source = str(part["source"] or "")
    if row is None:
        conn.execute(
            """INSERT INTO stock_balances (
                 org_id, part_id, sku, source, qty_on_hand, qty_reserved, min_qty
               ) VALUES (?,?,?,?,?,?,?)""",
            (user["org_id"], body.part_id, sku, source, on_hand, reserved, min_qty),
        )
    else:
        conn.execute(
            """UPDATE stock_balances
               SET sku=?, source=?, qty_on_hand=?, qty_reserved=?, min_qty=?,
                   updated_at=datetime('now')
               WHERE org_id=? AND part_id=?""",
            (sku, source, on_hand, reserved, min_qty, user["org_id"], body.part_id),
        )
    cur = conn.execute(
        """INSERT INTO stock_movements (
             org_id, part_id, kind, qty, project_id, actor_id, note
           ) VALUES (?,?,?,?,?,?,?)""",
        (user["org_id"], body.part_id, kind, qty, project_id, user["id"], note),
    )
    updated = conn.execute(
        "SELECT * FROM stock_balances WHERE org_id=? AND part_id=?",
        (user["org_id"], body.part_id),
    ).fetchone()
    item = present_balance(updated, part)
    item["reservations"] = _reservation_lines(conn, user).get(body.part_id, [])
    _write_stock_version(conn, user, kind, body.part_id, before, _balance_snap(updated, sku=item["sku"]))
    return item, int(cur.lastrowid), _movement_detail(item, qty, project_id, note)


def _balance_snap(row, *, sku: Optional[str] = None) -> dict[str, Any]:
    return {
        "part_id": int(row["part_id"]),
        "sku": str(sku if sku is not None else (row["sku"] or "")),
        "qty_on_hand": round_qty(row["qty_on_hand"]),
        "qty_reserved": round_qty(row["qty_reserved"]),
        "min_qty": round_qty(row["min_qty"]),
    }


def _write_stock_version(conn, user, kind: str, part_id: int, before, after) -> None:
    from .versions import record_version

    record_version(
        conn,
        user["org_id"],
        user["id"],
        "stock",
        part_id,
        AUDIT_ACTION[kind],
        before,
        after,
    )


def present_balance(row, part) -> dict[str, Any]:
    on_hand = round_qty(row["qty_on_hand"])
    reserved = round_qty(row["qty_reserved"])
    min_qty = round_qty(row["min_qty"])
    free = round_qty(on_hand - reserved)
    deficit = round_qty(max(0.0, min_qty - free))
    sku = str((part["sku"] if part is not None else row["sku"]) or "")
    source = str((part["source"] if part is not None else row["source"]) or "")
    name = str(part["name"] or "") if part is not None else ""
    group_id = str(part["group_id"] or "") if part is not None else ""
    group_title = str(part["group_title"] or "") if part is not None else ""
    return {
        "part_id": int(row["part_id"]),
        "sku": sku,
        "source": source,
        "name": name or sku or f"#{row['part_id']}",
        "group_id": group_id,
        "group_title": group_title,
        "qty_on_hand": on_hand,
        "qty_reserved": reserved,
        "qty_free": free,
        "min_qty": min_qty,
        "deficit": deficit,
        "below_min": deficit > 0,
    }


def fetch_catalog_part(part_id: int) -> sqlite3.Row:
    conn = _open_catalog()
    if conn is None:
        raise HTTPException(status_code=503, detail="Каталог ещё не загружен")
    try:
        row = conn.execute(
            """SELECT p.id, p.sku, p.name, p.source, p.group_id, g.title_ru AS group_title
               FROM parts p
               LEFT JOIN part_groups g ON g.id = p.group_id
               WHERE p.id=?""",
            (part_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Позиция каталога не найдена")
    return row


def fetch_catalog_parts(ids: list[int]) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    conn = _open_catalog()
    if conn is None:
        return {}
    found: dict[int, sqlite3.Row] = {}
    try:
        for start in range(0, len(ids), 400):
            chunk = ids[start : start + 400]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"""SELECT p.id, p.sku, p.name, p.source, p.group_id, g.title_ru AS group_title
                    FROM parts p
                    LEFT JOIN part_groups g ON g.id = p.group_id
                    WHERE p.id IN ({marks})""",
                chunk,
            ).fetchall()
            found.update({int(row["id"]): row for row in rows})
    finally:
        conn.close()
    return found


def round_qty(value: float) -> float:
    return math.floor(float(value) * 1000 + 0.5) / 1000.0


def clean_qty(value: Any) -> float:
    number = _finite_number(value, "Некорректное количество")
    qty = round_qty(number)
    if qty <= 0 or qty > 1_000_000:
        raise HTTPException(status_code=400, detail="Количество от 0.001 до 1 000 000")
    return qty


def clean_min(value: Any) -> float:
    number = _finite_number(value, "Некорректный минимальный остаток")
    qty = round_qty(number)
    if qty < 0 or qty > 1_000_000:
        raise HTTPException(status_code=400, detail="Минимальный остаток от 0 до 1 000 000")
    return qty


def clean_note(value: str) -> str:
    text = (value or "").strip()
    if len(text) > 300:
        raise HTTPException(status_code=400, detail="Комментарий не длиннее 300 символов")
    return text


def _movement_detail(item: dict[str, Any], qty: float, project_id: Optional[int], note: str) -> str:
    label = item["sku"] or str(item["part_id"])
    qty_text = f"{qty:.3f}".rstrip("0").rstrip(".")
    bits = [label, f"qty={qty_text}"]
    if project_id:
        bits.append(f"project={project_id}")
    if note:
        bits.append(note)
    return " ".join(bits)[:300]


def _assert_project(conn, user, project_id: Optional[int], *, required: bool) -> Optional[int]:
    if project_id is None:
        if required:
            raise HTTPException(status_code=400, detail="Укажите свой проект")
        return None
    if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id < 1:
        raise HTTPException(status_code=400, detail="Некорректный проект")
    row = conn.execute(
        """SELECT id, org_id, responsible_user_id, created_by
           FROM projects WHERE id=? AND org_id=?""",
        (project_id, user["org_id"]),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Проект не найден")
    if user["role"] == "manager" and not can_write_project(user, row):
        raise HTTPException(status_code=404, detail="Проект не найден")
    return int(row["id"])


def _reserved_bucket(conn, org_id: int, part_id: int, project_id: Optional[int]) -> float:
    if project_id is None:
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE kind
                   WHEN 'reserve' THEN qty WHEN 'release' THEN -qty ELSE 0 END), 0) AS net
               FROM stock_movements
               WHERE org_id=? AND part_id=? AND project_id IS NULL
                 AND kind IN ('reserve', 'release')""",
            (org_id, part_id),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE kind
                   WHEN 'reserve' THEN qty WHEN 'release' THEN -qty ELSE 0 END), 0) AS net
               FROM stock_movements
               WHERE org_id=? AND part_id=? AND project_id=?
                 AND kind IN ('reserve', 'release')""",
            (org_id, part_id, project_id),
        ).fetchone()
    return float(row["net"] or 0)


def _reservation_lines(conn, user) -> dict[int, list[dict[str, Any]]]:
    rows = conn.execute(
        """SELECT part_id, project_id,
                  SUM(CASE kind WHEN 'reserve' THEN qty WHEN 'release' THEN -qty ELSE 0 END) AS net
           FROM stock_movements
           WHERE org_id=? AND kind IN ('reserve', 'release')
           GROUP BY part_id, project_id""",
        (user["org_id"],),
    ).fetchall()
    allowed = _manager_project_ids(conn, user) if user["role"] == "manager" else None
    pending = []
    project_ids = []
    for row in rows:
        net = round_qty(float(row["net"] or 0))
        if net <= 0:
            continue
        project_id = int(row["project_id"]) if row["project_id"] is not None else None
        if allowed is not None and project_id not in allowed:
            continue
        pending.append((int(row["part_id"]), project_id, net))
        if project_id:
            project_ids.append(project_id)
    names = _customer_names(conn, user["org_id"], project_ids)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for part_id, project_id, net in pending:
        grouped.setdefault(part_id, []).append(
            {
                "project_id": project_id,
                "customer_name": names.get(project_id, "") if project_id else "",
                "qty": net,
            }
        )
    for lines in grouped.values():
        lines.sort(key=lambda line: (-line["qty"], line["project_id"] or 0))
    return grouped


def _own_reserves(conn, user) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT m.part_id, m.project_id, p.customer_name,
                  SUM(CASE m.kind WHEN 'reserve' THEN m.qty WHEN 'release' THEN -m.qty ELSE 0 END) AS net
           FROM stock_movements m
           JOIN projects p ON p.id = m.project_id AND p.org_id = m.org_id
           WHERE m.org_id=? AND p.responsible_user_id=?
             AND m.kind IN ('reserve', 'release')
           GROUP BY m.part_id, m.project_id
           HAVING SUM(CASE m.kind WHEN 'reserve' THEN m.qty WHEN 'release' THEN -m.qty ELSE 0 END) > 0""",
        (user["org_id"], user["id"]),
    ).fetchall()
    if not rows:
        return []
    part_ids = [int(row["part_id"]) for row in rows]
    parts = fetch_catalog_parts(part_ids)
    snapshots = {
        int(row["part_id"]): row
        for row in conn.execute(
            "SELECT part_id, sku, source FROM stock_balances WHERE org_id=?",
            (user["org_id"],),
        ).fetchall()
    }
    items = []
    for row in rows:
        part_id = int(row["part_id"])
        part = parts.get(part_id)
        snap = snapshots.get(part_id)
        sku = str((part["sku"] if part is not None else (snap["sku"] if snap else "")) or "")
        source = str((part["source"] if part is not None else (snap["source"] if snap else "")) or "")
        name = str(part["name"] or "") if part is not None else ""
        items.append(
            {
                "part_id": part_id,
                "sku": sku,
                "source": source,
                "name": name or sku or f"#{part_id}",
                "group_title": str(part["group_title"] or "") if part is not None else "",
                "project_id": int(row["project_id"]),
                "customer_name": row["customer_name"] or "",
                "qty_reserved": round_qty(float(row["net"] or 0)),
            }
        )
    items.sort(key=lambda item: (item["customer_name"].casefold(), item["name"].casefold(), item["part_id"]))
    return items


def _project_choices(conn, user) -> list[dict[str, Any]]:
    if user["role"] == "manager":
        rows = conn.execute(
            """SELECT id, customer_name FROM projects
               WHERE org_id=? AND (responsible_user_id=? OR created_by=?)
               ORDER BY updated_at DESC, id DESC
               LIMIT 200""",
            (user["org_id"], user["id"], user["id"]),
        ).fetchall()
    elif user["role"] in FULL_ROLES:
        rows = conn.execute(
            """SELECT id, customer_name FROM projects
               WHERE org_id=?
               ORDER BY updated_at DESC, id DESC
               LIMIT 200""",
            (user["org_id"],),
        ).fetchall()
    else:
        return []
    return [{"id": int(row["id"]), "customer_name": row["customer_name"] or ""} for row in rows]


def _manager_project_ids(conn, user) -> set[int]:
    rows = conn.execute(
        """SELECT id FROM projects
           WHERE org_id=? AND (responsible_user_id=? OR created_by=?)""",
        (user["org_id"], user["id"], user["id"]),
    ).fetchall()
    return {int(row["id"]) for row in rows}


def _customer_names(conn, org_id: int, project_ids: list[int]) -> dict[int, str]:
    unique = list(dict.fromkeys(project_ids))
    names: dict[int, str] = {}
    for start in range(0, len(unique), 400):
        chunk = unique[start : start + 400]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT id, customer_name FROM projects WHERE org_id=? AND id IN ({marks})",
            [org_id, *chunk],
        ).fetchall()
        names.update({int(row["id"]): row["customer_name"] or "" for row in rows})
    return names


def _facets(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    for item in items:
        group_id = item["group_id"]
        if group_id:
            bucket = groups.setdefault(
                group_id,
                {"id": group_id, "title_ru": item["group_title"] or group_id, "count": 0},
            )
            bucket["count"] += 1
        source = item["source"]
        if source:
            bucket = sources.setdefault(source, {"id": source, "count": 0})
            bucket["count"] += 1
    return (
        sorted(groups.values(), key=lambda item: (item["title_ru"].casefold(), item["id"])),
        sorted(sources.values(), key=lambda item: item["id"]),
    )


def _matches(item: dict[str, Any], group_id: Optional[str], source: Optional[str], query: Optional[str]) -> bool:
    if group_id and item["group_id"] != group_id:
        return False
    if source and (item["source"] or "").lower() != source:
        return False
    if not query:
        return True
    hay = " ".join(
        [
            item["name"],
            item["sku"],
            item["source"],
            item["group_title"],
            item["group_id"],
        ]
    ).casefold()
    return query.casefold() in hay


def _sort_key(item: dict[str, Any]):
    # Deficit first, then more reserved, then smaller on-hand, then higher minimum.
    return (
        -item["deficit"],
        -item["qty_reserved"],
        item["qty_on_hand"],
        -item["min_qty"],
        item["part_id"],
    )


def _open_catalog() -> Optional[sqlite3.Connection]:
    path = catalog_db_path()
    if not path.exists():
        return None
    uri = "file:" + quote(path.as_posix(), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _require_row(row) -> None:
    if row is None:
        raise HTTPException(status_code=400, detail="Позиции нет на складе")


def _clean_filter(value: Optional[str], limit: int, message: str) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > limit:
        raise HTTPException(status_code=400, detail=message)
    return text


def _finite_number(value: Any, message: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(status_code=400, detail=message)
    if math.isnan(value) or math.isinf(value):
        raise HTTPException(status_code=400, detail=message)
    return float(value)
