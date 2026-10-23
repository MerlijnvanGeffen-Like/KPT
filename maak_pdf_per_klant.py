"""PDF-overzicht per klant en per land (ReportLab)."""
from __future__ import annotations

import io
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BLUE = colors.HexColor("#EE1D23")
LIGHT_BLUE = colors.HexColor("#FEE2E2")
GREY = colors.HexColor("#6B7280")


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]', "_", value.strip())
    value = re.sub(r"\s+", " ", value)
    return value.rstrip(". ") or "Onbekend"


def euro(value: float) -> str:
    return f"€ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def number(value: float, decimals: int = 1) -> str:
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def register_font() -> str:
    candidates = [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\calibri.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            pdfmetrics.registerFont(TTFont("KPTFont", str(path)))
            return "KPTFont"
    return "Helvetica"


FONT = register_font()


def add_page_number(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(GREY)
    canvas.drawRightString(A4[0] - 16 * mm, 10 * mm, f"Pagina {doc.page}")
    canvas.restoreState()


def make_pdf(
    target,
    customer: str,
    rows: list[dict],
    invoice: dict,
    report_name: str,
    country: str = "",
) -> None:
    countries = sorted({row.get("country") for row in rows if row.get("country")})
    country_label = country or ", ".join(countries) or "—"
    styles = getSampleStyleSheet()
    normal = ParagraphStyle(
        "KPTNormal",
        parent=styles["Normal"],
        fontName=FONT,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#26323B"),
    )
    title = ParagraphStyle(
        "KPTTitle",
        parent=normal,
        fontSize=19,
        leading=23,
        textColor=BLUE,
        spaceAfter=4 * mm,
    )
    right = ParagraphStyle("KPTRight", parent=normal, alignment=TA_RIGHT)
    table_text = ParagraphStyle(
        "KPTTableText",
        parent=normal,
        fontSize=7.5,
        leading=9,
    )
    header = ParagraphStyle(
        "KPTHeader",
        parent=normal,
        fontSize=7.5,
        leading=9,
        textColor=colors.white,
        alignment=1,
    )

    week_id = invoice.get("id") or "Week"
    inv_label = week_id if str(week_id).lower().startswith("week") else f"Rittenboek {week_id}"
    period = f"{invoice.get('from') or '-'} t/m {invoice.get('to') or '-'}"

    document = SimpleDocTemplate(
        target if hasattr(target, "write") else str(target),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Toloverzicht {week_id} - {customer}",
        author="Kuijpers Transport V.O.F.",
    )

    story = [
        Table(
            [
                [
                    Paragraph(
                        '<font color="#EE1D23" size="14"><b>Kuijpers</b></font> '
                        '<font color="#6B7280"><b>TRANSPORT</b></font>'
                        "<br/>Huygensweg 18<br/>5466 AN Veghel",
                        normal,
                    ),
                    Paragraph(f"<b>{inv_label}</b><br/>{invoice.get('date') or ''}<br/>{period}", right),
                ]
            ],
            colWidths=[95 * mm, 80 * mm],
        ),
        Spacer(1, 7 * mm),
        Paragraph("Toloverzicht", title),
        Paragraph(
            f"<b>Klant:</b> {customer}<br/><b>Land:</b> {country_label}",
            normal,
        ),
        Spacer(1, 6 * mm),
    ]

    show_tunnel = any(round(float(row.get("tunnelEur") or 0), 2) > 0 for row in rows)
    headers = [
        Paragraph("Datum", header),
        Paragraph("Kenteken", header),
        Paragraph("Ritnummer", header),
        Paragraph("Land", header),
        Paragraph("KM rit<br/><font size='6.5'>planning</font>", header),
        Paragraph("KM tol<br/><font size='6.5'>rapport</font>", header),
        Paragraph("Tol", header),
    ]
    if show_tunnel:
        headers.extend(
            [
                Paragraph("Tunnel", header),
                Paragraph("Tunnel €", header),
            ]
        )
    data = [headers]
    total_plan_km = 0.0
    for row in rows:
        plan_km = float(row.get("planKm") or 0)
        total_plan_km += plan_km
        if row.get("km"):
            km_tol = number(row.get("km") or 0)
        else:
            km_tol = "—"
        line = [
            row["date"].strftime("%d-%m-%Y") if row.get("date") else "",
            row.get("plate") or "",
            Paragraph((row.get("ritnr") or "—").replace(" / ", "<br/>"), table_text),
            row.get("country") or "—",
            number(plan_km) if plan_km else "—",
            km_tol,
            euro(row.get("eur") or 0),
        ]
        if show_tunnel:
            tunnel_eur = float(row.get("tunnelEur") or 0)
            line.extend(
                [
                    Paragraph(row.get("tunnel") or "—", table_text),
                    euro(tunnel_eur) if tunnel_eur else "—",
                ]
            )
        data.append(line)

    total_km = round(sum(row.get("km") or 0 for row in rows), 1)
    total_eur = round(sum(row.get("eur") or 0 for row in rows), 2)
    total_tunnel = round(sum(float(row.get("tunnelEur") or 0) for row in rows), 2)
    footer = ["", "", "Totaal", "", number(round(total_plan_km, 1)), number(total_km), euro(total_eur)]
    if show_tunnel:
        footer.extend(["", euro(total_tunnel) if total_tunnel else "—"])
    data.append(footer)

    widths = [16 * mm, 21 * mm, 22 * mm, 16 * mm, 16 * mm, 16 * mm, 18 * mm]
    if show_tunnel:
        widths.extend([28 * mm, 19 * mm])
    table = Table(
        data,
        colWidths=widths,
        repeatRows=1,
        hAlign="LEFT",
    )
    style = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), FONT),
        ("ALIGN", (4, 1), (6, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -2), 0.35, colors.HexColor("#CBD5DC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F6F8FA")]),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ("LINEABOVE", (0, -1), (-1, -1), 1, BLUE),
        ("FONTNAME", (0, -1), (-1, -1), FONT),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if show_tunnel:
        style.append(("ALIGN", (8, 1), (8, -1), "RIGHT"))
    table.setStyle(TableStyle(style))
    guide = (
        "Leeswijzer: <b>KM rit</b> is de planning per land. "
        "<b>KM tol</b> is wat het tolrapport telt. "
        "<b>Tol</b> is de wegentol."
    )
    if show_tunnel:
        guide += " <b>Tunnel €</b> hoort bij de tunnel in die kolom en zit niet in Tol."
    if any(row.get("kmFromPlan") for row in rows):
        guide += (
            " Bij Frankrijk ontbreken vaak kilometers in het tolrapport; "
            "die vullen we aan uit het rittenboek. Het bedrag blijft uit het rapport."
        )
    story.extend(
        [
            table,
            Spacer(1, 7 * mm),
            Paragraph(guide, normal),
        ]
    )
    document.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)


def make_pdf_bytes(
    customer: str,
    rows: list[dict],
    invoice: dict,
    report_name: str,
    country: str = "",
) -> bytes:
    buffer = io.BytesIO()
    make_pdf(buffer, customer, rows, invoice, report_name, country=country)
    return buffer.getvalue()
