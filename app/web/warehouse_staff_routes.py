"""HTTP API сотрудников и ролей новой панели /warehouse."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

from app.warehouse_roles_repository import WarehouseRolesRepository
from app.warehouse_users_repository import WarehouseUserRow, WarehouseUsersRepository
from app.warehouse_permissions import permissions_schema, sanitize_permissions


def _employee_filters_from_query(params: Any) -> dict[str, str]:
    keys = (
        "q",
        "display_name",
        "login",
        "group_id",
        "telegram_nick",
        "role_id",
        "is_active",
    )
    out: dict[str, str] = {}
    for key in keys:
        raw = params.get(key)
        if raw is not None and str(raw).strip():
            out[key] = str(raw).strip()
    return out


def register_warehouse_staff_routes(
    app,
    users_repo: WarehouseUsersRepository,
    roles_repo: WarehouseRolesRepository,
    require_warehouse_admin,
) -> None:
    def _employee_dict(user: WarehouseUserRow) -> dict:
        roles = roles_repo.get_user_roles(user.id)
        role_items = [{"id": r.id, "name": r.name, "is_admin": r.is_admin} for r in roles]
        return users_repo.user_to_public_dict(
            user,
            roles=role_items,
            role_ids=[r.id for r in roles],
        )

    def _apply_user_roles(user_id: int, role_ids: list[int]) -> WarehouseUserRow | None:
        roles = roles_repo.set_user_roles(user_id, role_ids)
        has_admin = any(role.is_admin for role in roles)
        user = users_repo.update_user(user_id, is_admin=has_admin)
        roles_repo.sync_admin_role_for_user(user_id, is_admin=has_admin)
        return user

    @app.get("/api/warehouse/roles")
    async def api_warehouse_roles_list(
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        rows = roles_repo.list_roles(with_members=False)
        return {"roles": [roles_repo.role_to_dict(row) for row in rows]}

    @app.get("/api/warehouse/roles/{role_id}")
    async def api_warehouse_role_get(
        role_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        row = roles_repo.get_role(role_id, with_members=True)
        if row is None:
            raise HTTPException(status_code=404, detail="Роль не найдена")
        return {"role": roles_repo.role_to_dict(row)}

    @app.post("/api/warehouse/roles")
    async def api_warehouse_role_create(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        permissions_raw = body.get("permissions")
        permissions = None
        if permissions_raw is not None:
            if not isinstance(permissions_raw, dict):
                raise HTTPException(status_code=400, detail="permissions должен быть объектом")
            permissions = sanitize_permissions(permissions_raw)
        try:
            row = roles_repo.create_role(
                name=str(body.get("name") or ""),
                description=str(body.get("description") or ""),
                comment=str(body.get("comment") or ""),
                permissions=permissions,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"role": roles_repo.role_to_dict(row)}

    @app.put("/api/warehouse/roles/{role_id}")
    async def api_warehouse_role_update(
        role_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        permissions_raw = body.get("permissions")
        permissions = None
        if permissions_raw is not None:
            if not isinstance(permissions_raw, dict):
                raise HTTPException(status_code=400, detail="permissions должен быть объектом")
            permissions = sanitize_permissions(permissions_raw)
        try:
            row = roles_repo.update_role(
                role_id,
                name=str(body.get("name")) if body.get("name") is not None else None,
                description=str(body.get("description"))
                if body.get("description") is not None
                else None,
                comment=str(body.get("comment")) if body.get("comment") is not None else None,
                permissions=permissions,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Роль не найдена")
        return {"role": roles_repo.role_to_dict(row)}

    @app.delete("/api/warehouse/roles/{role_id}")
    async def api_warehouse_role_delete(
        role_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        try:
            deleted = roles_repo.delete_role(role_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Роль не найдена")
        return {"ok": True}

    @app.get("/api/warehouse/employees/meta")
    async def api_warehouse_employees_meta(
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        return users_repo.get_employee_meta()

    @app.put("/api/warehouse/employees/groups")
    async def api_warehouse_employee_groups_save(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        return {"groups": users_repo.save_employee_groups(items)}

    @app.get("/api/warehouse/employees")
    async def api_warehouse_employees_list(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        filters = _employee_filters_from_query(request.query_params)
        users = users_repo.list_users(filters)
        return {"employees": [_employee_dict(user) for user in users]}

    @app.post("/api/warehouse/employees")
    async def api_warehouse_employee_create(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        login = str(body.get("login") or "").strip()
        password = str(body.get("password") or "")
        display_name = str(body.get("display_name") or "").strip()
        is_active = bool(body.get("is_active", True))
        group_id = body.get("group_id")
        telegram_nick = str(body.get("telegram_nick") or "")
        role_ids_raw = body.get("role_ids") or []
        if not isinstance(role_ids_raw, list):
            raise HTTPException(status_code=400, detail="role_ids должен быть массивом")
        role_ids = []
        for raw in role_ids_raw:
            try:
                role_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        try:
            user = users_repo.create_user(
                login=login,
                password=password,
                display_name=display_name,
                group_id=int(group_id) if group_id not in (None, "") else None,
                telegram_nick=telegram_nick,
                is_admin=False,
                is_active=is_active,
                permissions={},
            )
            user = _apply_user_roles(user.id, role_ids) or user
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"employee": _employee_dict(user)}

    @app.put("/api/warehouse/employees/{user_id}")
    async def api_warehouse_employee_update(
        user_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        login = body.get("login")
        display_name = body.get("display_name")
        password = body.get("password")
        is_active = body.get("is_active")
        group_id_set = "group_id" in body
        group_id = body.get("group_id")
        telegram_nick = body.get("telegram_nick")
        role_ids_raw = body.get("role_ids")
        role_ids = None
        if role_ids_raw is not None:
            if not isinstance(role_ids_raw, list):
                raise HTTPException(status_code=400, detail="role_ids должен быть массивом")
            role_ids = []
            for raw in role_ids_raw:
                try:
                    role_ids.append(int(raw))
                except (TypeError, ValueError):
                    continue
        try:
            kwargs: dict[str, Any] = {}
            if login is not None:
                kwargs["login"] = str(login).strip()
            if password is not None:
                kwargs["password"] = str(password)
            if display_name is not None:
                kwargs["display_name"] = str(display_name)
            if is_active is not None:
                kwargs["is_active"] = bool(is_active)
            if group_id_set:
                kwargs["group_id"] = int(group_id) if group_id not in (None, "") else None
            if telegram_nick is not None:
                kwargs["telegram_nick"] = str(telegram_nick)
            updated = users_repo.update_user(user_id, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if updated is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if role_ids is not None:
            updated = _apply_user_roles(user_id, role_ids) or updated
        return {"employee": _employee_dict(updated)}

    @app.get("/api/warehouse/permissions-schema")
    async def api_warehouse_permissions_schema(
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        return {"schema": permissions_schema()}
