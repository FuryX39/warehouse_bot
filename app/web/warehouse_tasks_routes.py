"""HTTP API задач: панель /warehouse и внешнее планирование /api/v1/tasks."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, HTTPException, Request

from app.web.warehouse_tasks_api_auth import TasksApiActor, resolve_created_by
from app.crm_repository import CrmRepository
from app.warehouse_tasks_repository import ENTITY_LABELS, WarehouseTasksRepository
from app.warehouse_users_repository import WarehouseUsersRepository

logger = logging.getLogger(__name__)


def register_warehouse_tasks_routes(
    app,
    tasks_repo: WarehouseTasksRepository,
    users_repo: WarehouseUsersRepository,
    crm_repo: CrmRepository,
    require_tasks_access,
) -> None:
    prefixes = ("/api/warehouse/tasks", "/api/v1/tasks")

    for prefix in prefixes:
        _register_on_prefix(app, prefix, tasks_repo, users_repo, crm_repo, require_tasks_access)


def _register_on_prefix(
    app,
    prefix: str,
    tasks_repo: WarehouseTasksRepository,
    users_repo: WarehouseUsersRepository,
    crm_repo: CrmRepository,
    require_tasks_access,
) -> None:
    @app.get(f"{prefix}/schema")
    async def api_tasks_schema(_: TasksApiActor = Depends(require_tasks_access)) -> dict:
        schema = tasks_repo.api_schema()
        schema["base_paths"] = ["/api/warehouse/tasks", "/api/v1/tasks"]
        schema["endpoints"] = _api_endpoints_catalog()
        return schema

    @app.get(f"{prefix}/meta")
    async def api_tasks_meta(
        actor: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        meta = tasks_repo.get_meta()
        meta["assignees"] = users_repo.list_assignee_picker()
        meta["counterparties"] = crm_repo.list_counterparty_picker()
        meta["document_types"] = [
            {"id": key, "title": label} for key, label in ENTITY_LABELS.items()
        ]
        meta["current_user_id"] = actor.created_by_user_id
        if actor.user is not None:
            meta["current_user_name"] = str(actor.user.display_name or actor.user.login)
        else:
            meta["current_user_name"] = ""
        meta["auth_via_api_token"] = actor.via_api_token
        return meta

    @app.get(f"{prefix}/assignees")
    async def api_tasks_assignees(
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        return {"assignees": users_repo.list_assignee_picker()}

    @app.get(f"{prefix}/types")
    async def api_tasks_types_list(
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        return {"task_types": tasks_repo.list_task_types()}

    @app.get(f"{prefix}/types/{{type_id}}")
    async def api_tasks_type_get(
        type_id: int,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        row = tasks_repo.get_task_type(type_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Тип задачи не найден")
        return {"task_type": row}

    @app.post(f"{prefix}/types")
    async def api_tasks_type_create(
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        try:
            row = tasks_repo.create_task_type(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"task_type": row}

    @app.put(f"{prefix}/types")
    async def api_tasks_types_bulk_save(
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        return {"task_types": tasks_repo.save_task_types(items)}

    @app.put(f"{prefix}/types/{{type_id}}")
    async def api_tasks_type_update(
        type_id: int,
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        try:
            row = tasks_repo.update_task_type(type_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Тип задачи не найден")
        return {"task_type": row}

    @app.delete(f"{prefix}/types/{{type_id}}")
    async def api_tasks_type_delete(
        type_id: int,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        try:
            if not tasks_repo.delete_task_type(type_id):
                raise HTTPException(status_code=404, detail="Тип задачи не найден")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.get(f"{prefix}/custom-fields")
    async def api_tasks_custom_fields_list(
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        return {"custom_fields": tasks_repo.list_custom_fields()}

    @app.get(f"{prefix}/custom-fields/{{field_id}}")
    async def api_tasks_custom_field_get(
        field_id: int,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        row = tasks_repo.get_custom_field(field_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Дополнительное поле не найдено")
        return {"custom_field": row}

    @app.post(f"{prefix}/custom-fields")
    async def api_tasks_custom_field_create(
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        try:
            row = tasks_repo.create_custom_field(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"custom_field": row}

    @app.put(f"{prefix}/custom-fields")
    async def api_tasks_custom_fields_bulk_save(
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        return {"custom_fields": tasks_repo.save_custom_fields(items)}

    @app.put(f"{prefix}/custom-fields/{{field_id}}")
    async def api_tasks_custom_field_update(
        field_id: int,
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        try:
            row = tasks_repo.update_custom_field(field_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Дополнительное поле не найдено")
        return {"custom_field": row}

    @app.delete(f"{prefix}/custom-fields/{{field_id}}")
    async def api_tasks_custom_field_delete(
        field_id: int,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        if not tasks_repo.delete_custom_field(field_id):
            raise HTTPException(status_code=404, detail="Дополнительное поле не найдено")
        return {"ok": True}

    @app.get(f"{prefix}/documents/search")
    async def api_tasks_search_documents(
        request: Request,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        params = request.query_params
        q = str(params.get("q") or "").strip()
        entity_type = str(params.get("entity_type") or "").strip().lower()
        try:
            limit = int(params.get("limit") or 40)
        except ValueError:
            limit = 40
        items = tasks_repo.search_documents(q=q, entity_type=entity_type, limit=limit)
        return {"documents": items}

    @app.get(f"{prefix}/documents/{{entity_type}}/{{entity_id}}/tasks")
    async def api_tasks_by_document(
        entity_type: str,
        entity_id: int,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        try:
            rows = tasks_repo.list_tasks_by_document(entity_type, entity_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"tasks": [tasks_repo.task_to_dict(r) for r in rows]}

    @app.get(f"{prefix}/summary/calendar")
    async def api_tasks_summary_calendar(
        request: Request,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        from datetime import date as date_cls

        params = request.query_params
        today = date_cls.today()
        try:
            year = int(params.get("year") or today.year)
            month = int(params.get("month") or today.month)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректные year или month") from exc
        filters = _filters_from_query(params, skip_pagination=True)
        try:
            return tasks_repo.cost_summary_calendar(year=year, month=month, filters=filters)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(f"{prefix}/planning/summary")
    async def api_tasks_planning_summary(
        request: Request,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        params = request.query_params
        date_from = str(params.get("date_from") or params.get("end_date_from") or "").strip()
        date_to = str(params.get("date_to") or params.get("end_date_to") or "").strip()
        group_by = str(params.get("group_by") or "day").strip()
        filters = _filters_from_query(params, skip_pagination=True)
        return tasks_repo.planning_summary(
            date_from=date_from,
            date_to=date_to,
            group_by=group_by,
            filters=filters,
        )

    @app.get(f"{prefix}/planning/calendar")
    async def api_tasks_planning_calendar(
        request: Request,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        params = request.query_params
        date_from = str(params.get("date_from") or params.get("end_date_from") or "").strip()
        date_to = str(params.get("date_to") or params.get("end_date_to") or "").strip()
        filters = _filters_from_query(params, skip_pagination=True)
        return tasks_repo.planning_calendar(
            date_from=date_from,
            date_to=date_to,
            filters=filters,
        )

    @app.get(prefix)
    async def api_tasks_list(
        request: Request,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        params = request.query_params
        filters = _filters_from_query(params)
        limit, offset = _pagination_from_query(params)
        rows = tasks_repo.list_tasks(filters, limit=limit, offset=offset)
        total = tasks_repo.count_tasks(filters)
        return {
            "tasks": [tasks_repo.task_to_dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get(f"{prefix}/{{task_id}}")
    async def api_tasks_get(
        task_id: int,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        row = tasks_repo.get_task(task_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return {"task": tasks_repo.task_to_dict(row)}

    @app.post(f"{prefix}/bulk")
    async def api_tasks_bulk_create(
        body: dict,
        actor: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        try:
            created_by = resolve_created_by(actor, body)
            rows = tasks_repo.bulk_create_tasks(items, created_by_user_id=created_by)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"tasks": [tasks_repo.task_to_dict(r) for r in rows]}

    @app.post(prefix)
    async def api_tasks_create(
        body: dict,
        actor: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        try:
            created_by = resolve_created_by(actor, body)
            row = tasks_repo.create_task(body, created_by_user_id=created_by)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Ошибка создания задачи")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"task": tasks_repo.task_to_dict(row)}

    @app.put(f"{prefix}/{{task_id}}")
    async def api_tasks_update(
        task_id: int,
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        try:
            row = tasks_repo.update_task(task_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return {"task": tasks_repo.task_to_dict(row)}

    @app.patch(f"{prefix}/{{task_id}}")
    async def api_tasks_patch(
        task_id: int,
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        try:
            row = tasks_repo.patch_task(task_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return {"task": tasks_repo.task_to_dict(row)}

    @app.delete(f"{prefix}/{{task_id}}")
    async def api_tasks_delete(
        task_id: int,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        if not tasks_repo.delete_task(task_id):
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return {"ok": True}

    @app.put(f"{prefix}/{{task_id}}/assignees")
    async def api_tasks_set_assignees(
        task_id: int,
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        raw = body.get("assignee_ids")
        if not isinstance(raw, list):
            raise HTTPException(status_code=400, detail="assignee_ids должен быть массивом")
        try:
            row = tasks_repo.set_assignees(task_id, [int(x) for x in raw])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректные assignee_ids") from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return {"task": tasks_repo.task_to_dict(row)}

    @app.post(f"{prefix}/{{task_id}}/assignees")
    async def api_tasks_add_assignees(
        task_id: int,
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        raw = body.get("assignee_ids")
        if not isinstance(raw, list):
            raise HTTPException(status_code=400, detail="assignee_ids должен быть массивом")
        try:
            row = tasks_repo.add_assignees(task_id, [int(x) for x in raw])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректные assignee_ids") from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return {"task": tasks_repo.task_to_dict(row)}

    @app.delete(f"{prefix}/{{task_id}}/assignees/{{user_id}}")
    async def api_tasks_remove_assignee(
        task_id: int,
        user_id: int,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        row = tasks_repo.remove_assignee(task_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return {"task": tasks_repo.task_to_dict(row)}

    @app.put(f"{prefix}/{{task_id}}/documents")
    async def api_tasks_set_documents(
        task_id: int,
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        raw = body.get("documents")
        if not isinstance(raw, list):
            raise HTTPException(status_code=400, detail="documents должен быть массивом")
        try:
            row = tasks_repo.set_documents(task_id, raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return {"task": tasks_repo.task_to_dict(row)}

    @app.post(f"{prefix}/{{task_id}}/documents")
    async def api_tasks_link_document(
        task_id: int,
        body: dict,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        entity_type = str(body.get("entity_type") or "").strip().lower()
        try:
            entity_id = int(body.get("entity_id"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Укажите entity_type и entity_id") from exc
        try:
            row = tasks_repo.link_document(task_id, entity_type, entity_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return {"task": tasks_repo.task_to_dict(row)}

    @app.delete(f"{prefix}/{{task_id}}/documents/{{entity_type}}/{{entity_id}}")
    async def api_tasks_unlink_document(
        task_id: int,
        entity_type: str,
        entity_id: int,
        _: TasksApiActor = Depends(require_tasks_access),
    ) -> dict:
        row = tasks_repo.unlink_document(task_id, entity_type, entity_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return {"task": tasks_repo.task_to_dict(row)}


def _filters_from_query(params: Any, *, skip_pagination: bool = False) -> dict[str, str]:
    keys = (
        "q",
        "comment",
        "task_type_id",
        "assignee_id",
        "created_by_user_id",
        "counterparty_id",
        "entity_type",
        "entity_id",
        "start_date_from",
        "start_date_to",
        "end_date_from",
        "end_date_to",
    )
    out: dict[str, str] = {}
    for key in keys:
        raw = params.get(key)
        if raw is not None and str(raw).strip():
            out[key] = str(raw).strip()
    if not skip_pagination:
        for key in ("limit", "offset"):
            raw = params.get(key)
            if raw is not None and str(raw).strip():
                out[key] = str(raw).strip()
    return out


def _pagination_from_query(params: Any) -> tuple[int, int]:
    try:
        limit = int(params.get("limit") or 500)
    except ValueError:
        limit = 500
    try:
        offset = int(params.get("offset") or 0)
    except ValueError:
        offset = 0
    return max(1, min(2000, limit)), max(0, offset)


def _api_endpoints_catalog() -> list[dict[str, str]]:
    routes = [
        ("GET", "/schema", "Описание API и полей"),
        ("GET", "/meta", "Справочники для UI"),
        ("GET", "/assignees", "Список сотрудников для назначения"),
        ("GET", "/types", "Список типов задач"),
        ("GET", "/types/{id}", "Тип задачи"),
        ("POST", "/types", "Создать тип задачи"),
        ("PUT", "/types", "Массовое сохранение типов (как в панели)"),
        ("PUT", "/types/{id}", "Обновить тип задачи"),
        ("DELETE", "/types/{id}", "Удалить тип задачи"),
        ("GET", "/custom-fields", "Список дополнительных полей"),
        ("GET", "/custom-fields/{id}", "Дополнительное поле"),
        ("POST", "/custom-fields", "Создать дополнительное поле"),
        ("PUT", "/custom-fields", "Массовое сохранение дополнительных полей"),
        ("PUT", "/custom-fields/{id}", "Обновить дополнительное поле"),
        ("DELETE", "/custom-fields/{id}", "Удалить дополнительное поле"),
        ("GET", "/documents/search", "Поиск документов для привязки"),
        ("GET", "/documents/{entity_type}/{entity_id}/tasks", "Задачи по документу"),
        ("GET", "/summary/calendar", "Сводная по задачам: сумма стоимостей групп по дате начала"),
        ("GET", "/planning/summary", "Сводка для планирования (group_by=day|task_type|assignee)"),
        ("GET", "/planning/calendar", "Календарь задач по дате окончания"),
        ("GET", "", "Список задач с фильтрами и пагинацией"),
        ("GET", "/{id}", "Задача"),
        ("POST", "", "Создать задачу"),
        ("POST", "/bulk", "Массовое создание задач"),
        ("PUT", "/{id}", "Полное обновление задачи"),
        ("PATCH", "/{id}", "Частичное обновление задачи"),
        ("DELETE", "/{id}", "Удалить задачу"),
        ("PUT", "/{id}/assignees", "Заменить ответственных"),
        ("POST", "/{id}/assignees", "Добавить ответственных"),
        ("DELETE", "/{id}/assignees/{user_id}", "Убрать ответственного"),
        ("PUT", "/{id}/documents", "Заменить привязанные документы"),
        ("POST", "/{id}/documents", "Привязать документ"),
        ("DELETE", "/{id}/documents/{entity_type}/{entity_id}", "Отвязать документ"),
    ]
    return [{"method": m, "path": p, "description": d} for m, p, d in routes]
