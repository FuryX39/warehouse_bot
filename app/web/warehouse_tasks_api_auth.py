"""Аутентификация API задач: сессия панели или Bearer-токен."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException, Request

from app.warehouse_users_repository import WarehouseUserRow


@dataclass
class TasksApiActor:
    """Кто вызывает API задач."""

    user: WarehouseUserRow | None
    via_api_token: bool

    @property
    def created_by_user_id(self) -> int | None:
        if self.user is None:
            return None
        return int(self.user.id)


def extract_api_token(request: Request) -> str:
    auth = str(request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = str(request.headers.get("x-api-key") or "").strip()
    if header:
        return header
    return str(request.query_params.get("api_token") or "").strip()


def make_require_tasks_access(
    api_token: str,
    get_session_user: Callable[[Request], WarehouseUserRow | None],
) -> Any:
    configured_token = str(api_token or "").strip()

    async def require_tasks_access(request: Request) -> TasksApiActor:
        if configured_token:
            provided = extract_api_token(request)
            if provided and secrets.compare_digest(provided, configured_token):
                return TasksApiActor(user=None, via_api_token=True)
        user = get_session_user(request)
        if user is not None:
            return TasksApiActor(user=user, via_api_token=False)
        if configured_token:
            raise HTTPException(
                status_code=401,
                detail="Требуется Bearer-токен (Authorization: Bearer <token>) или вход в панель",
            )
        raise HTTPException(status_code=401, detail="Требуется вход в новую панель")

    return require_tasks_access


def resolve_created_by(actor: TasksApiActor, body: dict[str, Any]) -> int | None:
    if actor.user is not None:
        return int(actor.user.id)
    raw = body.get("created_by_user_id")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректный created_by_user_id") from exc
