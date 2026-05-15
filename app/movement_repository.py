"""Журнал перемещений остатков — отдельная БД (MOVEMENT_DB_URL)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import ForeignKey, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


class MovementBase(DeclarativeBase):
    pass


class StockMovement(MovementBase):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # in | out
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="telegram")
    sheet_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    sku_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings_json: Mapped[str] = mapped_column(String(8192), nullable=False, default="[]")

    lines: Mapped[list["StockMovementLine"]] = relationship(
        back_populates="movement",
        cascade="all, delete-orphan",
        order_by="StockMovementLine.sku",
    )


class StockMovementLine(MovementBase):
    __tablename__ = "stock_movement_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    movement_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stock_movements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)

    movement: Mapped[StockMovement] = relationship(back_populates="lines")


@dataclass(frozen=True)
class MovementSummary:
    id: int
    created_at_ts: int
    direction: str
    direction_label: str
    source: str
    sheet_url: str
    sku_count: int
    total_quantity: int


@dataclass(frozen=True)
class MovementLineRow:
    sku: str
    quantity: int
    delta: int


@dataclass(frozen=True)
class MovementDetail:
    id: int
    created_at_ts: int
    direction: str
    direction_label: str
    source: str
    sheet_url: str
    sku_count: int
    total_quantity: int
    warnings: list[str]
    lines: list[MovementLineRow]


_DIRECTION_LABELS = {"in": "Приход", "out": "Расход"}


class MovementRepository:
    def __init__(self, db_url: str) -> None:
        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        MovementBase.metadata.create_all(self.engine)

    def create_movement(
        self,
        *,
        created_at_ts: int,
        direction: str,
        source: str,
        sheet_url: str,
        lines: list[tuple[str, int, int]],
        warnings: list[str] | None = None,
    ) -> int:
        """lines: (sku, quantity>0, signed delta). Возвращает id перемещения."""
        direction = direction if direction in ("in", "out") else "in"
        warn_json = json.dumps(list(warnings or [])[:200], ensure_ascii=False)
        if len(warn_json) > 8000:
            warn_json = warn_json[:8000] + "]"
        sku_count = len(lines)
        total_qty = sum(int(q) for _, q, _ in lines)
        with Session(self.engine) as session:
            movement = StockMovement(
                created_at_ts=int(created_at_ts),
                direction=direction,
                source=str(source or "unknown")[:32],
                sheet_url=(sheet_url or "")[:2048],
                sku_count=sku_count,
                total_quantity=total_qty,
                warnings_json=warn_json,
            )
            session.add(movement)
            session.flush()
            for sku_raw, qty, delta in lines:
                sku = str(sku_raw).strip()
                if not sku or qty <= 0:
                    continue
                session.add(
                    StockMovementLine(
                        movement_id=movement.id,
                        sku=sku[:128],
                        quantity=int(qty),
                        delta=int(delta),
                    )
                )
            session.commit()
            return int(movement.id)

    def list_movements(
        self,
        *,
        from_ts: int | None = None,
        to_ts: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MovementSummary]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        with Session(self.engine) as session:
            q = select(StockMovement).order_by(StockMovement.created_at_ts.desc(), StockMovement.id.desc())
            if from_ts is not None:
                q = q.where(StockMovement.created_at_ts >= from_ts)
            if to_ts is not None:
                q = q.where(StockMovement.created_at_ts <= to_ts)
            rows = session.scalars(q.limit(limit).offset(offset)).all()
            return [self._to_summary(r) for r in rows]

    def get_movement(self, movement_id: int) -> MovementDetail | None:
        with Session(self.engine) as session:
            row = session.get(StockMovement, movement_id)
            if row is None:
                return None
            lines = session.scalars(
                select(StockMovementLine)
                .where(StockMovementLine.movement_id == movement_id)
                .order_by(StockMovementLine.sku)
            ).all()
            return self._to_detail(row, lines)

    def _to_summary(self, row: StockMovement) -> MovementSummary:
        return MovementSummary(
            id=int(row.id),
            created_at_ts=int(row.created_at_ts),
            direction=str(row.direction),
            direction_label=_DIRECTION_LABELS.get(str(row.direction), str(row.direction)),
            source=str(row.source),
            sheet_url=str(row.sheet_url or ""),
            sku_count=int(row.sku_count),
            total_quantity=int(row.total_quantity),
        )

    def _to_detail(self, row: StockMovement, lines: list[StockMovementLine]) -> MovementDetail:
        try:
            warnings = json.loads(row.warnings_json or "[]")
            if not isinstance(warnings, list):
                warnings = []
        except json.JSONDecodeError:
            warnings = []
        warn_str = [str(w) for w in warnings]
        line_rows = [
            MovementLineRow(sku=ln.sku, quantity=int(ln.quantity), delta=int(ln.delta))
            for ln in lines
        ]
        return MovementDetail(
            id=int(row.id),
            created_at_ts=int(row.created_at_ts),
            direction=str(row.direction),
            direction_label=_DIRECTION_LABELS.get(str(row.direction), str(row.direction)),
            source=str(row.source),
            sheet_url=str(row.sheet_url or ""),
            sku_count=int(row.sku_count),
            total_quantity=int(row.total_quantity),
            warnings=warn_str,
            lines=line_rows,
        )
