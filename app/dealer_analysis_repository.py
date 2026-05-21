"""Хранение загруженных Excel и отчётов анализа дилерских заказов."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import ForeignKey, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class DealerAnalysisBase(DeclarativeBase):
    pass


class DealerAnalysisFileRow(DealerAnalysisBase):
    __tablename__ = "dealer_analysis_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # source_a | source_b | report
    period_label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    run_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("dealer_analysis_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    stored_name: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    uploaded_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, index=True)


class DealerAnalysisRunRow(DealerAnalysisBase):
    __tablename__ = "dealer_analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period_a_label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    period_b_label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    source_a_file_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_b_file_id: Mapped[int] = mapped_column(Integer, nullable=False)
    report_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    stats_json: Mapped[str] = mapped_column(String(8192), nullable=False, default="{}")


@dataclass(frozen=True)
class DealerAnalysisFileInfo:
    id: int
    file_kind: str
    period_label: str
    run_id: int | None
    original_filename: str
    file_size: int
    mime_type: str
    uploaded_at_ts: int


@dataclass(frozen=True)
class DealerAnalysisRunInfo:
    id: int
    period_a_label: str
    period_b_label: str
    source_a_file_id: int
    source_b_file_id: int
    report_file_id: int | None
    created_at_ts: int
    stats: dict


class DealerAnalysisRepository:
    def __init__(self, db_url: str, data_dir: Path) -> None:
        self.engine = create_engine(db_url, future=True)
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def init_schema(self) -> None:
        DealerAnalysisBase.metadata.create_all(self.engine)

    def _now_ts(self) -> int:
        return int(datetime.now(tz=timezone.utc).timestamp())

    def store_file(
        self,
        *,
        file_kind: str,
        period_label: str,
        original_filename: str,
        content: bytes,
        mime_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        run_id: int | None = None,
    ) -> int:
        ext = Path(original_filename or "").suffix.lower()
        if ext not in (".xlsx", ".xlsm", ".xltx"):
            ext = ".xlsx"
        stored_name = f"{uuid.uuid4().hex}{ext}"
        path = self.data_dir / stored_name
        path.write_bytes(content)
        ts = self._now_ts()
        with Session(self.engine) as session:
            row = DealerAnalysisFileRow(
                file_kind=file_kind,
                period_label=(period_label or "").strip()[:128],
                run_id=run_id,
                original_filename=(original_filename or stored_name)[:512],
                stored_name=stored_name,
                file_size=len(content),
                mime_type=(mime_type or "")[:128],
                uploaded_at_ts=ts,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return int(row.id)

    def file_path(self, file_id: int) -> Path | None:
        with Session(self.engine) as session:
            row = session.get(DealerAnalysisFileRow, file_id)
            if row is None:
                return None
            path = self.data_dir / row.stored_name
            return path if path.is_file() else None

    def get_file(self, file_id: int) -> DealerAnalysisFileInfo | None:
        with Session(self.engine) as session:
            row = session.get(DealerAnalysisFileRow, file_id)
            if row is None:
                return None
            return _file_info(row)

    def list_files(self, *, limit: int = 200) -> list[DealerAnalysisFileInfo]:
        limit = max(1, min(500, limit))
        with Session(self.engine) as session:
            rows = session.scalars(
                select(DealerAnalysisFileRow).order_by(DealerAnalysisFileRow.uploaded_at_ts.desc()).limit(limit)
            ).all()
            return [_file_info(r) for r in rows]

    def create_run(
        self,
        *,
        period_a_label: str,
        period_b_label: str,
        source_a_file_id: int,
        source_b_file_id: int,
        report_file_id: int | None,
        stats: dict,
    ) -> int:
        stats_json = json.dumps(stats, ensure_ascii=False)
        if len(stats_json) > 8000:
            stats_json = stats_json[:8000] + "}"
        with Session(self.engine) as session:
            run = DealerAnalysisRunRow(
                period_a_label=(period_a_label or "").strip()[:128],
                period_b_label=(period_b_label or "").strip()[:128],
                source_a_file_id=source_a_file_id,
                source_b_file_id=source_b_file_id,
                report_file_id=report_file_id,
                created_at_ts=self._now_ts(),
                stats_json=stats_json,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = int(run.id)
            for fid in (source_a_file_id, source_b_file_id, report_file_id):
                if fid is None:
                    continue
                frow = session.get(DealerAnalysisFileRow, fid)
                if frow is not None:
                    frow.run_id = run_id
            session.commit()
            return run_id

    def list_runs(self, *, limit: int = 50) -> list[DealerAnalysisRunInfo]:
        limit = max(1, min(200, limit))
        with Session(self.engine) as session:
            rows = session.scalars(
                select(DealerAnalysisRunRow).order_by(DealerAnalysisRunRow.created_at_ts.desc()).limit(limit)
            ).all()
            out: list[DealerAnalysisRunInfo] = []
            for row in rows:
                try:
                    stats = json.loads(row.stats_json or "{}")
                except json.JSONDecodeError:
                    stats = {}
                if not isinstance(stats, dict):
                    stats = {}
                out.append(
                    DealerAnalysisRunInfo(
                        id=int(row.id),
                        period_a_label=row.period_a_label,
                        period_b_label=row.period_b_label,
                        source_a_file_id=int(row.source_a_file_id),
                        source_b_file_id=int(row.source_b_file_id),
                        report_file_id=int(row.report_file_id) if row.report_file_id else None,
                        created_at_ts=int(row.created_at_ts),
                        stats=stats,
                    )
                )
            return out

    def attach_report_to_run(self, run_id: int, report_file_id: int, stats: dict) -> None:
        stats_json = json.dumps(stats, ensure_ascii=False)
        if len(stats_json) > 8000:
            stats_json = stats_json[:8000] + "}"
        with Session(self.engine) as session:
            run = session.get(DealerAnalysisRunRow, run_id)
            if run is None:
                return
            run.report_file_id = report_file_id
            run.stats_json = stats_json
            frow = session.get(DealerAnalysisFileRow, report_file_id)
            if frow is not None:
                frow.run_id = run_id
            session.commit()


def _file_info(row: DealerAnalysisFileRow) -> DealerAnalysisFileInfo:
    return DealerAnalysisFileInfo(
        id=int(row.id),
        file_kind=row.file_kind,
        period_label=row.period_label,
        run_id=int(row.run_id) if row.run_id else None,
        original_filename=row.original_filename,
        file_size=int(row.file_size),
        mime_type=row.mime_type,
        uploaded_at_ts=int(row.uploaded_at_ts),
    )
