#!/usr/bin/env python3
"""Simple PDF conversion without complex dependencies."""

import subprocess
import sys

# Install fpdf2 if needed
try:
    from fpdf import FPDF
except ImportError:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "fpdf2", "--break-system-packages", "-q"],
        check=True
    )
    from fpdf import FPDF

# Read markdown
with open("PROTOCOL_UK.md", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Create simple PDF
pdf = FPDF(format="A4", orient="P")
pdf.set_auto_page_break(auto=True, margin=15)
pdf.add_page()
pdf.set_font("Helvetica", size=11)

# Add title
pdf.set_font("Helvetica", "B", size=16)
pdf.cell(0, 10, "Protocol", ln=True)
pdf.set_font("Helvetica", size=10)

# Add content line by line
for line in lines[:100]:
    line = line.strip()
    if line:
        try:
            pdf.multi_cell(0, 5, line)
        except:
            pass

# Save
pdf.output("PROTOCOL_UK.pdf")
print("PDF created: PROTOCOL_UK.pdf")
