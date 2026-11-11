"""Per-organization demo mode and manual suspend.

Missing org_access row means access_mode=full and suspended=0. Nothing here
runs on a timer, calls a license server, or updates every organization.
System admin is the existing role ``admin`` (Maxim). Tenant owner is ``owner``
and is not exempt.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from typing import Any, Mapping, Optional

MODE_FULL = "full"
MODE_DEMO = "demo"
MODES = frozenset({MODE_FULL, MODE_DEMO})
SYSTEM_ADMIN_ROLE = "admin"

# Interactive catalog page used by the configurator search. Larger pages and
# offset walks are treated as a bulk export.
DEMO_CATALOG_LIMIT = 40
# A real shower BOM stays under this. A longer parts list is a catalog dump.
DEMO_BOM_LINES = 40

SUSPENDED_DETAIL = "Доступ организации приостановлен"
DEMO_USERS_DETAIL = "В демонстрационном режиме управление пользователями и ролями недоступно"
DEMO_EXPORT_DETAIL = "В демонстрационном режиме выгрузка недоступна"
DEMO_RULES_DETAIL = "В демонстрационном режиме правила каталога и их выгрузка недоступны"

# Query flags that turn an ordinary list into a download on any current or
# future route. Checked on list endpoints, not only on routes named "export".
EXPORT_QUERY_KEYS = ("export", "format", "download", "dump")


class OrgAccessError(Exception):
    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.message = message
        self.status = status


def read_org_access(conn: sqlite3.Connection, org_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT access_mode, suspended FROM org_access WHERE org_id=?",
        (int(org_id),),
    ).fetchone()
    if not row:
        return {"access_mode": MODE_FULL, "suspended": False}
    mode = row["access_mode"] if row["access_mode"] in MODES else MODE_FULL
    return {"access_mode": mode, "suspended": bool(row["suspended"])}


def attach_access(row: Any, access: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["access_mode"] = access["access_mode"]
    data["org_suspended"] = bool(access["suspended"])
    return data


def load_user(conn: sqlite3.Connection, user_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM users WHERE id=? AND is_active=1", (int(user_id),)
    ).fetchone()
    if not row:
        return None
    return attach_access(row, read_org_access(conn, int(row["org_id"])))


def is_system_admin(user: Mapping[str, Any]) -> bool:
    return user.get("role") == SYSTEM_ADMIN_ROLE


def demo_restricted(user: Mapping[str, Any]) -> bool:
    """Demo limits apply to every role except the system admin, including owner."""
    return user.get("access_mode") == MODE_DEMO and not is_system_admin(user)


def assert_not_suspended(user: Mapping[str, Any]) -> None:
    if user.get("org_suspended") and not is_system_admin(user):
        raise OrgAccessError(SUSPENDED_DETAIL)


def assert_users_allowed(user: Mapping[str, Any]) -> None:
    if demo_restricted(user):
        raise OrgAccessError(DEMO_USERS_DETAIL)


def assert_rules_allowed(user: Mapping[str, Any]) -> None:
    if demo_restricted(user):
        raise OrgAccessError(DEMO_RULES_DETAIL)


def assert_export_allowed(user: Mapping[str, Any]) -> None:
    if demo_restricted(user):
        raise OrgAccessError(DEMO_EXPORT_DETAIL)


def assert_no_export_query(user: Mapping[str, Any], params: Mapping[str, str]) -> None:
    if not demo_restricted(user):
        return
    for key in EXPORT_QUERY_KEYS:
        raw = params.get(key)
        if raw is not None and str(raw).strip() != "":
            raise OrgAccessError(DEMO_EXPORT_DETAIL)


def assert_catalog_page(user: Mapping[str, Any], limit: int, offset: int) -> None:
    if not demo_restricted(user):
        return
    if int(limit) > DEMO_CATALOG_LIMIT or int(offset) > 0:
        raise OrgAccessError(DEMO_EXPORT_DETAIL)


def assert_bom_size(user: Mapping[str, Any], item_count: int) -> None:
    if demo_restricted(user) and int(item_count) > DEMO_BOM_LINES:
        raise OrgAccessError(DEMO_EXPORT_DETAIL)


def _audit(
    conn: sqlite3.Connection,
    org_id: int,
    actor_id: Optional[int],
    action: str,
    detail: str,
) -> None:
    conn.execute(
        """INSERT INTO audit_log (org_id, actor_id, action, target_type, target_id, detail)
           VALUES (?,?,?,?,?,?)""",
        (int(org_id), actor_id, action, "organization", int(org_id), detail),
    )


def apply_org_access(
    conn: sqlite3.Connection,
    org_id: int,
    actor_id: Optional[int],
    *,
    access_mode: Optional[str] = None,
    suspended: Optional[bool] = None,
) -> dict[str, Any]:
    """Write one organization's flags. Does not touch users, passwords, or catalog.db."""
    org = conn.execute("SELECT id FROM organizations WHERE id=?", (int(org_id),)).fetchone()
    if not org:
        raise OrgAccessError("Организация не найдена", status=404)
    current = read_org_access(conn, int(org_id))
    new_mode = current["access_mode"]
    new_suspended = current["suspended"]
    if access_mode is not None:
        mode = access_mode.strip().lower()
        if mode not in MODES:
            raise OrgAccessError("Режим доступа: full или demo", status=400)
        new_mode = mode
    if suspended is not None:
        new_suspended = bool(suspended)
    changed_mode = new_mode != current["access_mode"]
    changed_susp = new_suspended != current["suspended"]
    if not changed_mode and not changed_susp:
        return {
            "access_mode": new_mode,
            "suspended": new_suspended,
            "changed": False,
        }
    conn.execute(
        """INSERT INTO org_access (org_id, access_mode, suspended, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(org_id) DO UPDATE SET
             access_mode=excluded.access_mode,
             suspended=excluded.suspended,
             updated_at=datetime('now')""",
        (int(org_id), new_mode, 1 if new_suspended else 0),
    )
    if changed_mode:
        _audit(
            conn,
            int(org_id),
            actor_id,
            "org_access_mode",
            f"{current['access_mode']} -> {new_mode}",
        )
    if changed_susp:
        before = "suspended" if current["suspended"] else "active"
        after = "suspended" if new_suspended else "active"
        _audit(conn, int(org_id), actor_id, "org_suspend", f"{before} -> {after}")
    return {"access_mode": new_mode, "suspended": new_suspended, "changed": True}


def public_access(access: Mapping[str, Any]) -> dict[str, Any]:
    mode = access["access_mode"] if access.get("access_mode") in MODES else MODE_FULL
    return {"access_mode": mode, "suspended": bool(access.get("suspended"))}


def _users(conn: sqlite3.Connection, org_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT id, email, name, role, is_active
           FROM users WHERE org_id=? ORDER BY id""",
        (int(org_id),),
    ).fetchall()


def format_status(conn: sqlite3.Connection) -> str:
    """Operator view. Emails and roles only — never password hashes."""
    lines = ["Организации (пароли не показываются):", ""]
    orgs = conn.execute(
        "SELECT id, name, slug FROM organizations ORDER BY id"
    ).fetchall()
    if not orgs:
        lines.append("Организаций нет.")
        return "\n".join(lines)
    for org in orgs:
        access = read_org_access(conn, int(org["id"]))
        lines.append(
            f"id={org['id']} slug={org['slug']} name={org['name']} "
            f"access_mode={access['access_mode']} suspended={int(access['suspended'])}"
        )
        users = _users(conn, int(org["id"]))
        if not users:
            lines.append("  пользователей нет")
        for user in users:
            active = "active" if user["is_active"] else "inactive"
            lines.append(
                f"  user id={user['id']} role={user['role']} {active} "
                f"email={user['email']} name={user['name'] or ''}"
            )
        lines.append("")
    lines.append(
        "Строки org_access нет — это full и не suspended. "
        "Команды ниже меняют только одну организацию и только с --apply."
    )
    return "\n".join(lines).rstrip()


def _require_org(conn: sqlite3.Connection, org_id: int, slug: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, name, slug FROM organizations WHERE id=?", (int(org_id),)
    ).fetchone()
    if not row:
        raise SystemExit(f"Нет организации id={org_id}")
    if row["slug"] != slug:
        raise SystemExit(
            f"slug не совпал: в базе {row['slug']!r}, передано {slug!r}. Ничего не записано."
        )
    return row


def _require_owner(conn: sqlite3.Connection, org_id: int, email: str) -> sqlite3.Row:
    row = conn.execute(
        """SELECT id, email, role, is_active FROM users
           WHERE org_id=? AND lower(email)=lower(?)""",
        (int(org_id), email.strip()),
    ).fetchone()
    if not row:
        raise SystemExit(f"В организации нет пользователя {email}. Ничего не записано.")
    if row["role"] != "owner" or not row["is_active"]:
        raise SystemExit(
            f"{email} имеет роль {row['role']}, active={row['is_active']}. "
            "Для демо нужен активный owner. Системный admin этой командой не ограничивается. "
            "Ничего не записано."
        )
    return row


def _require_actor(conn: sqlite3.Connection, org_id: int, email: str) -> sqlite3.Row:
    row = conn.execute(
        """SELECT id, email, role, is_active FROM users
           WHERE org_id=? AND lower(email)=lower(?)""",
        (int(org_id), email.strip()),
    ).fetchone()
    if not row or row["role"] != SYSTEM_ADMIN_ROLE or not row["is_active"]:
        raise SystemExit(
            f"actor {email} должен быть активным admin этой организации. Ничего не записано."
        )
    return row


def _require_ack_admins(conn: sqlite3.Connection, org_id: int, ack: int) -> list[str]:
    rows = conn.execute(
        """SELECT email FROM users
           WHERE org_id=? AND role=? AND is_active=1 ORDER BY id""",
        (int(org_id), SYSTEM_ADMIN_ROLE),
    ).fetchall()
    emails = [row["email"] for row in rows]
    if ack != len(emails):
        shown = ", ".join(emails) if emails else "—"
        raise SystemExit(
            f"Активных admin в организации: {len(emails)} ({shown}). "
            f"Повторите с --ack-admins {len(emails)} после проверки, что это системные "
            "админы, а не хозяин фирмы. Ничего не записано."
        )
    return emails


def _plan(
    conn: sqlite3.Connection,
    *,
    org_id: int,
    slug: str,
    owner_email: str,
    actor_email: str,
    ack_admins: int,
    access_mode: Optional[str],
    suspended: Optional[bool],
    apply: bool,
) -> int:
    org = _require_org(conn, org_id, slug)
    owner = _require_owner(conn, org_id, owner_email)
    actor = _require_actor(conn, org_id, actor_email)
    admins = _require_ack_admins(conn, org_id, ack_admins)
    current = read_org_access(conn, org_id)
    print(f"Организация id={org['id']} slug={org['slug']} name={org['name']}")
    print(f"Owner подтверждён: id={owner['id']} email={owner['email']} role={owner['role']}")
    print(f"Actor (системный admin): id={actor['id']} email={actor['email']}")
    print("Активные admin, которых режим не ограничивает: " + ", ".join(admins))
    print(
        f"Сейчас: access_mode={current['access_mode']} suspended={int(current['suspended'])}"
    )
    print(
        "Пароли, роли и catalog.db эта команда не меняет. "
        "Другие организации не затрагиваются."
    )
    if not apply:
        print("DRY-RUN: запись не выполнялась. Добавьте --apply, чтобы записать эту одну организацию.")
        return 0
    result = apply_org_access(
        conn,
        org_id,
        int(actor["id"]),
        access_mode=access_mode,
        suspended=suspended,
    )
    conn.commit()
    print(
        f"Записано: access_mode={result['access_mode']} "
        f"suspended={int(result['suspended'])} changed={int(result['changed'])}"
    )
    return 0


def _add_confirm_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--org-id", type=int, required=True)
    parser.add_argument("--confirm-slug", required=True, help="Должен совпасть со slug этой организации")
    parser.add_argument("--confirm-owner-email", required=True)
    parser.add_argument("--actor-email", required=True, help="Активный admin этой же организации")
    parser.add_argument(
        "--ack-admins",
        type=int,
        required=True,
        help="Число активных admin в организации. Сверьте со status.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Без этого флага команда только печатает план и ничего не пишет",
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ручное включение demo или приостановка одной организации. Глобального режима нет."
    )
    parser.add_argument("--db", default=None, help="Путь к app.db (иначе DUSHEVYE_DB)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Показать организации, роли и флаги доступа")

    enable = sub.add_parser("enable-demo", help="Поставить access_mode=demo одной организации")
    _add_confirm_flags(enable)

    disable = sub.add_parser("disable-demo", help="Вернуть access_mode=full одной организации")
    _add_confirm_flags(disable)

    suspend = sub.add_parser("suspend", help="Приостановить доступ одной организации")
    _add_confirm_flags(suspend)

    resume = sub.add_parser("resume", help="Снять приостановку одной организации")
    _add_confirm_flags(resume)

    args = parser.parse_args(argv)
    if args.db:
        os.environ["DUSHEVYE_DB"] = args.db

    from .db import connect, init_db

    init_db()
    conn = connect()
    try:
        if args.cmd == "status":
            print(format_status(conn))
            return 0
        mode = None
        suspended = None
        if args.cmd == "enable-demo":
            mode = MODE_DEMO
        elif args.cmd == "disable-demo":
            mode = MODE_FULL
        elif args.cmd == "suspend":
            suspended = True
        elif args.cmd == "resume":
            suspended = False
        return _plan(
            conn,
            org_id=args.org_id,
            slug=args.confirm_slug,
            owner_email=args.confirm_owner_email,
            actor_email=args.actor_email,
            ack_admins=args.ack_admins,
            access_mode=mode,
            suspended=suspended,
            apply=args.apply,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
