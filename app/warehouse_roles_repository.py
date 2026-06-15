"""Роли и назначение ролей сотрудникам новой панели /warehouse."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Boolean, Integer, String, UniqueConstraint, delete, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.warehouse_permissions import (
    full_access_permissions,
    permissions_from_json,
    permissions_to_json,
    sanitize_permissions,
)

_DEFAULT_ADMIN_ROLE_NAME = "Админ"


class _Base(DeclarativeBase):
    pass


class WarehouseRole(_Base):
    __tablename__ = "warehouse_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    comment: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    permissions_json: Mapped[str] = mapped_column(String(8192), nullable=False, default="{}")
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WarehouseUserRole(_Base):
    __tablename__ = "warehouse_user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_warehouse_user_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role_id: Mapped[int] = mapped_column(Integer, nullable=False)


@dataclass
class WarehouseRoleMember:
    id: int
    login: str
    display_name: str


@dataclass
class WarehouseRoleRow:
    id: int
    name: str
    description: str
    comment: str
    permissions: dict[str, list[str]]
    is_admin: bool
    is_system: bool
    sort_order: int
    created_at_ts: int
    updated_at_ts: int
    member_count: int = 0
    members: list[WarehouseRoleMember] = field(default_factory=list)


class WarehouseRolesRepository:
    def __init__(self, db_url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self.ensure_defaults()

    def ensure_defaults(self) -> None:
        now = int(time.time())
        with Session(self.engine) as session:
            admin = session.scalar(
                select(WarehouseRole).where(WarehouseRole.is_system.is_(True))
            )
            if admin is None:
                session.add(
                    WarehouseRole(
                        name=_DEFAULT_ADMIN_ROLE_NAME,
                        description="Полный доступ ко всем разделам системы",
                        comment="",
                        permissions_json=permissions_to_json(full_access_permissions()),
                        is_admin=True,
                        is_system=True,
                        sort_order=0,
                        created_at_ts=now,
                        updated_at_ts=now,
                    )
                )
                session.commit()

    def get_admin_role_id(self) -> int | None:
        with Session(self.engine) as session:
            role_id = session.scalar(
                select(WarehouseRole.id).where(WarehouseRole.is_admin.is_(True)).limit(1)
            )
            return int(role_id) if role_id is not None else None

    def sync_admin_role_for_user(self, user_id: int, *, is_admin: bool) -> None:
        admin_role_id = self.get_admin_role_id()
        if admin_role_id is None:
            return
        with Session(self.engine) as session:
            if is_admin:
                exists = session.scalar(
                    select(WarehouseUserRole.id).where(
                        WarehouseUserRole.user_id == int(user_id),
                        WarehouseUserRole.role_id == int(admin_role_id),
                    )
                )
                if exists is None:
                    session.add(
                        WarehouseUserRole(user_id=int(user_id), role_id=int(admin_role_id))
                    )
                    session.commit()
            else:
                session.execute(
                    delete(WarehouseUserRole).where(
                        WarehouseUserRole.user_id == int(user_id),
                        WarehouseUserRole.role_id == int(admin_role_id),
                    )
                )
                session.commit()

    def migrate_legacy_admin_users(self, admin_user_ids: list[int]) -> None:
        admin_role_id = self.get_admin_role_id()
        if admin_role_id is None:
            return
        with Session(self.engine) as session:
            for user_id in admin_user_ids:
                exists = session.scalar(
                    select(WarehouseUserRole.id).where(
                        WarehouseUserRole.user_id == int(user_id),
                        WarehouseUserRole.role_id == int(admin_role_id),
                    )
                )
                if exists is None:
                    session.add(
                        WarehouseUserRole(user_id=int(user_id), role_id=int(admin_role_id))
                    )
            session.commit()

    def list_roles(self, *, with_members: bool = False) -> list[WarehouseRoleRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseRole).order_by(WarehouseRole.sort_order, WarehouseRole.name)
            ).all()
            return [self._role_row(session, row, with_members=with_members) for row in rows]

    def get_role(self, role_id: int, *, with_members: bool = True) -> WarehouseRoleRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseRole, int(role_id))
            if row is None:
                return None
            return self._role_row(session, row, with_members=with_members)

    def create_role(
        self,
        *,
        name: str,
        description: str = "",
        comment: str = "",
        permissions: dict[str, list[str]] | None = None,
    ) -> WarehouseRoleRow:
        name_n = name.strip()
        if not name_n:
            raise ValueError("Название роли обязательно")
        if name_n.casefold() == _DEFAULT_ADMIN_ROLE_NAME.casefold():
            raise ValueError(f"Роль «{_DEFAULT_ADMIN_ROLE_NAME}» зарезервирована системой")
        perms = sanitize_permissions(permissions or {})
        now = int(time.time())
        with Session(self.engine) as session:
            if session.scalar(select(WarehouseRole.id).where(WarehouseRole.name == name_n)):
                raise ValueError(f"Роль «{name_n}» уже существует")
            sort_order = int(
                session.scalar(select(func.max(WarehouseRole.sort_order)).select_from(WarehouseRole)) or 0
            ) + 1
            row = WarehouseRole(
                name=name_n[:128],
                description=description.strip()[:512],
                comment=comment.strip()[:1024],
                permissions_json=permissions_to_json(perms),
                is_admin=False,
                is_system=False,
                sort_order=sort_order,
                created_at_ts=now,
                updated_at_ts=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._role_row(session, row, with_members=True)

    def update_role(
        self,
        role_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        comment: str | None = None,
        permissions: dict[str, list[str]] | None = None,
    ) -> WarehouseRoleRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseRole, int(role_id))
            if row is None:
                return None
            if name is not None:
                name_n = name.strip()
                if not name_n:
                    raise ValueError("Название роли обязательно")
                if (
                    not row.is_system
                    and name_n.casefold() == _DEFAULT_ADMIN_ROLE_NAME.casefold()
                ):
                    raise ValueError(f"Роль «{_DEFAULT_ADMIN_ROLE_NAME}» зарезервирована системой")
                if row.is_system and name_n != row.name:
                    raise ValueError("Системную роль нельзя переименовать")
                other = session.scalar(
                    select(WarehouseRole.id).where(
                        WarehouseRole.name == name_n,
                        WarehouseRole.id != row.id,
                    )
                )
                if other is not None:
                    raise ValueError(f"Роль «{name_n}» уже существует")
                if not row.is_system:
                    row.name = name_n[:128]
            if description is not None:
                row.description = description.strip()[:512]
            if comment is not None:
                row.comment = comment.strip()[:1024]
            if permissions is not None and not row.is_admin:
                row.permissions_json = permissions_to_json(sanitize_permissions(permissions))
            row.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(row)
            return self._role_row(session, row, with_members=True)

    def delete_role(self, role_id: int) -> bool:
        with Session(self.engine) as session:
            row = session.get(WarehouseRole, int(role_id))
            if row is None:
                return False
            if row.is_system:
                raise ValueError("Системную роль удалить нельзя")
            members = int(
                session.scalar(
                    select(func.count())
                    .select_from(WarehouseUserRole)
                    .where(WarehouseUserRole.role_id == row.id)
                )
                or 0
            )
            if members > 0:
                raise ValueError("Нельзя удалить роль, пока к ней привязаны сотрудники")
            session.delete(row)
            session.commit()
            return True

    def get_user_role_ids(self, user_id: int) -> list[int]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseUserRole.role_id)
                .where(WarehouseUserRole.user_id == int(user_id))
                .order_by(WarehouseUserRole.role_id)
            ).all()
            return [int(r) for r in rows]

    def get_user_roles(self, user_id: int) -> list[WarehouseRoleRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseRole)
                .join(WarehouseUserRole, WarehouseUserRole.role_id == WarehouseRole.id)
                .where(WarehouseUserRole.user_id == int(user_id))
                .order_by(WarehouseRole.sort_order, WarehouseRole.name)
            ).all()
            return [self._role_row(session, row, with_members=False) for row in rows]

    def set_user_roles(self, user_id: int, role_ids: list[int]) -> list[WarehouseRoleRow]:
        unique_ids: list[int] = []
        seen: set[int] = set()
        for raw in role_ids:
            try:
                rid = int(raw)
            except (TypeError, ValueError):
                continue
            if rid in seen:
                continue
            seen.add(rid)
            unique_ids.append(rid)
        with Session(self.engine) as session:
            if unique_ids:
                found = session.scalars(
                    select(WarehouseRole.id).where(WarehouseRole.id.in_(unique_ids))
                ).all()
                found_ids = {int(r) for r in found}
                missing = [rid for rid in unique_ids if rid not in found_ids]
                if missing:
                    raise ValueError("Указана несуществующая роль")
            session.execute(
                delete(WarehouseUserRole).where(WarehouseUserRole.user_id == int(user_id))
            )
            for rid in unique_ids:
                session.add(WarehouseUserRole(user_id=int(user_id), role_id=int(rid)))
            session.commit()
        return self.get_user_roles(user_id)

    def resolve_user_access(
        self,
        *,
        user_id: int,
        is_admin: bool,
        legacy_permissions: dict[str, list[str]],
    ) -> tuple[bool, dict[str, list[str]]]:
        roles = self.get_user_roles(user_id)
        if is_admin or any(role.is_admin for role in roles):
            return True, full_access_permissions()
        merged: dict[str, set[str]] = {}
        for role in roles:
            for section_id, items in role.permissions.items():
                merged.setdefault(section_id, set()).update(items)
        if merged:
            return False, {section: sorted(items) for section, items in merged.items()}
        if legacy_permissions:
            return False, legacy_permissions
        return False, {}

    def _role_row(
        self, session: Session, row: WarehouseRole, *, with_members: bool
    ) -> WarehouseRoleRow:
        member_count = int(
            session.scalar(
                select(func.count())
                .select_from(WarehouseUserRole)
                .where(WarehouseUserRole.role_id == row.id)
            )
            or 0
        )
        members: list[WarehouseRoleMember] = []
        if with_members:
            member_rows = session.execute(
                text(
                    """
                    SELECT u.id, u.login, u.display_name
                    FROM warehouse_users u
                    JOIN warehouse_user_roles ur ON ur.user_id = u.id
                    WHERE ur.role_id = :role_id
                    ORDER BY u.display_name, u.login
                    """
                ),
                {"role_id": int(row.id)},
            ).all()
            members = [
                WarehouseRoleMember(
                    id=int(r[0]),
                    login=str(r[1]),
                    display_name=str(r[2] or ""),
                )
                for r in member_rows
            ]
        perms = (
            full_access_permissions()
            if row.is_admin
            else permissions_from_json(row.permissions_json)
        )
        return WarehouseRoleRow(
            id=int(row.id),
            name=row.name,
            description=row.description or "",
            comment=row.comment or "",
            permissions=perms,
            is_admin=bool(row.is_admin),
            is_system=bool(row.is_system),
            sort_order=int(row.sort_order),
            created_at_ts=int(row.created_at_ts),
            updated_at_ts=int(row.updated_at_ts),
            member_count=member_count,
            members=members,
        )

    def role_to_dict(self, row: WarehouseRoleRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "comment": row.comment,
            "permissions": row.permissions,
            "is_admin": row.is_admin,
            "is_system": row.is_system,
            "sort_order": row.sort_order,
            "created_at_ts": row.created_at_ts,
            "updated_at_ts": row.updated_at_ts,
            "member_count": row.member_count,
            "members": [
                {"id": m.id, "login": m.login, "display_name": m.display_name}
                for m in row.members
            ],
        }
