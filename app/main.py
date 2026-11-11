"""Dushevye / Stanok — auth API (FastAPI + SQLite)."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .db import get_db, init_db
from .projects import (
    ProjectCreateIn,
    ProjectQuoteIn,
    ProjectStatusIn,
    STATUS_LABEL,
    bind_quote,
    create_project,
    list_projects,
    load_project,
    normalize_customer_filter,
    normalize_status_filter,
    project_meta,
    set_project_status,
)
from .catalog_compat import (
    CompatError,
    evaluate_compat,
    apply_compat,
    create_rule,
    delete_rule,
    list_rules,
    load_compat_rules,
)
from .config_rules import list_templates, merge_selected_parts, validate_config
from .org_access import (
    OrgAccessError,
    apply_org_access,
    assert_bom_size,
    assert_catalog_page,
    assert_export_allowed,
    assert_no_export_query,
    assert_not_suspended,
    assert_rules_allowed,
    assert_users_allowed,
    attach_access,
    demo_restricted,
    load_user,
    public_access,
    read_org_access,
)
from .quotes import (
    QuoteIn,
    compose_quote,
    fetch_parts,
    glass_priced,
    normalize_quote,
    parts_for_rules,
    present_quote,
    public_estimate,
    quote_is_valid,
    render_quote_pdf,
    validation_error,
)
from .stock import AUDIT_ACTION, StockMoveIn, apply_movement, list_stock
from .versions import list_versions, record_version
from .security import (
    CSRF_HEADER,
    CSRF_SESSION_KEY,
    PRIVILEGED,
    ROLE_LABELS_RU,
    ROLE_PAGE,
    ROLES,
    check_login_rate_limit,
    check_public_rate_limit,
    clear_login_rate,
    csrf_ok,
    generate_password,
    hash_password,
    login_rate_key,
    new_csrf_token,
    normalize_ip,
    verify_password,
)
from .vitrine import VitrineChatIn, VitrineLeadIn, answer_chat, create_vitrine_lead

SECRET = os.environ.get("DUSHEVYE_SESSION_SECRET") or secrets.token_hex(32)
COOKIE_NAME = "dushevye_session"
# Secure flag: only when DUSHEVYE_COOKIE_SECURE=1/true/yes.
# Site is still served over plain HTTP by IP; enabling Secure would break login until HTTPS.
# SessionMiddleware always sets HttpOnly; we always use SameSite=Strict.
COOKIE_SECURE = os.environ.get("DUSHEVYE_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes")

app = FastAPI(title="Dushevye Auth API", version="0.1.0", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET,
    session_cookie=COOKIE_NAME,
    same_site="strict",
    https_only=COOKIE_SECURE,
    max_age=60 * 60 * 24 * 14,
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


def user_public(row) -> dict[str, Any]:
    if isinstance(row, dict):
        mode = row.get("access_mode") or "full"
        suspended = bool(row.get("org_suspended"))
    else:
        mode = "full"
        suspended = False
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"] or "",
        "role": row["role"],
        "role_label": ROLE_LABELS_RU.get(row["role"], row["role"]),
        "org_id": row["org_id"],
        "cabinet": ROLE_PAGE.get(row["role"], "index.html"),
        "must_change_password": bool(row["must_change_password"]),
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "org_access": public_access({"access_mode": mode, "suspended": suspended}),
    }


def enforce_access(check, *args) -> None:
    try:
        check(*args)
    except OrgAccessError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


def audit(conn, org_id: int, actor_id: Optional[int], action: str, *, target_type=None, target_id=None, detail=None):
    conn.execute(
        "INSERT INTO audit_log (org_id, actor_id, action, target_type, target_id, detail) VALUES (?,?,?,?,?,?)",
        (org_id, actor_id, action, target_type, target_id, detail),
    )


def current_user(request: Request):
    """Load the user and the organization's access flags from the database.

    Flags are not stored in the session cookie, so a suspend or demo change
    applies on the next protected request of an already issued session.
    """
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не авторизован")
    with get_db() as conn:
        user = load_user(conn, int(uid))
    if not user:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия устарела")
    return user


def require_active_org(user=Depends(current_user)):
    """Block a suspended organization. System admin (role admin) stays allowed."""
    enforce_access(assert_not_suspended, user)
    return user


def require_privileged(user=Depends(require_active_org)):
    if user["role"] not in PRIVILEGED:
        raise HTTPException(status_code=403, detail="Только админ или хозяин")
    return user


def require_system_admin(user=Depends(current_user)):
    """Manual demo/suspend. Owner is not a system admin, even in the same org."""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Только системный админ")
    return user


def client_ip(request: Request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = new_csrf_token()
        request.session[CSRF_SESSION_KEY] = token
    return token


def require_csrf(request: Request) -> None:
    """Reject mutating requests without a matching session CSRF token (403)."""
    expected = request.session.get(CSRF_SESSION_KEY)
    provided = request.headers.get(CSRF_HEADER) or request.headers.get("x-csrf-token")
    if not csrf_ok(expected, provided):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


class LoginIn(BaseModel):
    email: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class InviteIn(BaseModel):
    email: str
    role: str
    name: str = ""


class RolePatchIn(BaseModel):
    role: str


class OrgAccessIn(BaseModel):
    """Only flags. org_id in the body is ignored so one tenant cannot target another."""

    model_config = {"extra": "ignore"}
    access_mode: Optional[str] = None
    suspended: Optional[bool] = None


class CompatRuleIn(BaseModel):
    """A catalog allow/deny row. Points at a group, a name fragment, or a part id."""

    model_config = {"extra": "ignore"}
    effect: str = ""
    group_id: str = ""
    group_name: str = ""
    part_id: Optional[int] = None
    name_like: str = ""
    glass_mm: Optional[int] = None
    template_id: str = ""
    template_kind: str = "any"
    reason: str = ""


class ConfigValidateIn(BaseModel):
    """template_id, opening sizes, glass, and selected catalog parts."""

    model_config = {"extra": "ignore"}
    template_id: str = ""
    width_mm: Optional[int] = None
    height_mm: Optional[int] = None
    depth_mm: Optional[int] = None
    depth2_mm: Optional[int] = None
    angle_deg: Optional[int] = None
    doors: Optional[int] = None
    glass_thick_mm: Optional[int] = None
    glass_kind: str = ""
    part_ids: list[int] = Field(default_factory=list)
    items: list[Any] = Field(default_factory=list)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "dushevye-auth"}


@app.get("/api/auth/csrf")
def get_csrf(request: Request):
    """Issue / refresh CSRF token bound to the session cookie."""
    token = ensure_csrf_token(request)
    return {"csrfToken": token}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request):
    require_csrf(request)
    email = body.email.strip().lower()
    key = login_rate_key(client_ip(request), email)
    try:
        check_login_rate_limit(key)
    except ValueError:
        raise HTTPException(
            status_code=429,
            detail="Слишком много попыток входа. Подождите 15 минут.",
        )
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE lower(email)=? AND is_active=1", (email,)
        ).fetchone()
        if not row or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Неверный email или пароль")
        signed_in = attach_access(row, read_org_access(conn, int(row["org_id"])))
        # Keep existing CSRF token across login so the SPA does not need a second fetch.
        csrf = request.session.get(CSRF_SESSION_KEY)
        request.session.clear()
        if csrf:
            request.session[CSRF_SESSION_KEY] = csrf
        else:
            ensure_csrf_token(request)
        request.session["user_id"] = signed_in["id"]
        request.session["org_id"] = signed_in["org_id"]
        audit(conn, signed_in["org_id"], signed_in["id"], "login", detail=email)
        clear_login_rate(key)
        return {"user": user_public(signed_in)}


@app.post("/api/auth/logout")
def logout(request: Request):
    require_csrf(request)
    uid = request.session.get("user_id")
    org_id = request.session.get("org_id")
    if uid and org_id:
        with get_db() as conn:
            audit(conn, org_id, uid, "logout")
    csrf = request.session.get(CSRF_SESSION_KEY)
    request.session.clear()
    if csrf:
        request.session[CSRF_SESSION_KEY] = csrf
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return {"user": user_public(user)}


@app.post("/api/auth/change-password")
def change_password(body: ChangePasswordIn, request: Request, user=Depends(current_user)):
    require_csrf(request)
    if not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Текущий пароль неверный")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Новый пароль минимум 8 символов")
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash=?, must_change_password=0, updated_at=datetime('now') WHERE id=?",
            (hash_password(body.new_password), user["id"]),
        )
        audit(conn, user["org_id"], user["id"], "change_password", target_type="user", target_id=user["id"])
    return {"ok": True}


@app.get("/api/users")
def list_users(user=Depends(require_privileged)):
    enforce_access(assert_users_allowed, user)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE org_id=? ORDER BY id", (user["org_id"],)
        ).fetchall()
        access = read_org_access(conn, int(user["org_id"]))
    return {"users": [user_public(attach_access(r, access)) for r in rows]}


@app.post("/api/users", status_code=201)
def invite_user(body: InviteIn, request: Request, user=Depends(require_privileged)):
    require_csrf(request)
    enforce_access(assert_users_allowed, user)
    role = body.role.strip().lower()
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"Неизвестная роль. Допустимо: {', '.join(ROLES)}")
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Некорректный email")
    name = (body.name or "").strip() or email.split("@")[0]
    temp = generate_password(12)
    with get_db() as conn:
        exists = conn.execute(
            "SELECT id FROM users WHERE org_id=? AND lower(email)=?",
            (user["org_id"], email),
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="Такой email уже есть")
        cur = conn.execute(
            """INSERT INTO users (org_id, email, name, role, password_hash, must_change_password)
               VALUES (?,?,?,?,?,1)""",
            (user["org_id"], email, name, role, hash_password(temp)),
        )
        new_id = cur.lastrowid
        audit(
            conn,
            user["org_id"],
            user["id"],
            "invite_user",
            target_type="user",
            target_id=new_id,
            detail=f"{email} role={role}",
        )
        # SMTP отложен: пароль один раз в ответе
        print(f"[dushevye] email skipped for {email}; temp password issued once in API response", flush=True)
        row = conn.execute("SELECT * FROM users WHERE id=?", (new_id,)).fetchone()
        access = read_org_access(conn, int(user["org_id"]))
        return {
            "user": user_public(attach_access(row, access)),
            "tempPassword": temp,
            "emailSent": False,
            "note": "Письмо не отправлялось (SMTP пока выключен). Сохраните пароль — больше не покажем.",
        }


@app.patch("/api/users/{user_id}/role")
def patch_role(user_id: int, body: RolePatchIn, request: Request, user=Depends(require_privileged)):
    require_csrf(request)
    enforce_access(assert_users_allowed, user)
    role = body.role.strip().lower()
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="Неизвестная роль")
    with get_db() as conn:
        target = conn.execute(
            "SELECT * FROM users WHERE id=? AND org_id=?", (user_id, user["org_id"])
        ).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        access = read_org_access(conn, int(user["org_id"]))
        old = target["role"]
        if old == role:
            return {"user": user_public(attach_access(target, access))}
        # не даём снять последнего owner/admin сами себе критично — мягко разрешаем, но логируем
        conn.execute(
            "UPDATE users SET role=?, updated_at=datetime('now') WHERE id=?",
            (role, user_id),
        )
        audit(
            conn,
            user["org_id"],
            user["id"],
            "change_role",
            target_type="user",
            target_id=user_id,
            detail=f"{old} -> {role}",
        )
        record_version(
            conn,
            user["org_id"],
            user["id"],
            "user",
            user_id,
            "change_role",
            {"role": old, "email": target["email"], "name": target["name"] or ""},
            {"role": role, "email": target["email"], "name": target["name"] or ""},
        )
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return {"user": user_public(attach_access(row, access))}


@app.get("/api/org/access")
def get_org_access(user=Depends(current_user)):
    """Own organization only. Suspended users can still read why the cabinet stopped."""
    with get_db() as conn:
        access = read_org_access(conn, int(user["org_id"]))
    return {"org_id": int(user["org_id"]), **public_access(access)}


@app.patch("/api/org/access")
def patch_org_access(body: OrgAccessIn, request: Request, user=Depends(require_system_admin)):
    """Manual demo or suspend for the admin's own organization. No timer, no other tenant."""
    require_csrf(request)
    if body.access_mode is None and body.suspended is None:
        raise HTTPException(status_code=400, detail="Укажите access_mode или suspended")
    try:
        with get_db() as conn:
            result = apply_org_access(
                conn,
                int(user["org_id"]),
                int(user["id"]),
                access_mode=body.access_mode,
                suspended=body.suspended,
            )
    except OrgAccessError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return {"org_id": int(user["org_id"]), **public_access(result), "changed": result["changed"]}


@app.get("/api/audit")
def get_audit(request: Request, limit: int = 100, user=Depends(require_active_org)):
    enforce_access(assert_no_export_query, user, request.query_params)
    limit = max(1, min(limit, 500))
    with get_db() as conn:
        if user["role"] in PRIVILEGED:
            rows = conn.execute(
                """SELECT a.*, u.email AS actor_email, u.name AS actor_name
                   FROM audit_log a
                   LEFT JOIN users u ON u.id = a.actor_id
                   WHERE a.org_id=?
                   ORDER BY a.id DESC LIMIT ?""",
                (user["org_id"], limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT a.*, u.email AS actor_email, u.name AS actor_name
                   FROM audit_log a
                   LEFT JOIN users u ON u.id = a.actor_id
                   WHERE a.org_id=? AND a.actor_id=?
                   ORDER BY a.id DESC LIMIT ?""",
                (user["org_id"], user["id"], limit),
            ).fetchall()
    items = []
    for r in rows:
        items.append(
            {
                "id": r["id"],
                "action": r["action"],
                "detail": r["detail"],
                "target_type": r["target_type"],
                "target_id": r["target_id"],
                "actor_id": r["actor_id"],
                "actor_email": r["actor_email"],
                "actor_name": r["actor_name"],
                "created_at": r["created_at"],
            }
        )
    return {"items": items}


@app.get("/api/versions")
def get_versions(
    request: Request,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    limit: int = 50,
    user=Depends(require_active_org),
):
    """Before/after history. Admin and owner see the org; other roles see their own rows.

    GET does not require a CSRF token. /api/audit is unchanged.
    """
    enforce_access(assert_no_export_query, user, request.query_params)
    with get_db() as conn:
        return list_versions(
            conn,
            user,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
        )


@app.get("/api/meta/roles")
def meta_roles():
    return {
        "roles": [
            {"id": r, "label": ROLE_LABELS_RU[r], "cabinet": ROLE_PAGE[r]} for r in ROLES
        ]
    }


# --- catalog (parts from catalog.db) ---
CATALOG_DB = Path(os.environ.get("DUSHEVYE_CATALOG_DB", "/var/www/dushevye/data/catalog.db"))
PRICE_ROLES = frozenset({"admin", "owner", "accountant"})


def _catalog_conn():
    if not CATALOG_DB.exists():
        raise HTTPException(status_code=503, detail="Каталог ещё не загружен")
    conn = sqlite3.connect(str(CATALOG_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _part_public(row, *, show_price: bool) -> dict:
    params = {}
    raw = row["params_json"] if "params_json" in row.keys() else None
    if raw:
        try:
            params = json.loads(raw) or {}
        except Exception:
            params = {}
    return {
        "id": row["id"],
        "group_id": row["group_id"],
        "group_title": row["group_title"] if "group_title" in row.keys() else None,
        "source": row["source"],
        "sku": row["sku"],
        "name": row["name"],
        "price": row["price"] if show_price else None,
        "currency": row["currency"] if show_price else None,
        "show_price": show_price,
        "url": row["url"],
        "in_stock": None if row["in_stock"] is None else bool(row["in_stock"]),
        "glass_mm": row["glass_mm"],
        "image_url": row["image_url"],
        "material": row["material"],
        "finish": row["finish"],
        "params": params,
    }


@app.get("/api/catalog")
def catalog_list(
    request: Request,
    group: Optional[str] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    user=Depends(require_active_org),
):
    """List parts with cross-filtered manufacturer (source) and group counts."""
    enforce_access(assert_no_export_query, user, request.query_params)
    enforce_access(assert_catalog_page, user, limit, offset)
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    show_price = _show_price(user, catalog=True)
    group = (group or "").strip() or None
    source = (source or "").strip().lower() or None
    q = (q or "").strip() or None

    def _q_clause(alias: str = "p"):
        if not q:
            return "", []
        qq = f"%{q}%"
        return (
            f" AND ({alias}.name LIKE ? OR {alias}.sku LIKE ?"
            f" OR IFNULL({alias}.material,'') LIKE ? OR IFNULL({alias}.finish,'') LIKE ?)",
            [qq, qq, qq, qq],
        )

    where = ["1=1"]
    args: list = []
    if group:
        where.append("p.group_id=?")
        args.append(group)
    if source:
        where.append("p.source=?")
        args.append(source)
    q_sql, q_args = _q_clause("p")
    if q_sql:
        # strip leading AND from helper; append as full clause piece
        where.append(q_sql.lstrip(" AND "))
        args.extend(q_args)
    sql_where = " AND ".join(where)

    with _catalog_conn() as conn:
        # Groups: filter by manufacturer + search (not by selected group)
        g_where = ["1=1"]
        g_args: list = []
        if source:
            g_where.append("p.source=?")
            g_args.append(source)
        gq, gqa = _q_clause("p")
        if gq:
            g_where.append(gq.lstrip(" AND "))
            g_args.extend(gqa)
        g_sql = " AND ".join(g_where)
        groups = [
            {"id": r["id"], "title_ru": r["title_ru"], "sort": r["sort"], "count": r["cnt"]}
            for r in conn.execute(
                f"""SELECT g.id, g.title_ru, g.sort, COUNT(p.id) AS cnt
                    FROM part_groups g
                    LEFT JOIN parts p ON p.group_id=g.id AND {g_sql}
                    GROUP BY g.id
                    ORDER BY g.sort""",
                g_args,
            ).fetchall()
        ]

        # Manufacturers (sources): filter by group + search (not by selected source)
        s_where = ["1=1"]
        s_args: list = []
        if group:
            s_where.append("p.group_id=?")
            s_args.append(group)
        sq, sqa = _q_clause("p")
        if sq:
            s_where.append(sq.lstrip(" AND "))
            s_args.extend(sqa)
        s_sql = " AND ".join(s_where)
        sources = [
            {"id": r["source"], "count": r["cnt"]}
            for r in conn.execute(
                f"""SELECT p.source, COUNT(*) AS cnt
                    FROM parts p
                    WHERE {s_sql}
                    GROUP BY p.source
                    ORDER BY p.source""",
                s_args,
            ).fetchall()
        ]

        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM parts p WHERE {sql_where}", args
        ).fetchone()["c"]
        rows = conn.execute(
            f"""SELECT p.*, g.title_ru AS group_title
                FROM parts p
                LEFT JOIN part_groups g ON g.id=p.group_id
                WHERE {sql_where}
                ORDER BY g.sort, p.source, p.name
                LIMIT ? OFFSET ?""",
            [*args, limit, offset],
        ).fetchall()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "show_price": show_price,
        "groups": groups,
        "sources": sources,
        "items": [_part_public(r, show_price=show_price) for r in rows],
    }


def _compat_glass(glass_mm: Optional[int]) -> Optional[int]:
    if glass_mm is None:
        return None
    if glass_mm < 4 or glass_mm > 19:
        raise HTTPException(status_code=400, detail="Толщина стекла: целое число от 4 до 19 мм.")
    return glass_mm


@app.get("/api/catalog/compat")
def catalog_compat(
    glass_mm: Optional[int] = None,
    template_id: Optional[str] = None,
    user=Depends(require_active_org),
):
    """Allow/deny hints for the current glass and template. Any signed-in role."""
    thick = _compat_glass(glass_mm)
    template = (template_id or "").strip()
    rules = load_compat_rules(int(user["org_id"]))
    return evaluate_compat(thick, template, rules)


@app.get("/api/catalog/compat/rules")
def catalog_compat_rules_list(user=Depends(require_privileged)):
    """Seed matrix plus this org's own rules. Manager is not allowed."""
    enforce_access(assert_rules_allowed, user)
    return {"items": list_rules(int(user["org_id"]))}


@app.post("/api/catalog/compat/rules", status_code=201)
def catalog_compat_rules_create(body: CompatRuleIn, request: Request, user=Depends(require_privileged)):
    require_csrf(request)
    enforce_access(assert_rules_allowed, user)
    try:
        rule = create_rule(int(user["org_id"]), int(user["id"]), body.model_dump())
    except CompatError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    with get_db() as conn:
        audit(
            conn,
            int(user["org_id"]),
            int(user["id"]),
            "catalog_compat_create",
            target_type="catalog_compat_rule",
            target_id=rule["id"],
            detail=(
                f"{rule['effect']} group={rule['group_id'] or '-'} "
                f"part={rule['part_id'] or '-'} glass={rule['glass_mm'] or '-'} "
                f"{rule['reason']}"
            )[:500],
        )
    return {"rule": rule}


@app.delete("/api/catalog/compat/rules/{rule_id}")
def catalog_compat_rules_delete(rule_id: int, request: Request, user=Depends(require_privileged)):
    require_csrf(request)
    enforce_access(assert_rules_allowed, user)
    try:
        result = delete_rule(int(user["org_id"]), rule_id)
    except CompatError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    with get_db() as conn:
        audit(
            conn,
            int(user["org_id"]),
            int(user["id"]),
            "catalog_compat_delete",
            target_type="catalog_compat_rule",
            target_id=rule_id,
            detail=f"deleted rule {rule_id}",
        )
    return result


@app.get("/api/catalog/{part_id}")
def catalog_one(request: Request, part_id: int, user=Depends(require_active_org)):
    enforce_access(assert_no_export_query, user, request.query_params)
    show_price = _show_price(user, catalog=True)
    with _catalog_conn() as conn:
        row = conn.execute(
            """SELECT p.*, g.title_ru AS group_title
               FROM parts p
               LEFT JOIN part_groups g ON g.id=p.group_id
               WHERE p.id=?""",
            (part_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    return {"item": _part_public(row, show_price=show_price)}


def _show_price(user, *, catalog: bool = False) -> bool:
    """Purchase prices follow the role matrix.

    In demo mode the catalog list is not a price dump: non-admins, including
    the owner, get items without purchase prices. A quote they actually build
    still prices only the lines on that quote.
    """
    if user["role"] not in PRICE_ROLES:
        return False
    if catalog and demo_restricted(user):
        return False
    return True


def _load_quote(conn, quote_id: int, user, *, with_items: bool):
    row = conn.execute(
        """SELECT q.*, u.name AS author_name, u.email AS author_email
           FROM quotes q
           JOIN users u ON u.id = q.user_id
           WHERE q.id=? AND q.org_id=?""",
        (quote_id, user["org_id"]),
    ).fetchone()
    if not row or (user["role"] not in PRIVILEGED and row["user_id"] != user["id"]):
        raise HTTPException(status_code=404, detail="КП не найдено")
    items = []
    if with_items:
        items = conn.execute(
            "SELECT * FROM quote_items WHERE quote_id=? ORDER BY line_no, id",
            (quote_id,),
        ).fetchall()
    return row, items


def _allow_pdf(user) -> bool:
    return not demo_restricted(user)


@app.get("/api/quotes")
def list_quotes(request: Request, limit: int = 50, user=Depends(require_active_org)):
    """Own drafts. Admin and owner see every quote in the org; other roles see their own."""
    enforce_access(assert_no_export_query, user, request.query_params)
    limit = max(1, min(limit, 200))
    show_price = _show_price(user)
    allow_pdf = _allow_pdf(user)
    with get_db() as conn:
        if user["role"] in PRIVILEGED:
            rows = conn.execute(
                """SELECT q.*, u.name AS author_name, u.email AS author_email,
                          (SELECT COUNT(*) FROM quote_items i
                           WHERE i.quote_id=q.id AND i.kind='hardware') AS hardware_count
                   FROM quotes q
                   JOIN users u ON u.id = q.user_id
                   WHERE q.org_id=?
                   ORDER BY q.id DESC
                   LIMIT ?""",
                (user["org_id"], limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT q.*, u.name AS author_name, u.email AS author_email,
                          (SELECT COUNT(*) FROM quote_items i
                           WHERE i.quote_id=q.id AND i.kind='hardware') AS hardware_count
                   FROM quotes q
                   JOIN users u ON u.id = q.user_id
                   WHERE q.org_id=? AND q.user_id=?
                   ORDER BY q.id DESC
                   LIMIT ?""",
                (user["org_id"], user["id"], limit),
            ).fetchall()
        items = [
            present_quote(row, [], show_price=show_price, include_lines=False, allow_pdf=allow_pdf)
            for row in rows
        ]
    return {
        "items": items,
        "show_price": show_price,
        "can_see_org": user["role"] in PRIVILEGED,
    }


@app.get("/api/quotes/{quote_id}")
def get_quote(request: Request, quote_id: int, user=Depends(require_active_org)):
    enforce_access(assert_no_export_query, user, request.query_params)
    show_price = _show_price(user)
    with get_db() as conn:
        row, items = _load_quote(conn, quote_id, user, with_items=True)
        quote = present_quote(
            row, items, show_price=show_price, include_lines=True, allow_pdf=_allow_pdf(user)
        )
    return {"quote": quote}


@app.get("/api/quotes/{quote_id}/pdf")
def get_quote_pdf(
    request: Request,
    quote_id: int,
    download: bool = False,
    user=Depends(require_active_org),
):
    enforce_access(assert_export_allowed, user)
    enforce_access(assert_no_export_query, user, request.query_params)
    show_price = _show_price(user)
    with get_db() as conn:
        row, items = _load_quote(conn, quote_id, user, with_items=True)
        quote = present_quote(row, items, show_price=show_price, include_lines=True)
    try:
        pdf_bytes = render_quote_pdf(quote)
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="Не удалось собрать PDF: на сервере нет шрифта с кириллицей (fonts-dejavu-core)",
        )
    filename = f"kp-{quote_id}.pdf"
    disposition = "attachment" if download else "inline"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


def _evaluate_config(payload: dict, items: list[dict], org_id: int) -> dict:
    part_message = None
    rows = {}
    if items:
        try:
            rows = fetch_parts([item["id"] for item in items])
        except HTTPException as exc:
            if exc.status_code != 400:
                raise
            part_message = exc.detail if isinstance(exc.detail, str) else "В каталоге нет позиций"
    prepared = parts_for_rules(rows, items) if rows else []
    result = validate_config(payload, prepared)
    result = apply_compat(result, payload, prepared, load_compat_rules(org_id))
    if part_message:
        result["errors"].append({"code": "part_id", "message": str(part_message)})
        result["ok"] = False
    return result


@app.get("/api/templates")
def templates_list():
    """Construction templates. Static seed, no session required."""
    return {"items": list_templates()}


@app.post("/api/config/estimate")
def config_estimate(body: QuoteIn, request: Request, user=Depends(require_active_org)):
    """Glass and hardware totals. The rate table is not part of the response.

    A rule error still includes the glass figures. The screen used to calculate
    those locally even when hardware could not be saved.
    """
    require_csrf(request)
    enforce_access(assert_bom_size, user, len(body.items))
    draft = normalize_quote(body)
    enforce_access(assert_bom_size, user, len(draft["items"]))
    checked = None
    parts: dict = {}
    if draft["items"]:
        try:
            parts = fetch_parts([item["id"] for item in draft["items"]])
        except HTTPException as exc:
            if exc.status_code != 400:
                raise
            message = exc.detail if isinstance(exc.detail, str) else "В каталоге нет позиций"
            checked = {
                "ok": False,
                "errors": [{"code": "part_id", "message": message}],
                "warnings": [],
            }
            parts = {}
            draft = {**draft, "items": []}
    if draft.get("template_id"):
        validation = quote_is_valid(
            draft,
            parts_for_rules(parts, draft["items"]) if draft["items"] else [],
            org_id=int(user["org_id"]),
        )
        if checked is None:
            checked = validation
        else:
            checked = {
                "ok": False,
                "errors": list(validation["errors"]) + list(checked["errors"]),
                "warnings": list(validation["warnings"]) + list(checked["warnings"]),
            }
    if not glass_priced(draft):
        return {
            "ok": False,
            "errors": (checked or {}).get("errors") or [
                {"code": "glass_thickness", "message": "Для этой толщины нет ставки стекла."}
            ],
            "warnings": (checked or {}).get("warnings") or [],
        }
    payload = public_estimate(compose_quote(draft, parts), show_price=_show_price(user))
    if checked is not None:
        payload["warnings"] = checked.get("warnings") or []
        if not checked["ok"]:
            payload["ok"] = False
            payload["errors"] = checked["errors"]
    return payload


@app.post("/api/config/validate")
def config_validate(body: ConfigValidateIn, request: Request, user=Depends(require_active_org)):
    """Check a draft against the template. Does not save a quote."""
    require_csrf(request)
    items, item_errors = merge_selected_parts(body.items, body.part_ids)
    payload = {
        "template_id": body.template_id,
        "width_mm": body.width_mm,
        "height_mm": body.height_mm,
        "depth_mm": body.depth_mm,
        "depth2_mm": body.depth2_mm,
        "angle_deg": body.angle_deg,
        "doors": body.doors,
        "glass_thick_mm": body.glass_thick_mm,
        "glass_kind": body.glass_kind,
    }
    result = _evaluate_config(payload, items, int(user["org_id"]))
    if item_errors:
        result["errors"] = item_errors + result["errors"]
        result["ok"] = False
    return result


@app.post("/api/quotes", status_code=201)
def create_quote(body: QuoteIn, request: Request, user=Depends(require_active_org)):
    require_csrf(request)
    enforce_access(assert_bom_size, user, len(body.items))
    draft = normalize_quote(body)
    enforce_access(assert_bom_size, user, len(draft["items"]))
    validation = None
    if draft.get("template_id"):
        geometry = validate_config(draft, None)
        if not geometry["ok"]:
            raise validation_error(geometry)
    parts = fetch_parts([item["id"] for item in draft["items"]]) if draft["items"] else {}
    if draft.get("template_id"):
        validation = quote_is_valid(
            draft, parts_for_rules(parts, draft["items"]), org_id=int(user["org_id"])
        )
        if not validation["ok"]:
            raise validation_error(validation)
    built = compose_quote(draft, parts)
    show_price = _show_price(user)
    size = f"{draft['width_mm']}x{draft['height_mm']}"
    if draft.get("depth_mm"):
        size += f"x{draft['depth_mm']}"
    if draft.get("depth2_mm"):
        size += f"x{draft['depth2_mm']}"
    detail = (
        f"{draft.get('template_id') or draft['fence_type']} {size} "
        f"glass={draft['glass_thick_mm']}/{draft['glass_kind']} items={len(draft['items'])}"
    )
    project_id = body.project_id
    if project_id is not None and project_id < 1:
        raise HTTPException(status_code=400, detail="Некорректный проект")
    with get_db() as conn:
        if project_id is not None:
            load_project(conn, user, project_id, writable=True)
        cur = conn.execute(
            """INSERT INTO quotes (
                 org_id, user_id, status, fence_type, width_mm, height_mm, depth_mm,
                 template_id, depth2_mm, angle_deg, door_count,
                 glass_thick_mm, glass_kind, glass_area_m2, glass_unit_price, glass_sum,
                 hardware_sum, total, currency
               ) VALUES (?,?, 'draft', ?,?,?,?, ?,?,?,?, ?,?,?,?,?,?,?, 'RUB')""",
            (
                user["org_id"],
                user["id"],
                draft["fence_type"],
                draft["width_mm"],
                draft["height_mm"],
                draft["depth_mm"],
                draft.get("template_id"),
                draft.get("depth2_mm"),
                draft.get("angle_deg"),
                draft.get("door_count"),
                draft["glass_thick_mm"],
                draft["glass_kind"],
                built["glass_area_m2"],
                built["glass_unit_price"],
                built["glass_sum"],
                built["hardware_sum"],
                built["total"],
            ),
        )
        quote_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO quote_items (
                 quote_id, line_no, kind, part_id, sku, name, qty, unit,
                 unit_price, line_sum, source, group_title
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    quote_id,
                    line["line_no"],
                    line["kind"],
                    line["part_id"],
                    line["sku"],
                    line["name"],
                    line["qty"],
                    line["unit"],
                    line["unit_price"],
                    line["line_sum"],
                    line["source"],
                    line["group_title"],
                )
                for line in built["lines"]
            ],
        )
        record_version(
            conn,
            user["org_id"],
            user["id"],
            "quote",
            quote_id,
            "quote_create",
            None,
            {
                "status": "draft",
                "fence_type": draft["fence_type"],
                "width_mm": draft["width_mm"],
                "height_mm": draft["height_mm"],
                "depth_mm": draft["depth_mm"],
                "glass_thick_mm": draft["glass_thick_mm"],
                "glass_kind": draft["glass_kind"],
                "hardware_count": len(draft["items"]),
                "project_id": project_id,
                **(
                    {
                        "template_id": draft["template_id"],
                        "depth2_mm": draft.get("depth2_mm"),
                        "angle_deg": draft.get("angle_deg"),
                        "door_count": draft.get("door_count"),
                    }
                    if draft.get("template_id")
                    else {}
                ),
            },
        )
        if project_id is not None:
            bind_quote(conn, user, project_id, quote_id)
            detail += f" project={project_id}"
        audit(
            conn,
            user["org_id"],
            user["id"],
            "quote_create",
            target_type="quote",
            target_id=quote_id,
            detail=detail,
        )
        row, items = _load_quote(conn, quote_id, user, with_items=True)
        quote = present_quote(
            row, items, show_price=show_price, include_lines=True, allow_pdf=_allow_pdf(user)
        )
    payload = {"quote": quote}
    if validation is not None:
        payload["validation"] = validation
    return payload


@app.get("/api/projects")
def projects_list(
    request: Request,
    status: Optional[str] = None,
    customer: Optional[str] = None,
    limit: int = 100,
    user=Depends(require_active_org),
):
    """Projects in the caller's scope. Admin, owner, and accountant see the org."""
    enforce_access(assert_no_export_query, user, request.query_params)
    limit = max(1, min(limit, 200))
    status_code = normalize_status_filter(status)
    customer_q = normalize_customer_filter(customer)
    with get_db() as conn:
        items = list_projects(
            conn, user, status=status_code, customer=customer_q, limit=limit
        )
    return {"items": items, **project_meta(user)}


@app.post("/api/projects", status_code=201)
def projects_create(body: ProjectCreateIn, request: Request, user=Depends(require_active_org)):
    require_csrf(request)
    with get_db() as conn:
        project = create_project(conn, user, body)
        detail = project["customer_name"]
        if project["quote_id"]:
            detail += f" · КП {project['quote_id']}"
        audit(
            conn,
            user["org_id"],
            user["id"],
            "project_create",
            target_type="project",
            target_id=project["id"],
            detail=detail[:300],
        )
    return {"project": project}


@app.get("/api/projects/{project_id}")
def projects_get(request: Request, project_id: int, user=Depends(require_active_org)):
    enforce_access(assert_no_export_query, user, request.query_params)
    with get_db() as conn:
        project = load_project(conn, user, project_id)
    return {"project": project, **project_meta(user)}


@app.patch("/api/projects/{project_id}/status")
def projects_status(
    project_id: int,
    body: ProjectStatusIn,
    request: Request,
    user=Depends(require_active_org),
):
    require_csrf(request)
    with get_db() as conn:
        project, previous = set_project_status(conn, user, project_id, body.status)
        if previous is not None:
            audit(
                conn,
                user["org_id"],
                user["id"],
                "project_status_change",
                target_type="project",
                target_id=project["id"],
                detail=f"{STATUS_LABEL.get(previous, previous)} -> {project['status_label']}",
            )
    return {"project": project}


@app.patch("/api/projects/{project_id}/quote")
def projects_quote(
    project_id: int,
    body: ProjectQuoteIn,
    request: Request,
    user=Depends(require_active_org),
):
    require_csrf(request)
    with get_db() as conn:
        project = bind_quote(conn, user, project_id, body.quote_id)
    return {"project": project}


def _stock_move(kind: str, body: StockMoveIn, request: Request, user):
    require_csrf(request)
    try:
        with get_db() as conn:
            item, movement_id, detail = apply_movement(conn, user, kind, body)
            audit(
                conn,
                user["org_id"],
                user["id"],
                AUDIT_ACTION[kind],
                target_type="stock",
                target_id=item["part_id"],
                detail=detail,
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Операция не сходится с остатком")
    return {"item": item, "movement_id": movement_id}


@app.get("/api/stock")
def stock_list(
    request: Request,
    group: Optional[str] = None,
    source: Optional[str] = None,
    manufacturer: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 200,
    user=Depends(require_active_org),
):
    """Org balances over catalog parts. Sorted by deficit, then reserve, qty, min."""
    enforce_access(assert_no_export_query, user, request.query_params)
    limit = max(1, min(limit, 500))
    with get_db() as conn:
        return list_stock(
            conn,
            user,
            group=group,
            source=source or manufacturer,
            q=q,
            limit=limit,
        )


@app.post("/api/stock/in")
def stock_in(body: StockMoveIn, request: Request, user=Depends(require_active_org)):
    return _stock_move("in", body, request, user)


@app.post("/api/stock/out")
def stock_out(body: StockMoveIn, request: Request, user=Depends(require_active_org)):
    return _stock_move("out", body, request, user)


@app.post("/api/stock/reserve")
def stock_reserve(body: StockMoveIn, request: Request, user=Depends(require_active_org)):
    return _stock_move("reserve", body, request, user)


@app.post("/api/stock/release")
def stock_release(body: StockMoveIn, request: Request, user=Depends(require_active_org)):
    return _stock_move("release", body, request, user)


def vitrine_ip(request: Request) -> str:
    """Client address for the public vitrine limiter.

    Nginx overwrites X-Real-IP with $remote_addr. The first X-Forwarded-For
    hop is chosen by the caller, so this limiter ignores that header.
    """
    real = normalize_ip(request.headers.get("x-real-ip"))
    if real:
        return real
    host = request.client.host if request.client and request.client.host else ""
    return normalize_ip(host) or host or "unknown"


def _limit_vitrine(request: Request, bucket: str) -> None:
    try:
        check_public_rate_limit(bucket, vitrine_ip(request))
    except ValueError:
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов с этого адреса. Подождите минуту.",
            headers={"Retry-After": "60"},
        )


@app.post("/api/vitrine/chat")
def vitrine_chat(body: VitrineChatIn, request: Request):
    """Public storefront assistant. Guests have no session, so there is no CSRF cookie."""
    _limit_vitrine(request, "vitrine_chat")
    return answer_chat(body.message, body.history)


@app.post("/api/vitrine/leads", status_code=201)
def vitrine_lead(body: VitrineLeadIn, request: Request):
    """Store a guest request. Status is always «заявка с витрины»."""
    _limit_vitrine(request, "vitrine_lead")
    with get_db() as conn:
        lead = create_vitrine_lead(conn, body)
        if lead["org_id"]:
            audit(
                conn,
                lead["org_id"],
                None,
                "vitrine_lead",
                target_type="vitrine_lead",
                target_id=lead["id"],
                detail=f"{lead['name']} {lead['phone']}"[:300],
            )
    return {"ok": True, "id": lead["id"], "status": lead["status"]}


def _maybe_serve_public() -> None:
    """Same-origin static files for local checks (cfg.html + session cookie).

    Off unless DUSHEVYE_SERVE_PUBLIC=1. Production nginx serves public/ itself
    and proxies only /api/, so this mount stays disabled on the VDS.
    """
    flag = os.environ.get("DUSHEVYE_SERVE_PUBLIC", "").strip().lower()
    if flag not in ("1", "true", "yes"):
        return
    raw = os.environ.get("DUSHEVYE_PUBLIC_DIR", "").strip()
    public = Path(raw).expanduser() if raw else Path(__file__).resolve().parent.parent / "public"
    if not public.is_dir():
        raise RuntimeError(f"DUSHEVYE_SERVE_PUBLIC: нет каталога {public}")
    app.mount("/", StaticFiles(directory=str(public), html=True), name="public")


_maybe_serve_public()
