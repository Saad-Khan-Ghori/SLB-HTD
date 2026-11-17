#!/usr/bin/env python3
"""Render MEASURED_RESULTS.md as a printable PDF (requires reportlab)."""

from __future__ import annotations

import html
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import simpleSplit
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "MEASURED_RESULTS.md"
OUTPUT = ROOT / "output" / "pdf" / "SLB_HTD_Measured_Results.pdf"
NAVY = colors.HexColor("#153153")
TEAL = colors.HexColor("#216E82")
INK = colors.HexColor("#172337")
PALE = colors.HexColor("#F1F5F8")


def inline(markdown: str) -> str:
    value = html.escape(markdown)
    value = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', value)
    value = re.sub(r"\*\*([^*]+)\*\*", r'<b>\1</b>', value)
    return value


def footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D5DCE5"))
    canvas.line(42, 36, A4[0] - 42, 36)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawString(42, 24, "SLB-HTD | Measured results | 3 November 2026")
    canvas.drawRightString(A4[0] - 42, 24, f"Page {doc.page}")
    canvas.restoreState()


def main() -> None:
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.4, leading=13.4, textColor=INK, spaceAfter=8)
    title = ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=19, leading=23,
                           textColor=NAVY, spaceAfter=14)
    heading = ParagraphStyle("heading", parent=body, fontName="Helvetica-Bold", fontSize=12.5,
                             leading=16, textColor=NAVY, spaceBefore=13, spaceAfter=8,
                             keepWithNext=True)
    cell = ParagraphStyle("cell", parent=body, fontSize=8, leading=10, spaceAfter=0)
    header = ParagraphStyle("header", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
    story = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("# "):
            story.append(Paragraph(inline(line[2:]), title)); i += 1; continue
        if line.startswith("## "):
            story.append(Paragraph(inline(line[3:]), heading)); i += 1; continue
        if line.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].startswith("|"):
                cols = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cols):
                    table_lines.append(cols)
                i += 1
            ncols = len(table_lines[0])
            if ncols == 5 and table_lines[0][0] == "SLB policy":
                widths = [103, 93, 82, 85, 146]
            elif ncols == 6:
                widths = [42, 65, 102, 94, 98, 108]
            else:
                widths = [49, 73, 132, 130, 125]
            data = []
            for idx, row in enumerate(table_lines):
                data.append([Paragraph(inline(value), header if idx == 0 else cell) for value in row])
            table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
            commands = [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, 0), 1, TEAL),
            ]
            for idx in range(2, len(data), 2):
                commands.append(("BACKGROUND", (0, idx), (-1, idx), PALE))
            table.setStyle(TableStyle(commands))
            story.append(table)
            story.append(Spacer(1, 8))
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(("# ", "## ", "|")):
            para.append(lines[i].strip()); i += 1
        story.append(Paragraph(inline(" ".join(para)), body))
    doc = SimpleDocTemplate(str(OUTPUT), pagesize=A4, leftMargin=42, rightMargin=42,
                            topMargin=42, bottomMargin=52, title="SLB-HTD Measured Results",
                            author="SLB-HTD project")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
