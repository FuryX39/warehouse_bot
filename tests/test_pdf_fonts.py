from io import BytesIO

from reportlab.pdfgen import canvas

from app.pdf_fonts import get_pdf_label_fonts


def test_pdf_label_fonts_embed_cyrillic() -> None:
    get_pdf_label_fonts.cache_clear()
    regular, bold = get_pdf_label_fonts()
    assert regular != "Helvetica"
    assert bold != "Helvetica-Bold"

    buf = BytesIO()
    pdf = canvas.Canvas(buf)
    pdf.setFont(regular, 12)
    pdf.drawString(40, 800, "Поставщик")
    pdf.setFont(bold, 12)
    pdf.drawString(40, 780, "ВсеИнструменты")
    pdf.save()
    data = buf.getvalue()
    assert b"DejaVu" in data
    text = "".join(
        (page.extract_text() or "")
        for page in __import__("pypdf").PdfReader(BytesIO(data)).pages
    )
    assert "Поставщик" in text
    assert "ВсеИнструменты" in text
