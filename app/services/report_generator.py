"""
Report generator — fpdf2 (pure Python, no system deps)
Professional Site Visit Report matching construction industry format.
Structure: Cover → Document Control → Snag Items (with photos) → Closing
"""
import io
import os
import tempfile
from datetime import datetime
from typing import List, Dict, Any, Optional
from fpdf import FPDF

# ─── Unicode font detection ──────────────────────────────────────
# Try to find DejaVu Sans for full Unicode support (Polish, Czech, etc.)
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",           # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",      # Linux bold
    "C:/Windows/Fonts/DejaVuSans.ttf",                            # Windows
    "/System/Library/Fonts/Supplemental/DejaVuSans.ttf",          # macOS
]

def _find_font(bold=False):
    """Find DejaVu Sans font file on the system."""
    suffix = "-Bold" if bold else ""
    for p in _FONT_PATHS:
        if suffix and suffix not in p:
            continue
        if not suffix and "-Bold" in p:
            continue
        if os.path.exists(p):
            return p
    return None

DEJAVU_REGULAR = _find_font(bold=False)
DEJAVU_BOLD = _find_font(bold=True)
HAS_UNICODE_FONT = bool(DEJAVU_REGULAR)

# ─── Brand colours (no green — VoxSite orange + neutral dark) ──────
ORANGE = (255, 107, 53)       # #FF6B35 — primary brand
DARK = (26, 38, 56)           # #1A2638 — headings, sidebar
MID_GREY = (136, 136, 136)    # #888888
LIGHT_GREY = (245, 244, 242)  # #F5F4F2
HEADER_GREY = (208, 208, 210) # #D0D0D2 — table headers
BORDER = (187, 187, 187)      # #BBBBBB
WHITE = (255, 255, 255)
BLACK = (17, 17, 17)          # #111111
RED = (239, 68, 68)
GREEN = (34, 197, 94)
AMBER = (245, 158, 11)

# Page dimensions (A4)
PAGE_W = 210
PAGE_H = 297
MARGIN = 15
USABLE_W = PAGE_W - 2 * MARGIN


def _hex_to_rgb(hex_str: str, fallback=ORANGE) -> tuple:
    """
    Parse '#RRGGBB' (case-insensitive) to an (r, g, b) tuple.
    Returns `fallback` on anything malformed — the PDF must render
    even if a company stored garbage in their settings.
    """
    if not hex_str or not isinstance(hex_str, str):
        return fallback
    s = hex_str.strip().lstrip("#")
    if len(s) != 6:
        return fallback
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return fallback


def _safe(text: str) -> str:
    """Sanitise text for Helvetica (latin-1 only). Replace common Unicode chars."""
    if not text:
        return ""
    replacements = {
        "\u2013": "-",   # en dash
        "\u2014": "-",   # em dash
        "\u2018": "'",   # left single quote
        "\u2019": "'",   # right single quote
        "\u201c": '"',   # left double quote
        "\u201d": '"',   # right double quote
        "\u2026": "...", # ellipsis
        "\u00b0": "°",   # degree (already latin-1, but just in case)
        "\u2022": "-",   # bullet
        "\u00a0": " ",   # non-breaking space
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    # Final fallback: strip anything outside latin-1
    return text.encode("latin-1", errors="replace").decode("latin-1")


class SiteVisitReport(FPDF):
    """
    Multi-page site visit / snagging report.

    Accepts:
      - project: dict with name, client, address, id, created_at
      - snags: list of snag dicts (note, location, priority, status, photo_url, created_at)
      - inspector: str (email or name)
      - logo_bytes: optional PNG/JPG bytes for company logo
      - visit_no: optional visit number string
      - weather: optional weather string
      - attendees: optional attendees string
      - access_notes: optional access/site notes
    """

    def __init__(
        self,
        project: Dict[str, Any],
        snags: List[Dict[str, Any]],
        inspector: str = "",
        logo_bytes: Optional[bytes] = None,
        visit_no: str = "",
        visit_display: str = "",  # optional override for human-facing display
        weather: str = "",
        attendees: str = "",
        access_notes: str = "",
        company_name: str = "",
        checker: str = "",
        reviewer: str = "",
        approver: str = "",
        closing_notes: str = "",
        show_watermark: bool = False,
        show_logo: bool = True,
        # ── Phase 1: per-company report settings ─────────────────
        brand_colour: str = "#F97316",
        footer_text: Optional[str] = None,
        include_rectification: bool = False,
    ):
        super().__init__()
        self.project = project
        self.snags = snags
        self.inspector = inspector
        self.inspector_email = ""  # set separately
        self.visit_no = visit_no or "1"
        # visit_display is what the user sees on the page. Falls back to
        # the numeric visit_no when no ref was set. Never used for the
        # internal doc_ref (which stays based on the integer so filenames
        # and references remain stable even when the user changes their
        # display scheme mid-project).
        self.visit_display = (visit_display or "").strip() or self.visit_no
        self.weather = weather
        self.attendees = attendees
        self.access_notes = access_notes
        self.company_name = company_name
        self.checker = checker
        self.reviewer = reviewer
        self.approver = approver
        self.closing_notes = closing_notes or (
            "If requested, notice must be given to allow for a site visit "
            "to review prior to closing up or concealing the item of works.\n\n"
            "The contractor is to confirm that the above actions have been carried out "
            "and provide photographic record of the associated works. The contractor is "
            "to sign the items as closed and e-mail to originator."
        )
        self._logo_path: Optional[str] = None
        self._is_cover = False
        self._show_watermark = show_watermark
        self._show_logo = show_logo
        # Phase 1 settings
        self._brand_rgb = _hex_to_rgb(brand_colour)
        self._footer_text = (footer_text or "").strip()
        self._include_rectification = bool(include_rectification)
        # Document reference: custom or auto-generated
        p_name = project.get("name", "")[:3].upper()
        self.doc_ref = f"{p_name}-SV{self.visit_no.zfill(2)}"

        self.set_auto_page_break(auto=True, margin=25)

        # Register Unicode font if available
        self._use_unicode = False
        if HAS_UNICODE_FONT:
            try:
                self.add_font("DejaVu", "", DEJAVU_REGULAR, uni=True)
                if DEJAVU_BOLD:
                    self.add_font("DejaVu", "B", DEJAVU_BOLD, uni=True)
                else:
                    self.add_font("DejaVu", "B", DEJAVU_REGULAR, uni=True)
                self._use_unicode = True
            except Exception:
                pass  # fall back to Helvetica

        # Write logo bytes to a temp file so fpdf2 can load it
        # Only keep a logo path if the plan allows logo rendering.
        if logo_bytes and show_logo:
            try:
                self._logo_tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                self._logo_tmp.write(logo_bytes)
                self._logo_tmp.flush()
                self._logo_path = self._logo_tmp.name
            except Exception:
                self._logo_path = None

    # ─── Text sanitisation wrappers ─────────────────────────────
    def cell(self, w=0, h=0, txt="", **kwargs):
        t = str(txt) if txt else ""
        if not self._use_unicode:
            t = _safe(t)
        return super().cell(w=w, h=h, txt=t, **kwargs)

    def multi_cell(self, w, h=0, txt="", **kwargs):
        t = str(txt) if txt else ""
        if not self._use_unicode:
            t = _safe(t)
        return super().multi_cell(w=w, h=h, txt=t, **kwargs)

    # ─── Header (inner pages only — cover has its own) ──────────
    def header(self):
        # Watermark first, so content draws on top of it.
        # Runs on every page including the cover.
        if self._show_watermark:
            self._draw_watermark()

        if self._is_cover:
            return
        # Right-aligned logo or company name
        if self._logo_path:
            try:
                self.image(self._logo_path, x=PAGE_W - MARGIN - 45, y=8, h=12)
            except Exception:
                pass
        self.set_y(22)
        self.set_draw_color(*MID_GREY)
        self.set_line_width(0.3)
        self.line(MARGIN, self.get_y(), PAGE_W - MARGIN, self.get_y())
        self.ln(4)

    # ─── Watermark (Free plan only) ─────────────────────────────
    def _draw_watermark(self):
        """
        Draws a diagonal 'VOXSITE · FREE PLAN' watermark across the page.
        Called from header() so it's behind all other content.
        Uses a wide cell with align='C' inside a rotation context so centring
        doesn't depend on measuring string width (which was brittle at large
        font sizes).
        """
        # Save state we're about to change
        prev_font_family = self.font_family
        prev_font_style = self.font_style
        prev_font_size = self.font_size_pt
        prev_x, prev_y = self.get_x(), self.get_y()

        try:
            self.set_font(
                "DejaVu" if self._use_unicode else "Helvetica",
                "B",
                44,
            )
            # Very pale grey so it doesn't overpower content
            self.set_text_color(230, 230, 232)

            cx, cy = PAGE_W / 2, PAGE_H / 2
            text = "VOXSITE  ·  FREE PLAN"
            cell_w = 200  # wider than any plausible string at 44pt
            cell_h = 20

            # Rotate around the page centre and draw a centred cell at
            # (cx, cy). With align='C' the text is always visually centred
            # inside the cell regardless of its width.
            with self.rotation(angle=45, x=cx, y=cy):
                self.set_xy(cx - cell_w / 2, cy - cell_h / 2)
                self.cell(cell_w, cell_h, text, align="C")
        except Exception:
            # Watermark must never break the report
            pass
        finally:
            # Restore so subsequent drawing is unaffected
            try:
                self.set_font(
                    prev_font_family or ("DejaVu" if self._use_unicode else "Helvetica"),
                    prev_font_style or "",
                    prev_font_size or 10,
                )
            except Exception:
                pass
            self.set_text_color(*BLACK)
            self.set_xy(prev_x, prev_y)

    # ─── Footer (all pages) ─────────────────────────────────────
    def footer(self):
        self.set_y(-18)
        self.set_draw_color(*BORDER)
        self.set_line_width(0.2)
        self.line(MARGIN, self.get_y(), PAGE_W - MARGIN, self.get_y())
        self.set_y(-15)
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "", 7)
        self.set_text_color(*MID_GREY)
        p_code = self.project.get("name", "")
        left = f"{p_code}  |  Ref: {self.doc_ref}"
        self.cell(USABLE_W / 3, 5, left, align="L")
        self.cell(USABLE_W / 3, 5, f"Site visit No {self.visit_display}", align="C")
        self.cell(USABLE_W / 3, 5, f"{self.page_no()}", align="R")

    # ─── Helpers ────────────────────────────────────────────────
    def _set_brand(self, size=11, bold=True):
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B" if bold else "", size)
        self.set_text_color(*self._brand_rgb)

    def _set_body(self, size=9, bold=False):
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B" if bold else "", size)
        self.set_text_color(*BLACK)

    def _set_muted(self, size=8):
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "", size)
        self.set_text_color(*MID_GREY)

    def _table_header_cell(self, w, txt, align="L"):
        """Grey header cell for tables."""
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 8)
        self.set_fill_color(*HEADER_GREY)
        self.set_text_color(*DARK)
        self.set_draw_color(*BORDER)
        self.cell(w, 7, txt, border=1, fill=True, align=align)

    def _table_cell(self, w, txt, h=6, align="L", bold=False, fill=False):
        """Standard table body cell."""
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B" if bold else "", 8)
        self.set_text_color(*BLACK)
        self.set_draw_color(*BORDER)
        if fill:
            self.set_fill_color(*LIGHT_GREY)
        self.cell(w, h, txt, border=1, fill=fill, align=align)

    def _section_title(self, title):
        self.ln(3)
        self._set_brand(10, bold=True)
        self.cell(0, 7, title.upper(), ln=True)
        self.set_draw_color(*BORDER)
        self.set_line_width(0.2)
        self.line(MARGIN, self.get_y(), PAGE_W - MARGIN, self.get_y())
        self.ln(2)

    # ─── PAGE 1: Cover ──────────────────────────────────────────
    def _build_cover(self):
        self._is_cover = True
        self.add_page()

        # Logo
        y = 40
        if self._logo_path:
            try:
                self.image(self._logo_path, x=MARGIN, y=y, h=22)
                y += 30
            except Exception:
                y += 5

        # Push project info to mid-page
        self.set_y(PAGE_H * 0.35)

        # Company name
        if self.company_name:
            self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 14)
            self.set_text_color(*BLACK)
            self.cell(0, 8, self.company_name, ln=True)
            self.ln(4)

        # Project name
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "", 24)
        self.set_text_color(*DARK)
        self.multi_cell(USABLE_W, 12, self.project.get("name", "[Project Name]"))
        self.ln(4)

        # "SITE VISIT REPORT"
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 18)
        self.set_text_color(*DARK)
        self.cell(0, 10, "SITE VISIT REPORT", ln=True)
        self.ln(2)

        # Visit and issue number
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 14)
        self.set_text_color(*DARK)
        self.cell(0, 8, f"Site visit No. {self.visit_display}  |  Issue No. {self.visit_display}", ln=True)
        self.ln(2)

        # Document reference
        self._set_muted(11)
        self.cell(0, 6, f"Document Ref: {self.doc_ref}", ln=True)
        self.ln(4)

        # Client + date
        self._set_muted(10)
        client = self.project.get("client", "")
        if client:
            self.cell(0, 6, f"Client: {client}", ln=True)
        address = self.project.get("address", "")
        if address:
            self.cell(0, 6, f"Site: {address}", ln=True)
        self.cell(0, 6, f"Date: {datetime.now().strftime('%d %B %Y')}", ln=True)

        # Decorative accent bar at bottom (replaces HP dots + green sidebar)
        self.set_fill_color(*self._brand_rgb)
        self.rect(PAGE_W - 8, 0, 8, PAGE_H, "F")

        self._is_cover = False

    # ─── PAGE 2: Document Control ───────────────────────────────
    def _build_doc_control(self):
        self.add_page()

        # Title
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 13)
        self.set_text_color(*DARK)
        self.cell(0, 10, "DOCUMENT CONTROL SHEET", align="C", ln=True)
        self.ln(4)

        # ── Current Issue table ──
        self._table_header_cell(USABLE_W, "CURRENT ISSUE", align="C")
        self.ln()

        # Clean 4-column layout: label | value | label | value
        c1 = USABLE_W * 0.15  # labels
        c2 = USABLE_W * 0.35  # values
        self._table_cell(c1, "Issue No:", h=10, bold=True, fill=True)
        self._table_cell(c2, self.visit_display, h=10)
        self._table_cell(c1, "Date:", h=10, bold=True, fill=True)
        self._table_cell(c2, datetime.now().strftime("%d/%m/%Y"), h=10)
        self.ln()
        self._table_cell(c1, "Reason:", h=10, bold=True, fill=True)
        self._table_cell(c2, "Site Inspection", h=10)
        self._table_cell(c1, "Doc Ref:", h=10, bold=True, fill=True)
        self._table_cell(c2 - c1, self.doc_ref, h=10)
        self.ln()

        # ── Sign-off table ──
        self.ln(3)
        s0 = USABLE_W * 0.15       # label col
        sr = (USABLE_W - s0) / 4   # 4 equal role cols
        sign_cols = [s0, sr, sr, sr, sr]

        # Header row
        for w, txt in zip(sign_cols, ["", "Originator", "Checker", "Reviewer", "Approver"]):
            self._table_header_cell(w, txt, align="C")
        self.ln()

        # Print Name row
        self._table_cell(sign_cols[0], "Print Name", bold=True, fill=True)
        self._table_cell(sign_cols[1], self.inspector)
        self._table_cell(sign_cols[2], self.checker)
        self._table_cell(sign_cols[3], self.reviewer)
        self._table_cell(sign_cols[4], self.approver)
        self.ln()

        # Date row
        self._table_cell(sign_cols[0], "Date", bold=True, fill=True)
        self._table_cell(sign_cols[1], datetime.now().strftime("%d/%m/%Y"))
        for w in sign_cols[2:]:
            self._table_cell(w, "")
        self.ln()

        # ── Visit Information ──
        if self.weather or self.attendees or self.access_notes:
            self.ln(5)
            self._table_header_cell(USABLE_W, "VISIT INFORMATION", align="C")
            self.ln()
            label_w = USABLE_W * 0.25
            val_w = USABLE_W * 0.75
            if self.weather:
                self._table_cell(label_w, "Weather", bold=True, fill=True)
                self._table_cell(val_w, self.weather)
                self.ln()
            if self.attendees:
                self._table_cell(label_w, "Attendees", bold=True, fill=True)
                self._table_cell(val_w, self.attendees)
                self.ln()
            if self.access_notes:
                self._table_cell(label_w, "Access / Notes", bold=True, fill=True)
                self._table_cell(val_w, self.access_notes[:80])
                self.ln()

        # ── Disclaimer ──
        self.ln(6)
        self._set_muted(7)
        disclaimer = (
            "This report is confidential to the Client and we accept no responsibility "
            "to third parties to whom this report, or any part thereof, is made known. "
            "Any such party relies on the contents of the report at their own risk."
        )
        self.multi_cell(USABLE_W, 3.5, disclaimer)

    # ─── Summary Page ───────────────────────────────────────────
    def _build_summary(self):
        self.add_page()

        open_snags = [s for s in self.snags if s.get("status") == "open"]
        closed_snags = [s for s in self.snags if s.get("status") == "closed"]
        high_pri = [s for s in open_snags if s.get("priority") == "high"]

        self._section_title("Summary")
        self.ln(2)

        # Summary boxes
        y_start = self.get_y()
        box_w = USABLE_W / 4 - 2
        stats = [
            ("Total Items", len(self.snags), BLACK),
            ("Open", len(open_snags), RED),
            ("Closed", len(closed_snags), GREEN),
            ("High Priority", len(high_pri), AMBER),
        ]
        for i, (label, val, color) in enumerate(stats):
            x = MARGIN + i * (box_w + 2.6)
            self.set_xy(x, y_start)
            self.set_fill_color(*LIGHT_GREY)
            self.rect(x, y_start, box_w, 20, "F")
            self.set_xy(x, y_start + 2)
            self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 18)
            self.set_text_color(*color)
            self.cell(box_w, 10, str(val), align="C")
            self.set_xy(x, y_start + 13)
            self.set_font("DejaVu" if self._use_unicode else "Helvetica", "", 6.5)
            self.set_text_color(*MID_GREY)
            self.cell(box_w, 4, label.upper(), align="C")

        self.set_y(y_start + 26)

        # ── Open Snags table ──
        if open_snags:
            self._section_title(f"Open Items ({len(open_snags)})")
            self._snag_table(open_snags, show_priority=True)
            self.ln(4)

        # ── Closed Snags table ──
        if closed_snags:
            self._section_title(f"Closed Items ({len(closed_snags)})")
            self._snag_table(closed_snags, show_priority=False)

    def _snag_table(self, snags_list, show_priority=True):
        if show_priority:
            col_w = [10, 70, 40, 22, 22, 16]
            headers = ["#", "Description", "Location", "Priority", "Date", "Status"]
        else:
            col_w = [10, 80, 45, 25, 20]
            headers = ["#", "Description", "Location", "Date", "Status"]

        def _draw_header():
            y = self.get_y()
            for i, h in enumerate(headers):
                x = MARGIN + sum(col_w[:i])
                self.set_xy(x, y)
                self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 8)
                self.set_fill_color(*HEADER_GREY)
                self.set_text_color(*DARK)
                self.set_draw_color(*BORDER)
                self.rect(x, y, col_w[i], 7, "DF")
                self.set_xy(x + 1, y + 0.5)
                self.cell(col_w[i] - 2, 6, h)
            self.set_y(y + 7)

        _draw_header()
        line_h = 4

        for idx, snag in enumerate(snags_list):
            if self.get_y() > PAGE_H - 35:
                self.add_page()
                _draw_header()

            note = snag.get("note", "")
            location = snag.get("location", "-") or "-"
            date_str = snag.get("created_at", "")[:10] if snag.get("created_at") else ""

            row = [str(idx + 1), note, location]
            if show_priority:
                row.append(snag.get("priority", "medium").upper())
            row.append(date_str)
            row.append(snag.get("status", "").upper())

            # Calculate row height
            self.set_font("DejaVu" if self._use_unicode else "Helvetica", "", 8)
            desc_lines = max(1, int(self.get_string_width(note) / max(col_w[1] - 4, 1)) + 1)
            loc_lines = max(1, int(self.get_string_width(location) / max(col_w[2] - 4, 1)) + 1)
            row_h = max(7, int(max(desc_lines, loc_lines) * line_h) + 2)

            y_row = self.get_y()

            for i, val in enumerate(row):
                x = MARGIN + sum(col_w[:i])
                # Draw cell border
                self.set_draw_color(*BORDER)
                self.rect(x, y_row, col_w[i], row_h)
                # Write text inside
                self.set_xy(x + 1, y_row + 1)
                self.set_font("DejaVu" if self._use_unicode else "Helvetica", "", 8)
                self.set_text_color(*BLACK)
                if i in (1, 2):
                    # Wrapping columns
                    self.multi_cell(col_w[i] - 2, line_h, val)
                else:
                    self.cell(col_w[i] - 2, row_h - 2, val)

            self.set_y(y_row + row_h)

    # ─── Item Pages (with photos) ───────────────────────────────
    @staticmethod
    def _get_image_size(img_bytes: bytes):
        """Get (width, height) in pixels from image bytes without PIL."""
        # Try JPEG: scan for SOF0/SOF2 markers
        data = img_bytes
        if data[:2] == b'\xff\xd8':  # JPEG
            i = 2
            while i < len(data) - 8:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC2):  # SOF0 or SOF2
                    h = (data[i + 5] << 8) | data[i + 6]
                    w = (data[i + 7] << 8) | data[i + 8]
                    return w, h
                length = (data[i + 2] << 8) | data[i + 3]
                i += 2 + length
        # Try PNG: IHDR chunk
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            w = int.from_bytes(data[16:20], 'big')
            h = int.from_bytes(data[20:24], 'big')
            return w, h
        return None, None

    @staticmethod
    def _fit_dimensions(img_w, img_h, max_w, max_h):
        """Scale (img_w, img_h) to fit within (max_w, max_h), preserving aspect ratio."""
        if not img_w or not img_h:
            return max_w, max_h * 0.5
        ratio = img_w / img_h
        # Try fitting to width first
        w = max_w
        h = w / ratio
        # If too tall, fit to height instead
        if h > max_h:
            h = max_h
            w = h * ratio
        return w, h

    def _build_item_pages(self, photo_data: Dict[str, Any]):
        """
        Build one page per snag item.
        Shows ALL snags (open and closed) with status indicator.
        Uses snag_no for fixed numbering. Supports up to 4 photos.
        """
        if not self.snags:
            return

        # Show ALL snags sorted by snag_no (fixed order)
        all_snags = sorted(self.snags, key=lambda s: s.get("snag_no", 0))

        for idx, snag in enumerate(all_snags):
            self.add_page()

            if idx == 0:
                self._set_body(9)
                self.cell(0, 6, "List of items requiring attention:", ln=True)
                self.ln(2)

            photo_w = USABLE_W * 0.62
            action_w = USABLE_W * 0.38

            # ── Header row ──
            hdr_y = self.get_y()
            hdr_h = 9
            self.set_fill_color(*HEADER_GREY)
            self.set_draw_color(*BORDER)
            self.rect(MARGIN, hdr_y, photo_w, hdr_h, "DF")
            self.rect(MARGIN + photo_w, hdr_y, action_w, hdr_h, "DF")
            self.set_xy(MARGIN, hdr_y + 1)
            self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 9)
            self.set_text_color(*DARK)
            self.cell(photo_w, 7, "Item number", align="C")
            self.set_xy(MARGIN + photo_w, hdr_y + 1)
            self.cell(action_w, 7, "Action required", align="C")
            self.set_y(hdr_y + hdr_h)
            y_content = self.get_y()

            # ── Item number row (report-local numbering: 01, 02, 03...) ──
            item_no = idx + 1
            is_closed = snag.get("status") == "closed"

            self.set_xy(MARGIN, y_content)
            self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 11)
            self.set_text_color(*BLACK)
            self.set_draw_color(*BORDER)
            num_text = f"{item_no:02d}"
            if is_closed:
                num_text += "  [CLOSED]"
            self.cell(photo_w, 8, num_text, border="LR")
            self.ln()
            sep_y = self.get_y()
            self.set_draw_color(*LIGHT_GREY)
            self.line(MARGIN + 2, sep_y, MARGIN + photo_w - 2, sep_y)

            # ── Resolve photos (up to 4 per snag) ──
            snag_id = snag.get("id", "")
            raw = photo_data.get(snag_id)
            if raw is None:
                photos_list = []
            elif isinstance(raw, (bytes, bytearray)):
                photos_list = [raw]
            elif isinstance(raw, list):
                photos_list = raw[:4]  # max 4
            else:
                photos_list = []

            n_photos = len(photos_list)
            photo_inner_w = photo_w - 8
            caption_h = 5
            gap = 3

            # For >2 photos, we'll use 2 per page
            photos_page1 = photos_list[:2]
            photos_page2 = photos_list[2:4]

            avail_h = PAGE_H - 25 - (sep_y + 2)
            n_p1 = len(photos_page1)
            if n_p1 >= 2:
                max_per_photo = (avail_h - caption_h * 2 - gap) / 2
            elif n_p1 == 1:
                max_per_photo = avail_h - caption_h
            else:
                max_per_photo = 80

            cur_y = sep_y + 2

            def _render_photos(photo_list, start_idx, cur_y, max_h):
                for pi, p_bytes in enumerate(photo_list):
                    rendered = False
                    try:
                        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                        tmp.write(p_bytes)
                        tmp.flush()
                        pw, ph = self._get_image_size(p_bytes)
                        render_w, render_h = self._fit_dimensions(pw, ph, photo_inner_w, max_h)
                        img_x = MARGIN + 4 + (photo_inner_w - render_w) / 2
                        self.image(tmp.name, x=img_x, y=cur_y, w=render_w, h=render_h)
                        img_bottom = cur_y + render_h
                        rendered = True
                        # Caption under photo
                        self.set_xy(MARGIN, img_bottom + 1)
                        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "I", 7.5)
                        self.set_text_color(*MID_GREY)
                        self.cell(photo_w, caption_h, f"Photo {item_no}.{start_idx + pi + 1}", align="C")
                        cur_y = img_bottom + 1 + caption_h + gap
                    except Exception:
                        if not rendered:
                            self.set_xy(MARGIN, cur_y)
                            self._set_muted(9)
                            self.cell(photo_w, 20, f"[Photo {item_no}.{start_idx + pi + 1} unavailable]", align="C")
                            cur_y += 20 + gap
                        else:
                            # Photo rendered but caption failed — just skip caption
                            cur_y = cur_y + max_h + gap
                return cur_y

            if photos_page1:
                cur_y = _render_photos(photos_page1, 0, cur_y, max_per_photo)
                self.set_y(cur_y)
            else:
                self.set_xy(MARGIN, cur_y)
                self._set_muted(10)
                self.cell(photo_w, 80, "[No photo]", align="C")
                self.set_y(cur_y + 80)

            photo_bottom = self.get_y()

            # ── Action text (right column) ──
            self.set_xy(MARGIN + photo_w, y_content)
            self._set_body(9)
            action_text = snag.get("note", "[No description]")

            x_right = MARGIN + photo_w
            self.set_xy(x_right + 2, y_content + 2)
            self.set_font("DejaVu" if self._use_unicode else "Helvetica", "", 9)
            self.set_text_color(*BLACK)
            self.multi_cell(action_w - 4, 4.5, action_text)
            self.ln(6)

            # Metadata block
            loc = snag.get("location", "")
            pri = snag.get("priority", "medium")
            status = snag.get("status", "open")
            meta = []
            if loc:
                meta.append(f"Location: {loc}")
            meta.append(f"Priority: {pri.upper()}")
            meta.append(f"Status: {status.upper()}")
            date_str = snag.get("created_at", "")[:10] if snag.get("created_at") else ""
            if date_str:
                meta.append(f"Date: {date_str}")

            # Status highlight for closed snags
            if is_closed:
                self.set_x(x_right + 2)
                self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 8)
                self.set_text_color(*GREEN)
                self.cell(action_w - 4, 5, "CLOSED", ln=True)

            self.set_font("DejaVu" if self._use_unicode else "Helvetica", "", 7)
            self.set_text_color(*MID_GREY)
            for m in meta:
                self.set_x(x_right + 2)
                self.multi_cell(action_w - 4, 3.5, m)

            text_bottom = self.get_y()

            # ── Rectification block (Phase 1 — company toggle) ──
            # Small signature panel in the action column for contractors
            # to fill in when the item is physically rectified. Empty
            # fields by design — the PDF is printed or sent to the
            # contractor who writes directly on it (or fills it in a
            # PDF editor) and returns it to the inspector.
            if self._include_rectification and not is_closed:
                rect_top = text_bottom + 3
                rect_inner_x = x_right + 2
                rect_inner_w = action_w - 4
                line_gap = 6  # vertical gap between fields

                # Light-grey divider above the block
                self.set_draw_color(*LIGHT_GREY)
                self.line(rect_inner_x, rect_top, rect_inner_x + rect_inner_w, rect_top)

                # "Rectification" title
                self.set_xy(rect_inner_x, rect_top + 1.5)
                self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 7)
                self.set_text_color(*DARK)
                self.cell(rect_inner_w, 3.5, "RECTIFICATION", ln=True)

                # Three labelled fields, each with an underline to write on
                fields = [
                    "Rectified on:  ____ / ____ / ________",
                    "Rectified by:  _________________________",
                    "Signature:     _________________________",
                ]
                self.set_font("DejaVu" if self._use_unicode else "Helvetica", "", 7)
                self.set_text_color(*BLACK)
                for fld in fields:
                    self.set_x(rect_inner_x)
                    self.cell(rect_inner_w, line_gap, fld, ln=True)

                text_bottom = self.get_y() + 1

            # ── Draw borders ──
            bottom = max(photo_bottom, text_bottom) + 4
            self.set_draw_color(*BORDER)
            self.rect(MARGIN, y_content, photo_w, bottom - y_content)
            self.rect(MARGIN + photo_w, y_content, action_w, bottom - y_content)

            # ── Overflow page for photos 3-4 ──
            if photos_page2:
                self.add_page()
                self._set_body(9)
                self.cell(0, 6, f"Item {item_no:02d} - continued", ln=True)
                self.ln(2)
                overflow_y = self.get_y()
                overflow_avail = PAGE_H - 25 - overflow_y
                n_p2 = len(photos_page2)
                max_p2 = (overflow_avail - caption_h * n_p2 - gap) / max(n_p2, 1)
                _render_photos(photos_page2, 2, overflow_y, max_p2)

    # ─── Closing Page ───────────────────────────────────────────
    def _build_closing(self):
        self.add_page()
        self.ln(10)
        self._set_body(9)

        self.multi_cell(USABLE_W, 5, self.closing_notes)
        self.ln(8)
        self._set_body(10)
        self.cell(0, 6, "Signed:", ln=True)
        self.ln(6)
        # Name above the line
        self._set_body(10, bold=True)
        self.cell(70, 6, self.inspector, ln=True)
        self.ln(1)
        self.set_draw_color(*BLACK)
        self.set_line_width(0.3)
        self.line(MARGIN, self.get_y(), MARGIN + 70, self.get_y())
        self.ln(4)
        # Date and email below
        self._set_muted(8)
        self.cell(70, 5, f"Date: {datetime.now().strftime('%d/%m/%Y')}", ln=True)
        if self.inspector_email:
            self.cell(70, 5, self.inspector_email, ln=True)

        # Company-level footer text (Phase 1). If set, appears at the
        # very bottom of the closing page — typically used for standard
        # disclaimers, T&Cs references, or contact blocks.
        if self._footer_text:
            self.ln(10)
            self.set_draw_color(*LIGHT_GREY)
            self.line(MARGIN, self.get_y(), PAGE_W - MARGIN, self.get_y())
            self.ln(3)
            self._set_muted(7)
            self.multi_cell(USABLE_W, 3.5, self._footer_text)

    # ─── Build the full report ──────────────────────────────────
    def build(self, photo_data: Optional[Dict[str, Any]] = None) -> bytes:
        """
        Generate the complete PDF and return as bytes.

        photo_data: dict mapping snag_id to photo(s):
            - {snag_id: bytes}          single photo per snag
            - {snag_id: [bytes, bytes]}  up to 2 photos per snag
            If None, photos are skipped.
        """
        self.alias_nb_pages()

        self._build_cover()
        self._build_doc_control()
        self._build_summary()
        self._build_item_pages(photo_data or {})
        self._build_closing()

        return self.output()


# ─── Public API (backwards-compatible + new) ────────────────────

def generate_report_pdf(
    project: Dict[str, Any],
    snags: List[Dict[str, Any]],
    inspector_email: str,
    logo_bytes: Optional[bytes] = None,
    photo_data: Optional[Dict[str, Any]] = None,
    visit_no: str = "",
    visit_display: str = "",
    weather: str = "",
    attendees: str = "",
    access_notes: str = "",
    company_name: str = "",
    checker: str = "",
    reviewer: str = "",
    approver: str = "",
    closing_notes: str = "",
    user_email: str = "",
    plan: str = "free",
    # ── Phase 1: per-company report settings ─────────────────────
    brand_colour: str = "#F97316",
    footer_text: Optional[str] = None,
    include_rectification: bool = False,
) -> bytes:
    """
    Generate a professional site visit report PDF.

    `plan` drives plan-gated rendering:
      - Free plan: diagonal "VOXSITE · FREE PLAN" watermark on every page,
                   company logo is suppressed (logo is a paid feature).
      - Starter+: no watermark, logo rendered if provided.

    Phase 1 additions (per-company configurable via Settings):
      - brand_colour: hex '#RRGGBB' that recolours the cover accent bar
                       and all brand-coloured text. Falls back to orange
                       on malformed input.
      - footer_text:  optional paragraph appended at the bottom of the
                       closing page (disclaimers / company T&Cs).
      - include_rectification: when True, adds a small signature block
                       (Rectified on / Rectified by / Signature) under
                       each OPEN item for contractors to fill in.
    """
    # Import here to avoid circular import with services.plan_limits at module load
    from app.services.plan_limits import has_feature

    show_watermark = has_feature(plan, "pdf_watermark")
    show_logo = has_feature(plan, "company_logo")

    report = SiteVisitReport(
        project=project,
        snags=snags,
        inspector=inspector_email,
        logo_bytes=logo_bytes,
        visit_no=visit_no,
        visit_display=visit_display,
        weather=weather,
        attendees=attendees,
        access_notes=access_notes,
        company_name=company_name,
        checker=checker,
        reviewer=reviewer,
        approver=approver,
        closing_notes=closing_notes,
        show_watermark=show_watermark,
        show_logo=show_logo,
        brand_colour=brand_colour,
        footer_text=footer_text,
        include_rectification=include_rectification,
    )
    report.inspector_email = user_email
    return report.build(photo_data=photo_data)
