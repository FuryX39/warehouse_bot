"""Файловое хранилище PDF-этикеток грузомест FBO Ozon."""

from __future__ import annotations

from pathlib import Path

from app.config import _PROJECT_ROOT

_LABELS_ROOT = _PROJECT_ROOT / "data" / "ozon_fbo_labels"


def labels_root() -> Path:
    root = _LABELS_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def cargo_label_relpath(supply_id: int, cargo_id: int) -> str:
    return f"{int(supply_id)}/{int(cargo_id)}.pdf"


def cargo_labels_url(cargo_id: int) -> str:
    return f"/api/warehouse/marketplaces/ozon-fbo/cargoes/{int(cargo_id)}/labels.pdf"


def resolve_label_path(relpath: str) -> Path | None:
    rel = str(relpath or "").strip().replace("\\", "/")
    if not rel or ".." in rel.split("/"):
        return None
    path = (labels_root() / rel).resolve()
    root = labels_root()
    if not str(path).startswith(str(root)):
        return None
    return path


def save_cargo_label(supply_id: int, cargo_id: int, pdf_bytes: bytes) -> str:
    relpath = cargo_label_relpath(supply_id, cargo_id)
    path = resolve_label_path(relpath)
    if path is None:
        raise ValueError("Некорректный путь этикетки")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)
    return relpath


def read_cargo_label(relpath: str) -> bytes | None:
    path = resolve_label_path(relpath)
    if path is None or not path.is_file():
        return None
    return path.read_bytes()


def delete_cargo_label(relpath: str) -> None:
    path = resolve_label_path(relpath)
    if path is None or not path.is_file():
        return
    path.unlink(missing_ok=True)
    parent = path.parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()


def delete_supply_labels(supply_id: int) -> None:
    folder = labels_root() / str(int(supply_id))
    if not folder.is_dir():
        return
    for pdf in folder.glob("*.pdf"):
        pdf.unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        pass
