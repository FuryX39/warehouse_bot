"""Связка FBS-задания (волны) со складскими заказами покупателей."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fbs_packing_repository import (
    JOB_STATUS_DONE,
    JOB_STATUS_IN_PROGRESS,
    JOB_STATUS_OPEN,
    FbsPackingJob,
    FbsPackingJobRow,
    FbsPackingLine,
)
from app.warehouse_orders_repository import WarehouseOrdersRepository, packing_source

JOB_WMS_SHIPPABLE_STATUSES = (JOB_STATUS_OPEN, JOB_STATUS_IN_PROGRESS, JOB_STATUS_DONE)


def _job_lines(job: FbsPackingJobRow | dict[str, Any]) -> list[Any]:
    if isinstance(job, dict):
        return list(job.get("lines") or [])
    return list(job.lines or [])


def packing_job_payload_from_db(engine, job_id: int) -> dict[str, Any] | None:
    """Снимок задания из тех же таблиц FBS, что использует упаковка."""
    with Session(engine) as session:
        job = session.get(FbsPackingJob, int(job_id))
        if job is None:
            return None
        lines = session.scalars(
            select(FbsPackingLine)
            .where(FbsPackingLine.job_id == int(job.id))
            .order_by(FbsPackingLine.seq, FbsPackingLine.id)
        ).all()
        return {
            "id": int(job.id),
            "marketplace": str(job.marketplace or ""),
            "status": str(job.status or ""),
            "lines": [
                {
                    "order_id": str(line.order_id or ""),
                    "sku": str(line.sku or ""),
                    "product_name": str(line.product_name or ""),
                }
                for line in lines
            ],
        }


def ensure_packing_job_attached(
    orders_repo: WarehouseOrdersRepository | None,
    engine,
    job_id: int,
    warehouse_id: int | None,
) -> dict[str, Any] | None:
    """Привязать заказы покупателей к заданию, если этого не сделали при создании."""
    payload = packing_job_payload_from_db(engine, job_id)
    if payload is None:
        return None
    attach_packing_job_to_orders(orders_repo, payload, warehouse_id)
    mark_packed_if_done(orders_repo, payload)
    return payload


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


def link_shipped_orders_to_packing_job(
    orders_repo: WarehouseOrdersRepository | None,
    engine,
    job_id: int,
) -> list[Any]:
    """Привязать уже shipped-заказы к волне по order_id из строк FBS."""
    if orders_repo is None:
        return []
    payload = packing_job_payload_from_db(engine, int(job_id))
    if payload is None:
        return []
    source = packing_source(str(payload.get("marketplace") or ""))
    ids: list[int] = []
    linked: list[Any] = []
    for line in payload.get("lines") or []:
        pid = str(line.get("order_id") or "").strip()
        if not pid:
            continue
        row = orders_repo.get_by_posting(source, pid)
        if row is None:
            continue
        linked.append(row)
        ids.append(int(row.id))
    if ids:
        orders_repo.set_packing_job(ids, int(job_id))
        linked = orders_repo.list_by_packing_job(int(job_id))
    return linked


def resolve_packing_job_ship_flags(
    orders_repo: WarehouseOrdersRepository | None,
    engine,
    job_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Флаги отгрузки: сначала по packing_job_id, иначе по posting из строк FBS."""
    ids = sorted({int(i) for i in job_ids if int(i) > 0})
    empty: dict[str, Any] = {
        "shipped": False,
        "can_ship": True,
        "order_count": 0,
        "shipped_count": 0,
        "pending_count": 0,
    }
    if orders_repo is None:
        return {jid: dict(empty) for jid in ids}
    flags = orders_repo.packing_job_ship_flags(ids)
    missing = [jid for jid in ids if int((flags.get(jid) or {}).get("order_count") or 0) == 0]
    if not missing or engine is None:
        return flags
    from app.warehouse_orders_repository import ORDER_SHIPPED, TERMINAL_STATUSES

    with Session(engine) as session:
        jobs = {
            int(j.id): j
            for j in session.scalars(select(FbsPackingJob).where(FbsPackingJob.id.in_(missing))).all()
        }
        lines = session.scalars(
            select(FbsPackingLine).where(FbsPackingLine.job_id.in_(missing))
        ).all()
    postings_by_job: dict[int, set[str]] = {jid: set() for jid in missing}
    for line in lines:
        pid = str(line.order_id or "").strip()
        if pid:
            postings_by_job.setdefault(int(line.job_id), set()).add(pid)
    for jid in missing:
        job = jobs.get(jid)
        if job is None:
            continue
        source = packing_source(str(job.marketplace or ""))
        statuses: list[str] = []
        for pid in sorted(postings_by_job.get(jid) or []):
            row = orders_repo.get_by_posting(source, pid)
            if row is not None:
                statuses.append(str(row.status or ""))
        if not statuses:
            continue
        order_count = len(statuses)
        shipped_count = sum(1 for item in statuses if item == ORDER_SHIPPED)
        pending_count = sum(1 for item in statuses if item not in TERMINAL_STATUSES)
        flags[jid] = {
            "shipped": pending_count == 0 and shipped_count > 0,
            "can_ship": pending_count > 0,
            "order_count": order_count,
            "shipped_count": shipped_count,
            "pending_count": pending_count,
        }
    return flags


def annotate_jobs_with_wms_ship_status(
    job_dicts: list[dict[str, Any]],
    flags: dict[int, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Добавляет wms_shipped / can_ship к словарям FBS-заданий."""
    by_job = flags or {}
    for item in job_dicts:
        jid = int(item.get("id") or 0)
        flag = by_job.get(jid) or {}
        status = str(item.get("status") or "")
        shipped = bool(flag.get("shipped"))
        pending = int(flag.get("pending_count") or 0)
        order_count = int(flag.get("order_count") or 0)
        # Пустая привязка: для open/in_progress оставляем can_ship (отгрузка сама привяжет).
        # Для done пустая привязка не даёт кнопку — иначе висят уже отгруженные через синк МП волны.
        can_ship_orders = pending > 0 or (
            order_count == 0 and status in (JOB_STATUS_OPEN, JOB_STATUS_IN_PROGRESS)
        )
        item["wms_shipped"] = shipped
        item["wms_order_count"] = order_count
        item["wms_shipped_count"] = int(flag.get("shipped_count") or 0)
        item["can_ship"] = (not shipped) and can_ship_orders and status in JOB_WMS_SHIPPABLE_STATUSES
    return job_dicts


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
