"""Файловое хранилище PDF-вложений задач упаковщикам."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

TASK_ATTACHMENT_KINDS = frozenset({"a4", "label"})
TASK_ATTACHMENT_KIND_LABELS = {"a4": "А4", "label": "Этикетки"}
MAX_TASK_ATTACHMENT_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class StoredTaskAttachment:
    id: int
    task_id: int
    kind: str
    original_filename: str
    file_size: int
    mime_type: str
    sort_order: int
    uploaded_at_ts: int


class WarehouseTaskFileStorage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def store_pdf(
        self,
        *,
        content: bytes,
        original_filename: str,
    ) -> tuple[str, int]:
        if not content:
            raise ValueError("Файл пустой")
        if len(content) > MAX_TASK_ATTACHMENT_BYTES:
            raise ValueError("PDF слишком большой (макс. 20 МБ)")
        if not content.startswith(b"%PDF"):
            raise ValueError("Нужен файл PDF")
        stored_name = f"{uuid.uuid4().hex}.pdf"
        path = self.data_dir / stored_name
        path.write_bytes(content)
        return stored_name, len(content)

    def path_for(self, stored_name: str) -> Path | None:
        name = str(stored_name or "").strip()
        if not name or "/" in name or "\\" in name or ".." in name:
            return None
        path = self.data_dir / name
        return path if path.is_file() else None

    def delete_stored(self, stored_name: str) -> None:
        path = self.path_for(stored_name)
        if path is not None:
            path.unlink(missing_ok=True)
