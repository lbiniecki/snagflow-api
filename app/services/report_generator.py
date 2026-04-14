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
        weather: str = "",
        attendees: str = "",
        access_notes: str = "",
    ):
        super().__init__()
        self.project = project
        self.snags = snags
        self.inspector = inspector
        self.visit_no = visit_no or "1"
        self.weather = weather
        self.attendees = attendees
        self.access_notes = access_notes
        self._logo_path: Optional[str] = None
        self._is_cover = False

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
        if logo_bytes:
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
        p_id = self.project.get("id", "")[:8].upper()
        left = f"{p_code}  |  Ref: VS-{p_id}"
        self.cell(USABLE_W / 3, 5, left, align="L")
        self.cell(USABLE_W / 3, 5, f"Site visit No {self.visit_no}", align="C")
        self.cell(USABLE_W / 3, 5, f"{self.page_no()}", align="R")

    # ─── Helpers ────────────────────────────────────────────────
    def _set_brand(self, size=11, bold=True):
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B" if bold else "", size)
        self.set_text_color(*ORANGE)

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
        self.set_y(PAGE_H * 0.4)

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

        # Visit number
        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 14)
        self.set_text_color(*DARK)
        self.cell(0, 8, f"Site visit No. {self.visit_no}", ln=True)
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
        self.set_fill_color(*ORANGE)
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
        self._table_cell(c2, self.visit_no, h=10)
        self._table_cell(c1, "Date:", h=10, bold=True, fill=True)
        self._table_cell(c2, datetime.now().strftime("%d/%m/%Y"), h=10)
        self.ln()
        self._table_cell(c1, "Reason:", h=10, bold=True, fill=True)
        self._table_cell(c1 + c2 + c2, "Site Inspection", h=10)
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
        self._table_cell(sign_cols[2], "")
        self._table_cell(sign_cols[3], "")
        self._table_cell(sign_cols[4], "")
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
            ("Total Snags", len(self.snags), BLACK),
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
            self._section_title(f"Open Snags ({len(open_snags)})")
            self._snag_table(open_snags, show_priority=True)
            self.ln(4)

        # ── Closed Snags table ──
        if closed_snags:
            self._section_title(f"Closed Snags ({len(closed_snags)})")
            self._snag_table(closed_snags, show_priority=False)

    def _snag_table(self, snags_list, show_priority=True):
        if show_priority:
            col_w = [10, 70, 40, 22, 22, 16]
            headers = ["#", "Description", "Location", "Priority", "Date", "Status"]
        else:
            col_w = [10, 80, 45, 25, 20]
            headers = ["#", "Description", "Location", "Date", "Status"]

        for i, h in enumerate(headers):
            self._table_header_cell(col_w[i], h)
        self.ln()

        self._set_body(8)
        for idx, snag in enumerate(snags_list):
            # Check if we need a new page
            if self.get_y() > PAGE_H - 30:
                self.add_page()
                for i, h in enumerate(headers):
                    self._table_header_cell(col_w[i], h)
                self.ln()

            note = snag.get("note", "")
            if len(note) > 55:
                note = note[:52] + "..."
            location = snag.get("location", "-") or "-"
            if len(location) > 30:
                location = location[:27] + "..."
            date_str = snag.get("created_at", "")[:10] if snag.get("created_at") else ""

            row = [str(idx + 1), note, location]
            if show_priority:
                row.append(snag.get("priority", "medium").upper())
            row.append(date_str)
            row.append(snag.get("status", "").upper())

            for i, val in enumerate(row):
                self._table_cell(col_w[i], val)
            self.ln()

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
        Build one page per snag item, matching the HP template layout:
        65% photo column | 35% action/description column.
        Up to 2 photos per item, stacked vertically with captions.
        """
        if not self.snags:
            return

        open_snags = [s for s in self.snags if s.get("status") == "open"]
        if not open_snags:
            open_snags = self.snags

        for idx, snag in enumerate(open_snags):
            self.add_page()

            if idx == 0:
                self._set_body(9)
                self.cell(0, 6, "Summary of actions to be carried out by contractor:", ln=True)
                self.ln(2)

            photo_w = USABLE_W * 0.70
            action_w = USABLE_W * 0.30

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

            # ── Item number row ──
            self.set_xy(MARGIN, y_content)
            self.set_font("DejaVu" if self._use_unicode else "Helvetica", "B", 11)
            self.set_text_color(*BLACK)
            self.set_draw_color(*BORDER)
            self.cell(photo_w, 8, f"{idx + 1:02d}", border="LR")
            self.ln()
            sep_y = self.get_y()
            self.set_draw_color(*LIGHT_GREY)
            self.line(MARGIN + 2, sep_y, MARGIN + photo_w - 2, sep_y)

            # ── Resolve photos ──
            snag_id = snag.get("id", "")
            raw = photo_data.get(snag_id)
            if raw is None:
                photos_list = []
            elif isinstance(raw, (bytes, bytearray)):
                photos_list = [raw]
            elif isinstance(raw, list):
                photos_list = raw[:2]
            else:
                photos_list = []

            item_no = idx + 1
            n_photos = len(photos_list)
            photo_inner_w = photo_w - 8
            caption_h = 5  # height for caption line
            gap = 3         # gap between photos

            # Available height for photos in the left column
            # Page usable: from sep_y+2 to PAGE_H - 25 (footer margin)
            avail_h = PAGE_H - 25 - (sep_y + 2)
            if n_photos >= 2:
                max_per_photo = (avail_h - caption_h * 2 - gap) / 2
            elif n_photos == 1:
                max_per_photo = avail_h - caption_h
            else:
                max_per_photo = 80

            cur_y = sep_y + 2

            if photos_list:
                for pi, p_bytes in enumerate(photos_list):
                    try:
                        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                        tmp.write(p_bytes)
                        tmp.flush()

                        # Calculate exact rendered size
                        pw, ph = self._get_image_size(p_bytes)
                        render_w, render_h = self._fit_dimensions(
                            pw, ph, photo_inner_w, max_per_photo
                        )

                        # Center horizontally in column
                        img_x = MARGIN + 4 + (photo_inner_w - render_w) / 2

                        self.image(
                            tmp.name,
                            x=img_x,
                            y=cur_y,
                            w=render_w,
                            h=render_h,
                        )
                        img_bottom = cur_y + render_h

                        # Caption right under the photo
                        self.set_xy(MARGIN, img_bottom + 1)
                        self.set_font("DejaVu" if self._use_unicode else "Helvetica", "I", 7.5)
                        self.set_text_color(*MID_GREY)
                        self.cell(photo_w, caption_h, f"Photo {item_no}.{pi + 1}", align="C")

                        cur_y = img_bottom + 1 + caption_h + gap

                    except Exception:
                        self.set_xy(MARGIN, cur_y)
                        self._set_muted(9)
                        self.cell(photo_w, 40, f"[Photo {item_no}.{pi + 1} unavailable]", align="C")
                        cur_y += 40 + gap

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

            # Write action text in the right column
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

            self.set_font("DejaVu" if self._use_unicode else "Helvetica", "", 7)
            self.set_text_color(*MID_GREY)
            for m in meta:
                self.set_x(x_right + 2)
                self.cell(action_w - 4, 3.5, m, ln=True)

            text_bottom = self.get_y()

            # ── Draw borders around both columns to the same bottom ──
            bottom = max(photo_bottom, text_bottom) + 4
            self.set_draw_color(*BORDER)
            self.rect(MARGIN, y_content, photo_w, bottom - y_content)
            self.rect(MARGIN + photo_w, y_content, action_w, bottom - y_content)

    # ─── Closing Page ───────────────────────────────────────────
    def _build_closing(self):
        self.add_page()
        self.ln(10)
        self._set_body(9)

        self.multi_cell(
            USABLE_W, 5,
            "• If requested, notice must be given to allow for a site visit "
            "to review prior to closing up or concealing the item of works."
        )
        self.ln(4)
        self.multi_cell(
            USABLE_W, 5,
            "The contractor is to confirm that the above actions have been carried out "
            "and provide photographic record of the associated works. The contractor is "
            "to sign the items as closed and e-mail to originator."
        )
        self.ln(8)
        self._set_body(10)
        self.cell(0, 6, "Signed:", ln=True)
        self.ln(12)
        self.set_draw_color(*BLACK)
        self.set_line_width(0.3)
        self.line(MARGIN, self.get_y(), MARGIN + 70, self.get_y())
        self.ln(2)
        self._set_body(9)
        self.cell(70, 5, self.inspector)

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
    weather: str = "",
    attendees: str = "",
    access_notes: str = "",
) -> bytes:
    """
    Generate a professional site visit report PDF.

    Args:
        project:     Project dict (name, client, address, id)
        snags:       List of snag dicts
        inspector_email: Inspector name or email
        logo_bytes:  Optional company logo image bytes (PNG/JPG)
        photo_data:  Optional dict {snag_id: image_bytes} or {snag_id: [bytes, bytes]} for 1-2 photos
        visit_no:    Optional visit number string
        weather:     Optional weather conditions
        attendees:   Optional attendees list
        access_notes: Optional site access notes

    Returns:
        PDF file as bytes
    """
    report = SiteVisitReport(
        project=project,
        snags=snags,
        inspector=inspector_email,
        logo_bytes=logo_bytes,
        visit_no=visit_no,
        weather=weather,
        attendees=attendees,
        access_notes=access_notes,
    )
    return report.build(photo_data=photo_data)
