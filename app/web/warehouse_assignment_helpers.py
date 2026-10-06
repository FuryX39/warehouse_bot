"""Общие проверки назначения упаковщиков на задания."""

from __future__ import annotations

from typing import Any

from app.warehouse_users_repository import WarehouseUsersRepository


def requested_packer_ids(
    body: dict[str, Any] | None,
    users_repo: WarehouseUsersRepository,
) -> list[int]:
    payload = body if isinstance(body, dict) else {}
    available = {
        int(item["id"]): str(item.get("display_name") or item.get("login") or item["id"])
        for item in users_repo.list_assignee_picker()
    }
    if payload.get("all"):
        ids = list(available)
    else:
        raw = payload.get("packer_user_ids")
        if not isinstance(raw, list):
            raise ValueError("packer_user_ids должен быть массивом")
        try:
            ids = list(dict.fromkeys(int(value) for value in raw if int(value) > 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректный упаковщик") from exc
    if not ids:
        raise ValueError("Назначьте хотя бы одного упаковщика")
    unavailable = [user_id for user_id in ids if user_id not in available]
    if unavailable:
        raise ValueError("Один из выбранных упаковщиков недоступен для назначения")
    return ids
