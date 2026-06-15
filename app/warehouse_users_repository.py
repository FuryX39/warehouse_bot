"""Учётные записи сотрудников новой панели /warehouse."""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Boolean, Integer, String, func, or_, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.warehouse_permissions import (
    full_access_permissions,
    permissions_from_json,
    permissions_to_json,
)

_PBKDF2_ITERATIONS = 260_000


class _Base(DeclarativeBase):
    pass


class WarehouseEmployeeGroup(_Base):
    __tablename__ = "warehouse_employee_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WarehouseUser(_Base):
    __tablename__ = "warehouse_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    login: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    group_id: Mapped[int] = mapped_column(Integer, nullable=True)
    telegram_nick: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    permissions_json: Mapped[str] = mapped_column(String(8192), nullable=False, default="{}")
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@dataclass
class WarehouseUserRow:
    id: int
    login: str
    display_name: str
    group_id: Optional[int]
    group_name: str
    telegram_nick: str
    is_admin: bool
    is_active: bool
    permissions: dict[str, list[str]]
    created_at_ts: int
    updated_at_ts: int


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iterations_s, salt, digest_hex = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iterations_s)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return secrets.compare_digest(digest.hex(), digest_hex)


def _like(pattern: str) -> str:
    p = pattern.strip()
    if not p:
        return ""
    return f"%{p}%"


class WarehouseUsersRepository:
    def __init__(self, db_url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self._ensure_user_columns()

    def _ensure_user_columns(self) -> None:
        with self.engine.begin() as conn:
            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(warehouse_users)")).all()
            }
            if "group_id" not in cols:
                conn.execute(text("ALTER TABLE warehouse_users ADD COLUMN group_id INTEGER"))
            if "telegram_nick" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE warehouse_users ADD COLUMN telegram_nick "
                        "VARCHAR(128) NOT NULL DEFAULT ''"
                    )
                )

    def get_employee_meta(self) -> dict[str, list[dict[str, Any]]]:
        with Session(self.engine) as session:
            groups = session.scalars(
                select(WarehouseEmployeeGroup).order_by(
                    WarehouseEmployeeGroup.sort_order, WarehouseEmployeeGroup.name
                )
            ).all()
        return {
            "groups": [{"id": g.id, "name": g.name, "sort_order": g.sort_order} for g in groups],
        }

    def save_employee_groups(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {
                r.id: r for r in session.scalars(select(WarehouseEmployeeGroup)).all()
            }
            keep_ids: set[int] = set()
            for i, item in enumerate(items):
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                raw_id = item.get("id")
                row = None
                if raw_id is not None:
                    try:
                        row = existing.get(int(raw_id))
                    except (TypeError, ValueError):
                        row = None
                if row is None:
                    row = WarehouseEmployeeGroup(name=name, sort_order=i)
                    session.add(row)
                else:
                    row.name = name
                    row.sort_order = i
                session.flush()
                keep_ids.add(int(row.id))
            for rid, row in existing.items():
                if rid not in keep_ids:
                    session.delete(row)
            session.commit()
            rows = session.scalars(
                select(WarehouseEmployeeGroup).order_by(
                    WarehouseEmployeeGroup.sort_order, WarehouseEmployeeGroup.name
                )
            ).all()
            return [{"id": r.id, "name": r.name, "sort_order": r.sort_order} for r in rows]

    def count_users(self) -> int:
        with Session(self.engine) as session:
            return int(session.scalar(select(func.count()).select_from(WarehouseUser)) or 0)

    def ensure_bootstrap_admin(self, login: str, password: str, *, display_name: str = "Администратор") -> None:
        if self.count_users() > 0:
            return
        login_n = login.strip()
        password_n = password.strip()
        if not login_n or not password_n:
            raise RuntimeError(
                "В БД нет пользователей новой панели. Задайте WAREHOUSE_ADMIN_LOGIN и "
                "WAREHOUSE_ADMIN_PASSWORD в .env и перезапустите веб."
            )
        self.create_user(
            login=login_n,
            password=password_n,
            display_name=display_name or "Администратор",
            is_admin=True,
            is_active=True,
            permissions=full_access_permissions(),
        )

    def sync_env_admin(self, login: str, password: str, *, display_name: str = "Администратор") -> WarehouseUserRow | None:
        """Гарантирует, что логин из .env — активный администратор с полным доступом."""
        login_n = login.strip()
        password_n = password.strip()
        if not login_n or not password_n:
            return None
        if self.count_users() == 0:
            self.ensure_bootstrap_admin(login_n, password_n, display_name=display_name)
            return self.get_by_login(login_n)
        return self.upsert_env_admin(login_n, password_n, display_name=display_name)

    def get_by_login(self, login: str) -> WarehouseUserRow | None:
        login_n = login.strip()
        if not login_n:
            return None
        with Session(self.engine) as session:
            row = session.scalar(select(WarehouseUser).where(WarehouseUser.login == login_n))
            if row is None:
                return None
            group_name = ""
            if row.group_id is not None:
                group_name = (
                    session.scalar(
                        select(WarehouseEmployeeGroup.name).where(
                            WarehouseEmployeeGroup.id == int(row.group_id)
                        )
                    )
                    or ""
                )
            return self._row_from_model(row, group_name=str(group_name))

    def upsert_env_admin(self, login: str, password: str, *, display_name: str = "Администратор") -> WarehouseUserRow:
        login_n = login.strip()
        password_n = password.strip()
        if not login_n or not password_n:
            raise ValueError("Логин и пароль администратора обязательны")
        name = (display_name or "Администратор").strip()[:128]
        now = int(time.time())
        full_perms = permissions_to_json(full_access_permissions())
        with Session(self.engine) as session:
            row = session.scalar(select(WarehouseUser).where(WarehouseUser.login == login_n))
            if row is None:
                row = WarehouseUser(
                    login=login_n,
                    password_hash=hash_password(password_n),
                    display_name=name,
                    is_admin=True,
                    is_active=True,
                    permissions_json=full_perms,
                    created_at_ts=now,
                    updated_at_ts=now,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
                return self._user_row_after_flush(session, row)
            row.is_admin = True
            row.is_active = True
            row.password_hash = hash_password(password_n)
            row.permissions_json = full_perms
            if name and not (row.display_name or "").strip():
                row.display_name = name
            row.updated_at_ts = now
            session.commit()
            session.refresh(row)
            group_name = ""
            if row.group_id is not None:
                group_name = (
                    session.scalar(
                        select(WarehouseEmployeeGroup.name).where(
                            WarehouseEmployeeGroup.id == int(row.group_id)
                        )
                    )
                    or ""
                )
            return self._row_from_model(row, group_name=str(group_name))

    def _normalize_telegram_nick(self, raw: str) -> str:
        return str(raw or "").strip().lstrip("@")[:128]

    def _user_row_after_flush(self, session: Session, row: WarehouseUser) -> WarehouseUserRow:
        group_name = ""
        if row.group_id is not None:
            group_name = (
                session.scalar(
                    select(WarehouseEmployeeGroup.name).where(
                        WarehouseEmployeeGroup.id == int(row.group_id)
                    )
                )
                or ""
            )
        return self._row_from_model(row, group_name=str(group_name))

    def _row_from_model(self, row: WarehouseUser, *, group_name: str = "") -> WarehouseUserRow:
        return WarehouseUserRow(
            id=int(row.id),
            login=row.login,
            display_name=row.display_name or "",
            group_id=int(row.group_id) if row.group_id is not None else None,
            group_name=group_name,
            telegram_nick=row.telegram_nick or "",
            is_admin=bool(row.is_admin),
            is_active=bool(row.is_active),
            permissions=permissions_from_json(row.permissions_json),
            created_at_ts=int(row.created_at_ts),
            updated_at_ts=int(row.updated_at_ts),
        )

    def _group_name_map(self, session: Session) -> dict[int, str]:
        rows = session.scalars(select(WarehouseEmployeeGroup)).all()
        return {int(r.id): r.name for r in rows}

    def _resolve_group_id(self, session: Session, raw: Any) -> int | None:
        if raw is None or raw == "":
            return None
        try:
            group_id = int(raw)
        except (TypeError, ValueError):
            raise ValueError("Некорректная группа")
        exists = session.scalar(
            select(WarehouseEmployeeGroup.id).where(WarehouseEmployeeGroup.id == group_id)
        )
        if exists is None:
            raise ValueError("Группа не найдена")
        return group_id

    def authenticate(self, login: str, password: str) -> WarehouseUserRow | None:
        login_n = login.strip()
        if not login_n or not password:
            return None
        with Session(self.engine) as session:
            row = session.scalar(select(WarehouseUser).where(WarehouseUser.login == login_n))
            if row is None or not row.is_active:
                return None
            if not verify_password(password, row.password_hash):
                return None
            return self._row_from_model(row, group_name=self._group_name_map(session).get(int(row.group_id or 0), ""))

    def get_by_id(self, user_id: int) -> WarehouseUserRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseUser, int(user_id))
            if row is None:
                return None
            group_name = ""
            if row.group_id is not None:
                group_name = (
                    session.scalar(
                        select(WarehouseEmployeeGroup.name).where(
                            WarehouseEmployeeGroup.id == int(row.group_id)
                        )
                    )
                    or ""
                )
            return self._row_from_model(row, group_name=str(group_name))

    def list_users(self, filters: dict[str, str] | None = None) -> list[WarehouseUserRow]:
        filters = filters or {}
        with Session(self.engine) as session:
            q = select(WarehouseUser).order_by(WarehouseUser.display_name, WarehouseUser.login)
            conds = self._filter_conditions(session, filters)
            if conds:
                q = q.where(*conds)
            rows = session.scalars(q).all()
            groups = self._group_name_map(session)
            return [
                self._row_from_model(
                    r,
                    group_name=groups.get(int(r.group_id), "") if r.group_id is not None else "",
                )
                for r in rows
            ]

    def _filter_conditions(self, session: Session, filters: dict[str, str]) -> list:
        from app.warehouse_roles_repository import WarehouseUserRole

        conds = []
        quick = _like(filters.get("q", ""))
        if quick:
            conds.append(
                or_(
                    WarehouseUser.display_name.ilike(quick),
                    WarehouseUser.login.ilike(quick),
                    WarehouseUser.telegram_nick.ilike(quick),
                )
            )
        for key, col in (
            ("display_name", WarehouseUser.display_name),
            ("login", WarehouseUser.login),
            ("telegram_nick", WarehouseUser.telegram_nick),
        ):
            pat = _like(filters.get(key, ""))
            if pat:
                conds.append(col.ilike(pat))
        raw_group = (filters.get("group_id") or "").strip()
        if raw_group:
            try:
                conds.append(WarehouseUser.group_id == int(raw_group))
            except ValueError:
                pass
        raw_role = (filters.get("role_id") or "").strip()
        if raw_role:
            try:
                role_id = int(raw_role)
            except ValueError:
                role_id = None
            if role_id is not None:
                subq = select(WarehouseUserRole.user_id).where(
                    WarehouseUserRole.role_id == role_id
                )
                conds.append(WarehouseUser.id.in_(subq))
        status = (filters.get("is_active") or "").strip().lower()
        if status in {"1", "true", "yes", "active"}:
            conds.append(WarehouseUser.is_active.is_(True))
        elif status in {"0", "false", "no", "inactive"}:
            conds.append(WarehouseUser.is_active.is_(False))
        return conds

    def create_user(
        self,
        *,
        login: str,
        password: str,
        display_name: str = "",
        group_id: int | None = None,
        telegram_nick: str = "",
        is_admin: bool = False,
        is_active: bool = True,
        permissions: dict[str, list[str]] | None = None,
    ) -> WarehouseUserRow:
        login_n = login.strip()
        if not login_n or len(login_n) > 64:
            raise ValueError("Логин обязателен (до 64 символов)")
        if not password:
            raise ValueError("Пароль обязателен")
        name = (display_name or "").strip()[:128]
        now = int(time.time())
        perms_json = permissions_to_json(permissions or {})
        with Session(self.engine) as session:
            if session.scalar(select(WarehouseUser.id).where(WarehouseUser.login == login_n)):
                raise ValueError(f"Логин «{login_n}» уже занят")
            row = WarehouseUser(
                login=login_n,
                password_hash=hash_password(password),
                display_name=name,
                group_id=self._resolve_group_id(session, group_id),
                telegram_nick=self._normalize_telegram_nick(telegram_nick),
                is_admin=bool(is_admin),
                is_active=bool(is_active),
                permissions_json=perms_json,
                created_at_ts=now,
                updated_at_ts=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._user_row_after_flush(session, row)

    def update_user(
        self,
        user_id: int,
        *,
        login: str | None = None,
        password: str | None = None,
        display_name: str | None = None,
        group_id: Any = ...,
        telegram_nick: str | None = None,
        is_admin: bool | None = None,
        is_active: bool | None = None,
        permissions: dict[str, list[str]] | None = None,
    ) -> WarehouseUserRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseUser, int(user_id))
            if row is None:
                return None
            if login is not None:
                login_n = login.strip()
                if not login_n or len(login_n) > 64:
                    raise ValueError("Логин обязателен (до 64 символов)")
                other = session.scalar(
                    select(WarehouseUser.id).where(
                        WarehouseUser.login == login_n,
                        WarehouseUser.id != row.id,
                    )
                )
                if other is not None:
                    raise ValueError(f"Логин «{login_n}» уже занят")
                row.login = login_n
            if password is not None and password.strip():
                row.password_hash = hash_password(password.strip())
            if display_name is not None:
                row.display_name = display_name.strip()[:128]
            if group_id is not ...:
                row.group_id = self._resolve_group_id(session, group_id)
            if telegram_nick is not None:
                row.telegram_nick = self._normalize_telegram_nick(telegram_nick)
            if is_admin is not None:
                if row.is_admin and not is_admin:
                    admins = session.scalars(
                        select(WarehouseUser).where(WarehouseUser.is_admin.is_(True))
                    ).all()
                    if len(admins) <= 1:
                        raise ValueError("Нельзя снять права администратора у единственного админа")
                row.is_admin = bool(is_admin)
            if is_active is not None:
                if row.is_admin and not is_active:
                    admins = session.scalars(
                        select(WarehouseUser).where(
                            WarehouseUser.is_admin.is_(True),
                            WarehouseUser.is_active.is_(True),
                        )
                    ).all()
                    if len(admins) <= 1:
                        raise ValueError("Нельзя деактивировать единственного активного администратора")
                row.is_active = bool(is_active)
            if permissions is not None and not row.is_admin:
                row.permissions_json = permissions_to_json(permissions)
            row.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(row)
            return self._user_row_after_flush(session, row)

    def user_to_public_dict(
        self,
        row: WarehouseUserRow,
        *,
        roles: list[dict] | None = None,
        role_ids: list[int] | None = None,
    ) -> dict:
        role_items = roles or []
        ids = role_ids if role_ids is not None else [int(r["id"]) for r in role_items]
        return {
            "id": row.id,
            "login": row.login,
            "display_name": row.display_name,
            "group_id": row.group_id,
            "group_name": row.group_name,
            "telegram_nick": row.telegram_nick,
            "is_admin": bool(row.is_admin),
            "is_active": row.is_active,
            "permissions": row.permissions,
            "role_ids": ids,
            "roles": role_items,
            "created_at_ts": row.created_at_ts,
            "updated_at_ts": row.updated_at_ts,
        }
