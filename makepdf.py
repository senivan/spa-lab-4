#!/usr/bin/env python3
"""Generate PDF from PROTOCOL_UK.md"""
import sys

# Install fpdf2 with pip
import subprocess
subprocess.run([sys.executable, "-m", "pip", "install", "fpdf2", "--break-system-packages", "-q"], check=False)

from fpdf import FPDF

# Read markdown file
with open("PROTOCOL_UK.md", "r", encoding="utf-8") as f:
    content = f.read()

# Create PDF
pdf = FPDF(format="A4", orient="P")
pdf.set_auto_page_break(auto=True, margin=15)
pdf.add_page()

# Set font
pdf.set_font("Courier", size=9)

# Split content into lines and add to PDF
for line in content.split("\n"):
    line_stripped = line.strip()
    if line_stripped:
        # Truncate long lines
        if len(line_stripped) > 100:
            line_stripped = line_stripped[:100]
        try:
            # Use latin-1 subset of unicode that fpdf supports
            encodable = line_stripped.encode('latin-1', 'ignore').decode('latin-1')
            pdf.cell(0, 5, encodable, ln=True)
        except Exception as e:
            pass

# Save PDF
pdf.output("PROTOCOL_UK.pdf")
print("✓ PDF created: PROTOCOL_UK.pdf")
