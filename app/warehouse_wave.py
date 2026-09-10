"""Связка FBS-задания (волны) со складскими заказами покупателей."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.fbs_packing_repository import JOB_STATUS_DONE, FbsPackingJobRow
from app.warehouse_orders_repository import WarehouseOrdersRepository, packing_source


def _job_lines(job: FbsPackingJobRow | dict[str, Any]) -> list[Any]:
    if isinstance(job, dict):
        return list(job.get("lines") or [])
    return list(job.lines or [])


def attach_packing_job_to_orders(
    orders_repo: WarehouseOrdersRepository | None,
    job: FbsPackingJobRow | dict[str, Any] | None,
    warehouse_id: int | None,
) -> None:
    if orders_repo is None or job is None or warehouse_id is None:
        return
    marketplace = job["marketplace"] if isinstance(job, dict) else job.marketplace
    job_id = int(job["id"] if isinstance(job, dict) else job.id)
    source = packing_source(str(marketplace))
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for line in _job_lines(job):
        if isinstance(line, dict):
            pid = str(line.get("order_id") or "").strip()
            sku = str(line.get("sku") or "").strip()
            name = str(line.get("product_name") or line.get("name") or "")
        else:
            pid = str(getattr(line, "order_id", "") or "").strip()
            sku = str(getattr(line, "sku", "") or "").strip()
            name = str(getattr(line, "product_name", "") or "")
        if not pid or not sku:
            continue
        prev = grouped[pid].get(sku)
        if prev is None:
            grouped[pid][sku] = {"sku": sku, "quantity": 1, "name": name}
        else:
            prev["quantity"] = int(prev["quantity"]) + 1
    ids: list[int] = []
    for pid, by_sku in grouped.items():
        orders_repo.upsert_from_posting(
            source=source,
            posting_id=pid,
            warehouse_id=int(warehouse_id),
            lines=list(by_sku.values()),
        )
        row = orders_repo.get_by_posting(source, pid)
        if row is not None:
            ids.append(int(row.id))
    orders_repo.set_packing_job(ids, job_id)


def release_packing_job_orders(
    orders_repo: WarehouseOrdersRepository | None, job_id: int
) -> None:
    if orders_repo is None:
        return
    orders_repo.clear_packing_job(int(job_id))


def mark_packed_if_done(
    orders_repo: WarehouseOrdersRepository | None, job: FbsPackingJobRow | dict[str, Any] | None
) -> None:
    if orders_repo is None or job is None:
        return
    status = job["status"] if isinstance(job, dict) else job.status
    if str(status) != JOB_STATUS_DONE:
        return
    job_id = int(job["id"] if isinstance(job, dict) else job.id)
    orders_repo.mark_job_packed(job_id)
