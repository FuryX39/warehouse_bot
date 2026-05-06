from dataclasses import dataclass

from sqlalchemy import Integer, String, UniqueConstraint, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.adapters.base import ReservationAction


class Base(DeclarativeBase):
    pass


class ProductStock(Base):
    __tablename__ = "product_stocks"

    sku: Mapped[str] = mapped_column(String(128), primary_key=True)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Reserve(Base):
    __tablename__ = "reserves"
    __table_args__ = (UniqueConstraint("source", "external_order_id", name="uq_order"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class SyncState(Base):
    """Ключ-значение (int) для якорей синхронизации маркетплейсов."""

    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_int: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@dataclass
class InventorySnapshot:
    sku: str
    stock: int
    reserve: int
    available: int


class InventoryRepository:
    def __init__(self, db_url: str) -> None:
        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        Base.metadata.create_all(self.engine)

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
        """Активные резервы: (external_order_id, sku, quantity)."""
        with Session(self.engine) as session:
            rows = session.execute(
                select(Reserve.external_order_id, Reserve.sku, Reserve.quantity).where(
                    Reserve.source == source,
                    Reserve.status == "active",
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

    def apply_reservations(self, actions: list[ReservationAction]) -> int:
        inserted = 0
        with Session(self.engine) as session:
            for action in actions:
                exists = session.scalar(
                    select(Reserve).where(
                        Reserve.source == action.source,
                        Reserve.external_order_id == action.external_order_id,
                    )
                )
                if exists:
                    continue
                session.add(
                    Reserve(
                        source=action.source,
                        external_order_id=action.external_order_id,
                        sku=action.sku,
                        quantity=action.quantity,
                        status="active",
                    )
                )
                inserted += 1
            session.commit()
        return inserted

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
                select(Reserve).where(Reserve.source == source, Reserve.status == "active")
            ).all()
            for row in active:
                key = (row.source, row.external_order_id)
                if key not in desired_keys:
                    session.delete(row)
                    removed += 1

            for action in by_key.values():
                row = session.scalar(
                    select(Reserve).where(
                        Reserve.source == action.source,
                        Reserve.external_order_id == action.external_order_id,
                        Reserve.status == "active",
                    )
                )
                if row is not None and (row.sku != action.sku or row.quantity != action.quantity):
                    row.sku = action.sku
                    row.quantity = action.quantity
                    updated += 1

            session.commit()
        return removed, updated

    def get_active_reserve_external_ids(self, source: str) -> set[str]:
        with Session(self.engine) as session:
            ids = session.scalars(
                select(Reserve.external_order_id).where(
                    Reserve.source == source,
                    Reserve.status == "active",
                )
            ).all()
            return set(ids)

    def get_available_stock_map(self) -> dict[str, int]:
        with Session(self.engine) as session:
            stocks = {
                row.sku: row.stock for row in session.scalars(select(ProductStock)).all()
            }
            reserves_by_sku: dict[str, int] = {}
            active_reserves = session.scalars(
                select(Reserve).where(Reserve.status == "active")
            ).all()
            for reserve in active_reserves:
                reserves_by_sku[reserve.sku] = reserves_by_sku.get(reserve.sku, 0) + reserve.quantity

            all_skus = set(stocks.keys()) | set(reserves_by_sku.keys())
            available: dict[str, int] = {}
            for sku in all_skus:
                stock = stocks.get(sku, 0)
                reserve = reserves_by_sku.get(sku, 0)
                available[sku] = max(stock - reserve, 0)
            return available

    def get_inventory_snapshot(self) -> list[InventorySnapshot]:
        with Session(self.engine) as session:
            stocks = {
                row.sku: row.stock for row in session.scalars(select(ProductStock)).all()
            }
            reserves_by_sku: dict[str, int] = {}
            active_reserves = session.scalars(
                select(Reserve).where(Reserve.status == "active")
            ).all()
            for reserve in active_reserves:
                reserves_by_sku[reserve.sku] = reserves_by_sku.get(reserve.sku, 0) + reserve.quantity

            snapshots: list[InventorySnapshot] = []
            all_skus = sorted(set(stocks.keys()) | set(reserves_by_sku.keys()))
            for sku in all_skus:
                stock = stocks.get(sku, 0)
                reserve = reserves_by_sku.get(sku, 0)
                snapshots.append(
                    InventorySnapshot(
                        sku=sku,
                        stock=stock,
                        reserve=reserve,
                        available=max(stock - reserve, 0),
                    )
                )
            return snapshots

    def clear_all_data(self) -> None:
        """Delete all stocks and reserves (irreversible)."""
        with Session(self.engine) as session:
            session.execute(delete(Reserve))
            session.execute(delete(ProductStock))
            session.execute(delete(SyncState))
            session.commit()

    def ship_active_reserves_by_external_ids(self, source: str, external_ids: set[str]) -> dict[str, int]:
        """
        Отгрузка выбранных резервов: списать их qty из `product_stocks.stock` и пометить резервы как `shipped`.
        Это предотвращает повторное создание этих резервов (uq_order на source+external_order_id).
        """
        if not external_ids:
            return {"affected_skus": 0, "reserved_units": 0, "reserves_shipped": 0, "stocks_updated": 0}

        with Session(self.engine) as session:
            rows = session.scalars(
                select(Reserve).where(
                    Reserve.source == source,
                    Reserve.status == "active",
                    Reserve.external_order_id.in_(list(external_ids)),
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
                r.status = "shipped"

            session.commit()

            return {
                "affected_skus": len(reserved_by_sku),
                "reserved_units": reserved_units,
                "reserves_shipped": len(rows),
                "stocks_updated": stocks_updated,
            }
