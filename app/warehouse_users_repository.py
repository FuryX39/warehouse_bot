"""Учётные записи сотрудников новой панели /warehouse."""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import Boolean, Integer, String, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.warehouse_permissions import (
    full_access_permissions,
    permissions_from_json,
    permissions_to_json,
)

_PBKDF2_ITERATIONS = 260_000


class _Base(DeclarativeBase):
    pass


class WarehouseUser(_Base):
    __tablename__ = "warehouse_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    login: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
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


class WarehouseUsersRepository:
    def __init__(self, db_url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)

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
            return self._row_from_model(row)

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
                return self._row_from_model(row)
            row.is_admin = True
            row.is_active = True
            row.password_hash = hash_password(password_n)
            row.permissions_json = full_perms
            if name and not (row.display_name or "").strip():
                row.display_name = name
            row.updated_at_ts = now
            session.commit()
            session.refresh(row)
            return self._row_from_model(row)

    def _row_from_model(self, row: WarehouseUser) -> WarehouseUserRow:
        return WarehouseUserRow(
            id=int(row.id),
            login=row.login,
            display_name=row.display_name or "",
            is_admin=bool(row.is_admin),
            is_active=bool(row.is_active),
            permissions=permissions_from_json(row.permissions_json),
            created_at_ts=int(row.created_at_ts),
            updated_at_ts=int(row.updated_at_ts),
        )

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
            return self._row_from_model(row)

    def get_by_id(self, user_id: int) -> WarehouseUserRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseUser, int(user_id))
            if row is None:
                return None
            return self._row_from_model(row)

    def list_users(self) -> list[WarehouseUserRow]:
        with Session(self.engine) as session:
            rows = session.scalars(select(WarehouseUser).order_by(WarehouseUser.login)).all()
            return [self._row_from_model(r) for r in rows]

    def create_user(
        self,
        *,
        login: str,
        password: str,
        display_name: str = "",
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
                is_admin=bool(is_admin),
                is_active=bool(is_active),
                permissions_json=perms_json,
                created_at_ts=now,
                updated_at_ts=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._row_from_model(row)

    def update_user(
        self,
        user_id: int,
        *,
        login: str | None = None,
        password: str | None = None,
        display_name: str | None = None,
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
            return self._row_from_model(row)

    def user_to_public_dict(self, row: WarehouseUserRow) -> dict:
        return {
            "id": row.id,
            "login": row.login,
            "display_name": row.display_name,
            "is_admin": bool(row.is_admin),
            "is_active": row.is_active,
            "permissions": row.permissions,
            "created_at_ts": row.created_at_ts,
            "updated_at_ts": row.updated_at_ts,
        }
