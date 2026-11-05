"""Field-level history for projects, quotes, stock balances, and roles.

audit_log stays an event list (login, invite, and the same mutations) without a
before/after diff. This table is the diff. Rows are append-only: nothing here
restores or merges a previous state.

Visibility matches the audit rule: admin and owner see the whole org; every
other role sees only rows they authored.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import HTTPException

from .quotes import FENCE_LABEL, KIND_LABEL
from .security import PRIVILEGED, ROLE_LABELS_RU

ENTITY_TYPES = frozenset({"project", "quote", "stock", "user"})

# Keep aligned with projects.STATUS_LABEL. Imported lazily there to avoid a cycle.
STATUS_LABEL = {
    "created": "Создан",
    "estimate": "Просчёт",
    "in_work": "В работе",
    "drawing": "Чертёж",
    "hardware": "Фурнитура",
    "done": "Завершено",
    "archive": "Архив",
    "draft": "Черновик",
}

ACTION_LABEL = {
    "project_create": "Создание проекта",
    "project_status_change": "Статус проекта",
    "project_quote_link": "Привязка КП",
    "quote_create": "Создание КП",
    "stock_in": "Приход",
    "stock_out": "Расход",
    "stock_reserve": "Резерв",
    "stock_release": "Снятие резерва",
    "change_role": "Смена роли",
}

# Order is the order of the short «было → стало» line.
SUMMARY_FIELDS = {
    "project": (
        "customer_name",
        "customer_phone",
        "customer_address",
        "responsible_name",
        "status",
        "quote_id",
        "amount",
        "due_date",
    ),
    "quote": (
        "fence_type",
        "width_mm",
        "height_mm",
        "depth_mm",
        "glass_thick_mm",
        "glass_kind",
        "hardware_count",
        "project_id",
    ),
    "stock": ("sku", "qty_on_hand", "qty_reserved", "min_qty"),
    "user": ("role",),
}

FIELD_LABEL = {
    "customer_name": "Клиент",
    "customer_phone": "Телефон",
    "customer_address": "Адрес",
    "responsible_name": "Ответственный",
    "status": "Статус",
    "quote_id": "КП",
    "amount": "Сумма",
    "due_date": "Срок",
    "fence_type": "Тип",
    "width_mm": "Ширина, мм",
    "height_mm": "Высота, мм",
    "depth_mm": "Глубина, мм",
    "glass_thick_mm": "Стекло, мм",
    "glass_kind": "Стекло",
    "hardware_count": "Позиции",
    "project_id": "Проект",
    "sku": "Артикул",
    "qty_on_hand": "Остаток",
    "qty_reserved": "Резерв",
    "min_qty": "Минимум",
    "role": "Роль",
}

# On create, a zero reserve or an empty phone is not a change worth a line.
_CREATE_SKIP = {
    "qty_reserved": (0, 0.0),
    "min_qty": (0, 0.0),
    "amount": (0, 0.0),
    "hardware_count": (0, 0.0),
}


def record_version(
    conn,
    org_id: int,
    actor_id: Optional[int],
    entity_type: str,
    entity_id: int,
    action: str,
    before: Optional[dict],
    after: Optional[dict],
) -> None:
    conn.execute(
        """INSERT INTO entity_versions (
             org_id, entity_type, entity_id, actor_id, action, before_json, after_json
           ) VALUES (?,?,?,?,?,?,?)""",
        (
            org_id,
            entity_type,
            int(entity_id),
            actor_id,
            action,
            _dump(before),
            _dump(after),
        ),
    )


def list_versions(
    conn,
    user,
    *,
    entity_type: Optional[str],
    entity_id: Optional[int],
    limit: int,
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), 200))
    kind = (entity_type or "").strip().lower() or None
    if kind and kind not in ENTITY_TYPES:
        allowed = ", ".join(sorted(ENTITY_TYPES))
        raise HTTPException(status_code=400, detail=f"Неизвестный тип сущности. Допустимо: {allowed}")
    if entity_id is not None:
        if isinstance(entity_id, bool) or not isinstance(entity_id, int) or entity_id < 1:
            raise HTTPException(status_code=400, detail="Некорректная сущность")
        if not kind:
            raise HTTPException(status_code=400, detail="Укажите entity_type вместе с entity_id")

    where = ["v.org_id=?"]
    args: list[Any] = [user["org_id"]]
    if user["role"] not in PRIVILEGED:
        where.append("v.actor_id=?")
        args.append(user["id"])
    if kind:
        where.append("v.entity_type=?")
        args.append(kind)
    if entity_id is not None:
        where.append("v.entity_id=?")
        args.append(entity_id)
    rows = conn.execute(
        f"""SELECT v.*, u.email AS actor_email, u.name AS actor_name
            FROM entity_versions v
            LEFT JOIN users u ON u.id = v.actor_id
            WHERE {' AND '.join(where)}
            ORDER BY v.id DESC
            LIMIT ?""",
        [*args, limit],
    ).fetchall()
    return {
        "items": [present_version(row) for row in rows],
        "can_see_org": user["role"] in PRIVILEGED,
    }


def present_version(row) -> dict[str, Any]:
    before = _load(row["before_json"])
    after = _load(row["after_json"])
    entity_type = row["entity_type"]
    return {
        "id": row["id"],
        "entity_type": entity_type,
        "entity_id": row["entity_id"],
        "entity_label": _entity_label(entity_type, row["entity_id"], before, after),
        "action": row["action"],
        "action_label": ACTION_LABEL.get(row["action"], row["action"]),
        "actor_id": row["actor_id"],
        "actor_name": row["actor_name"] or "",
        "actor_email": row["actor_email"] or "",
        "before": before,
        "after": after,
        "summary": summarize(entity_type, before, after),
        "created_at": row["created_at"],
    }


def summarize(entity_type: str, before: Optional[dict], after: Optional[dict]) -> str:
    fields = SUMMARY_FIELDS.get(entity_type, ())
    created = before is None
    bits = []
    for field in fields:
        after_value = None if after is None else after.get(field)
        if not _show_field(field, before, after_value, created):
            continue
        before_value = None if created else before.get(field)
        label = FIELD_LABEL.get(field, field)
        bits.append(f"{label}: {_format_value(field, before_value)} → {_format_value(field, after_value)}")
    return " · ".join(bits) if bits else "Поля не изменились"


def _show_field(field: str, before: Optional[dict], after_value: Any, created: bool) -> bool:
    if created:
        if after_value is None or after_value == "":
            return False
        if after_value in _CREATE_SKIP.get(field, ()):
            return False
        return True
    before_value = None if before is None else before.get(field)
    return before_value != after_value


def _format_value(field: str, value: Any) -> str:
    if value is None or value == "":
        return "—"
    if field == "status":
        return STATUS_LABEL.get(str(value), str(value))
    if field == "role":
        return ROLE_LABELS_RU.get(str(value), str(value))
    if field == "fence_type":
        return FENCE_LABEL.get(str(value), str(value))
    if field == "glass_kind":
        return KIND_LABEL.get(str(value), str(value))
    if field in ("quote_id", "project_id") and isinstance(value, int):
        return f"№{value}"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _entity_label(entity_type: str, entity_id: int, before: Optional[dict], after: Optional[dict]) -> str:
    blob = after or before or {}
    if entity_type == "project":
        return f"Проект №{entity_id}"
    if entity_type == "quote":
        return f"КП №{entity_id}"
    if entity_type == "user":
        name = blob.get("name") or blob.get("email") or ""
        return f"Сотрудник {name}".strip() if name else f"Сотрудник №{entity_id}"
    if entity_type == "stock":
        sku = blob.get("sku") or ""
        return f"Склад · {sku}" if sku else f"Склад · позиция {entity_id}"
    return f"{entity_type} #{entity_id}"


def _dump(payload: Optional[dict]) -> Optional[str]:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _load(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
