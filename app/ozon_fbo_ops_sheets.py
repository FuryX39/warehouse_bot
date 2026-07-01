"""Поля и строки для таблиц логистов (ОТГРУЗКА) и упаковщиков (ПОСТАВКИ)."""

from __future__ import annotations

from typing import Any

from app.ozon_fbo_labels_storage import supply_labels_url
from app.ozon_fbo_supply_repository import (
    FboSupplyRow,
    SUPPLY_KIND_BOX,
    SUPPLY_KIND_PALLET,
)

OPS_CLIENT_DEFAULT = "OZON"


def _str(raw: Any, limit: int = 512) -> str:
    return str(raw or "").strip()[:limit]


def _date_part(ts: str) -> str:
    val = _str(ts, 32)
    if len(val) >= 10:
        return val[:10]
    return val


def _time_part(ts: str) -> str:
    val = _str(ts, 32)
    if "T" in val and len(val) >= 16:
        return val[11:16]
    return ""


def default_cargoes_desc(supply_kind: str, cargo_count: int) -> str:
    if cargo_count <= 0:
        return ""
    suffix = "П" if supply_kind == SUPPLY_KIND_PALLET else "К"
    return f"{cargo_count} {suffix}"


def total_units(supply: FboSupplyRow) -> int:
    return sum(int(i.quantity or 0) for i in supply.items)


def ops_editable_field_names() -> tuple[str, ...]:
    return (
        "ops_assembly_date",
        "ops_ship_date",
        "ops_movement_number",
        "ops_units_count",
        "ops_cargoes_desc",
        "ops_packing_status",
        "ops_barcode_link",
        "ops_barcode_link_2",
        "ops_packing_comment",
        "ops_client",
        "ops_weight_kg",
        "ops_unload_address",
        "ops_ship_time",
        "ops_expense_doc_number",
        "ops_pallets_ready_time",
        "ops_logistics_comment",
        "ops_car_driver",
    )


def ops_editable_from_supply(supply: FboSupplyRow) -> dict[str, str]:
    return {name: _str(getattr(supply, name, "")) for name in ops_editable_field_names()}


def resolved_ops_values(supply: FboSupplyRow) -> dict[str, str]:
    cargo_count = len(supply.cargoes)
    units = total_units(supply)
    labels_url = supply_labels_url(supply.id) if _str(supply.labels_file) else ""
    editable = ops_editable_from_supply(supply)
    ship_date = editable["ops_ship_date"] or _date_part(supply.timeslot_from)
    ship_time = editable["ops_ship_time"] or _time_part(supply.timeslot_from)
    cargoes_desc = editable["ops_cargoes_desc"] or default_cargoes_desc(supply.supply_kind, cargo_count)
    units_count = editable["ops_units_count"] or (str(units) if units > 0 else "")
    barcode_link = editable["ops_barcode_link"] or labels_url
    unload_address = editable["ops_unload_address"] or _str(supply.dropoff_warehouse_name, 256)
    client = editable["ops_client"] or OPS_CLIENT_DEFAULT
    return {
        "assembly_date": editable["ops_assembly_date"],
        "cluster": _str(supply.ozon_cluster_name, 256),
        "warehouse": _str(supply.ozon_warehouse_name, 256),
        "ship_date": ship_date,
        "supply_id": _str(supply.ozon_supply_id, 64),
        "movement_number": editable["ops_movement_number"],
        "packer": _str(supply.assigned_user_name, 128),
        "units_count": units_count,
        "cargoes_desc": cargoes_desc,
        "packing_status": editable["ops_packing_status"],
        "barcode_link": barcode_link,
        "barcode_link_2": editable["ops_barcode_link_2"],
        "packing_comment": editable["ops_packing_comment"],
        "client": client,
        "weight_kg": editable["ops_weight_kg"],
        "unload_address": unload_address,
        "ship_time": ship_time,
        "logistics_cluster": _str(supply.ozon_cluster_name, 256),
        "expense_doc_number": editable["ops_expense_doc_number"],
        "pallets_ready_time": editable["ops_pallets_ready_time"],
        "logistics_comment": editable["ops_logistics_comment"],
        "car_driver": editable["ops_car_driver"],
    }


PACKING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("assembly_date", "дата сборки"),
    ("cluster", "кластер"),
    ("warehouse", "склад"),
    ("ship_date", "дата отгрузки"),
    ("supply_id", "ID поставки"),
    ("movement_number", "перемещение"),
    ("packer", "упаковщик"),
    ("units_count", "количество единиц"),
    ("cargoes_desc", "количество грузомест"),
    ("packing_status", "статус"),
    ("barcode_link", "ссылка на ШК"),
    ("barcode_link_2", "ссылка на ШК 2"),
    ("packing_comment", "коментарий"),
)

LOGISTICS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ship_date", "Дата отгрузки"),
    ("client", "Клиент"),
    ("cargoes_desc", "Кол-во палет коробок"),
    ("weight_kg", "Вес, кг"),
    ("unload_address", "Адрес выгрузки"),
    ("ship_time", "время отгрузки"),
    ("logistics_cluster", "Кластер"),
    ("expense_doc_number", "Номер расходной накладной"),
    ("pallets_ready_time", "Время когда паллеты готовы"),
    ("logistics_comment", "Комментарий"),
    ("car_driver", "Авто/ водитель"),
)

PACKING_EDITABLE_KEYS: tuple[tuple[str, str, str], ...] = (
    ("ops_assembly_date", "assembly_date", "дата сборки"),
    ("ops_ship_date", "ship_date", "дата отгрузки"),
    ("ops_movement_number", "movement_number", "перемещение"),
    ("ops_units_count", "units_count", "количество единиц"),
    ("ops_cargoes_desc", "cargoes_desc", "количество грузомест"),
    ("ops_packing_status", "packing_status", "статус"),
    ("ops_barcode_link", "barcode_link", "ссылка на ШК"),
    ("ops_barcode_link_2", "barcode_link_2", "ссылка на ШК 2"),
    ("ops_packing_comment", "packing_comment", "коментарий"),
)

LOGISTICS_EDITABLE_KEYS: tuple[tuple[str, str, str], ...] = (
    ("ops_ship_date", "ship_date", "дата отгрузки"),
    ("ops_client", "client", "Клиент"),
    ("ops_cargoes_desc", "cargoes_desc", "Кол-во палет коробок"),
    ("ops_weight_kg", "weight_kg", "Вес, кг"),
    ("ops_unload_address", "unload_address", "Адрес выгрузки"),
    ("ops_ship_time", "ship_time", "время отгрузки"),
    ("ops_expense_doc_number", "expense_doc_number", "Номер расходной накладной"),
    ("ops_pallets_ready_time", "pallets_ready_time", "Время когда паллеты готовы"),
    ("ops_logistics_comment", "logistics_comment", "Комментарий"),
    ("ops_car_driver", "car_driver", "Авто/ водитель"),
)


def packing_row(values: dict[str, str]) -> dict[str, str]:
    return {key: values.get(key, "") for key, _ in PACKING_COLUMNS}


def logistics_row(values: dict[str, str]) -> dict[str, str]:
    return {key: values.get(key, "") for key, _ in LOGISTICS_COLUMNS}


def packing_row_ordered(values: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": key, "label": label, "value": values.get(key, "")} for key, label in PACKING_COLUMNS]


def logistics_row_ordered(values: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": key, "label": label, "value": values.get(key, "")} for key, label in LOGISTICS_COLUMNS]


def ops_sheet_for_supply(supply: FboSupplyRow) -> dict[str, Any]:
    values = resolved_ops_values(supply)
    return {
        "supply_id": supply.id,
        "batch_id": supply.batch_id,
        "ozon_supply_id": supply.ozon_supply_id,
        "title": supply.title,
        "editable": ops_editable_from_supply(supply),
        "values": values,
        "packing_row": packing_row_ordered(values),
        "logistics_row": logistics_row_ordered(values),
        "packing_export": packing_row(values),
        "logistics_export": logistics_row(values),
    }


def ops_summary_for_supplies(supplies: list[FboSupplyRow]) -> dict[str, Any]:
    rows = [ops_sheet_for_supply(s) for s in supplies]
    return {
        "supply_count": len(rows),
        "packing_columns": [{"key": k, "label": l} for k, l in PACKING_COLUMNS],
        "logistics_columns": [{"key": k, "label": l} for k, l in LOGISTICS_COLUMNS],
        "rows": rows,
        "packing_rows": [r["packing_export"] for r in rows],
        "logistics_rows": [r["logistics_export"] for r in rows],
    }
