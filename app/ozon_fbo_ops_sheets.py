"""Поля и строки для таблиц логистов (ОТГРУЗКА) и упаковщиков (ПОСТАВКИ)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.ozon_fbo_labels_storage import supply_labels_url

if TYPE_CHECKING:
    from app.ozon_fbo_supply_repository import FboBatchRow, FboSupplyRow

SUPPLY_KIND_PALLET = "pallet"
SUPPLY_KIND_BOX = "box"


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


def total_units(supply: FboSupplyRow) -> int:  # noqa: F821
    return sum(int(i.quantity or 0) for i in supply.items)


def batch_ops_editable_field_names() -> tuple[str, ...]:
    return (
        "ops_assembly_date",
        "ops_ship_date",
        "ops_packing_status",
        "ops_barcode_link_2",
        "ops_packing_comment",
        "ops_counterparty_id",
        "ops_weight_kg",
        "ops_unload_address_id",
        "ops_ship_time",
        "ops_expense_doc_number",
        "ops_pallets_ready_time",
        "ops_logistics_comment",
        "ops_car_driver",
    )


def ops_editable_from_batch(batch: FboBatchRow) -> dict[str, str]:  # noqa: F821
    out: dict[str, str] = {}
    for name in batch_ops_editable_field_names():
        val = getattr(batch, name, "")
        if name.endswith("_id"):
            out[name] = str(val) if val is not None and val != "" else ""
        else:
            out[name] = _str(val)
    return out


def resolved_ops_values(
    supply: FboSupplyRow,  # noqa: F821
    batch: FboBatchRow,  # noqa: F821
    *,
    counterparty_name: str = "",
    unload_address: str = "",
) -> dict[str, str]:
    cargo_count = len(supply.cargoes)
    units = total_units(supply)
    labels_url = supply_labels_url(supply.id) if _str(supply.labels_file) else ""
    editable = ops_editable_from_batch(batch)
    ship_date = editable["ops_ship_date"] or _date_part(batch.timeslot_from or supply.timeslot_from)
    ship_time = editable["ops_ship_time"] or _time_part(batch.timeslot_from or supply.timeslot_from)
    cargoes_desc = default_cargoes_desc(supply.supply_kind, cargo_count)
    units_count = str(units) if units > 0 else ""
    if not unload_address:
        unload_address = _str(batch.dropoff_warehouse_name or supply.dropoff_warehouse_name, 256)
    client = counterparty_name or "—"
    return {
        "assembly_date": editable["ops_assembly_date"],
        "cluster": _str(supply.ozon_cluster_name, 256),
        "warehouse": _str(supply.ozon_warehouse_name, 256),
        "ship_date": ship_date,
        "supply_id": _str(supply.ozon_supply_id, 64),
        "movement_number": "",
        "packer": _str(supply.assigned_user_name, 128),
        "units_count": units_count,
        "cargoes_desc": cargoes_desc,
        "packing_status": editable["ops_packing_status"],
        "barcode_link": labels_url,
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

BATCH_PACKING_EDITABLE: tuple[tuple[str, str, str, str], ...] = (
    ("ops_assembly_date", "assembly_date", "дата сборки", "date"),
    ("ops_ship_date", "ship_date", "дата отгрузки", "date"),
    ("ops_packing_status", "packing_status", "статус", "text"),
    ("ops_barcode_link_2", "barcode_link_2", "ссылка на ШК 2", "text"),
    ("ops_packing_comment", "packing_comment", "коментарий", "text"),
)

BATCH_LOGISTICS_SELECTS: tuple[tuple[str, str, str, str], ...] = (
    ("ops_counterparty_id", "client", "Клиент", "counterparty"),
    ("ops_unload_address_id", "unload_address", "Адрес выгрузки", "unload_address"),
)

BATCH_LOGISTICS_EDITABLE: tuple[tuple[str, str, str, str], ...] = (
    ("ops_weight_kg", "weight_kg", "Вес, кг", "text"),
    ("ops_ship_time", "ship_time", "время отгрузки", "text"),
    ("ops_expense_doc_number", "expense_doc_number", "Номер расходной накладной", "text"),
    ("ops_pallets_ready_time", "pallets_ready_time", "Время когда паллеты готовы", "text"),
    ("ops_logistics_comment", "logistics_comment", "Комментарий", "text"),
    ("ops_car_driver", "car_driver", "Авто/ водитель", "text"),
)


def packing_row(values: dict[str, str]) -> dict[str, str]:
    return {key: values.get(key, "") for key, _ in PACKING_COLUMNS}


def logistics_row(values: dict[str, str]) -> dict[str, str]:
    return {key: values.get(key, "") for key, _ in LOGISTICS_COLUMNS}


def packing_row_ordered(values: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": key, "label": label, "value": values.get(key, "")} for key, label in PACKING_COLUMNS]


def logistics_row_ordered(values: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": key, "label": label, "value": values.get(key, "")} for key, label in LOGISTICS_COLUMNS]


def ops_sheet_for_supply(
    supply: FboSupplyRow,  # noqa: F821
    batch: FboBatchRow,  # noqa: F821
    *,
    counterparty_name: str = "",
    unload_address: str = "",
) -> dict[str, Any]:
    values = resolved_ops_values(
        supply,
        batch,
        counterparty_name=counterparty_name,
        unload_address=unload_address,
    )
    return {
        "supply_id": supply.id,
        "batch_id": supply.batch_id,
        "ozon_supply_id": supply.ozon_supply_id,
        "title": supply.title,
        "values": values,
        "packing_row": packing_row_ordered(values),
        "logistics_row": logistics_row_ordered(values),
        "packing_export": packing_row(values),
        "logistics_export": logistics_row(values),
    }


def ops_sheet_for_batch(
    batch: FboBatchRow,  # noqa: F821
    supplies: list[FboSupplyRow],  # noqa: F821
    *,
    counterparty_name: str = "",
    unload_address: str = "",
) -> dict[str, Any]:
    editable = ops_editable_from_batch(batch)
    rows = [
        ops_sheet_for_supply(
            s,
            batch,
            counterparty_name=counterparty_name,
            unload_address=unload_address,
        )
        for s in supplies
    ]
    return {
        "batch_id": batch.id,
        "editable": editable,
        "counterparty_name": counterparty_name,
        "unload_address": unload_address,
        "supply_rows": rows,
        "packing_rows": [r["packing_export"] for r in rows],
        "logistics_rows": [r["logistics_export"] for r in rows],
    }


def ops_summary_for_batch(
    batch: FboBatchRow,  # noqa: F821
    supplies: list[FboSupplyRow],  # noqa: F821
    *,
    counterparty_name: str = "",
    unload_address: str = "",
) -> dict[str, Any]:
    sheet = ops_sheet_for_batch(
        batch,
        supplies,
        counterparty_name=counterparty_name,
        unload_address=unload_address,
    )
    return {
        "supply_count": len(supplies),
        "batch_id": batch.id,
        "packing_columns": [{"key": k, "label": l} for k, l in PACKING_COLUMNS],
        "logistics_columns": [{"key": k, "label": l} for k, l in LOGISTICS_COLUMNS],
        "batch_ops": sheet,
        "rows": sheet["supply_rows"],
        "packing_rows": sheet["packing_rows"],
        "logistics_rows": sheet["logistics_rows"],
    }


def ops_summary_for_supplies(
    supplies: list[FboSupplyRow],  # noqa: F821
    batches: dict[int, FboBatchRow],  # noqa: F821
    *,
    counterparty_names: dict[int, str] | None = None,
    unload_addresses: dict[int, str] | None = None,
) -> dict[str, Any]:
    counterparty_names = counterparty_names or {}
    unload_addresses = unload_addresses or {}
    rows: list[dict[str, Any]] = []
    for supply in supplies:
        batch = batches.get(int(supply.batch_id or 0))
        if batch is None:
            continue
        cp_name = counterparty_names.get(int(batch.ops_counterparty_id or 0), "")
        addr = unload_addresses.get(int(batch.ops_unload_address_id or 0), "")
        rows.append(
            ops_sheet_for_supply(
                supply,
                batch,
                counterparty_name=cp_name,
                unload_address=addr,
            )
        )
    return {
        "supply_count": len(rows),
        "packing_columns": [{"key": k, "label": l} for k, l in PACKING_COLUMNS],
        "logistics_columns": [{"key": k, "label": l} for k, l in LOGISTICS_COLUMNS],
        "rows": rows,
        "packing_rows": [r["packing_export"] for r in rows],
        "logistics_rows": [r["logistics_export"] for r in rows],
    }
