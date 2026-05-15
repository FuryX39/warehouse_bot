"""Журнал перемещений остатков — отдельная БД (MOVEMENT_DB_URL)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, create_engine, inspect, select, text
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
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    comment: Mapped[str] = mapped_column(String(4096), nullable=False, default="")
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
    title: str
    comment: str
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
    title: str
    title_is_default: bool
    comment: str
    sku_count: int
    total_quantity: int
    warnings: list[str]
    lines: list[MovementLineRow]


_DIRECTION_LABELS = {"in": "Приход", "out": "Расход"}


def default_movement_title(movement_id: int, created_at_ts: int) -> str:
    dt = datetime.fromtimestamp(int(created_at_ts), tz=timezone.utc)
    return f"Перемещение №{movement_id} {dt.strftime('%d.%m.%Y')}"


def movement_display_title(row: StockMovement) -> tuple[str, bool]:
    raw = (row.title or "").strip()
    if raw:
        return raw, False
    return default_movement_title(int(row.id), int(row.created_at_ts)), True


class MovementRepository:
    def __init__(self, db_url: str) -> None:
        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        MovementBase.metadata.create_all(self.engine)
        self._ensure_title_comment_columns()

    def _ensure_title_comment_columns(self) -> None:
        insp = inspect(self.engine)
        if "stock_movements" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("stock_movements")}
        dialect = self.engine.dialect.name
        with self.engine.begin() as conn:
            if "title" not in cols:
                if dialect == "sqlite":
                    conn.execute(text("ALTER TABLE stock_movements ADD COLUMN title VARCHAR(512) NOT NULL DEFAULT ''"))
                elif dialect == "postgresql":
                    conn.execute(
                        text("ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS title VARCHAR(512) NOT NULL DEFAULT ''")
                    )
            if "comment" not in cols:
                if dialect == "sqlite":
                    conn.execute(text("ALTER TABLE stock_movements ADD COLUMN comment VARCHAR(4096) NOT NULL DEFAULT ''"))
                elif dialect == "postgresql":
                    conn.execute(
                        text(
                            "ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS comment VARCHAR(4096) NOT NULL DEFAULT ''"
                        )
                    )

    def create_movement(
        self,
        *,
        created_at_ts: int,
        direction: str,
        source: str,
        sheet_url: str,
        lines: list[tuple[str, int, int]],
        warnings: list[str] | None = None,
        title: str | None = None,
        comment: str | None = None,
    ) -> int:
        """lines: (sku, quantity>0, signed delta). Возвращает id перемещения."""
        direction = direction if direction in ("in", "out") else "in"
        warn_json = json.dumps(list(warnings or [])[:200], ensure_ascii=False)
        if len(warn_json) > 8000:
            warn_json = warn_json[:8000] + "]"
        sku_count = len(lines)
        total_qty = sum(int(q) for _, q, _ in lines)
        title_stored = (title or "").strip()[:512]
        comment_stored = (comment if comment is not None else "")[:4096]
        with Session(self.engine) as session:
            movement = StockMovement(
                created_at_ts=int(created_at_ts),
                direction=direction,
                source=str(source or "unknown")[:32],
                sheet_url=(sheet_url or "")[:2048],
                title=title_stored,
                comment=comment_stored,
                sku_count=sku_count,
                total_quantity=total_qty,
                warnings_json=warn_json,
            )
            session.add(movement)
            session.flush()
            if not title_stored:
                movement.title = default_movement_title(int(movement.id), int(movement.created_at_ts))[:512]
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

    def update_movement_meta(
        self,
        movement_id: int,
        *,
        title: str | None = None,
        comment: str | None = None,
        update_title: bool = False,
        update_comment: bool = False,
    ) -> bool:
        with Session(self.engine) as session:
            row = session.get(StockMovement, movement_id)
            if row is None:
                return False
            if update_title:
                row.title = (title or "").strip()[:512]
            if update_comment:
                row.comment = (comment if comment is not None else "")[:4096]
            session.commit()
            return True

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
        title, _ = movement_display_title(row)
        return MovementSummary(
            id=int(row.id),
            created_at_ts=int(row.created_at_ts),
            direction=str(row.direction),
            direction_label=_DIRECTION_LABELS.get(str(row.direction), str(row.direction)),
            source=str(row.source),
            sheet_url=str(row.sheet_url or ""),
            title=title,
            comment=str(row.comment or ""),
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
        title, title_is_default = movement_display_title(row)
        return MovementDetail(
            id=int(row.id),
            created_at_ts=int(row.created_at_ts),
            direction=str(row.direction),
            direction_label=_DIRECTION_LABELS.get(str(row.direction), str(row.direction)),
            source=str(row.source),
            sheet_url=str(row.sheet_url or ""),
            title=title,
            title_is_default=title_is_default,
            comment=str(row.comment or ""),
            sku_count=int(row.sku_count),
            total_quantity=int(row.total_quantity),
            warnings=warn_str,
            lines=line_rows,
        )
