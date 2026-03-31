"""
PDF export service – renders the notebook timeline to a PDF document.

Uses fpdf2 for lightweight, pure-Python PDF generation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fpdf import FPDF

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PAGE_W = 210  # A4 width in mm
PAGE_H = 297  # A4 height in mm
MARGIN = 15
CONTENT_W = PAGE_W - 2 * MARGIN
FONT_BODY = 9
FONT_SMALL = 7
FONT_TITLE = 11
FONT_HEADER = 14

# Type badge colours (R, G, B)
TYPE_COLOURS = {
    "text": (59, 130, 246),     # blue
    "header": (16, 185, 129),   # green
    "image": (168, 85, 247),    # purple
    "data": (245, 158, 11),     # amber
    "code": (107, 114, 128),    # grey
    "pvlog": (236, 72, 153),    # pink
}


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text or "")


def _safe_text(text: str) -> str:
    """Sanitise text for fpdf (replace unsupported chars)."""
    if not text:
        return ""
    # fpdf2 handles UTF-8 well, but strip control chars
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


class NotebookPDF(FPDF):
    """Custom PDF with neutroNote header/footer."""

    def __init__(self, ipts: str, title: str | None = None, instrument: str = "SNAP"):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.ipts = ipts
        self.notebook_title = title or ""
        self.instrument_name = instrument
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(MARGIN, MARGIN, MARGIN)
        # Use built-in fonts only (no font files needed)
        self.add_page()

    def header(self):
        """Page header with IPTS and title."""
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(120, 120, 120)
        left = f"{self.instrument_name}  |  IPTS-{self.ipts}"
        if self.notebook_title:
            left += f"  |  {self.notebook_title}"
        self.cell(CONTENT_W, 5, _safe_text(left), align="L")
        self.ln(2)
        # Thin line
        self.set_draw_color(200, 200, 200)
        self.line(MARGIN, self.get_y(), PAGE_W - MARGIN, self.get_y())
        self.ln(4)

    def footer(self):
        """Page footer with page number and export timestamp."""
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def _render_type_badge(self, entry_type: str):
        """Render a small coloured type badge."""
        r, g, b = TYPE_COLOURS.get(entry_type, (100, 100, 100))
        self.set_fill_color(r, g, b)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 7)
        badge_text = f" {entry_type.upper()} "
        badge_w = self.get_string_width(badge_text) + 3
        self.cell(badge_w, 4.5, badge_text, fill=True)
        self.set_text_color(0, 0, 0)

    def _render_entry_header(self, entry):
        """Render the common entry header line: type badge, author, timestamp."""
        y_start = self.get_y()

        # Type badge
        self._render_type_badge(entry.type)
        self.set_font("Helvetica", "", FONT_SMALL)
        self.set_text_color(100, 100, 100)
        self.cell(3, 4.5, "")  # spacer
        self.cell(0, 4.5, f"{entry.author}  ·  {entry.timestamp_display}")
        self.ln(6)

        # Title (if present and not a header entry)
        if entry.title and entry.type != "header":
            self.set_font("Helvetica", "B", FONT_TITLE)
            self.set_text_color(30, 30, 30)
            self.multi_cell(CONTENT_W, 5, _safe_text(entry.title))
            self.ln(1)

    def _render_tags(self, entry):
        """Render tags as inline pills."""
        tags = list(entry.tags)
        if not tags:
            return
        self.set_font("Helvetica", "I", FONT_SMALL)
        self.set_text_color(100, 100, 100)
        tag_text = "  ".join(f"#{t.name}" for t in tags)
        self.cell(CONTENT_W, 4, _safe_text(tag_text))
        self.ln(3)

    def _render_separator(self):
        """Light separator line between entries."""
        self.set_draw_color(220, 220, 220)
        y = self.get_y()
        self.line(MARGIN, y, PAGE_W - MARGIN, y)
        self.ln(4)

    # ------------------------------------------------------------------
    # Entry-type renderers
    # ------------------------------------------------------------------

    def _render_text_entry(self, entry):
        """Render a text/markdown entry."""
        self.set_font("Helvetica", "", FONT_BODY)
        self.set_text_color(30, 30, 30)
        # Strip HTML from markdown-rendered body; use raw body
        body = _safe_text(_strip_html(entry.body)) if entry.body else ""
        if body:
            self.multi_cell(CONTENT_W, 4.5, body)
        self.ln(2)

    def _render_header_entry(self, entry):
        """Render a run header entry (metadata table)."""
        try:
            meta = json.loads(entry.body) if entry.body else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}

        if meta.get("error"):
            self.set_font("Helvetica", "I", FONT_BODY)
            self.set_text_color(200, 50, 50)
            self.multi_cell(CONTENT_W, 4.5, f"Error: {meta['error']}")
            self.ln(2)
            return

        # Title line: "Run XXXXX" from entry title
        if entry.title:
            self.set_font("Helvetica", "B", FONT_TITLE)
            self.set_text_color(30, 30, 30)
            self.cell(CONTENT_W, 5, _safe_text(entry.title))
            self.ln(5)

        # Metadata key-value pairs
        fields = [
            ("Start", meta.get("start_time_formatted") or meta.get("start_time", "N/A")),
            ("End", meta.get("end_time_formatted") or meta.get("end_time", "N/A")),
            ("Duration", meta.get("duration_display") or f"{meta.get('duration', 'N/A')} sec"),
            ("Total Counts", f"{int(meta['total_counts']):,}" if meta.get("total_counts") else "N/A"),
            ("Count Rate", meta.get("count_rate_display", "N/A")),
            ("File Size", meta.get("file_size_display", "N/A")),
        ]

        for label, value in fields:
            self.set_font("Helvetica", "B", FONT_SMALL)
            self.set_text_color(80, 80, 80)
            self.cell(25, 4, f"{label}:")
            self.set_font("Helvetica", "", FONT_SMALL)
            self.set_text_color(30, 30, 30)
            self.cell(0, 4, _safe_text(str(value)))
            self.ln(4)
        self.ln(2)

    def _render_image_entry(self, entry, upload_folder: str):
        """Render an image entry – embed the image in the PDF."""
        filename = entry.body.strip() if entry.body else ""
        if not filename:
            return

        img_path = os.path.join(upload_folder, filename)
        if not os.path.exists(img_path):
            self.set_font("Helvetica", "I", FONT_SMALL)
            self.set_text_color(150, 50, 50)
            self.cell(CONTENT_W, 4, f"[Image not found: {filename}]")
            self.ln(4)
            return

        try:
            # Fit image to content width, max height ~80mm
            max_w = CONTENT_W
            max_h = 80

            # Check if we need a page break for the image
            if self.get_y() + max_h + 10 > PAGE_H - 20:
                self.add_page()

            self.image(img_path, x=MARGIN, w=max_w, h=0)  # h=0 = auto aspect ratio
            # Clamp to max height if needed
        except Exception as e:
            self.set_font("Helvetica", "I", FONT_SMALL)
            self.set_text_color(150, 50, 50)
            self.cell(CONTENT_W, 4, f"[Could not embed image: {e}]")
            self.ln(4)

        self.ln(2)

    def _render_data_entry(self, entry, upload_folder: str):
        """Render a data entry – embed snapshot if available, otherwise show run info."""
        try:
            data = json.loads(entry.body) if entry.body else {}
        except (json.JSONDecodeError, TypeError):
            data = {}

        # Run badges
        run_numbers = data.get("run_numbers") or ([data["run_number"]] if data.get("run_number") else [])
        workspace = data.get("workspace", "")

        if run_numbers:
            self.set_font("Helvetica", "B", FONT_BODY)
            self.set_text_color(30, 30, 30)
            runs_str = ", ".join(f"Run {r}" for r in run_numbers[:10])
            if len(run_numbers) > 10:
                runs_str += f" (+{len(run_numbers) - 10} more)"
            self.cell(CONTENT_W, 4.5, _safe_text(runs_str))
            self.ln(4)

        if workspace:
            self.set_font("Helvetica", "I", FONT_SMALL)
            self.set_text_color(80, 80, 80)
            self.cell(CONTENT_W, 4, f"Workspace: {workspace}")
            self.ln(4)

        # Note
        if data.get("note"):
            self.set_font("Helvetica", "", FONT_BODY)
            self.set_text_color(30, 30, 30)
            self.multi_cell(CONTENT_W, 4.5, _safe_text(data["note"]))
            self.ln(2)

        # Embed snapshot image if available
        snapshot = data.get("snapshot")
        if snapshot:
            img_path = os.path.join(upload_folder, snapshot)
            if os.path.exists(img_path):
                try:
                    if self.get_y() + 60 > PAGE_H - 20:
                        self.add_page()
                    self.image(img_path, x=MARGIN, w=CONTENT_W, h=0)
                except Exception as e:
                    self.set_font("Helvetica", "I", FONT_SMALL)
                    self.set_text_color(150, 50, 50)
                    self.cell(CONTENT_W, 4, f"[Could not embed plot snapshot: {e}]")
                    self.ln(4)
        else:
            self.set_font("Helvetica", "I", FONT_SMALL)
            self.set_text_color(120, 120, 120)
            self.cell(CONTENT_W, 4, "[Plot snapshot not available]")
            self.ln(4)

        self.ln(2)

    def _render_code_entry(self, entry):
        """Render a code cell entry."""
        try:
            code_data = json.loads(entry.body) if entry.body else {}
        except (json.JSONDecodeError, TypeError):
            code_data = {}

        code = code_data.get("code", entry.body or "")
        output = code_data.get("output", "")
        is_error = code_data.get("error", False)

        # Code block with grey background
        self.set_fill_color(245, 245, 245)
        self.set_font("Courier", "", 8)
        self.set_text_color(30, 30, 30)

        # Check for page break
        code_lines = _safe_text(code).split("\n")
        estimated_h = len(code_lines) * 3.5 + 4
        if self.get_y() + estimated_h > PAGE_H - 25:
            self.add_page()

        x_start = self.get_x()
        y_start = self.get_y()
        self.multi_cell(CONTENT_W, 3.5, _safe_text(code), fill=True)
        self.ln(2)

        # Output
        if output:
            self.set_font("Courier", "", 7)
            if is_error:
                self.set_text_color(200, 50, 50)
                self.set_fill_color(255, 240, 240)
            else:
                self.set_text_color(50, 120, 50)
                self.set_fill_color(240, 255, 240)

            # Truncate very long output
            output_text = _safe_text(output)
            if len(output_text) > 2000:
                output_text = output_text[:2000] + "\n... (truncated)"

            self.multi_cell(CONTENT_W, 3.5, output_text, fill=True)
            self.ln(2)

        self.set_text_color(0, 0, 0)

    def _render_pvlog_entry(self, entry, upload_folder: str):
        """Render a PV log entry – show PV names, date range, and snapshot if available."""
        try:
            pvdata = json.loads(entry.body) if entry.body else {}
        except (json.JSONDecodeError, TypeError):
            pvdata = {}

        if pvdata.get("error"):
            self.set_font("Helvetica", "I", FONT_BODY)
            self.set_text_color(200, 50, 50)
            self.multi_cell(CONTENT_W, 4.5, f"Error: {pvdata['error']}")
            self.ln(2)
            return

        traces = pvdata.get("traces", [])
        runs = pvdata.get("runs", [])
        start = pvdata.get("start", "")[:10] if pvdata.get("start") else "?"
        end = pvdata.get("end", "")[:10] if pvdata.get("end") else "?"

        # Summary line
        self.set_font("Helvetica", "", FONT_BODY)
        self.set_text_color(30, 30, 30)
        summary = f"{len(traces)} PV{'s' if len(traces) != 1 else ''}"
        if runs:
            summary += f" · {len(runs)} run{'s' if len(runs) != 1 else ''}"
        summary += f" · {start} → {end}"
        self.cell(CONTENT_W, 4.5, _safe_text(summary))
        self.ln(4)

        # List PV names
        if traces:
            self.set_font("Courier", "", 7)
            self.set_text_color(80, 80, 80)
            for t in traces[:20]:  # Cap at 20 PVs
                pv_name = t.get("pv", t.get("name", "unknown"))
                self.cell(CONTENT_W, 3.5, _safe_text(f"  {pv_name}"))
                self.ln(3.5)
            if len(traces) > 20:
                self.cell(CONTENT_W, 3.5, f"  ... and {len(traces) - 20} more")
                self.ln(3.5)

        # Snapshot
        snapshot = pvdata.get("snapshot")
        if snapshot:
            img_path = os.path.join(upload_folder, snapshot)
            if os.path.exists(img_path):
                try:
                    if self.get_y() + 60 > PAGE_H - 20:
                        self.add_page()
                    self.image(img_path, x=MARGIN, w=CONTENT_W, h=0)
                except Exception:
                    pass

        self.ln(2)

    # ------------------------------------------------------------------
    # Main entry renderer
    # ------------------------------------------------------------------

    def render_entry(self, entry, upload_folder: str):
        """Render a single entry of any type."""
        # Check if we need a new page (at least 25mm space)
        if self.get_y() > PAGE_H - 40:
            self.add_page()

        self._render_entry_header(entry)

        if entry.type == "text":
            self._render_text_entry(entry)
        elif entry.type == "header":
            self._render_header_entry(entry)
        elif entry.type == "image":
            self._render_image_entry(entry, upload_folder)
        elif entry.type == "data":
            self._render_data_entry(entry, upload_folder)
        elif entry.type == "code":
            self._render_code_entry(entry)
        elif entry.type == "pvlog":
            self._render_pvlog_entry(entry, upload_folder)
        else:
            self.set_font("Helvetica", "I", FONT_BODY)
            self.set_text_color(120, 120, 120)
            self.cell(CONTENT_W, 4, f"[Unsupported entry type: {entry.type}]")
            self.ln(4)

        self._render_tags(entry)
        self._render_separator()


def export_timeline_pdf(
    entries: list,
    ipts: str,
    upload_folder: str,
    output_path: str,
    title: str | None = None,
    instrument: str = "SNAP",
) -> str:
    """
    Export the notebook timeline to a PDF file.

    Parameters
    ----------
    entries : list[Entry]
        All entries to include, in chronological order.
    ipts : str
        The IPTS number (e.g. "36141").
    upload_folder : str
        Path to the uploads directory (for embedded images).
    output_path : str
        Full path for the output PDF file.
    title : str, optional
        Notebook title for the header.
    instrument : str
        Instrument name for the header.

    Returns
    -------
    str
        The path to the generated PDF file.
    """
    pdf = NotebookPDF(ipts=ipts, title=title, instrument=instrument)
    pdf.alias_nb_pages()

    # Cover info
    pdf.set_font("Helvetica", "B", FONT_HEADER)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(CONTENT_W, 8, f"neutroNote  -  IPTS-{ipts}", align="C")
    pdf.ln(8)

    if title:
        pdf.set_font("Helvetica", "", FONT_TITLE)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(CONTENT_W, 6, _safe_text(title), align="C")
        pdf.ln(6)

    pdf.set_font("Helvetica", "I", FONT_SMALL)
    pdf.set_text_color(130, 130, 130)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pdf.cell(CONTENT_W, 4, f"Exported {now}  |  {len(entries)} entries", align="C")
    pdf.ln(8)
    pdf._render_separator()

    # Render each entry
    for entry in entries:
        try:
            pdf.render_entry(entry, upload_folder)
        except Exception as e:
            logger.warning("Failed to render entry %s to PDF: %s", entry.id, e)
            pdf.set_font("Helvetica", "I", FONT_SMALL)
            pdf.set_text_color(200, 50, 50)
            pdf.cell(CONTENT_W, 4, f"[Error rendering entry {entry.id}: {e}]")
            pdf.ln(4)
            pdf._render_separator()

    # Write PDF
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pdf.output(output_path)
    logger.info("PDF exported: %s (%d entries)", output_path, len(entries))
    return output_path
