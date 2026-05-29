from dataclasses import dataclass
import zlib

from sqlalchemy import (
    Boolean,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    inspect,
    or_,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.adapters.base import ReservationAction
from app.nomenclature_barcodes import barcodes_from_json, barcodes_to_json

# Должен совпадать с ключом в StockCoordinator (сбрасывается при clear_stocks_only).
AVAILABLE_STOCK_SYNC_KEY = "available_stock_push_hash"


def available_stock_map_hash(available_stock: dict[str, int]) -> int:
    """Тот же алгоритм, что в авто-синке: чтобы хэш после ручного пуша совпадал с ожиданиями цикла."""
    payload = "|".join(
        f"{sku}:{int(qty)}" for sku, qty in sorted(available_stock.items(), key=lambda x: x[0])
    )
    return int(zlib.crc32(payload.encode("utf-8")) & 0xFFFFFFFF)


def _escape_sql_like(pattern: str) -> str:
    """Экранирование % и _ для LIKE/ILIKE с ESCAPE '\\'."""
    return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Текст, если для SKU нет строки в таблице nomenclature (или пустое имя).
MISSING_NOMENCLATURE_LABEL = "Товар отсутствует в номенклатуре"


class Base(DeclarativeBase):
    pass


class ProductStock(Base):
    __tablename__ = "product_stocks"

    sku: Mapped[str] = mapped_column(String(128), primary_key=True)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_top: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Reserve(Base):
    __tablename__ = "reserves"
    __table_args__ = (UniqueConstraint("source", "external_order_id", name="uq_order"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (UniqueConstraint("source", "external_order_id", name="uq_order_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="added")
    first_seen_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SyncState(Base):
    """Ключ-значение (int) для якорей синхронизации маркетплейсов."""

    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_int: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AdapterStockState(Base):
    """Последнее успешно отправленное available по SKU для каждого маркетплейса."""

    __tablename__ = "adapter_stock_state"
    __table_args__ = (UniqueConstraint("source", "sku", name="uq_adapter_stock_state_source_sku"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class NomenclatureItem(Base):
    """Справочник номенклатуры: артикул → название и ссылка на изображение (та же БД, таблица nomenclature)."""

    __tablename__ = "nomenclature"

    sku: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    image_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    barcodes_json: Mapped[str] = mapped_column(String(4096), nullable=False, default="[]")


@dataclass
class InventorySnapshot:
    sku: str
    stock: int
    reserve: int
    available: int
    name: str
    image_url: str
    is_top: bool


class InventoryRepository:
    def __init__(self, db_url: str) -> None:
        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        self._ensure_product_stocks_is_top_column()
        self._ensure_nomenclature_image_url_column()
        self._ensure_nomenclature_barcodes_column()

    def _ensure_product_stocks_is_top_column(self) -> None:
        insp = inspect(self.engine)
        if "product_stocks" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("product_stocks")}
        if "is_top" in cols:
            return
        dialect = self.engine.dialect.name
        if dialect == "sqlite":
            stmt = text("ALTER TABLE product_stocks ADD COLUMN is_top BOOLEAN NOT NULL DEFAULT 0")
        elif dialect == "postgresql":
            stmt = text("ALTER TABLE product_stocks ADD COLUMN IF NOT EXISTS is_top BOOLEAN NOT NULL DEFAULT FALSE")
        else:
            return
        with self.engine.begin() as conn:
            conn.execute(stmt)

    def _ensure_nomenclature_image_url_column(self) -> None:
        """SQLite: create_all не добавляет новые колонки к существующей таблице."""
        insp = inspect(self.engine)
        if "nomenclature" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("nomenclature")}
        if "image_url" in cols:
            return
        dialect = self.engine.dialect.name
        if dialect == "sqlite":
            stmt = text("ALTER TABLE nomenclature ADD COLUMN image_url VARCHAR(2048) NOT NULL DEFAULT ''")
        elif dialect == "postgresql":
            stmt = text("ALTER TABLE nomenclature ADD COLUMN IF NOT EXISTS image_url VARCHAR(2048) NOT NULL DEFAULT ''")
        else:
            return
        with self.engine.begin() as conn:
            conn.execute(stmt)

    def _ensure_nomenclature_barcodes_column(self) -> None:
        insp = inspect(self.engine)
        if "nomenclature" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("nomenclature")}
        if "barcodes_json" in cols:
            return
        dialect = self.engine.dialect.name
        if dialect == "sqlite":
            stmt = text("ALTER TABLE nomenclature ADD COLUMN barcodes_json VARCHAR(4096) NOT NULL DEFAULT '[]'")
        elif dialect == "postgresql":
            stmt = text(
                "ALTER TABLE nomenclature ADD COLUMN IF NOT EXISTS barcodes_json VARCHAR(4096) NOT NULL DEFAULT '[]'"
            )
        else:
            return
        with self.engine.begin() as conn:
            conn.execute(stmt)

    def upsert_nomenclature_items(self, items: dict[str, tuple[str, str, list[str]]]) -> int:
        """Массовая запись номенклатуры: sku -> (name, image_url, barcodes). Пустые SKU пропускаются."""
        if not items:
            return 0
        n = 0
        with Session(self.engine) as session:
            for sku_raw, pair in items.items():
                sku = str(sku_raw).strip()
                if not sku or len(sku) > 128:
                    continue
                title = str(pair[0] or "").strip()
                if len(title) > 512:
                    title = title[:512]
                img = str(pair[1] or "").strip()
                if len(img) > 2048:
                    img = img[:2048]
                codes = pair[2] if len(pair) > 2 else []
                if not isinstance(codes, list):
                    codes = []
                bc_json = barcodes_to_json(codes)
                if len(bc_json) > 4096:
                    bc_json = barcodes_to_json(barcodes_from_json(bc_json)[:50])
                row = session.get(NomenclatureItem, sku)
                if row is None:
                    session.add(
                        NomenclatureItem(sku=sku, name=title, image_url=img, barcodes_json=bc_json)
                    )
                else:
                    row.name = title
                    row.image_url = img
                    row.barcodes_json = bc_json
                n += 1
            session.commit()
        return n

    def list_nomenclature_all(self) -> list[tuple[str, str, str, list[str]]]:
        """Все строки номенклатуры: (sku, name, image_url, barcodes), по sku."""
        with Session(self.engine) as session:
            rows = session.scalars(select(NomenclatureItem).order_by(NomenclatureItem.sku)).all()
            return [
                (
                    r.sku,
                    (r.name or "").strip(),
                    (r.image_url or "").strip(),
                    barcodes_from_json(getattr(r, "barcodes_json", None)),
                )
                for r in rows
            ]

    def get_barcodes_for_sku(self, sku: str) -> list[str]:
        sku = str(sku or "").strip()
        if not sku:
            return []
        with Session(self.engine) as session:
            row = session.get(NomenclatureItem, sku)
            if row is None:
                return []
            return barcodes_from_json(getattr(row, "barcodes_json", None))

    def get_nomenclature_meta_for_skus(self, skus: list[str]) -> dict[str, dict[str, object]]:
        """sku -> {name, barcodes} для существующих в справочнике позиций."""
        if not skus:
            return {}
        out: dict[str, dict[str, object]] = {}
        with Session(self.engine) as session:
            rows_map = self._load_nomenclature_rows(session, list(skus))
            for sku, triple in rows_map.items():
                name, _, barcodes = triple
                out[sku] = {"name": name, "barcodes": barcodes}
        return out

    def delete_nomenclature_by_sku(self, sku: str) -> bool:
        """Удаляет строку справочника nomenclature по артикулу. Остатки и резервы не трогает."""
        sku = str(sku or "").strip()
        if not sku or len(sku) > 128:
            return False
        with Session(self.engine) as session:
            row = session.get(NomenclatureItem, sku)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def clear_nomenclature_all(self) -> int:
        """Удаляет все строки таблицы nomenclature. Остатки, резервы и заказы не трогает."""
        with Session(self.engine) as session:
            n = int(session.scalar(select(func.count()).select_from(NomenclatureItem)) or 0)
            if n:
                session.execute(delete(NomenclatureItem))
            session.commit()
            return n

    def _load_nomenclature_rows(self, session: Session, skus: list[str]) -> dict[str, tuple[str, str, list[str]]]:
        """Только существующие в БД строки: sku -> (name, image_url, barcodes), уже strip()."""
        if not skus:
            return {}
        out: dict[str, tuple[str, str, list[str]]] = {}
        step = 800
        for i in range(0, len(skus), step):
            chunk = list(skus[i : i + step])
            rows = session.scalars(select(NomenclatureItem).where(NomenclatureItem.sku.in_(chunk))).all()
            for row in rows:
                out[row.sku] = (
                    (row.name or "").strip(),
                    (row.image_url or "").strip(),
                    barcodes_from_json(getattr(row, "barcodes_json", None)),
                )
        return out

    def get_sync_int(self, key: str) -> int | None:
        with Session(self.engine) as session:
            row = session.get(SyncState, key)
            return int(row.value_int) if row is not None else None

    def set_sync_int(self, key: str, value: int) -> None:
        with Session(self.engine) as session:
            row = session.get(SyncState, key)
            if row is None:
                session.add(SyncState(key=key, value_int=value))
            else:
                row.value_int = value
            session.commit()

    def get_active_reserve_rows(self, source: str) -> list[tuple[str, str, int]]:
        """Активные резервы (из order_items.state='added'): (external_order_id, sku, quantity)."""
        with Session(self.engine) as session:
            rows = session.execute(
                select(OrderItem.external_order_id, OrderItem.sku, OrderItem.quantity).where(
                    OrderItem.source == source,
                    OrderItem.state == "added",
                )
            ).all()
            return [(str(a), str(b), int(c)) for a, b, c in rows]

    def upsert_stock(self, sku: str, stock: int) -> None:
        with Session(self.engine) as session:
            row = session.get(ProductStock, sku)
            if row is None:
                row = ProductStock(sku=sku, stock=max(stock, 0))
                session.add(row)
            else:
                row.stock = max(stock, 0)
            session.commit()

    def delete_stock_by_sku(self, sku: str) -> bool:
        """Удаляет строку остатка (product_stocks). Резервы и заказы не трогает."""
        sku = str(sku or "").strip()
        if not sku or len(sku) > 128:
            return False
        with Session(self.engine) as session:
            row = session.get(ProductStock, sku)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def upsert_stocks(self, stocks_by_sku: dict[str, int]) -> int:
        if not stocks_by_sku:
            return 0
        with Session(self.engine) as session:
            for sku, stock in stocks_by_sku.items():
                row = session.get(ProductStock, sku)
                normalized_stock = max(stock, 0)
                if row is None:
                    session.add(ProductStock(sku=sku, stock=normalized_stock))
                else:
                    row.stock = normalized_stock
            session.commit()
        return len(stocks_by_sku)

    def set_top_flags_by_skus(self, skus: list[str]) -> dict[str, int]:
        normalized: list[str] = []
        seen: set[str] = set()
        for sku_raw in skus:
            sku = str(sku_raw or "").strip()
            if not sku or len(sku) > 128 or sku in seen:
                continue
            seen.add(sku)
            normalized.append(sku)

        reset_to_false = 0
        marked_top = 0
        created_with_top = 0
        with Session(self.engine) as session:
            top_rows = session.scalars(select(ProductStock).where(ProductStock.is_top.is_(True))).all()
            for row in top_rows:
                row.is_top = False
                reset_to_false += 1

            for sku in normalized:
                row = session.get(ProductStock, sku)
                if row is None:
                    session.add(ProductStock(sku=sku, stock=0, is_top=True))
                    created_with_top += 1
                    continue
                if not bool(row.is_top):
                    row.is_top = True
                    marked_top += 1
            session.commit()

        return {
            "sheet_skus": len(normalized),
            "top_total": len(normalized),
            "reset_to_false": reset_to_false,
            "marked_top_existing": marked_top,
            "created_with_top": created_with_top,
        }

    def set_top_flag_for_sku(self, sku: str, is_top: bool) -> dict[str, object]:
        sku_n = str(sku or "").strip()
        if not sku_n or len(sku_n) > 128:
            raise ValueError("Некорректный SKU")
        top_n = bool(is_top)
        with Session(self.engine) as session:
            row = session.get(ProductStock, sku_n)
            created = False
            if row is None:
                row = ProductStock(sku=sku_n, stock=0, is_top=top_n)
                session.add(row)
                created = True
            else:
                row.is_top = top_n
            session.commit()
        return {"sku": sku_n, "is_top": top_n, "created": created}

    def get_missing_top_items(self, threshold: int) -> list[InventorySnapshot]:
        threshold_i = int(threshold)
        with Session(self.engine) as session:
            top_rows = session.scalars(
                select(ProductStock).where(ProductStock.is_top.is_(True)).order_by(ProductStock.sku)
            ).all()
            if not top_rows:
                return []
            top_skus = [str(r.sku) for r in top_rows]
            reserves_by_sku: dict[str, int] = {}
            active_order_items = session.scalars(
                select(OrderItem).where(OrderItem.state == "added", OrderItem.sku.in_(top_skus))
            ).all()
            for reserve in active_order_items:
                reserves_by_sku[reserve.sku] = reserves_by_sku.get(reserve.sku, 0) + int(reserve.quantity)
            nom = self._load_nomenclature_rows(session, top_skus)
            out: list[InventorySnapshot] = []
            for row in top_rows:
                stock = int(row.stock)
                reserve = int(reserves_by_sku.get(row.sku, 0))
                available = stock - reserve
                if available >= threshold_i:
                    continue
                meta = nom.get(row.sku)
                if meta is None:
                    disp_name = MISSING_NOMENCLATURE_LABEL
                    img = ""
                else:
                    raw_n, raw_i, _ = meta
                    disp_name = MISSING_NOMENCLATURE_LABEL if raw_n == "" else raw_n
                    img = raw_i
                out.append(
                    InventorySnapshot(
                        sku=row.sku,
                        stock=stock,
                        reserve=reserve,
                        available=available,
                        name=disp_name,
                        image_url=img,
                        is_top=True,
                    )
                )
            return out

    def apply_stock_movements(self, deltas_by_sku: dict[str, int]) -> int:
        """Прибавляет дельты к остаткам (отрицательные — списание). Остаток может уйти в минус."""
        if not deltas_by_sku:
            return 0
        n = 0
        with Session(self.engine) as session:
            for sku_raw, delta in deltas_by_sku.items():
                sku = str(sku_raw).strip()
                if not sku or len(sku) > 128:
                    continue
                delta_i = int(delta)
                if delta_i == 0:
                    continue
                row = session.get(ProductStock, sku)
                if row is None:
                    session.add(ProductStock(sku=sku, stock=delta_i))
                else:
                    row.stock = int(row.stock) + delta_i
                n += 1
            session.commit()
        return n

    def apply_reservations(self, actions: list[ReservationAction]) -> int:
        _ = actions
        # Legacy compatibility: reservations are now managed via order_items.
        return 0

    def upsert_order_items_from_actions(self, actions: list[ReservationAction], sync_ts: int) -> dict[str, int]:
        """
        Step 1 migration: keep order history in a dedicated table, in parallel with reserves.
        Does not change existing reserve behavior.
        """
        inserted = 0
        updated = 0
        touched = 0
        if not actions:
            return {"inserted": 0, "updated": 0, "touched": 0}
        with Session(self.engine) as session:
            for action in actions:
                row = session.scalar(
                    select(OrderItem).where(
                        OrderItem.source == action.source,
                        OrderItem.external_order_id == action.external_order_id,
                    )
                )
                if row is None:
                    session.add(
                        OrderItem(
                            source=action.source,
                            external_order_id=action.external_order_id,
                            sku=action.sku,
                            quantity=action.quantity,
                            state="added",
                            first_seen_ts=sync_ts,
                            last_seen_ts=sync_ts,
                        )
                    )
                    inserted += 1
                    touched += 1
                    continue
                changed = False
                if row.sku != action.sku:
                    row.sku = action.sku
                    changed = True
                if int(row.quantity) != int(action.quantity):
                    row.quantity = int(action.quantity)
                    changed = True
                if row.state not in ("added", "shipped"):
                    row.state = "added"
                    changed = True
                if int(row.last_seen_ts) != int(sync_ts):
                    row.last_seen_ts = int(sync_ts)
                    changed = True
                if changed:
                    updated += 1
                    touched += 1
            session.commit()
        return {"inserted": inserted, "updated": updated, "touched": touched}

    def list_order_items(
        self,
        from_ts: int | None = None,
        to_ts: int | None = None,
        *,
        source: str | None = None,
        sku_contains: str | None = None,
        order_contains: str | None = None,
        limit: int = 5000,
    ) -> list[tuple[str, str, str, int, str, int, int]]:
        """
        Строки заказов из order_items. Фильтр по first_seen_ts (unix), границы включительно.
        Дополнительно: source (точное имя МП), sku_contains и order_contains — подстроки (ILIKE).
        Кортеж: source, external_order_id, sku, quantity, state, first_seen_ts, last_seen_ts.
        """
        with Session(self.engine) as session:
            stmt = select(
                OrderItem.source,
                OrderItem.external_order_id,
                OrderItem.sku,
                OrderItem.quantity,
                OrderItem.state,
                OrderItem.first_seen_ts,
                OrderItem.last_seen_ts,
            )
            if from_ts is not None:
                stmt = stmt.where(OrderItem.first_seen_ts >= int(from_ts))
            if to_ts is not None:
                stmt = stmt.where(OrderItem.first_seen_ts <= int(to_ts))
            if source is not None and str(source).strip():
                stmt = stmt.where(OrderItem.source == str(source).strip())
            if sku_contains is not None and str(sku_contains).strip():
                pat = f"%{_escape_sql_like(str(sku_contains).strip())}%"
                stmt = stmt.where(OrderItem.sku.ilike(pat, escape="\\"))
            if order_contains is not None and str(order_contains).strip():
                pat = f"%{_escape_sql_like(str(order_contains).strip())}%"
                stmt = stmt.where(OrderItem.external_order_id.ilike(pat, escape="\\"))
            stmt = stmt.order_by(OrderItem.first_seen_ts.desc(), OrderItem.id.desc()).limit(max(1, min(limit, 20000)))
            rows = session.execute(stmt).all()
            return [
                (str(a), str(b), str(c), int(d), str(e), int(f), int(g))
                for a, b, c, d, e, f, g in rows
            ]

    def reconcile_active_reserves(self, source: str, desired: list[ReservationAction]) -> tuple[int, int]:
        """
        Привести активные резервы для `source` к снимку `desired`: удалить лишние, обновить qty/sku при изменении.
        Вызывать только если `desired` — полный актуальный список резервируемых заказов с этого маркетплейса.
        Возвращает (сколько удалено, сколько обновлено).
        """
        by_key: dict[tuple[str, str], ReservationAction] = {}
        for action in desired:
            if action.source != source:
                continue
            by_key[(action.source, action.external_order_id)] = action
        desired_keys = set(by_key.keys())

        removed = 0
        updated = 0
        with Session(self.engine) as session:
            active = session.scalars(
                select(OrderItem).where(OrderItem.source == source, OrderItem.state == "added")
            ).all()
            for row in active:
                key = (row.source, row.external_order_id)
                if key not in desired_keys:
                    row.state = "cancelled"
                    removed += 1

            for action in by_key.values():
                row = session.scalar(
                    select(OrderItem).where(
                        OrderItem.source == action.source,
                        OrderItem.external_order_id == action.external_order_id,
                    )
                )
                if row is None:
                    session.add(
                        OrderItem(
                            source=action.source,
                            external_order_id=action.external_order_id,
                            sku=action.sku,
                            quantity=action.quantity,
                            state="added",
                            first_seen_ts=0,
                            last_seen_ts=0,
                        )
                    )
                    updated += 1
                    continue
                changed = False
                # Не реанимируем уже отгруженные строки при reconcile.
                # shipped должен оставаться shipped, даже если заказ снова виден в API-снимке.
                if row.state not in ("added", "shipped"):
                    row.state = "added"
                    changed = True
                if row.sku != action.sku:
                    row.sku = action.sku
                    changed = True
                if int(row.quantity) != int(action.quantity):
                    row.quantity = int(action.quantity)
                    changed = True
                if changed:
                    updated += 1

            session.commit()
        return removed, updated

    def get_active_reserve_external_ids(self, source: str) -> set[str]:
        with Session(self.engine) as session:
            ids = session.scalars(
                select(OrderItem.external_order_id).where(
                    OrderItem.source == source,
                    OrderItem.state == "added",
                )
            ).all()
            return set(ids)

    def build_force_push_available_map(self) -> dict[str, int]:
        """
        Полная карта для принудительного пуша: все SKU из product_stocks и из order_items (любой статус).
        Нет строки на складе → stock=0; резерв только по order_items в состоянии added.
        """
        with Session(self.engine) as session:
            stocks = {row.sku: int(row.stock) for row in session.scalars(select(ProductStock)).all()}
            reserve_by_sku: dict[str, int] = {}
            for oi in session.scalars(select(OrderItem).where(OrderItem.state == "added")).all():
                reserve_by_sku[oi.sku] = reserve_by_sku.get(oi.sku, 0) + int(oi.quantity)
            order_skus = {str(s) for s in session.scalars(select(OrderItem.sku).distinct()).all()}
            all_skus = set(stocks.keys()) | set(reserve_by_sku.keys()) | order_skus
            return {
                sku: max(int(stocks.get(sku, 0)) - int(reserve_by_sku.get(sku, 0)), 0)
                for sku in sorted(all_skus)
            }

    def get_available_stock_map(self) -> dict[str, int]:
        with Session(self.engine) as session:
            stocks = {
                row.sku: row.stock for row in session.scalars(select(ProductStock)).all()
            }
            reserves_by_sku: dict[str, int] = {}
            active_order_items = session.scalars(
                select(OrderItem).where(OrderItem.state == "added")
            ).all()
            for reserve in active_order_items:
                reserves_by_sku[reserve.sku] = reserves_by_sku.get(reserve.sku, 0) + int(reserve.quantity)

            all_skus = set(stocks.keys()) | set(reserves_by_sku.keys())
            available: dict[str, int] = {}
            for sku in all_skus:
                stock = stocks.get(sku, 0)
                reserve = reserves_by_sku.get(sku, 0)
                available[sku] = stock - reserve
            return available

    def get_inventory_snapshot(self) -> list[InventorySnapshot]:
        with Session(self.engine) as session:
            stock_rows = session.scalars(select(ProductStock)).all()
            stocks = {row.sku: int(row.stock) for row in stock_rows}
            top_flags = {row.sku: bool(getattr(row, "is_top", False)) for row in stock_rows}
            reserves_by_sku: dict[str, int] = {}
            active_order_items = session.scalars(
                select(OrderItem).where(OrderItem.state == "added")
            ).all()
            for reserve in active_order_items:
                reserves_by_sku[reserve.sku] = reserves_by_sku.get(reserve.sku, 0) + int(reserve.quantity)

            all_skus = sorted(set(stocks.keys()) | set(reserves_by_sku.keys()))
            nom = self._load_nomenclature_rows(session, all_skus)
            snapshots: list[InventorySnapshot] = []
            for sku in all_skus:
                stock = stocks.get(sku, 0)
                reserve = reserves_by_sku.get(sku, 0)
                is_top = bool(top_flags.get(sku, False))
                meta = nom.get(sku)
                if meta is None:
                    disp_name = MISSING_NOMENCLATURE_LABEL
                    img = ""
                else:
                    raw_n, raw_i, _ = meta
                    disp_name = MISSING_NOMENCLATURE_LABEL if raw_n == "" else raw_n
                    img = raw_i
                snapshots.append(
                    InventorySnapshot(
                        sku=sku,
                        stock=stock,
                        reserve=reserve,
                        available=stock - reserve,
                        name=disp_name,
                        image_url=img,
                        is_top=is_top,
                    )
                )
            return snapshots

    def clear_all_data(self) -> None:
        """Удаляет остатки, резервы и sync state. Таблица nomenclature не трогается."""
        with Session(self.engine) as session:
            session.execute(delete(Reserve))
            session.execute(delete(OrderItem))
            session.execute(delete(ProductStock))
            session.execute(delete(AdapterStockState))
            session.execute(delete(SyncState))
            session.commit()

    def clear_stocks_only(self) -> int:
        """Delete only product stocks. Сбрасываем хэш последнего пуша остатков — иначе sync решит, что available не менялся."""
        with Session(self.engine) as session:
            row = session.get(SyncState, AVAILABLE_STOCK_SYNC_KEY)
            if row is not None:
                session.delete(row)
            deleted = session.execute(delete(ProductStock))
            session.commit()
            return int(deleted.rowcount or 0)

    def get_adapter_stock_push_delta(self, source: str, current_available: dict[str, int]) -> dict[str, int]:
        """
        Возвращает только изменившиеся значения available для указанного источника.
        Включает:
          - SKU, где available изменился;
          - SKU, которые раньше пушились, но сейчас отсутствуют (для них шлём 0).
        """
        src = str(source or "").strip()
        if not src:
            return {}

        normalized_current: dict[str, int] = {}
        for sku_raw, qty in (current_available or {}).items():
            sku = str(sku_raw or "").strip()
            if not sku:
                continue
            normalized_current[sku] = int(qty)

        with Session(self.engine) as session:
            rows = session.scalars(
                select(AdapterStockState).where(AdapterStockState.source == src)
            ).all()
            prev = {str(r.sku): int(r.available) for r in rows}

        changed: dict[str, int] = {}
        for sku, qty in normalized_current.items():
            if prev.get(sku) != qty:
                changed[sku] = qty

        # Если SKU пропал из текущего набора, публикуем 0 один раз.
        for sku in prev.keys():
            if sku not in normalized_current and prev.get(sku, 0) != 0:
                changed[sku] = 0

        return changed

    def mark_adapter_stock_push_applied(self, source: str, pushed_available: dict[str, int], ts: int) -> int:
        """Фиксирует успешно отправленные available значения (только для переданных SKU)."""
        src = str(source or "").strip()
        if not src or not pushed_available:
            return 0

        updated = 0
        with Session(self.engine) as session:
            for sku_raw, qty in pushed_available.items():
                sku = str(sku_raw or "").strip()
                if not sku or len(sku) > 128:
                    continue
                row = session.scalar(
                    select(AdapterStockState).where(
                        AdapterStockState.source == src,
                        AdapterStockState.sku == sku,
                    )
                )
                if row is None:
                    session.add(
                        AdapterStockState(
                            source=src,
                            sku=sku,
                            available=int(qty),
                            updated_at_ts=int(ts),
                        )
                    )
                else:
                    row.available = int(qty)
                    row.updated_at_ts = int(ts)
                updated += 1
            session.commit()
        return updated

    def ship_added_orders_before_cutoff(
        self, sources: set[str] | None, cutoff_ts: int
    ) -> dict[str, object]:
        """
        Отсечка по времени: все позиции state=added с first_seen_ts <= cutoff_ts (и строки с first_seen_ts=0)
        для выбранных источников — списать qty со склада и перевести в shipped.
        sources=None — все маркетплейсы в БД.
        """
        with Session(self.engine) as session:
            q = select(OrderItem).where(
                OrderItem.state == "added",
                or_(OrderItem.first_seen_ts <= int(cutoff_ts), OrderItem.first_seen_ts == 0),
            )
            if sources is not None and len(sources) > 0:
                q = q.where(OrderItem.source.in_(list(sources)))
            rows = session.scalars(q).all()
            if not rows:
                return {
                    "by_source": {},
                    "reserved_units": 0,
                    "reserves_shipped": 0,
                    "affected_skus": 0,
                }

            reserved_by_sku: dict[str, int] = {}
            per_source: dict[str, list[OrderItem]] = {}
            for r in rows:
                reserved_by_sku[r.sku] = reserved_by_sku.get(r.sku, 0) + int(r.quantity)
                per_source.setdefault(r.source, []).append(r)

            reserved_units = 0
            for sku, qty in reserved_by_sku.items():
                if qty <= 0:
                    continue
                stock_row = session.get(ProductStock, sku)
                if stock_row is None:
                    continue
                reserved_units += qty
                stock_row.stock = max(int(stock_row.stock) - qty, 0)

            for r in rows:
                r.state = "shipped"

            session.commit()

            by_source_out: dict[str, dict[str, int]] = {}
            total_skus_summed = 0
            for src, rlist in sorted(per_source.items()):
                skus = {r.sku for r in rlist}
                units = sum(int(r.quantity) for r in rlist)
                by_source_out[src] = {
                    "reserves_shipped": len(rlist),
                    "reserved_units": units,
                    "affected_skus": len(skus),
                }
                total_skus_summed += len(skus)

            return {
                "by_source": by_source_out,
                "reserved_units": reserved_units,
                "reserves_shipped": len(rows),
                "affected_skus": total_skus_summed,
            }

    def ship_active_reserves_by_external_ids(self, source: str, external_ids: set[str]) -> dict[str, int]:
        """
        Отгрузка выбранных резервов: списать их qty из `product_stocks.stock` и пометить резервы как `shipped`.
        Это предотвращает повторное создание этих резервов (uq_order на source+external_order_id).
        """
        if not external_ids:
            return {
                "affected_skus": 0,
                "reserved_units": 0,
                "reserves_shipped": 0,
                "stocks_updated": 0,
                "external_order_ids": [],
                "qty_by_sku": {},
            }

        with Session(self.engine) as session:
            rows = session.scalars(
                select(OrderItem).where(
                    OrderItem.source == source,
                    OrderItem.state == "added",
                    OrderItem.external_order_id.in_(list(external_ids)),
                )
            ).all()

            reserved_by_sku: dict[str, int] = {}
            for r in rows:
                reserved_by_sku[r.sku] = reserved_by_sku.get(r.sku, 0) + int(r.quantity)

            stocks_updated = 0
            reserved_units = 0
            for sku, qty in reserved_by_sku.items():
                if qty <= 0:
                    continue
                stock_row = session.get(ProductStock, sku)
                if stock_row is None:
                    continue
                reserved_units += qty
                stock_row.stock = max(int(stock_row.stock) - qty, 0)
                stocks_updated += 1

            for r in rows:
                r.state = "shipped"

            session.commit()

            return {
                "affected_skus": len(reserved_by_sku),
                "reserved_units": reserved_units,
                "reserves_shipped": len(rows),
                "stocks_updated": stocks_updated,
                "external_order_ids": [r.external_order_id for r in rows],
                "qty_by_sku": dict(reserved_by_sku),
            }
