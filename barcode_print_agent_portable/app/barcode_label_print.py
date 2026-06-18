"""Тихая печать PDF-этикеток со штрихкодом Code128 на локальном принтере."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def print_label_pdf(pdf_bytes: bytes, *, printer: str | None = None) -> None:
    if not pdf_bytes:
        raise ValueError("Пустой PDF")
    printer = (printer or os.getenv("BARCODE_PRINT_PRINTER") or "").strip() or None

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        path = Path(tmp.name)

    try:
        if os.name == "nt":
            _print_pdf_windows(path, printer=printer)
        else:
            _print_pdf_posix(path, printer=printer)
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _print_pdf_windows(path: Path, *, printer: str | None) -> None:
    sumatra = (os.getenv("BARCODE_PRINT_SUMATRA") or "").strip()
    candidates = [sumatra] if sumatra else []
    candidates.extend(
        [
            r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
            r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
        ]
    )
    exe = next((c for c in candidates if c and Path(c).is_file()), "")
    if not exe:
        raise RuntimeError(
            "Установите SumatraPDF и укажите BARCODE_PRINT_SUMATRA в config.env "
            "(полный путь к SumatraPDF.exe)."
        )
    cmd = [exe, "-silent"]
    if printer:
        cmd.extend(["-print-to", printer])
    else:
        cmd.append("-print-to-default")
    cmd.append(str(path))
    subprocess.run(cmd, check=True, timeout=90)


def _print_pdf_posix(path: Path, *, printer: str | None) -> None:
    lp = shutil.which("lp")
    if not lp:
        raise RuntimeError("Команда lp не найдена (нужен CUPS).")
    cmd = [lp]
    if printer:
        cmd.extend(["-d", printer])
    cmd.append(str(path))
    subprocess.run(cmd, check=True, timeout=90)
