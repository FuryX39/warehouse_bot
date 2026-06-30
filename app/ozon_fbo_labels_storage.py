"""Файловое хранилище PDF-этикеток FBO Ozon (один объединённый файл на поставку)."""

from __future__ import annotations

from pathlib import Path

from app.config import _PROJECT_ROOT

_LABELS_ROOT = _PROJECT_ROOT / "data" / "ozon_fbo_labels"


def labels_root() -> Path:
    root = _LABELS_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def supply_label_relpath(supply_id: int) -> str:
    return f"{int(supply_id)}.pdf"


def supply_labels_url(supply_id: int) -> str:
    return f"/api/warehouse/marketplaces/ozon-fbo/supplies/{int(supply_id)}/labels.pdf"


def batch_labels_url(batch_id: int) -> str:
    return f"/api/warehouse/marketplaces/ozon-fbo/batches/{int(batch_id)}/labels.pdf"


def resolve_label_path(relpath: str) -> Path | None:
    rel = str(relpath or "").strip().replace("\\", "/")
    if not rel or ".." in rel.split("/"):
        return None
    path = (labels_root() / rel).resolve()
    root = labels_root()
    if not str(path).startswith(str(root)):
        return None
    return path


def save_supply_label(supply_id: int, pdf_bytes: bytes) -> str:
    relpath = supply_label_relpath(supply_id)
    path = resolve_label_path(relpath)
    if path is None:
        raise ValueError("Некорректный путь этикетки")
    path.write_bytes(pdf_bytes)
    return relpath


def read_supply_label(relpath: str) -> bytes | None:
    path = resolve_label_path(relpath)
    if path is None or not path.is_file():
        return None
    return path.read_bytes()


def delete_supply_label(relpath: str) -> None:
    path = resolve_label_path(relpath)
    if path is None or not path.is_file():
        return
    path.unlink(missing_ok=True)


def delete_supply_labels(supply_id: int) -> None:
    delete_supply_label(supply_label_relpath(supply_id))
