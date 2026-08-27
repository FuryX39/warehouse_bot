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
    max_units: int | None = None,
) -> tuple[list[WbFbsListRow], list[dict], list[str], int]:
    """Список строк для превью (без PDF)."""
    warnings: list[str] = []
    substatus = normalize_wb_fbs_substatus(substatus)
    orders: list[dict] = []
    if substatus == "STARTED":
        orders = adapter.fetch_new_assembly_orders()
    else:
        sid = str(supply_id or "").strip()
        if not sid:
            raise ValueError("Выберите поставку WB для «готовы к отгрузке»")
        order_ids = adapter.fetch_supply_order_ids(sid)
        if not order_ids:
            return [], [], ["В поставке нет заказов"], 0
        orders = adapter.fetch_orders_by_ids(order_ids)
        found_ids = {_order_id(o) for o in orders}
        for oid in order_ids:
            if int(oid) not in found_ids:
                warnings.append(f"Заказ WB {oid} не найден в API")
    available = len(orders)
    if max_units is not None and max_units > 0:
        orders = orders[: int(max_units)]
    rows = [
        WbFbsListRow(
            seq=index,
            order_id=str(_order_id(order)),
            sku=_order_sku(order),
            quantity=max(1, int(order.get("quantity") or 1)),
            status=substatus,
            supply_id=str(supply_id or ""),
        )
        for index, order in enumerate(orders, start=1)
    ]
    return rows, orders, warnings, available


def collect_wb_unit_labels(
    adapter: WildberriesAdapter,
    orders: list[dict],
    *,
    substatus: str,
    supply_id: str = "",
) -> tuple[list[WbUnitLabel], list[str], str]:
    """Этикетки PNG→PDF. Для STARTED создаёт поставку и добавляет заказы."""
    warnings: list[str] = []
    if not orders:
        return [], warnings, ""
    substatus = normalize_wb_fbs_substatus(substatus)
    effective_supply = str(supply_id or "").strip()
    order_ids = [_order_id(order) for order in orders]

    if substatus == "STARTED":
        supply_name = f"FBS {time.strftime('%Y-%m-%d %H:%M')}"
        effective_supply = adapter.create_supply(supply_name)
        try:
            adapter.add_orders_to_supply(effective_supply, order_ids)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Не все заказы добавлены в поставку: {exc}")

    stickers = adapter.fetch_order_stickers_png(order_ids)
    units: list[WbUnitLabel] = []
    for order in orders:
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
    return units, warnings, effective_supply


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
