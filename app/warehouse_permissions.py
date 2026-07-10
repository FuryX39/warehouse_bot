"""Схема разделов новой панели /warehouse и проверка прав доступа."""

from __future__ import annotations

import json
from typing import Any

# Единый источник структуры навигации (сервер + фронт через API).
WAREHOUSE_NAV: list[dict[str, Any]] = [
    {
        "id": "warehouse",
        "title": "Склад",
        "items": [
            {"id": "receiving", "title": "Приемка"},
            {"id": "receipts", "title": "Оприходования"},
            {"id": "writeoffs", "title": "Списания"},
            {"id": "transfers", "title": "Перемещения"},
            {"id": "inventory", "title": "Инвентаризация"},
            {"id": "stock", "title": "Остатки"},
            {"id": "warehouses", "title": "Склады"},
            {"id": "bin-transfers", "title": "Перемещения по ячейкам"},
            {"id": "pick-waves", "title": "Волны отбора"},
        ],
    },
    {
        "id": "sales",
        "title": "Продажи",
        "items": [
            {"id": "customer-orders", "title": "Заказы покупателей"},
            {"id": "commission-sales", "title": "Комиссионные продажи"},
            {"id": "customer-returns", "title": "Возвраты покупателей"},
            {"id": "customer-invoices", "title": "Счета покупателям"},
            {"id": "shipments", "title": "Отгрузки"},
        ],
    },
    {
        "id": "products",
        "title": "Товары",
        "items": [
            {"id": "catalog", "title": "Товары и услуги"},
            {"id": "price-lists", "title": "Прайс-листы"},
            {"id": "price-types", "title": "Виды цен"},
            {"id": "serial-numbers", "title": "Серийные номера"},
            {"id": "marking-codes", "title": "Коды маркировки"},
            {"id": "marking", "title": "Маркировка"},
        ],
    },
    {
        "id": "reports",
        "title": "Отчеты",
        "items": [
            {"id": "commissioner-report", "title": "Отчет комиссионера"},
            {"id": "sales-analysis", "title": "Анализ продаж"},
            {"id": "stock-by-warehouse", "title": "Остатки товаров на складах"},
            {"id": "stock-movement", "title": "Движение товаров"},
            {"id": "demand", "title": "Потребность товаров"},
            {"id": "sales-funnel", "title": "Воронка продаж"},
            {"id": "product-turnover", "title": "Оборачиваемость товаров"},
            {"id": "sales-report", "title": "Отчет по продажам"},
        ],
    },
    {
        "id": "marketplaces",
        "title": "Маркетплейсы",
        "items": [
            {"id": "fbs", "title": "FBS"},
            {"id": "ozon-fbo-supplies", "title": "Поставки FBO"},
            {"id": "ozon-fbo-packing", "title": "Сборка FBO"},
            {"id": "stock-sync", "title": "Синхронизация остатков"},
            {"id": "pick-lists", "title": "Листы подбора"},
            {"id": "route-sheets", "title": "Маршрутные листы"},
        ],
    },
    {
        "id": "tools",
        "title": "Доп. инструменты",
        "items": [
            {"id": "pdf-merge", "title": "PDFMerge"},
        ],
    },
    {
        "id": "tasks",
        "title": "Задачи",
        "items": [
            {"id": "task-list", "title": "Список задач"},
            {"id": "tasks-summary", "title": "Сводная по задачам"},
        ],
    },
    {
        "id": "crm",
        "title": "CRM",
        "items": [
            {"id": "counterparties", "title": "Контрагенты"},
            {"id": "contracts", "title": "Договоры"},
        ],
    },
    {
        "id": "employees",
        "title": "Сотрудники",
        "admin_only": True,
        "items": [
            {"id": "employees", "title": "Сотрудники"},
            {"id": "schedule", "title": "График"},
            {"id": "roles", "title": "Роли"},
        ],
    },
    {
        "id": "admin",
        "title": "Админ панель",
        "admin_only": True,
        "items": [
            {"id": "env", "title": ".env настройки"},
        ],
    },
]

EMPLOYEES_SECTION_ID = "employees"


def permissions_schema() -> list[dict[str, Any]]:
    """Разделы для редактора прав (без admin-only «Сотрудники»)."""
    out: list[dict[str, Any]] = []
    for section in WAREHOUSE_NAV:
        if section.get("admin_only"):
            continue
        out.append(
            {
                "id": section["id"],
                "title": section["title"],
                "items": [{"id": i["id"], "title": i["title"]} for i in section.get("items", [])],
            }
        )
    return out


def full_access_permissions() -> dict[str, list[str]]:
    """Все подразделы для каждой обычной вкладки."""
    perms: dict[str, list[str]] = {}
    for section in WAREHOUSE_NAV:
        if section.get("admin_only"):
            continue
        perms[section["id"]] = [item["id"] for item in section.get("items", [])]
    return perms


def normalize_permissions(raw: dict[str, Any] | None) -> dict[str, list[str]]:
    if not raw:
        return {}
    out: dict[str, list[str]] = {}
    for section_id, val in raw.items():
        if val is True:
            for section in WAREHOUSE_NAV:
                if section["id"] == section_id and not section.get("admin_only"):
                    out[section_id] = [i["id"] for i in section.get("items", [])]
                    break
            continue
        if isinstance(val, list):
            out[section_id] = [str(x) for x in val if str(x).strip()]
    return out


def permissions_to_json(perms: dict[str, list[str]]) -> str:
    return json.dumps(perms, ensure_ascii=False, separators=(",", ":"))


def permissions_from_json(raw: str | None) -> dict[str, list[str]]:
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return normalize_permissions(data)


def can_access_item(*, is_admin: bool, permissions: dict[str, list[str]], section_id: str, item_id: str) -> bool:
    if is_admin:
        return True
    for section in WAREHOUSE_NAV:
        if section["id"] == section_id and section.get("admin_only"):
            return False
    allowed = permissions.get(section_id)
    if not allowed:
        return False
    return item_id in allowed


def sanitize_permissions(raw: dict[str, Any] | None) -> dict[str, list[str]]:
    """Оставляет только известные разделы и подпункты (без admin-only)."""
    normalized = normalize_permissions(raw)
    out: dict[str, list[str]] = {}
    for section in WAREHOUSE_NAV:
        if section.get("admin_only"):
            continue
        section_id = section["id"]
        allowed_ids = {item["id"] for item in section.get("items", [])}
        picked = [item_id for item_id in normalized.get(section_id, []) if item_id in allowed_ids]
        if picked:
            out[section_id] = picked
    return out


def filter_nav_for_user(*, is_admin: bool, permissions: dict[str, list[str]]) -> list[dict[str, Any]]:
    nav: list[dict[str, Any]] = []
    for section in WAREHOUSE_NAV:
        if section.get("admin_only"):
            if not is_admin:
                continue
            nav.append(
                {
                    "id": section["id"],
                    "title": section["title"],
                    "items": [{"id": i["id"], "title": i["title"]} for i in section.get("items", [])],
                }
            )
            continue
        items = [
            {"id": item["id"], "title": item["title"]}
            for item in section.get("items", [])
            if can_access_item(
                is_admin=is_admin,
                permissions=permissions,
                section_id=section["id"],
                item_id=item["id"],
            )
        ]
        if items:
            nav.append({"id": section["id"], "title": section["title"], "items": items})
    return nav
