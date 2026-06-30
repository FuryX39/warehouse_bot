"""Фоновые задачи FBO Ozon (создание пакета, синхронизация, отправка ГМ)."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_JOB_TTL_SEC = 3600


def _prune_old_jobs() -> None:
    now = time.time()
    stale = [jid for jid, row in _jobs.items() if now - float(row.get("created_at") or 0) > _JOB_TTL_SEC]
    for jid in stale:
        _jobs.pop(jid, None)


def start_job(kind: str, worker: Callable[[], dict[str, Any]]) -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        _prune_old_jobs()
        _jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "running",
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": None,
        }

    def run() -> None:
        try:
            result = worker()
            with _lock:
                row = _jobs.get(job_id)
                if row is None:
                    return
                row["status"] = "done"
                row["result"] = result
                row["updated_at"] = time.time()
        except Exception as exc:  # noqa: BLE001
            with _lock:
                row = _jobs.get(job_id)
                if row is None:
                    return
                row["status"] = "failed"
                row["error"] = str(exc)
                row["updated_at"] = time.time()

    threading.Thread(target=run, daemon=True, name=f"ozon-fbo-job-{kind}").start()
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _jobs.get(str(job_id or "").strip())
        if row is None:
            return None
        return dict(row)
