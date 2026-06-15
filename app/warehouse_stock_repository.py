"""Остатки новой панели /warehouse: кэш, пересчёт по SKU, комплекты."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from sqlalchemy import Boolean, Integer, String, delete, func, or_, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.catalog_repository import CatalogKitComponent, CatalogProduct, CatalogProductGroup
from app.storage_warehouse_repository import StorageStock, StorageWarehouse
from app.repositories import OrderItem

_AffectedSkusCallback = Callable[[set[str]], None]


class _Base(DeclarativeBase):
    pass


class StockBalanceCache(_Base):
    __tablename__ = "stock_balance_cache"

    sku: Mapped[str] = mapped_column(String(128), primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=True)
    is_kit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    group_id: Mapped[int] = mapped_column(Integer, nullable=True)
    group_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    image_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    full_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserve: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    free_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@dataclass
class StockBalanceRow:
    sku: str
    product_id: int | None
    is_kit: bool
    name: str
    code: str
    group_id: int | None
    group_name: str
    image_url: str
    full_stock: int
    reserve: int
    free_stock: int
    updated_at_ts: int


def _like(pattern: str) -> str:
    p = pattern.strip()
    if not p:
        return ""
    return f"%{p}%"


class WarehouseStockRepository:
    def __init__(self, db_url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)
        self._on_skus_changed: _AffectedSkusCallback | None = None

    def set_recalc_callback(self, callback: _AffectedSkusCallback | None) -> None:
        self._on_skus_changed = callback

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        if self._migrate_cache_columns():
            self.rebuild_all()
            return
        with Session(self.engine) as session:
            count = int(session.scalar(select(func.count()).select_from(StockBalanceCache)) or 0)
        if count == 0:
            self.rebuild_all()

    def _migrate_cache_columns(self) -> bool:
        from sqlalchemy import inspect, text

        if "stock_balance_cache" not in inspect(self.engine).get_table_names():
            return False
        cols = {c["name"] for c in inspect(self.engine).get_columns("stock_balance_cache")}
        if "image_url" in cols:
            return False
        with Session(self.engine) as session:
            session.execute(
                text(
                    "ALTER TABLE stock_balance_cache "
                    "ADD COLUMN image_url VARCHAR(2048) NOT NULL DEFAULT ''"
                )
            )
            session.commit()
        return True

    def notify_skus_changed(self, skus: Iterable[str]) -> None:
        normalized = {str(s or "").strip() for s in skus if str(s or "").strip()}
        if not normalized:
            return
        self.recalculate_skus(normalized)
        if self._on_skus_changed:
            self._on_skus_changed(normalized)

    def remove_cached_sku(self, sku: str) -> None:
        sku_n = str(sku or "").strip()
        if not sku_n:
            return
        with Session(self.engine) as session:
            row = session.scalar(select(StockBalanceCache).where(StockBalanceCache.sku == sku_n))
            if row is not None:
                session.delete(row)
                session.commit()

    def rebuild_all(self) -> int:
        with Session(self.engine) as session:
            skus: set[str] = set()
            for sku, in session.execute(select(StorageStock.sku).distinct()).all():
                if str(sku or "").strip():
                    skus.add(str(sku).strip())
            for sku, in session.execute(select(OrderItem.sku).distinct()).all():
                if str(sku or "").strip():
                    skus.add(str(sku).strip())
            for sku in session.scalars(select(CatalogProduct.sku)).all():
                if str(sku or "").strip():
                    skus.add(str(sku).strip())
        self.recalculate_skus(skus)
        return len(skus)

    def recalculate_skus(self, skus: Iterable[str]) -> None:
        seed = {str(s or "").strip() for s in skus if str(s or "").strip()}
        if not seed:
            return
        with Session(self.engine) as session:
            expanded = self._expand_with_parent_kits(session, seed)
            product_skus = {
                s
                for s in expanded
                if not self._is_kit_sku(session, s)
            }
            kit_ids = self._kit_ids_for_skus(session, expanded - product_skus)
            for sku in product_skus:
                self._recalc_product_sku(session, sku)
            for kit_id in self._kits_sorted_by_depth(session, kit_ids):
                self._recalc_kit(session, kit_id)
            session.commit()

    def list_by_products(self, filters: dict[str, str]) -> list[StockBalanceRow]:
        with Session(self.engine) as session:
            q = select(StockBalanceCache).order_by(StockBalanceCache.name, StockBalanceCache.sku)
            conds = self._product_filter_conditions(session, filters)
            if conds:
                q = q.where(*conds)
            rows = session.scalars(q).all()
            return [self._row_from_cache(r) for r in rows]

    def list_by_warehouses(self, filters: dict[str, str]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            wh_q = select(StorageWarehouse).order_by(
                StorageWarehouse.is_default.desc(), StorageWarehouse.name
            )
            wh_conds = self._warehouse_filter_conditions(filters)
            if wh_conds:
                wh_q = wh_q.where(*wh_conds)
            warehouses = session.scalars(wh_q).all()
            product_filters = dict(filters)
            balances = {
                r.sku: self._row_from_cache(r)
                for r in session.scalars(select(StockBalanceCache)).all()
            }
            out: list[dict[str, Any]] = []
            for wh in warehouses:
                stocks = session.scalars(
                    select(StorageStock).where(StorageStock.warehouse_id == int(wh.id))
                ).all()
                lines: list[dict[str, Any]] = []
                for st in stocks:
                    sku = str(st.sku or "").strip()
                    if not sku:
                        continue
                    bal = balances.get(sku)
                    if not self._matches_product_filters(bal, sku, product_filters):
                        continue
                    lines.append(
                        {
                            "sku": sku,
                            "name": bal.name if bal else sku,
                            "code": bal.code if bal else "",
                            "is_kit": bool(bal.is_kit) if bal else False,
                            "warehouse_stock": int(st.stock),
                            "full_stock": bal.full_stock if bal else int(st.stock),
                            "reserve": bal.reserve if bal else 0,
                            "free_stock": bal.free_stock if bal else int(st.stock),
                            "product_id": bal.product_id if bal else None,
                            "image_url": bal.image_url if bal else "",
                        }
                    )
                if filters.get("hide_empty") == "1" and not lines:
                    continue
                out.append(
                    {
                        "warehouse_id": int(wh.id),
                        "warehouse_name": wh.name,
                        "warehouse_code": wh.code,
                        "lines": sorted(lines, key=lambda x: (x["name"], x["sku"])),
                    }
                )
            return out

    def breakdown(self, sku: str, metric: str) -> dict[str, Any]:
        sku_n = sku.strip()
        if not sku_n:
            raise ValueError("SKU обязателен")
        metric_n = (metric or "full").strip().lower()
        if metric_n not in {"full", "reserve", "free"}:
            raise ValueError("metric должен быть full, reserve или free")
        with Session(self.engine) as session:
            if metric_n == "reserve":
                rows = session.scalars(
                    select(OrderItem).where(
                        OrderItem.sku == sku_n,
                        OrderItem.state == "added",
                    )
                ).all()
                lines = [
                    {
                        "source": r.source,
                        "external_order_id": r.external_order_id,
                        "quantity": int(r.quantity),
                    }
                    for r in rows
                ]
                total = sum(int(x["quantity"]) for x in lines)
                return {"metric": metric_n, "sku": sku_n, "lines": lines, "total": total}

            wh_rows = session.execute(
                select(
                    StorageWarehouse.id,
                    StorageWarehouse.name,
                    StorageWarehouse.code,
                    StorageStock.stock,
                )
                .join(StorageStock, StorageStock.warehouse_id == StorageWarehouse.id)
                .where(StorageStock.sku == sku_n)
                .order_by(StorageWarehouse.name)
            ).all()
            bal = session.scalar(select(StockBalanceCache).where(StockBalanceCache.sku == sku_n))
            reserve_total = int(bal.reserve) if bal else self._reserve_for_sku(session, sku_n)
            full_total = int(bal.full_stock) if bal else sum(int(r[3] or 0) for r in wh_rows)
            free_total = int(bal.free_stock) if bal else full_total - reserve_total
            lines = [
                {
                    "warehouse_id": int(r[0]),
                    "warehouse_name": str(r[1]),
                    "warehouse_code": str(r[2]),
                    "stock": int(r[3] or 0),
                    "reserve": 0,
                    "free": int(r[3] or 0),
                }
                for r in wh_rows
            ]
            if metric_n == "full":
                return {
                    "metric": metric_n,
                    "sku": sku_n,
                    "lines": [{"warehouse_id": x["warehouse_id"], "warehouse_name": x["warehouse_name"], "quantity": x["stock"]} for x in lines],
                    "total": full_total,
                }
            return {
                "metric": metric_n,
                "sku": sku_n,
                "lines": lines,
                "total": free_total,
                "reserve_total": reserve_total,
                "note": "Резерв учитывается на уровне товара и не распределён по складам.",
            }

    def get_meta(self) -> dict[str, Any]:
        from app.storage_warehouse_repository import StorageWarehouseGroup

        with Session(self.engine) as session:
            wh_groups = session.scalars(
                select(StorageWarehouseGroup).order_by(
                    StorageWarehouseGroup.sort_order, StorageWarehouseGroup.name
                )
            ).all()
            cat_groups = session.scalars(
                select(CatalogProductGroup).order_by(
                    CatalogProductGroup.sort_order, CatalogProductGroup.name
                )
            ).all()
            warehouses = session.scalars(
                select(StorageWarehouse).order_by(
                    StorageWarehouse.is_default.desc(), StorageWarehouse.name
                )
            ).all()
        return {
            "warehouse_groups": [{"id": g.id, "name": g.name} for g in wh_groups],
            "product_groups": [{"id": g.id, "name": g.name} for g in cat_groups],
            "warehouses": [
                {"id": w.id, "name": w.name, "code": w.code, "is_default": bool(w.is_default)}
                for w in warehouses
            ],
        }

    def balance_to_dict(self, row: StockBalanceRow) -> dict[str, Any]:
        return {
            "sku": row.sku,
            "product_id": row.product_id,
            "is_kit": row.is_kit,
            "name": row.name,
            "code": row.code,
            "group_id": row.group_id,
            "group_name": row.group_name,
            "image_url": row.image_url,
            "full_stock": row.full_stock,
            "reserve": row.reserve,
            "free_stock": row.free_stock,
            "updated_at_ts": row.updated_at_ts,
        }

    def _row_from_cache(self, row: StockBalanceCache) -> StockBalanceRow:
        return StockBalanceRow(
            sku=row.sku,
            product_id=int(row.product_id) if row.product_id is not None else None,
            is_kit=bool(row.is_kit),
            name=row.name or "",
            code=row.code or "",
            group_id=int(row.group_id) if row.group_id is not None else None,
            group_name=row.group_name or "",
            image_url=getattr(row, "image_url", "") or "",
            full_stock=int(row.full_stock),
            reserve=int(row.reserve),
            free_stock=int(row.free_stock),
            updated_at_ts=int(row.updated_at_ts),
        )

    def _product_filter_conditions(self, session: Session, filters: dict[str, str]) -> list:
        conds = []
        quick = _like(filters.get("q", ""))
        if quick:
            conds.append(
                or_(
                    StockBalanceCache.name.ilike(quick),
                    StockBalanceCache.sku.ilike(quick),
                    StockBalanceCache.code.ilike(quick),
                )
            )
        for key, col in (
            ("name", StockBalanceCache.name),
            ("sku", StockBalanceCache.sku),
            ("code", StockBalanceCache.code),
        ):
            pat = _like(filters.get(key, ""))
            if pat:
                conds.append(col.ilike(pat))
        raw_group = (filters.get("group_id") or "").strip()
        if raw_group:
            try:
                conds.append(StockBalanceCache.group_id == int(raw_group))
            except ValueError:
                pass
        kind = (filters.get("kind") or "").strip().lower()
        if kind == "kit":
            conds.append(StockBalanceCache.is_kit.is_(True))
        elif kind == "product":
            conds.append(StockBalanceCache.is_kit.is_(False))
        raw_wh = (filters.get("warehouse_id") or "").strip()
        if raw_wh:
            try:
                wh_id = int(raw_wh)
            except ValueError:
                wh_id = None
            if wh_id is not None:
                subq = select(StorageStock.sku).where(StorageStock.warehouse_id == wh_id)
                conds.append(StockBalanceCache.sku.in_(subq))
        if filters.get("only_nonzero") == "1":
            conds.append(
                or_(
                    StockBalanceCache.full_stock != 0,
                    StockBalanceCache.reserve != 0,
                    StockBalanceCache.free_stock != 0,
                )
            )
        return conds

    def _matches_product_filters(
        self, bal: StockBalanceRow | None, sku: str, filters: dict[str, str]
    ) -> bool:
        if not filters:
            return True
        name = bal.name if bal else sku
        code = bal.code if bal else ""
        is_kit = bool(bal.is_kit) if bal else False
        group_id = bal.group_id if bal else None
        full_stock = bal.full_stock if bal else 0
        reserve = bal.reserve if bal else 0
        free_stock = bal.free_stock if bal else 0

        quick = _like(filters.get("q", ""))
        if quick:
            pat = quick.strip("%").lower()
            blob = f"{name} {sku} {code}".lower()
            if pat not in blob:
                return False
        for key, val in (
            ("name", name),
            ("sku", sku),
            ("code", code),
        ):
            pat = _like(filters.get(key, ""))
            if pat and pat.strip("%").lower() not in str(val).lower():
                return False
        raw_group = (filters.get("group_id") or "").strip()
        if raw_group:
            try:
                if group_id != int(raw_group):
                    return False
            except ValueError:
                return False
        kind = (filters.get("kind") or "").strip().lower()
        if kind == "kit" and not is_kit:
            return False
        if kind == "product" and is_kit:
            return False
        if filters.get("only_nonzero") == "1" and full_stock == 0 and reserve == 0 and free_stock == 0:
            return False
        return True

    def _warehouse_filter_conditions(self, filters: dict[str, str]) -> list:
        conds = []
        for key, col in (
            ("warehouse_name", StorageWarehouse.name),
            ("warehouse_code", StorageWarehouse.code),
        ):
            pat = _like(filters.get(key, ""))
            if pat:
                conds.append(col.ilike(pat))
        raw_group = (filters.get("warehouse_group_id") or "").strip()
        if raw_group:
            try:
                conds.append(StorageWarehouse.group_id == int(raw_group))
            except ValueError:
                pass
        raw_wh = (filters.get("warehouse_id") or "").strip()
        if raw_wh:
            try:
                conds.append(StorageWarehouse.id == int(raw_wh))
            except ValueError:
                pass
        q_text = _like(filters.get("q", ""))
        if q_text:
            conds.append(
                or_(
                    StorageWarehouse.name.ilike(q_text),
                    StorageWarehouse.code.ilike(q_text),
                )
            )
        return conds

    def _full_by_sku(self, session: Session) -> dict[str, int]:
        rows = session.execute(
            select(StorageStock.sku, func.coalesce(func.sum(StorageStock.stock), 0)).group_by(
                StorageStock.sku
            )
        ).all()
        return {str(sku): int(total or 0) for sku, total in rows}

    def _reserve_by_sku(self, session: Session) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in session.scalars(select(OrderItem).where(OrderItem.state == "added")).all():
            sku = str(row.sku or "").strip()
            if not sku:
                continue
            out[sku] = out.get(sku, 0) + int(row.quantity)
        return out

    def _reserve_for_sku(self, session: Session, sku: str) -> int:
        return int(
            session.scalar(
                select(func.coalesce(func.sum(OrderItem.quantity), 0)).where(
                    OrderItem.sku == sku,
                    OrderItem.state == "added",
                )
            )
            or 0
        )

    def _catalog_product_by_sku(self, session: Session, sku: str) -> CatalogProduct | None:
        return session.scalar(select(CatalogProduct).where(CatalogProduct.sku == sku))

    def _is_kit_sku(self, session: Session, sku: str) -> bool:
        row = self._catalog_product_by_sku(session, sku)
        return bool(row and row.is_kit)

    def _upsert_cache(
        self,
        session: Session,
        *,
        sku: str,
        product: CatalogProduct | None,
        is_kit: bool,
        full_stock: int,
        reserve: int,
        free_stock: int,
    ) -> None:
        now = int(time.time())
        row = session.scalar(select(StockBalanceCache).where(StockBalanceCache.sku == sku))
        group_name = ""
        if product and product.group_id:
            gr = session.get(CatalogProductGroup, int(product.group_id))
            if gr:
                group_name = gr.name
        if row is None:
            row = StockBalanceCache(sku=sku, updated_at_ts=now)
            session.add(row)
        row.product_id = int(product.id) if product else None
        row.is_kit = bool(is_kit)
        row.name = (product.name if product else sku)[:512]
        row.code = (product.code if product else "")[:64]
        row.group_id = int(product.group_id) if product and product.group_id else None
        row.group_name = group_name[:128]
        row.image_url = ((product.image_url if product else "") or "")[:2048]
        row.full_stock = int(full_stock)
        row.reserve = int(reserve)
        row.free_stock = int(free_stock)
        row.updated_at_ts = now

    def _recalc_product_sku(self, session: Session, sku: str) -> None:
        product = self._catalog_product_by_sku(session, sku)
        if product and product.is_kit:
            return
        full_map = self._full_by_sku(session)
        reserve_map = self._reserve_by_sku(session)
        full_stock = int(full_map.get(sku, 0))
        reserve = int(reserve_map.get(sku, 0))
        free_stock = full_stock - reserve
        self._upsert_cache(
            session,
            sku=sku,
            product=product,
            is_kit=False,
            full_stock=full_stock,
            reserve=reserve,
            free_stock=free_stock,
        )

    def _recalc_kit(self, session: Session, kit_id: int) -> None:
        kit = session.get(CatalogProduct, int(kit_id))
        if kit is None or not kit.is_kit:
            return
        components = session.scalars(
            select(CatalogKitComponent).where(CatalogKitComponent.kit_product_id == int(kit_id))
        ).all()
        if not components:
            self._upsert_cache(
                session,
                sku=kit.sku,
                product=kit,
                is_kit=True,
                full_stock=0,
                reserve=0,
                free_stock=0,
            )
            return
        full_candidates: list[int] = []
        free_candidates: list[int] = []
        full_map = self._full_by_sku(session)
        reserve_map = self._reserve_by_sku(session)
        for comp in components:
            comp_product = session.get(CatalogProduct, int(comp.component_product_id))
            if comp_product is None:
                free_candidates.append(0)
                full_candidates.append(0)
                continue
            comp_sku = str(comp_product.sku or "").strip()
            qty = max(1, int(comp.quantity))
            if comp_product.is_kit:
                cache = session.scalar(
                    select(StockBalanceCache).where(StockBalanceCache.sku == comp_sku)
                )
                comp_full = int(cache.full_stock) if cache else 0
                comp_free = int(cache.free_stock) if cache else 0
            else:
                comp_full = int(full_map.get(comp_sku, 0))
                comp_reserve = int(reserve_map.get(comp_sku, 0))
                comp_free = comp_full - comp_reserve
            full_candidates.append(comp_full // qty)
            free_candidates.append(comp_free // qty)
        kit_full = min(full_candidates) if full_candidates else 0
        kit_free = min(free_candidates) if free_candidates else 0
        kit_reserve = kit_full - kit_free
        self._upsert_cache(
            session,
            sku=kit.sku,
            product=kit,
            is_kit=True,
            full_stock=kit_full,
            reserve=kit_reserve,
            free_stock=kit_free,
        )

    def _kit_ids_for_skus(self, session: Session, skus: set[str]) -> set[int]:
        out: set[int] = set()
        for sku in skus:
            product = self._catalog_product_by_sku(session, sku)
            if product and product.is_kit:
                out.add(int(product.id))
        return out

    def _expand_with_parent_kits(self, session: Session, seed: set[str]) -> set[str]:
        product_ids: set[int] = set()
        for sku in seed:
            p = self._catalog_product_by_sku(session, sku)
            if p is not None:
                product_ids.add(int(p.id))
        parent_kit_ids: set[int] = set()
        queue: deque[int] = deque(product_ids)
        seen_components = set(product_ids)
        while queue:
            comp_id = queue.popleft()
            rows = session.scalars(
                select(CatalogKitComponent.kit_product_id).where(
                    CatalogKitComponent.component_product_id == int(comp_id)
                )
            ).all()
            for kit_id in rows:
                kid = int(kit_id)
                if kid in parent_kit_ids:
                    continue
                parent_kit_ids.add(kid)
                if kid not in seen_components:
                    seen_components.add(kid)
                    queue.append(kid)
        expanded = set(seed)
        for kid in parent_kit_ids:
            kit = session.get(CatalogProduct, kid)
            if kit and kit.sku:
                expanded.add(str(kit.sku).strip())
        return expanded

    def _kits_sorted_by_depth(self, session: Session, kit_ids: set[int]) -> list[int]:
        depth_cache: dict[int, int] = {}

        def depth(kit_id: int) -> int:
            if kit_id in depth_cache:
                return depth_cache[kit_id]
            comps = session.scalars(
                select(CatalogKitComponent).where(CatalogKitComponent.kit_product_id == int(kit_id))
            ).all()
            if not comps:
                depth_cache[kit_id] = 1
                return 1
            child_depths = []
            for comp in comps:
                cp = session.get(CatalogProduct, int(comp.component_product_id))
                if cp and cp.is_kit:
                    child_depths.append(depth(int(cp.id)))
                else:
                    child_depths.append(0)
            depth_cache[kit_id] = 1 + max(child_depths)
            return depth_cache[kit_id]

        return sorted(kit_ids, key=lambda kid: depth(int(kid)))
