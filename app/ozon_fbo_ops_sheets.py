"""Поля и строки для таблиц логистов (ОТГРУЗКА) и упаковщиков (ПОСТАВКИ)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ozon_fbo_supply_repository import FboBatchRow, FboSupplyRow


def _str(raw: Any, limit: int = 512) -> str:
    return str(raw or "").strip()[:limit]


def _date_part(ts: str) -> str:
    val = _str(ts, 32)
    if len(val) >= 10:
        return val[:10]
    return val


def total_units(supply: FboSupplyRow) -> int:  # noqa: F821
    return sum(int(i.quantity or 0) for i in supply.items)


def _unique_join(parts: list[str], sep: str = ", ") -> str:
    seen: list[str] = []
    for part in parts:
        val = _str(part)
        if val and val not in seen:
            seen.append(val)
    return sep.join(seen)


def batch_ship_date(batch: FboBatchRow) -> str:  # noqa: F821
    return _date_part(batch.timeslot_from)


def batch_ops_editable_field_names() -> tuple[str, ...]:
    return (
        "ops_assembly_date",
        "ops_cargoes_desc",
        "ops_packing_status_id",
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


def resolved_supply_detail_values(
    supply: FboSupplyRow,  # noqa: F821
    batch: FboBatchRow,  # noqa: F821
) -> dict[str, str]:
    """Автоматические поля по одной заявке (для подсказки в форме пакета)."""
    units = total_units(supply)
    return {
        "cluster": _str(supply.ozon_cluster_name, 256),
        "warehouse": _str(supply.ozon_warehouse_name, 256),
        "supply_id": _str(supply.ozon_supply_id, 64),
        "packer": _str(supply.assigned_user_name, 128),
        "units_count": str(units) if units > 0 else "",
        "ship_date": batch_ship_date(batch),
    }


def resolved_batch_summary_values(
    batch: FboBatchRow,  # noqa: F821
    supplies: list[FboSupplyRow],  # noqa: F821
    *,
    counterparty_name: str = "",
    unload_address: str = "",
    packing_status_name: str = "",
) -> dict[str, str]:
    editable = ops_editable_from_batch(batch)
    ship_date = batch_ship_date(batch)
    ship_time = editable["ops_ship_time"]
    units_total = sum(total_units(s) for s in supplies)
    cargoes_desc = editable["ops_cargoes_desc"]
    if not unload_address:
        unload_address = _str(batch.dropoff_warehouse_name, 256)
    labels_url = batch.labels_url or ""
    client = counterparty_name or "—"
    return {
        "assembly_date": editable["ops_assembly_date"],
        "cluster": _unique_join([s.ozon_cluster_name for s in supplies]),
        "warehouse": _unique_join([s.ozon_warehouse_name for s in supplies]),
        "ship_date": ship_date,
        "supply_id": _unique_join([s.ozon_supply_id for s in supplies]),
        "movement_number": "",
        "packer": _unique_join([s.assigned_user_name for s in supplies]),
        "units_count": str(units_total) if units_total > 0 else "",
        "cargoes_desc": cargoes_desc,
        "packing_status": packing_status_name,
        "barcode_link": labels_url,
        "barcode_link_2": editable["ops_barcode_link_2"],
        "packing_comment": editable["ops_packing_comment"],
        "client": client,
        "weight_kg": editable["ops_weight_kg"],
        "unload_address": unload_address,
        "ship_time": ship_time,
        "logistics_cluster": _unique_join([s.ozon_cluster_name for s in supplies]),
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
    ("ops_cargoes_desc", "cargoes_desc", "количество грузомест", "text"),
    ("ops_packing_status_id", "packing_status", "статус", "packing_status"),
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


def _summary_row_dict(
    *,
    batch_id: int,
    batch_title: str,
    values: dict[str, str],
) -> dict[str, Any]:
    return {
        "batch_id": batch_id,
        "batch_title": batch_title,
        "title": batch_title,
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
    packing_status_name: str = "",
) -> dict[str, Any]:
    editable = ops_editable_from_batch(batch)
    supply_details = [
        {
            "supply_id": s.id,
            "ozon_supply_id": s.ozon_supply_id,
            "title": s.title,
            "values": resolved_supply_detail_values(s, batch),
        }
        for s in supplies
    ]
    summary_values = resolved_batch_summary_values(
        batch,
        supplies,
        counterparty_name=counterparty_name,
        unload_address=unload_address,
        packing_status_name=packing_status_name,
    )
    summary_row = _summary_row_dict(
        batch_id=int(batch.id),
        batch_title=str(batch.title or ""),
        values=summary_values,
    )
    return {
        "batch_id": batch.id,
        "batch_title": batch.title,
        "ship_date": batch_ship_date(batch),
        "cargoes_desc": editable["ops_cargoes_desc"],
        "editable": editable,
        "counterparty_name": counterparty_name,
        "unload_address": unload_address,
        "packing_status_name": packing_status_name,
        "supply_details": supply_details,
        "summary_row": summary_row,
        "summary_rows": [summary_row],
        "packing_rows": [summary_row["packing_export"]],
        "logistics_rows": [summary_row["logistics_export"]],
    }


def ops_summary_for_batch(
    batch: FboBatchRow,  # noqa: F821
    supplies: list[FboSupplyRow],  # noqa: F821
    *,
    counterparty_name: str = "",
    unload_address: str = "",
    packing_status_name: str = "",
) -> dict[str, Any]:
    sheet = ops_sheet_for_batch(
        batch,
        supplies,
        counterparty_name=counterparty_name,
        unload_address=unload_address,
        packing_status_name=packing_status_name,
    )
    summary_row = sheet["summary_row"]
    return {
        "supply_count": len(supplies),
        "batch_count": 1,
        "batch_id": batch.id,
        "packing_columns": [{"key": k, "label": l} for k, l in PACKING_COLUMNS],
        "logistics_columns": [{"key": k, "label": l} for k, l in LOGISTICS_COLUMNS],
        "batch_ops": sheet,
        "rows": sheet["summary_rows"],
        "packing_rows": sheet["packing_rows"],
        "logistics_rows": sheet["logistics_rows"],
        "summary_row": summary_row,
    }


def ops_summary_for_batches(
    batches: list[FboBatchRow],  # noqa: F821
    supplies_by_batch: dict[int, list[FboSupplyRow]],  # noqa: F821
    *,
    counterparty_names: dict[int, str] | None = None,
    unload_addresses: dict[int, str] | None = None,
    packing_status_names: dict[int, str] | None = None,
) -> dict[str, Any]:
    counterparty_names = counterparty_names or {}
    unload_addresses = unload_addresses or {}
    packing_status_names = packing_status_names or {}
    summary_rows: list[dict[str, Any]] = []
    packing_rows: list[dict[str, str]] = []
    logistics_rows: list[dict[str, str]] = []
    supply_count = 0
    for batch in batches:
        supplies = supplies_by_batch.get(int(batch.id), [])
        if not supplies:
            continue
        supply_count += len(supplies)
        cp_name = counterparty_names.get(int(batch.ops_counterparty_id or 0), "")
        addr = unload_addresses.get(int(batch.ops_unload_address_id or 0), "")
        status_name = packing_status_names.get(int(batch.ops_packing_status_id or 0), "")
        sheet = ops_sheet_for_batch(
            batch,
            supplies,
            counterparty_name=cp_name,
            unload_address=addr,
            packing_status_name=status_name,
        )
        row = sheet["summary_row"]
        summary_rows.append(row)
        packing_rows.append(row["packing_export"])
        logistics_rows.append(row["logistics_export"])
    return {
        "supply_count": supply_count,
        "batch_count": len(summary_rows),
        "packing_columns": [{"key": k, "label": l} for k, l in PACKING_COLUMNS],
        "logistics_columns": [{"key": k, "label": l} for k, l in LOGISTICS_COLUMNS],
        "rows": summary_rows,
        "packing_rows": packing_rows,
        "logistics_rows": logistics_rows,
    }
