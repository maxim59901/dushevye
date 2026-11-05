"""Project funnel: customer, status, optional quote link.

Statuses, in order: created → estimate → in_work → drawing → hardware → done → archive.
Any of these may be set directly. A later complaint status is intentionally absent.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from .quotes import FENCE_LABEL, round_money
from .security import PRIVILEGED

# Purchase-price visibility. Keep aligned with PRICE_ROLES in main.py.
PRICE_ROLES = frozenset({"admin", "owner", "accountant"})

PROJECT_STATUSES: tuple[tuple[str, str], ...] = (
    ("created", "Создан"),
    ("estimate", "Просчёт"),
    ("in_work", "В работе"),
    ("drawing", "Чертёж"),
    ("hardware", "Фурнитура"),
    ("done", "Завершено"),
    ("archive", "Архив"),
)
STATUS_LABEL = dict(PROJECT_STATUSES)
STATUS_IDS = frozenset(STATUS_LABEL)
TERMINAL_STATUSES = frozenset({"done", "archive"})

READ_ORG_ROLES = frozenset({"admin", "owner", "accountant"})

_PROJECT_COLUMNS = """
p.id, p.org_id, p.customer_name, p.customer_phone, p.customer_address,
p.responsible_user_id, p.created_by, p.status, p.quote_id,
p.amount, p.currency, p.due_date, p.closed_at, p.created_at, p.updated_at,
ru.name AS responsible_name, ru.email AS responsible_email,
cu.name AS created_by_name, cu.email AS created_by_email,
q.id AS quote_ref_id, q.user_id AS quote_user_id, q.org_id AS quote_org_id,
q.fence_type AS quote_fence_type, q.width_mm AS quote_width_mm,
q.height_mm AS quote_height_mm, q.depth_mm AS quote_depth_mm,
q.glass_sum AS quote_glass_sum, q.total AS quote_total,
q.created_at AS quote_created_at
"""

_PROJECT_FROM = """
FROM projects p
LEFT JOIN users ru ON ru.id = p.responsible_user_id
LEFT JOIN users cu ON cu.id = p.created_by
LEFT JOIN quotes q ON q.id = p.quote_id
"""


class ProjectCreateIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    customer_name: str = ""
    customer_phone: str = ""
    customer_address: str = ""
    responsible_user_id: Optional[int] = None
    quote_id: Optional[int] = None
    amount: Optional[float] = None
    due_date: Optional[str] = None


class ProjectStatusIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str = ""


class ProjectQuoteIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    quote_id: Optional[int] = None


def project_meta(user) -> dict[str, Any]:
    return {
        "statuses": [{"id": code, "label": label} for code, label in PROJECT_STATUSES],
        "can_create": user["role"] != "accountant",
        "scope": _scope_name(user),
    }


def normalize_status_filter(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    code = raw.strip().lower()
    if not code:
        return None
    if code not in STATUS_IDS:
        allowed = ", ".join(code for code, _label in PROJECT_STATUSES)
        raise HTTPException(status_code=400, detail=f"Неизвестный статус. Допустимо: {allowed}")
    return code


def normalize_customer_filter(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) > 200:
        raise HTTPException(status_code=400, detail="Фильтр клиента не длиннее 200 символов")
    return text


def list_projects(conn, user, *, status: Optional[str], customer: Optional[str], limit: int) -> list[dict[str, Any]]:
    where, args = _scope_clause(user)
    clauses = [where]
    if status:
        clauses.append("p.status=?")
        args.append(status)
    # A client search scans the newest rows in scope, then matches the name
    # and phone without SQLite's ASCII-only lower(). 500 is enough for one org.
    fetch_limit = 500 if customer else limit
    rows = conn.execute(
        f"""SELECT {_PROJECT_COLUMNS} {_PROJECT_FROM}
            WHERE {' AND '.join(clauses)}
            ORDER BY p.updated_at DESC, p.id DESC
            LIMIT ?""",
        [*args, fetch_limit],
    ).fetchall()
    if customer:
        needle = customer.casefold()
        rows = [row for row in rows if needle in _customer_hay(row)][:limit]
    return [project_payload(row, user) for row in rows]


def load_project(conn, user, project_id: int, *, writable: bool = False) -> dict[str, Any]:
    row = _fetch_project(conn, user, project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Проект не найден")
    if writable and not can_write_project(user, row):
        raise HTTPException(status_code=403, detail="Недостаточно прав, чтобы изменить проект")
    return project_payload(row, user)


def create_project(conn, user, body: ProjectCreateIn) -> dict[str, Any]:
    if user["role"] == "accountant":
        raise HTTPException(status_code=403, detail="Бухгалтер может только смотреть проекты")
    name = _require_text(body.customer_name, 200, "имя клиента")
    phone = _optional_text(body.customer_phone, 40, "Телефон")
    address = _optional_text(body.customer_address, 400, "Адрес")
    amount = _clean_amount(body.amount)
    due = _clean_due(body.due_date)
    responsible_id = _resolve_responsible(conn, user, body.responsible_user_id)
    if body.quote_id is not None:
        _assert_quote_readable(conn, user, body.quote_id)
    cur = conn.execute(
        """INSERT INTO projects (
             org_id, customer_name, customer_phone, customer_address,
             responsible_user_id, created_by, status, quote_id,
             amount, currency, due_date
           ) VALUES (?,?,?,?,?,?, 'created', ?,?, 'RUB', ?)""",
        (
            user["org_id"],
            name,
            phone,
            address,
            responsible_id,
            user["id"],
            body.quote_id,
            amount,
            due,
        ),
    )
    return load_project(conn, user, int(cur.lastrowid))


def set_project_status(conn, user, project_id: int, raw_status: str) -> tuple[dict[str, Any], Optional[str]]:
    """Return the project and the previous status. Previous is None when unchanged."""
    code = (raw_status or "").strip().lower()
    if code not in STATUS_IDS:
        allowed = ", ".join(item for item, _label in PROJECT_STATUSES)
        raise HTTPException(status_code=400, detail=f"Неизвестный статус. Допустимо: {allowed}")
    current = load_project(conn, user, project_id, writable=True)
    previous = current["status"]
    if previous == code:
        return current, None
    closed_sql = "COALESCE(closed_at, datetime('now'))" if code in TERMINAL_STATUSES else "NULL"
    conn.execute(
        f"""UPDATE projects
            SET status=?, updated_at=datetime('now'), closed_at={closed_sql}
            WHERE id=? AND org_id=?""",
        (code, project_id, user["org_id"]),
    )
    return load_project(conn, user, project_id), previous


def bind_quote(conn, user, project_id: int, quote_id: Optional[int]) -> dict[str, Any]:
    """Attach a quote the caller can read, or clear the link when quote_id is None."""
    load_project(conn, user, project_id, writable=True)
    if quote_id is not None:
        _assert_quote_readable(conn, user, quote_id)
    conn.execute(
        "UPDATE projects SET quote_id=?, updated_at=datetime('now') WHERE id=? AND org_id=?",
        (quote_id, project_id, user["org_id"]),
    )
    return load_project(conn, user, project_id)


def project_payload(row, user) -> dict[str, Any]:
    status = row["status"]
    amount = row["amount"]
    return {
        "id": row["id"],
        "customer_name": row["customer_name"],
        "customer_phone": row["customer_phone"] or "",
        "customer_address": row["customer_address"] or "",
        "responsible_user_id": row["responsible_user_id"],
        "responsible_name": row["responsible_name"] or "",
        "responsible_email": row["responsible_email"] or "",
        "created_by": row["created_by"],
        "created_by_name": row["created_by_name"] or "",
        "created_by_email": row["created_by_email"] or "",
        "status": status,
        "status_label": STATUS_LABEL.get(status, status),
        "quote_id": row["quote_id"],
        "quote": _quote_brief(row, user),
        "amount": None if amount is None else round_money(float(amount)),
        "currency": row["currency"] or "RUB",
        "due_date": row["due_date"],
        "closed_at": row["closed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "can_write": can_write_project(user, row),
    }


def can_write_project(user, row) -> bool:
    if row["org_id"] != user["org_id"] or user["role"] == "accountant":
        return False
    if user["role"] in PRIVILEGED:
        return True
    if user["role"] == "manager":
        return row["responsible_user_id"] == user["id"] or row["created_by"] == user["id"]
    return row["responsible_user_id"] == user["id"]


def _scope_name(user) -> str:
    if user["role"] in PRIVILEGED:
        return "org"
    if user["role"] == "accountant":
        return "org_read"
    if user["role"] == "manager":
        return "manager"
    return "assigned"


def _scope_clause(user) -> tuple[str, list]:
    org_id = user["org_id"]
    if user["role"] in READ_ORG_ROLES:
        return "p.org_id=?", [org_id]
    if user["role"] == "manager":
        return (
            "p.org_id=? AND (p.responsible_user_id=? OR p.created_by=?)",
            [org_id, user["id"], user["id"]],
        )
    return "p.org_id=? AND p.responsible_user_id=?", [org_id, user["id"]]


def _fetch_project(conn, user, project_id: int):
    where, args = _scope_clause(user)
    return conn.execute(
        f"SELECT {_PROJECT_COLUMNS} {_PROJECT_FROM} WHERE p.id=? AND {where}",
        [project_id, *args],
    ).fetchone()


def _customer_hay(row) -> str:
    return f"{row['customer_name'] or ''} {row['customer_phone'] or ''}".casefold()


def _quote_brief(row, user) -> Optional[dict[str, Any]]:
    quote_id = row["quote_id"]
    if not quote_id:
        return None
    quote_id = int(quote_id)
    brief: dict[str, Any] = {
        "id": quote_id,
        "pdf_url": f"/api/quotes/{quote_id}/pdf",
        "readable": False,
    }
    if row["quote_ref_id"] is None or row["quote_org_id"] != user["org_id"]:
        return brief
    if user["role"] not in PRIVILEGED and row["quote_user_id"] != user["id"]:
        return brief
    fence = row["quote_fence_type"] or ""
    show_price = user["role"] in PRICE_ROLES
    glass_sum = round_money(float(row["quote_glass_sum"] or 0))
    total = round_money(float(row["quote_total"] or 0)) if show_price else glass_sum
    brief.update(
        {
            "readable": True,
            "fence_type": fence,
            "fence_label": FENCE_LABEL.get(fence, fence),
            "width_mm": row["quote_width_mm"],
            "height_mm": row["quote_height_mm"],
            "depth_mm": row["quote_depth_mm"],
            "total": total,
            "currency": "RUB",
            "created_at": row["quote_created_at"],
        }
    )
    return brief


def _assert_quote_readable(conn, user, quote_id: int):
    if quote_id < 1:
        raise HTTPException(status_code=400, detail="Некорректное КП")
    row = conn.execute(
        "SELECT id, org_id, user_id FROM quotes WHERE id=?",
        (quote_id,),
    ).fetchone()
    if not row or row["org_id"] != user["org_id"]:
        raise HTTPException(status_code=404, detail="КП не найдено")
    if user["role"] not in PRIVILEGED and row["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="КП не найдено")
    return row


def _resolve_responsible(conn, user, requested: Optional[int]) -> int:
    if requested is None or requested == user["id"]:
        return user["id"]
    if requested < 1:
        raise HTTPException(status_code=400, detail="Некорректный ответственный")
    if user["role"] not in PRIVILEGED:
        raise HTTPException(status_code=403, detail="Назначать другого ответственного может админ или хозяин")
    found = conn.execute(
        "SELECT id FROM users WHERE id=? AND org_id=? AND is_active=1",
        (requested, user["org_id"]),
    ).fetchone()
    if not found:
        raise HTTPException(status_code=400, detail="Ответственный не найден в организации")
    return requested


def _require_text(value: str, limit: int, label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail=f"Укажите {label}")
    if len(text) > limit:
        raise HTTPException(status_code=400, detail=f"Поле «{label}» не длиннее {limit} символов")
    return text


def _optional_text(value: str, limit: int, label: str) -> str:
    text = (value or "").strip()
    if len(text) > limit:
        raise HTTPException(status_code=400, detail=f"{label}: не длиннее {limit} символов")
    return text


def _clean_amount(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(status_code=400, detail="Некорректная сумма")
    if math.isnan(value) or math.isinf(value) or value < 0 or value > 1_000_000_000_000:
        raise HTTPException(status_code=400, detail="Сумма от 0 до 1 000 000 000 000")
    return round_money(float(value))


def _clean_due(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Срок в формате ГГГГ-ММ-ДД")
    return text
