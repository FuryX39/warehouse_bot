"""Этикетки коробов FBW 58×40 мм."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.wb_fbw_box_label_pdf import (
    LABEL_HEIGHT_MM,
    LABEL_WIDTH_MM,
    build_fbw_box_label_rows,
    format_fbw_box_human_id,
    format_fbw_plan_date,
    generate_wb_fbw_box_labels_pdf,
    parse_fbw_box_id_from_package_code,
)

ROOT = Path(__file__).resolve().parents[1]

# Официальные этикетки кабинета: QR = packageCode, сверху человеческий номер.
# 41357389 — tables_examples/833f95dd-….pdf; 41322982 — 72bbc950-….pdf.
_OFFICIAL_PACKAGE_BOX_IDS = (
    ("$Ts;FCw;dfwy;1;Pyt2sDs;TAS", "4139894"),
    ("$Ts;N+q;R6a9;1;Pyu678c;TAS", "4139962"),
    ("$Ts;IPm;huVW;1;Pyw4/2U;TAS", "4140088"),
    ("$Ts;N5I;AUZi;1;OBtRDW0;TAS", "3677009"),
    ("$Ts;Owr;gYOG;1;OBt3f+I;TAS", "3677047"),
    ("$Ts;IEi;c2Zu;1;OBubJTY;TAS", "3677083"),
    ("$Ts;CG8;fG1X;1;OBwl//g;TAS", "3677221"),
    ("$Ts;ECZ;3NXf;1;OB4QeZA;TAS", "3677712"),
    ("$Ts;IEm;Ctt0;1;OB80or8;TAS", "3678004"),
)


def test_parse_box_id_from_package_code() -> None:
    for package_code, box_id in _OFFICIAL_PACKAGE_BOX_IDS:
        assert parse_fbw_box_id_from_package_code(package_code) == box_id
    assert format_fbw_box_human_id("4139894") == "413 9894"
    assert format_fbw_box_human_id("3677009") == "367 7009"
    assert parse_fbw_box_id_from_package_code("") == ""
    assert parse_fbw_box_id_from_package_code("WB_689") == ""
    assert parse_fbw_box_id_from_package_code("$Ts;only;TAS") == ""


def test_fbw_box_labels_pdf_matches_wb_layout() -> None:
    rows = build_fbw_box_label_rows(
        supply_id=41357389,
        goods=[{"barcode": "4673746970515", "vendorCode": "SS743"}],
        boxes=[
            {
                "packageCode": "$Ts;N+q;R6a9;1;Pyu678c;TAS",
                "quantity": 10,
                "barcodes": [{"barcode": "4673746970515", "quantity": 10}],
            }
        ],
        supply={
            "supplyDate": "2026-09-22T00:00:00Z",
            "warehouseName": "СЦ Новосибирск 4",
            "sellerName": 'ООО "ШАЙН СИСТЕМС"',
            "boxTypeID": 1,
        },
    )
    assert rows[0]["sku"] == "SS743"
    assert rows[0]["plan_date"] == "22.09.26"
    assert rows[0]["box_type"] == "Короб"
    assert rows[0]["box_id"] == "4139962"
    pdf = generate_wb_fbw_box_labels_pdf(rows)
    assert pdf.startswith(b"%PDF")
    page = PdfReader(BytesIO(pdf)).pages[0]
    width_pt = float(page.mediabox.width)
    height_pt = float(page.mediabox.height)
    assert round(width_pt / 72 * 25.4, 1) == LABEL_WIDTH_MM
    assert round(height_pt / 72 * 25.4, 1) == LABEL_HEIGHT_MM
    text = page.extract_text() or ""
    assert "№ поставки:" in text
    assert "41357389" in text
    assert "Плановая дата:" in text
    assert "22.09.26" in text
    assert "Пункт отгрузки:" in text
    assert "СЦ Новосибирск" in text
    assert "Продавец:" in text
    assert "Тип поставки:" in text
    assert "Короб" in text
    assert "Кол-во товаров:" in text
    assert "10 шт" in text
    assert "413" in text
    assert "9962" in text


def test_format_fbw_plan_date() -> None:
    assert format_fbw_plan_date("2026-09-22") == "22.09.26"


def test_official_label_pdfs_match_package_code_box_id() -> None:
    """Регрессия по PDF из кабинета, если файлы лежат локально."""
    cv2 = pytest.importorskip("cv2")
    fitz = pytest.importorskip("fitz")
    np = pytest.importorskip("numpy")

    samples = [
        ROOT / "tables_examples" / "833f95dd-743d-4611-a869-403050f6496b.pdf",
        ROOT / "tables_examples" / "72bbc950-758b-4f4f-a118-ba65e00493b5.pdf",
    ]
    present = [path for path in samples if path.is_file()]
    if not present:
        pytest.skip("нет локальных PDF этикеток WB в tables_examples")

    det = cv2.QRCodeDetector()
    mismatches: list[str] = []
    checked = 0
    for pdf_path in present:
        doc = fitz.open(pdf_path)
        for index, page in enumerate(doc, start=1):
            printed = _printed_box_id(page)
            code = _qr_package_code(page, det, np, cv2, fitz)
            parsed = parse_fbw_box_id_from_package_code(code)
            checked += 1
            if not printed or printed != parsed:
                mismatches.append(
                    f"{pdf_path.name} p{index}: printed={printed!r} parsed={parsed!r} code={code!r}"
                )
    assert checked >= 15
    assert mismatches == []


def _printed_box_id(page: object) -> str:
    digits: list[str] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if span["bbox"][1] > 20:
                    continue
                text = span["text"].replace(" ", "").strip()
                if text.isdigit():
                    digits.append(text)
    human = "".join(digits)
    supply = ""
    for line in page.get_text("text").splitlines():
        if "поставки" in line.lower() and any(ch.isdigit() for ch in line):
            supply = "".join(ch for ch in line if ch.isdigit())
            break
    if supply and human.endswith(supply) and human != supply:
        human = human[: -len(supply)]
    return human


def _qr_package_code(page: object, det: object, np: object, cv2: object, fitz: object) -> str:
    for scale in (4, 6, 8, 12):
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        _ok, decoded, _pts, _rest = det.detectAndDecodeMulti(bgr)
        code = next((item for item in (decoded or []) if item), "")
        if code:
            return code
        x0, y0, x1, y1 = (int(v * scale) for v in (5.67, 19.0, 53.86, 67.19))
        pad = 8 * scale
        crop = bgr[max(0, y0 - pad) : y1 + pad, max(0, x0 - pad) : x1 + pad]
        if crop.size:
            data, pts, _ = det.detectAndDecode(crop)
            if data:
                return data
    return ""
