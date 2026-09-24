"""Заказы Wildberries FBS и PDF-этикетки для заданий упаковки."""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.adapters.wildberries import WildberriesAdapter
from app.fbs_labels_common import merge_label_pdfs

if TYPE_CHECKING:
    from app.services import StockCoordinator

WB_FBS_SUBSTATUS_OPTIONS = {
    "STARTED": "Готовы к сборке",
    "READY_TO_SHIP": "Готовы к отгрузке",
}


def normalize_wb_supply_ids(*sources: object) -> list[str]:
    """Один или несколько WB-GI-… без дублей, порядок как в запросе."""
    out: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
            return
        text = str(value).strip()
        if not text:
            return
        if any(ch in text for ch in ",;\n\r\t"):
            for part in text.replace(",", " ").replace(";", " ").split():
                add(part)
            return
        if text in seen:
            return
        seen.add(text)
        out.append(text)

    for src in sources:
        add(src)
    return out


def merged_wb_supply_title(supply_ids: list[str]) -> str:
    ids = [str(x).strip() for x in supply_ids if str(x).strip()]
    if len(ids) < 2:
        return ""
    return f"Объединено: {' + '.join(ids)}"[:256]


def normalize_wb_fbs_substatus(value: object) -> str:
    substatus = str(value or "STARTED").strip().upper()
    if substatus not in WB_FBS_SUBSTATUS_OPTIONS:
        raise ValueError("Выберите статус: готовы к сборке или готовы к отгрузке")
    return substatus


@dataclass(frozen=True)
class WbFbsListRow:
    seq: int
    order_id: str
    sku: str
    quantity: int
    status: str
    supply_id: str = ""


@dataclass
class WbUnitLabel:
    sku: str
    order_id: str
    wb_order_id: int
    pdf: bytes | None
    barcode: str = ""
    error: str = ""

    def scan_keys(self) -> list[str]:
        keys = [self.order_id]
        if self.barcode:
            keys.append(self.barcode)
        return keys


def get_configured_wb_adapter(coordinator: StockCoordinator) -> WildberriesAdapter | None:
    for adapter in coordinator.adapters:
        if isinstance(adapter, WildberriesAdapter) and adapter.is_configured():
            return adapter
    return None


def _order_sku(order: dict) -> str:
    return str(
        order.get("supplierArticle") or order.get("article") or order.get("vendorCode") or ""
    ).strip()


def _order_id(order: dict) -> int:
    return int(order["id"])


def png_bytes_to_label_pdf(png: bytes, *, width_mm: float = 58.0, height_mm: float = 40.0) -> bytes:
    from PIL import Image
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    img = Image.open(io.BytesIO(png)).convert("RGB")
    buf = io.BytesIO()
    page_w = width_mm * mm
    page_h = height_mm * mm
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.drawImage(ImageReader(img), 0, 0, width=page_w, height=page_h, preserveAspectRatio=True, anchor="sw")
    c.showPage()
    c.save()
    return buf.getvalue()


def load_wb_fbs_list_rows(
    adapter: WildberriesAdapter,
    *,
    substatus: str,
    supply_id: str = "",
    supply_ids: list[str] | None = None,
    max_units: int | None = None,
) -> tuple[list[WbFbsListRow], list[dict], list[str], int]:
    """Список строк для превью (без PDF)."""
    warnings: list[str] = []
    substatus = normalize_wb_fbs_substatus(substatus)
    orders: list[dict] = []
    supplies = normalize_wb_supply_ids(supply_ids, supply_id)
    if substatus == "STARTED":
        orders = adapter.fetch_new_assembly_orders()
    else:
        if not supplies:
            raise ValueError("Выберите поставку WB для «готовы к отгрузке»")
        seen_oids: set[int] = set()
        for sid in supplies:
            order_ids = adapter.fetch_supply_order_ids(sid)
            if not order_ids:
                warnings.append(f"{sid}: в поставке нет заказов")
                continue
            fetched = adapter.fetch_orders_by_ids(order_ids)
            found_ids = {_order_id(o) for o in fetched}
            for oid in order_ids:
                if int(oid) not in found_ids:
                    warnings.append(f"Заказ WB {oid} не найден в API")
            for order in fetched:
                oid = _order_id(order)
                if oid in seen_oids:
                    continue
                seen_oids.add(oid)
                orders.append(order)
    available = len(orders)
    if max_units is not None and max_units > 0:
        orders = orders[: int(max_units)]
    title_supply = supplies[0] if len(supplies) == 1 else ""
    rows = [
        WbFbsListRow(
            seq=index,
            order_id=str(_order_id(order)),
            sku=_order_sku(order),
            quantity=max(1, int(order.get("quantity") or 1)),
            status=substatus,
            supply_id=title_supply,
        )
        for index, order in enumerate(orders, start=1)
    ]
    return rows, orders, warnings, available


def _order_flag(order: dict, *names: str) -> str:
    for name in names:
        if name in order and order.get(name) not in (None, ""):
            return str(order.get(name)).strip()
    return ""


def order_is_b2b(order: dict) -> bool:
    options = order.get("options")
    if isinstance(options, dict) and (options.get("isB2B") or options.get("isB2b")):
        return True
    return bool(order.get("isB2B") or order.get("isB2b"))


def order_destination(order: dict) -> str:
    """Склад WB, куда направлен заказ."""
    offices = order.get("offices")
    if isinstance(offices, list):
        names = [str(item).strip() for item in offices if str(item).strip()]
        if names:
            return ", ".join(names)
    return _order_flag(order, "officeId", "office_id", "destinationOfficeId")


def wb_supply_group_key(order: dict) -> tuple[str, str, bool, str, str]:
    """Один склад и один тип покупателя — одна поставка.

    Склад — куда упал заказ. Название склада берётся из заказа, список складов не задан заранее.
    Физлица и юрлица одного склада идут в разные поставки. Несколько заказов одной группы — в одну поставку.
    """
    warehouse = _order_flag(order, "warehouseId", "warehouse_id") or "0"
    destination = order_destination(order)
    cargo = _order_flag(order, "cargoType") or "0"
    cross = _order_flag(order, "crossBorderType") or "0"
    return warehouse, destination, order_is_b2b(order), cargo, cross


def wb_supply_group_name(stamp: str, key: tuple[str, str, bool, str, str]) -> str:
    warehouse, destination, b2b, _cargo, _cross = key
    place = destination or warehouse
    audience = "юрлица" if b2b else "физлица"
    return f"{place} {stamp} {audience}"[:128]


def group_orders_for_supplies(
    orders: list[dict],
) -> list[tuple[tuple[str, str, bool, str, str], list[dict]]]:
    buckets: dict[tuple[str, str, bool, str, str], list[dict]] = {}
    order: list[tuple[str, str, bool, str, str]] = []
    for item in orders:
        key = wb_supply_group_key(item)
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(item)
    return [(key, buckets[key]) for key in order]


def collect_wb_unit_labels(
    adapter: WildberriesAdapter,
    orders: list[dict],
    *,
    substatus: str,
    supply_id: str = "",
) -> tuple[list[WbUnitLabel], list[str], list[str]]:
    """Этикетки PNG→PDF. Для «к сборке» создаёт поставку на каждую группу заказов."""
    warnings: list[str] = []
    if not orders:
        return [], warnings, []
    substatus = normalize_wb_fbs_substatus(substatus)
    created_supplies: list[str] = []
    sticker_orders = orders

    if substatus == "STARTED":
        stamp = time.strftime("%d.%m.%Y")
        sticker_orders = []
        for key, group in group_orders_for_supplies(orders):
            name = wb_supply_group_name(stamp, key)
            try:
                supply_id_new = adapter.create_supply(name)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Не создана поставка «{name}»: {exc}")
                continue
            ids = [_order_id(item) for item in group]
            try:
                adapter.add_orders_to_supply(supply_id_new, ids)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Поставка {supply_id_new} («{name}»): заказы не добавлены: {exc}")
                continue
            created_supplies.append(supply_id_new)
            sticker_orders.extend(group)
            place = key[1] or key[0]
            audience = "юрлица" if key[2] else "физлица"
            warnings.append(
                f"Поставка {supply_id_new}: склад {place}, {audience}, заказов {len(group)}"
            )
        if not sticker_orders:
            return [], warnings, created_supplies
    else:
        if str(supply_id or "").strip():
            created_supplies.append(str(supply_id).strip())

    order_ids = [_order_id(order) for order in sticker_orders]
    stickers = adapter.fetch_order_stickers_png(order_ids)
    units: list[WbUnitLabel] = []
    for order in sticker_orders:
        oid = _order_id(order)
        sku = _order_sku(order)
        sticker = stickers.get(oid)
        if sticker is None:
            units.append(
                WbUnitLabel(
                    sku=sku,
                    order_id=str(oid),
                    wb_order_id=oid,
                    pdf=None,
                    error=f"Нет стикера для заказа {oid}",
                )
            )
            continue
        try:
            png = base64.b64decode(sticker["png_b64"])
            pdf = png_bytes_to_label_pdf(png)
        except Exception as exc:  # noqa: BLE001
            units.append(
                WbUnitLabel(
                    sku=sku,
                    order_id=str(oid),
                    wb_order_id=oid,
                    pdf=None,
                    error=f"Стикер {oid}: {exc}",
                )
            )
            continue
        units.append(
            WbUnitLabel(
                sku=sku,
                order_id=str(oid),
                wb_order_id=oid,
                pdf=pdf,
                barcode=sticker.get("barcode") or "",
            )
        )
    missing = [u for u in units if u.error]
    for item in missing[:5]:
        warnings.append(item.error)
    return units, warnings, created_supplies


def list_rows_payload(list_rows: list[WbFbsListRow]) -> list[dict]:
    return [
        {
            "seq": row.seq,
            "sku": row.sku,
            "quantity": row.quantity,
            "order_id": row.order_id,
            "order_display": row.order_id,
            "posting_number": row.order_id,
            "supply_id": row.supply_id,
        }
        for row in list_rows
    ]
