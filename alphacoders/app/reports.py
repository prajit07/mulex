from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# MULEX Institutional Palette
NAVY_DEEP = colors.HexColor("#10243A")
NAVY_INST = colors.HexColor("#172F49")
STEEL_BLUE = colors.HexColor("#49657F")
TEAL = colors.HexColor("#168C89")

ROW_HEADER_BG = colors.HexColor("#F1F5F9")
GRID_BORDER = colors.HexColor("#CBD5E1")
GRID_STRONG = colors.HexColor("#94A3B8")
TEXT_MAIN = colors.HexColor("#0F172A")
TEXT_MUTED = colors.HexColor("#475569")

SEV_COLORS = {
    "critical": colors.HexColor("#B91C1C"),
    "high": colors.HexColor("#B45309"),
    "review": colors.HexColor("#334155"),
    "low": colors.HexColor("#0F766E"),
    "verified": colors.HexColor("#0F766E"),
    "approved": colors.HexColor("#0F766E"),
    "manual_review": colors.HexColor("#B45309"),
    "escalated": colors.HexColor("#B45309"),
    "rejected": colors.HexColor("#B91C1C"),
    "rejected_by_officer": colors.HexColor("#B91C1C"),
}

PAGE_W, PAGE_H = A4
MARGIN_X = 15 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X
LOGO_PATH = Path(__file__).resolve().parent.parent / "web" / "logo.png"


def _register_fonts():
    """Register corporate sans-serif TTF fonts when readable, falling back to Helvetica."""
    candidates = [
        (
            "MulexSans",
            Path(r"C:\Windows\Fonts\calibri.ttf"),
            Path(r"C:\Windows\Fonts\calibrib.ttf"),
            Path(r"C:\Windows\Fonts\calibrii.ttf"),
        ),
        (
            "MulexSans",
            Path(r"C:\Windows\Fonts\arial.ttf"),
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
            Path(r"C:\Windows\Fonts\ariali.ttf"),
        ),
        (
            "MulexSans",
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf"),
        ),
        (
            "MulexSans",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
        ),
    ]
    for family, reg, bold, ital in candidates:
        try:
            if reg.exists() and bold.exists():
                if family not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(family, str(reg)))
                    pdfmetrics.registerFont(TTFont(f"{family}-Bold", str(bold)))
                    if ital.exists():
                        pdfmetrics.registerFont(TTFont(f"{family}-Italic", str(ital)))
                italic_name = f"{family}-Italic" if ital.exists() else family
                return family, f"{family}-Bold", italic_name, True
        except Exception:
            continue
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", False


FONT_REG, FONT_BOLD, FONT_ITALIC, UNICODE_READY = _register_fonts()


def _clean_text(val):
    text = str(val if val is not None else "")
    if not UNICODE_READY:
        text = text.replace("\u2192", "->").replace("\u20b9", "INR ").replace("\u2026", "...")
    return escape(text)


def _money(paise):
    return f"INR {paise / 100:,.2f}"


def _styles():
    base = getSampleStyleSheet()
    return {
        "th": ParagraphStyle(
            "MulexTh",
            parent=base["Normal"],
            fontName=FONT_BOLD,
            fontSize=8.2,
            leading=10.5,
            textColor=NAVY_DEEP,
        ),
        "th_num": ParagraphStyle(
            "MulexThNum",
            parent=base["Normal"],
            fontName=FONT_BOLD,
            fontSize=8.2,
            leading=10.5,
            textColor=NAVY_DEEP,
            alignment=TA_RIGHT,
        ),
        "td": ParagraphStyle(
            "MulexTd",
            parent=base["Normal"],
            fontName=FONT_REG,
            fontSize=8.3,
            leading=11,
            textColor=TEXT_MAIN,
        ),
        "td_bold": ParagraphStyle(
            "MulexTdBold",
            parent=base["Normal"],
            fontName=FONT_BOLD,
            fontSize=8.3,
            leading=11,
            textColor=NAVY_DEEP,
        ),
        "td_num": ParagraphStyle(
            "MulexTdNum",
            parent=base["Normal"],
            fontName=FONT_BOLD,
            fontSize=8.3,
            leading=11,
            textColor=NAVY_DEEP,
            alignment=TA_RIGHT,
        ),
        "muted": ParagraphStyle(
            "MulexMuted",
            parent=base["Normal"],
            fontName=FONT_REG,
            fontSize=8,
            leading=11,
            textColor=TEXT_MUTED,
        ),
        "disclaimer": ParagraphStyle(
            "MulexDisclaimer",
            parent=base["Normal"],
            fontName=FONT_ITALIC,
            fontSize=7.8,
            leading=10.5,
            textColor=TEXT_MUTED,
            alignment=TA_LEFT,
        ),
    }


class _SectionHeader(Flowable):
    """Circular vector icon badge paired with a bold section title (matches clean form theme)."""

    def __init__(self, icon_kind, title, badge_color=NAVY_INST):
        super().__init__()
        self.icon_kind = icon_kind
        self.title = title
        self.badge_color = badge_color
        self.width = CONTENT_W
        self.height = 10.5 * mm
        self.keepWithNext = True

    def draw(self):
        c = self.canv
        c.saveState()
        r = 4.2 * mm
        cx = r
        cy = self.height / 2.0

        # Circular icon badge
        c.setFillColor(self.badge_color)
        c.circle(cx, cy, r, stroke=0, fill=1)

        # Clean white vector glyph inside the circle
        c.setStrokeColor(colors.white)
        c.setFillColor(colors.white)
        c.setLineWidth(0.9)
        c.setLineCap(1)
        c.setLineJoin(1)

        if self.icon_kind == "doc":
            c.rect(cx - 2.0 * mm, cy - 2.3 * mm, 4.0 * mm, 4.6 * mm, stroke=1, fill=0)
            c.line(cx - 1.2 * mm, cy + 0.9 * mm, cx + 1.2 * mm, cy + 0.9 * mm)
            c.line(cx - 1.2 * mm, cy, cx + 1.2 * mm, cy)
            c.line(cx - 1.2 * mm, cy - 0.9 * mm, cx + 0.5 * mm, cy - 0.9 * mm)
        elif self.icon_kind == "case":
            c.rect(cx - 2.1 * mm, cy - 2.1 * mm, 4.2 * mm, 4.2 * mm, stroke=1, fill=0)
            c.line(cx - 0.9 * mm, cy - 0.8 * mm, cx + 1.5 * mm, cy + 1.6 * mm)
        elif self.icon_kind == "check":
            for dy in (1.3 * mm, 0, -1.3 * mm):
                c.line(cx - 2.0 * mm, cy + dy, cx - 1.3 * mm, cy + dy)
                c.line(cx - 0.6 * mm, cy + dy, cx + 2.0 * mm, cy + dy)
        elif self.icon_kind == "network":
            c.circle(cx - 1.5 * mm, cy + 1.2 * mm, 0.7 * mm, stroke=0, fill=1)
            c.circle(cx + 1.6 * mm, cy + 1.0 * mm, 0.7 * mm, stroke=0, fill=1)
            c.circle(cx, cy - 1.6 * mm, 0.7 * mm, stroke=0, fill=1)
            c.line(cx - 1.5 * mm, cy + 1.2 * mm, cx + 1.6 * mm, cy + 1.0 * mm)
            c.line(cx - 1.5 * mm, cy + 1.2 * mm, cx, cy - 1.6 * mm)
            c.line(cx + 1.6 * mm, cy + 1.0 * mm, cx, cy - 1.6 * mm)
        elif self.icon_kind == "trail":
            c.line(cx - 2.2 * mm, cy + 0.9 * mm, cx + 2.0 * mm, cy + 0.9 * mm)
            c.line(cx + 1.1 * mm, cy + 1.7 * mm, cx + 2.0 * mm, cy + 0.9 * mm)
            c.line(cx + 2.0 * mm, cy - 0.9 * mm, cx - 2.2 * mm, cy - 0.9 * mm)
            c.line(cx - 1.1 * mm, cy - 1.7 * mm, cx - 2.2 * mm, cy - 0.9 * mm)
        else:  # shield / vcip
            p = c.beginPath()
            p.moveTo(cx, cy + 2.5 * mm)
            p.lineTo(cx + 2.1 * mm, cy + 1.6 * mm)
            p.lineTo(cx + 2.1 * mm, cy - 0.2 * mm)
            p.curveTo(cx + 2.1 * mm, cy - 1.7 * mm, cx, cy - 2.5 * mm, cx, cy - 2.5 * mm)
            p.curveTo(cx, cy - 2.5 * mm, cx - 2.1 * mm, cy - 1.7 * mm, cx - 2.1 * mm, cy - 0.2 * mm)
            p.lineTo(cx - 2.1 * mm, cy + 1.6 * mm)
            p.close()
            c.drawPath(p, stroke=1, fill=0)

        # Bold dark heading
        c.setFillColor(NAVY_DEEP)
        c.setFont(FONT_BOLD, 10.8)
        c.drawString(2 * r + 3.5 * mm, cy - 1.3 * mm, self.title)
        c.restoreState()


class _CheckMarkCell(Flowable):
    """Small crisp circle/checked dot indicator for checklist tables."""

    def __init__(self, checked=False, accent=TEAL):
        super().__init__()
        self.checked = checked
        self.accent = accent
        self.width = 4.5 * mm
        self.height = 4.5 * mm

    def draw(self):
        c = self.canv
        c.saveState()
        cx, cy = self.width / 2, self.height / 2
        if self.checked:
            c.setFillColor(self.accent)
            c.circle(cx, cy, 1.8 * mm, stroke=0, fill=1)
        else:
            c.setStrokeColor(GRID_STRONG)
            c.setLineWidth(0.8)
            c.circle(cx, cy, 1.8 * mm, stroke=1, fill=0)
        c.restoreState()


class _NumberedCanvas(canvas.Canvas):
    """Canvas drawing the solid banner header on page 1 and crisp footer on every page."""

    def __init__(self, *args, meta=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []
        self._meta = meta or {}

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_chrome(total)
            super().showPage()
        super().save()

    def _draw_chrome(self, total_pages):
        page_num = self._pageNumber
        m = self._meta
        self.saveState()

        if page_num == 1:
            banner_h = 22 * mm
            y0 = PAGE_H - banner_h
            self.setFillColor(NAVY_INST)
            self.rect(0, y0, PAGE_W, banner_h, fill=1, stroke=0)
            self.setFillColor(TEAL)
            self.rect(0, y0, PAGE_W, 1.0 * mm, fill=1, stroke=0)

            # Large crisp report title on left
            self.setFillColor(colors.white)
            self.setFont(FONT_BOLD, 16.5)
            self.drawString(MARGIN_X, PAGE_H - 13.2 * mm, m.get("heading", "Investigation Report"))

            # MULEX brand logo + identity on right
            rx = PAGE_W - MARGIN_X
            if LOGO_PATH.exists():
                self.drawImage(
                    str(LOGO_PATH),
                    rx - 52 * mm,
                    PAGE_H - 18.0 * mm,
                    width=17.5 * mm,
                    height=13.5 * mm,
                    mask="auto",
                    preserveAspectRatio=True,
                    anchor="c",
                )
            else:
                self.setStrokeColor(colors.HexColor("#7890A8"))
                self.setLineWidth(0.9)
                self.roundRect(rx - 44 * mm, PAGE_H - 15.8 * mm, 9 * mm, 9.5 * mm, 1.8, stroke=1, fill=0)
                self.setFillColor(TEAL)
                self.circle(rx - 39.5 * mm, PAGE_H - 11.0 * mm, 1.8 * mm, stroke=0, fill=1)

            self.setFillColor(colors.white)
            self.setFont(FONT_BOLD, 9.8)
            self.drawString(rx - 32.5 * mm, PAGE_H - 10.2 * mm, "MULEX")
            self.setFillColor(colors.HexColor("#B7C8D8"))
            self.setFont(FONT_REG, 7.8)
            self.drawString(rx - 32.5 * mm, PAGE_H - 14.4 * mm, "Financial Forensics")
        else:
            bar_h = 10.5 * mm
            y0 = PAGE_H - bar_h
            self.setFillColor(NAVY_INST)
            self.rect(0, y0, PAGE_W, bar_h, fill=1, stroke=0)
            self.setFillColor(TEAL)
            self.rect(0, y0, PAGE_W, 0.7 * mm, fill=1, stroke=0)
            self.setFillColor(colors.white)
            self.setFont(FONT_BOLD, 8.5)
            self.drawString(MARGIN_X, PAGE_H - 6.8 * mm, f"MULEX  |  {m.get('heading', '')} ({m.get('entity', '')})")
            self.setFillColor(colors.HexColor("#B7C8D8"))
            self.setFont(FONT_REG, 7.8)
            self.drawRightString(PAGE_W - MARGIN_X, PAGE_H - 6.8 * mm, "Financial Forensics")

        # Clean centered/balanced footer like reference template
        foot_y = 10.5 * mm
        self.setStrokeColor(GRID_BORDER)
        self.setLineWidth(0.5)
        self.line(MARGIN_X, foot_y + 4.2 * mm, PAGE_W - MARGIN_X, foot_y + 4.2 * mm)

        self.setFillColor(TEXT_MUTED)
        self.setFont(FONT_REG, 7.5)
        sha = m.get("sha256", "")
        gen = m.get("generated_at", "")
        self.drawString(MARGIN_X, foot_y, f"MULEX Workbench  |  SHA-256: {sha[:24]}...  |  UTC: {gen[:19]}")
        self.setFont(FONT_BOLD, 7.8)
        self.setFillColor(NAVY_INST)
        self.drawRightString(PAGE_W - MARGIN_X, foot_y, f"Page {page_num} of {total_pages}")

        self.restoreState()


def _grid_table(headers, rows, col_widths, st, right_cols=()):
    """Clean light form table matching the reference template."""
    data = []
    if headers:
        data.append(
            [
                Paragraph(_clean_text(h), st["th_num"] if i in right_cols else st["th"])
                for i, h in enumerate(headers)
            ]
        )
    data.extend(rows)
    tbl = Table(data, colWidths=col_widths, repeatRows=1 if headers else 0)
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.75, GRID_STRONG),
        ("INNERGRID", (0, 0), (-1, -1), 0.55, GRID_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if headers:
        style.append(("BACKGROUND", (0, 0), (-1, 0), ROW_HEADER_BG))
        style.append(("LINEBELOW", (0, 0), (-1, 0), 0.9, GRID_STRONG))
    tbl.setStyle(TableStyle(style))
    return tbl


def _kv_grid(pairs, st, col_widths=None):
    """2-column or 4-column structured form grid (label | value | label | value)."""
    tbl = Table(pairs, colWidths=col_widths)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.75, GRID_STRONG),
                ("INNERGRID", (0, 0), (-1, -1), 0.55, GRID_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return tbl


def _build_pdf(title, meta, story):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title=title,
    )
    story_with_lead = [Spacer(1, 10.5 * mm)] + story
    doc.build(
        story_with_lead,
        canvasmaker=lambda *a, **kw: _NumberedCanvas(*a, meta=meta, **kw),
    )
    return buffer.getvalue()


def _signoff_block(reviewer_label, reviewer_value, st):
    sign_rows = [
        [
            Paragraph(f"<b>{_clean_text(reviewer_label)}</b>", st["td_bold"]),
            Paragraph(_clean_text(reviewer_value), st["td"]),
            Paragraph("<b>Digital Digest:</b> Verified on export", st["muted"]),
        ]
    ]
    tbl = Table(sign_rows, colWidths=[CONTENT_W * 0.34, CONTENT_W * 0.38, CONTENT_W * 0.28])
    tbl.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.6, GRID_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return tbl


def _onboarding_block(story, session, st):
    story.append(Spacer(1, 3))
    story.append(_SectionHeader("shield", "Onboarding (V-CIP) Record", TEAL))
    if not session:
        story.append(
            _kv_grid(
                [
                    [
                        Paragraph("<b>Onboarding Status</b>", st["td_bold"]),
                        Paragraph(
                            "No V-CIP onboarding session is linked to this account in this workspace.",
                            st["td"],
                        ),
                    ]
                ],
                st,
                col_widths=[CONTENT_W * 0.28, CONTENT_W * 0.72],
            )
        )
        return
    decision = session.get("decision") or {}
    trust_val = decision.get("trust")
    trust_str = f"{trust_val:.1f} / 100" if isinstance(trust_val, (int, float)) else "Blocked"
    officer = session.get("officer")
    rows = [
        [
            Paragraph("<b>Session ID</b>", st["td_bold"]),
            Paragraph(_clean_text(session["id"]), st["td"]),
            Paragraph("<b>Applicant</b>", st["td_bold"]),
            Paragraph(_clean_text(session["applicant"]), st["td"]),
        ],
        [
            Paragraph("<b>Document</b>", st["td_bold"]),
            Paragraph(_clean_text(f"{session['doc_type']} {session.get('id_masked') or ''}"), st["td"]),
            Paragraph("<b>Trust Verdict</b>", st["td_bold"]),
            Paragraph(_clean_text(f"{trust_str} ({decision.get('decision') or session['stage']})"), st["td_bold"]),
        ],
        [
            Paragraph("<b>Reasons</b>", st["td_bold"]),
            Paragraph(_clean_text("; ".join(decision.get("reasons", [])) or "None"), st["td"]),
            Paragraph("<b>Officer Action</b>", st["td_bold"]),
            Paragraph(
                _clean_text(
                    f"{officer['officer']}: {officer['action']} ({officer['reason_code']}) - {officer['note']}"
                    if officer
                    else "Pending officer decision"
                ),
                st["td"],
            ),
        ],
    ]
    story.append(
        _kv_grid(
            rows,
            st,
            col_widths=[CONTENT_W * 0.16, CONTENT_W * 0.34, CONTENT_W * 0.17, CONTENT_W * 0.33],
        )
    )


def report(snapshot):
    st = _styles()
    workspace = snapshot["dataset"]
    detail = snapshot["account"]
    case_status = snapshot["case_history"][0]["status"] if snapshot["case_history"] else "New"
    sev_color = SEV_COLORS.get(detail["level"].lower(), NAVY_DEEP)

    meta = {
        "heading": "Account Investigation Report",
        "dataset": workspace,
        "entity": detail["id"],
        "generated_at": snapshot["generated_at"],
        "sha256": snapshot["ledger_sha256"],
    }

    story = []

    # Top 4-column Metadata Form Grid (matches reference top grid)
    sev_style = ParagraphStyle("SevVal", parent=st["td_bold"], textColor=sev_color)
    top_grid = [
        [
            Paragraph("<b>Account ID</b>", st["td_bold"]),
            Paragraph(_clean_text(detail["id"]), st["td"]),
            Paragraph("<b>Risk Assessment</b>", st["td_bold"]),
            Paragraph(_clean_text(f"{detail['score']} / 100 ({detail['level']})"), sev_style),
        ],
        [
            Paragraph("<b>Subject Name</b>", st["td_bold"]),
            Paragraph(_clean_text(detail.get("name") or detail["id"]), st["td"]),
            Paragraph("<b>Assessed Role</b>", st["td_bold"]),
            Paragraph(_clean_text(detail.get("role") or "No mule role inferred"), st["td"]),
        ],
        [
            Paragraph("<b>Funds Flow</b>", st["td_bold"]),
            Paragraph(
                _clean_text(
                    f"Received {_money(detail['incoming'])}  |  Sent {_money(detail['outgoing'])}  |  <15m {_money(detail['rapid'])}"
                ),
                st["td"],
            ),
            Paragraph("<b>Case Status</b>", st["td_bold"]),
            Paragraph(_clean_text(case_status), st["td_bold"]),
        ],
        [
            Paragraph("<b>Linked Complaints</b>", st["td_bold"]),
            Paragraph(_clean_text(str(len(detail["filings"]))), st["td"]),
            Paragraph("<b>Generated (UTC)</b>", st["td_bold"]),
            Paragraph(_clean_text(snapshot["generated_at"][:19] + "Z"), st["td"]),
        ],
    ]
    story.append(
        _kv_grid(
            top_grid,
            st,
            col_widths=[CONTENT_W * 0.19, CONTENT_W * 0.34, CONTENT_W * 0.18, CONTENT_W * 0.29],
        )
    )

    # Section 1: Evidence Indicators
    story.append(Spacer(1, 3))
    story.append(_SectionHeader("doc", "Evidence Indicators & Scoring", NAVY_INST))
    if detail["reasons"]:
        rows = [
            [
                Paragraph(_clean_text(r["indicator"]), st["td_bold"]),
                Paragraph(_clean_text(f"+{r['points']}"), st["td_num"]),
                Paragraph(_clean_text(f"{r['maximum']}"), st["td_num"]),
                Paragraph(_clean_text(r["evidence"]), st["td"]),
            ]
            for r in detail["reasons"]
        ]
        story.append(
            _grid_table(
                ["Indicator", "Points", "Max", "Ledger Evidence"],
                rows,
                [CONTENT_W * 0.27, CONTENT_W * 0.10, CONTENT_W * 0.08, CONTENT_W * 0.55],
                st,
                right_cols=(1, 2),
            )
        )
    else:
        story.append(
            _kv_grid(
                [[Paragraph("<b>Indicators</b>", st["td_bold"]), Paragraph("No positive risk indicators triggered.", st["td"])]],
                st,
                col_widths=[CONTENT_W * 0.25, CONTENT_W * 0.75],
            )
        )

    # Section 2: Investigation & Case Description (Key-Value form grid like reference)
    story.append(Spacer(1, 3))
    story.append(_SectionHeader("case", "Investigation Summary & Case History", NAVY_INST))
    incident = detail.get("incident")
    inc_str = (
        f"{incident['id']} ({len(incident['complaints'])} complaints, {_money(incident['reported_value'])}) across {', '.join(incident['accounts'])}"
        if incident
        else "None"
    )
    cyc_str = (
        "; ".join(
            " -> ".join(r["members"] + r["members"][:1])
            + f" ({r['hours']} h via {', '.join(r['transfers'])})"
            for r in detail.get("cycles", [])
        )
        or "None detected"
    )
    desc_rows = [
        [Paragraph("<b>Correlated Incident</b>", st["td_bold"]), Paragraph(_clean_text(inc_str), st["td"])],
        [
            Paragraph("<b>Shared Device Peers</b>", st["td_bold"]),
            Paragraph(_clean_text(", ".join(detail["device_peers"]) or "None"), st["td"]),
        ],
        [
            Paragraph("<b>Review Cluster</b>", st["td_bold"]),
            Paragraph(_clean_text(", ".join(detail["cluster"]) or "None"), st["td"]),
        ],
        [Paragraph("<b>Circular Fund Flows</b>", st["td_bold"]), Paragraph(_clean_text(cyc_str), st["td"])],
    ]
    if snapshot["case_history"]:
        for ev in snapshot["case_history"]:
            desc_rows.append(
                [
                    Paragraph(_clean_text(f"Case Rev #{ev['revision']} ({ev['status']})"), st["td_bold"]),
                    Paragraph(_clean_text(f"{ev['analyst']} at {ev['created']} - {ev['note']}"), st["td"]),
                ]
            )
    else:
        desc_rows.append(
            [
                Paragraph("<b>Case Notes</b>", st["td_bold"]),
                Paragraph("New lead. No analyst entries recorded yet.", st["td"]),
            ]
        )
    story.append(_kv_grid(desc_rows, st, col_widths=[CONTENT_W * 0.27, CONTENT_W * 0.73]))

    # Section 3: Triggered Signal Checklist (2-column radio/dot grid matching reference)
    story.append(Spacer(1, 3))
    story.append(_SectionHeader("check", "Risk Indicators Checklist", TEAL))
    all_indicators = [
        "Rapid onward transfers",
        "Linked complaints",
        "Multiple funding sources",
        "Shared device",
        "Layering chain",
        "Complaint-linked counterparties",
        "Peer anomaly",
        "Distribution to multiple recipients",
        "Circular fund flow",
        "Departure from account history",
    ]
    active_map = {r["indicator"]: r["points"] for r in detail["reasons"]}
    half = (len(all_indicators) + 1) // 2
    chk_rows = []
    for i in range(half):
        left_name = all_indicators[i]
        left_on = left_name in active_map
        left_lbl = f"{left_name} (+{active_map[left_name]})" if left_on else left_name

        right_idx = i + half
        if right_idx < len(all_indicators):
            right_name = all_indicators[right_idx]
            right_on = right_name in active_map
            right_lbl = f"{right_name} (+{active_map[right_name]})" if right_on else right_name
            r_dot = _CheckMarkCell(right_on)
            r_txt = Paragraph(_clean_text(right_lbl), st["td_bold"] if right_on else st["muted"])
        else:
            r_dot = ""
            r_txt = ""

        chk_rows.append(
            [
                _CheckMarkCell(left_on),
                Paragraph(_clean_text(left_lbl), st["td_bold"] if left_on else st["muted"]),
                r_dot,
                r_txt,
            ]
        )
    story.append(
        _kv_grid(
            chk_rows,
            st,
            col_widths=[CONTENT_W * 0.06, CONTENT_W * 0.44, CONTENT_W * 0.06, CONTENT_W * 0.44],
        )
    )

    # Section 4: Complaint References
    if detail["filings"]:
        story.append(Spacer(1, 3))
        story.append(_SectionHeader("doc", "Complaint References", NAVY_INST))
        comp_rows = [
            [
                Paragraph(_clean_text(c["id"]), st["td_bold"]),
                Paragraph(_clean_text(c["timestamp"]), st["td"]),
                Paragraph(_clean_text(c["category"]), st["td"]),
                Paragraph(_clean_text(c["transfer"] or "Account-level"), st["td"]),
            ]
            for c in detail["filings"]
        ]
        story.append(
            _grid_table(
                ["Complaint ID", "Filed (UTC)", "Category", "Linked Transfer"],
                comp_rows,
                [CONTENT_W * 0.18, CONTENT_W * 0.25, CONTENT_W * 0.33, CONTENT_W * 0.24],
                st,
            )
        )

    # Section 5: Time-Ordered Onward Candidate Transfers
    story.append(Spacer(1, 3))
    story.append(_SectionHeader("trail", "Time-Ordered Onward Transfers", TEAL))
    if detail["trail"]:
        rows = [
            [
                Paragraph(_clean_text(str(t["hop"])), st["td_num"]),
                Paragraph(_clean_text(t["id"]), st["td_bold"]),
                Paragraph(_clean_text(f"{t['sender']} -> {t['receiver']}"), st["td"]),
                Paragraph(_clean_text(_money(t["amount_paise"])), st["td_num"]),
                Paragraph(_clean_text(t["timestamp"]), st["td"]),
            ]
            for t in detail["trail"]
        ]
        story.append(
            _grid_table(
                ["Hop", "Transfer ID", "From -> To", "Amount", "Timestamp (UTC)"],
                rows,
                [CONTENT_W * 0.07, CONTENT_W * 0.22, CONTENT_W * 0.28, CONTENT_W * 0.19, CONTENT_W * 0.24],
                st,
                right_cols=(0, 3),
            )
        )
        if detail.get("trail_truncated"):
            story.append(Spacer(1, 3))
            story.append(Paragraph("Display limit of 140 onward transfers reached.", st["muted"]))
    else:
        story.append(
            _kv_grid(
                [[Paragraph("<b>Onward Trail</b>", st["td_bold"]), Paragraph("No onward transfers recorded.", st["td"])]],
                st,
                col_widths=[CONTENT_W * 0.25, CONTENT_W * 0.75],
            )
        )

    # Section 6: Source of Funds
    story.append(Spacer(1, 3))
    story.append(_SectionHeader("trail", "Source of Funds (Earlier Inbound Transfers)", NAVY_INST))
    if detail.get("source_trail"):
        rows = [
            [
                Paragraph(_clean_text(str(t["hop"])), st["td_num"]),
                Paragraph(_clean_text(t["id"]), st["td_bold"]),
                Paragraph(_clean_text(f"{t['sender']} -> {t['receiver']}"), st["td"]),
                Paragraph(_clean_text(_money(t["amount_paise"])), st["td_num"]),
                Paragraph(_clean_text(t["timestamp"]), st["td"]),
            ]
            for t in detail["source_trail"]
        ]
        story.append(
            _grid_table(
                ["Hop", "Transfer ID", "From -> To", "Amount", "Timestamp (UTC)"],
                rows,
                [CONTENT_W * 0.07, CONTENT_W * 0.22, CONTENT_W * 0.28, CONTENT_W * 0.19, CONTENT_W * 0.24],
                st,
                right_cols=(0, 3),
            )
        )
    else:
        story.append(
            _kv_grid(
                [[Paragraph("<b>Source Trail</b>", st["td_bold"]), Paragraph("No earlier inbound transfers.", st["td"])]],
                st,
                col_widths=[CONTENT_W * 0.25, CONTENT_W * 0.75],
            )
        )

    # Section 7: Onboarding Record
    _onboarding_block(story, snapshot.get("onboarding"), st)

    story.append(Spacer(1, 8))
    latest_analyst = (
        f"{snapshot['case_history'][0]['analyst']} ({snapshot['case_history'][0]['created'][:10]})"
        if snapshot["case_history"]
        else "Unassigned / Automated Workbench Output"
    )
    story.append(_signoff_block("Investigating Officer / Reviewer:", latest_analyst, st))
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "Scores prioritise investigative review and are not findings of wrongdoing. Linked transfers represent candidate routes preserving exact ledger references.",
            st["disclaimer"],
        )
    )

    return _build_pdf(f"MULEX investigation {detail['id']}", meta, story)


def incident_report(snapshot):
    st = _styles()
    incident = snapshot["incident"]
    meta = {
        "heading": "Incident Referral Report",
        "dataset": snapshot["dataset"],
        "entity": incident["id"],
        "generated_at": snapshot["generated_at"],
        "sha256": snapshot["ledger_sha256"],
    }

    story = []

    ben = "; ".join(
        f"{b['account']} (paid by {', '.join(b['payers'])})"
        for b in incident.get("common_beneficiaries", [])
    )
    top_grid = [
        [
            Paragraph("<b>Incident ID</b>", st["td_bold"]),
            Paragraph(_clean_text(incident["id"]), st["td_bold"]),
            Paragraph("<b>Reported Value</b>", st["td_bold"]),
            Paragraph(_clean_text(_money(incident["reported_value"])), ParagraphStyle("ValRed", parent=st["td_bold"], textColor=SEV_COLORS["critical"])),
        ],
        [
            Paragraph("<b>Reported Accounts</b>", st["td_bold"]),
            Paragraph(_clean_text(", ".join(incident["reported_accounts"])), st["td"]),
            Paragraph("<b>Complaints</b>", st["td_bold"]),
            Paragraph(_clean_text(str(len(incident["complaints"]))), st["td_bold"]),
        ],
        [
            Paragraph("<b>Categories</b>", st["td_bold"]),
            Paragraph(_clean_text(", ".join(incident["categories"])), st["td"]),
            Paragraph("<b>Filing Window</b>", st["td_bold"]),
            Paragraph(_clean_text(f"{incident['first'][:19]} to {incident['last'][:19]}"), st["td"]),
        ],
        [
            Paragraph("<b>Network Accounts</b>", st["td_bold"]),
            Paragraph(_clean_text(str(len(snapshot["accounts"]))), st["td"]),
            Paragraph("<b>Common Beneficiary</b>", st["td_bold"]),
            Paragraph(_clean_text(ben or "None"), st["td"]),
        ],
    ]
    story.append(
        _kv_grid(
            top_grid,
            st,
            col_widths=[CONTENT_W * 0.19, CONTENT_W * 0.31, CONTENT_W * 0.19, CONTENT_W * 0.31],
        )
    )

    # Section 1: Accounts in the Network
    story.append(Spacer(1, 3))
    story.append(_SectionHeader("network", "Accounts in the Correlated Network", NAVY_INST))
    acc_rows = []
    for acc in snapshot["accounts"]:
        top_reasons = "; ".join(
            f"+{r['points']} {r['indicator']}" for r in acc["reasons"][:3]
        ) or "None"
        acc_rows.append(
            [
                Paragraph(_clean_text(f"{acc['id']} ({acc['name']})"), st["td_bold"]),
                Paragraph(_clean_text(acc.get("role") or "None"), st["td"]),
                Paragraph(_clean_text(f"{acc['score']} ({acc['level']})"), st["td_bold"]),
                Paragraph(_clean_text(acc["case_status"]), st["td"]),
                Paragraph(_clean_text(top_reasons), st["td"]),
            ]
        )
    story.append(
        _grid_table(
            ["Account", "Assessed Role", "Risk", "Case", "Top Indicators"],
            acc_rows,
            [CONTENT_W * 0.25, CONTENT_W * 0.17, CONTENT_W * 0.14, CONTENT_W * 0.11, CONTENT_W * 0.33],
            st,
        )
    )

    # Section 2: Complaints
    story.append(Spacer(1, 3))
    story.append(_SectionHeader("doc", "Correlated Complaints", NAVY_INST))
    comp_rows = [
        [
            Paragraph(_clean_text(c["id"]), st["td_bold"]),
            Paragraph(_clean_text(c["timestamp"]), st["td"]),
            Paragraph(_clean_text(c["account"]), st["td"]),
            Paragraph(_clean_text(c["category"]), st["td"]),
            Paragraph(_clean_text(c["transfer"] or "Account-level"), st["td"]),
        ]
        for c in snapshot["complaints"]
    ]
    story.append(
        _grid_table(
            ["Complaint ID", "Timestamp (UTC)", "Account", "Category", "Transfer"],
            comp_rows,
            [CONTENT_W * 0.16, CONTENT_W * 0.24, CONTENT_W * 0.18, CONTENT_W * 0.24, CONTENT_W * 0.18],
            st,
        )
    )

    # Section 3: Circular Flows
    story.append(Spacer(1, 3))
    story.append(_SectionHeader("check", "Circular Fund Flows", TEAL))
    if snapshot["cycles"]:
        cyc_rows = [
            [
                Paragraph(_clean_text(" -> ".join(ring["members"] + ring["members"][:1])), st["td"]),
                Paragraph(_clean_text(f"{ring['hours']} h"), st["td_num"]),
                Paragraph(_clean_text(", ".join(ring["transfers"])), st["td"]),
            ]
            for ring in snapshot["cycles"]
        ]
        story.append(
            _grid_table(
                ["Ring Path", "Duration", "Transfer IDs"],
                cyc_rows,
                [CONTENT_W * 0.42, CONTENT_W * 0.14, CONTENT_W * 0.44],
                st,
                right_cols=(1,),
            )
        )
    else:
        story.append(
            _kv_grid(
                [[Paragraph("<b>Circular Flows</b>", st["td_bold"]), Paragraph("No time-ordered circular flows detected within 72 hours.", st["td"])]],
                st,
                col_widths=[CONTENT_W * 0.25, CONTENT_W * 0.75],
            )
        )

    # Section 4: Transfers Touching the Network
    story.append(Spacer(1, 3))
    story.append(_SectionHeader("trail", f"Transfers Touching the Network ({len(snapshot['transfers'])})", TEAL))
    tx_rows = [
        [
            Paragraph(_clean_text(t["timestamp"]), st["td"]),
            Paragraph(_clean_text(t["id"]), st["td_bold"]),
            Paragraph(_clean_text(f"{t['sender']} -> {t['receiver']}"), st["td"]),
            Paragraph(_clean_text(_money(t["amount_paise"])), st["td_num"]),
        ]
        for t in snapshot["transfers"][:200]
    ]
    if tx_rows:
        story.append(
            _grid_table(
                ["Timestamp (UTC)", "Transfer ID", "From -> To", "Amount"],
                tx_rows,
                [CONTENT_W * 0.24, CONTENT_W * 0.26, CONTENT_W * 0.31, CONTENT_W * 0.19],
                st,
                right_cols=(3,),
            )
        )
    if len(snapshot["transfers"]) > 200:
        story.append(Spacer(1, 3))
        story.append(
            Paragraph(
                "First 200 transfers shown; export the Referral JSON for the complete transfer log.",
                st["muted"],
            )
        )

    story.append(Spacer(1, 8))
    story.append(_signoff_block("Referral Prepared By:", "MULEX Investigation Workbench", st))
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "Scores prioritise investigation and are not findings of guilt. Linked transfers are candidate routes.",
            st["disclaimer"],
        )
    )

    return _build_pdf(f"MULEX incident {incident['id']}", meta, story)
