"""Generalization set: documents deliberately NOT shaped like the configured schemas.

METHODOLOGY - this file must never import docint.config.

The controlled fixtures in corpus/ were written alongside FIELD_SPECS, so extracting
them proves little: the schema and the document came from the same pen. These
documents are authored the other way round - realistic layouts and vocabulary first,
with no reference to what the pipeline expects - and FIELD_SPECS is NOT adjusted to
accommodate them. The question is whether genuinely varied documents map onto a
stable schema, not whether a schema can be recovered from documents built from it.

Deliberately included:
  - alternate labels     "Bill Date" / "Our Ref" / "Your Order" / "Amount Payable"
  - missing fields       an invoice with no PO reference at all (must extract null,
                         not hallucinate one)
  - extra fields         VAT breakdowns, shipping, discounts with no schema slot
  - multi-page           a line-item table continuing onto page 2
  - tables               blanket PO with per-line amounts and no single total
  - stamps/handwriting   a PAID stamp and a handwritten amendment over printed text
  - scan degradation     the same OCR ladder applied to unfamiliar layouts
  - unsupported types    retail receipts and a chemical safety data sheet

Run:  python scripts/build_generalization_set.py
"""
from __future__ import annotations

import io
import random
from pathlib import Path

import pymupdf
from openpyxl import Workbook
from openpyxl.styles import Font
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "eval" / "generalization"
BUILD = Path(__file__).resolve().parent.parent / "runs" / "_build"
SEED = 20260918
W, H = LETTER


def _label_value(c, x, y, label, value, *, lw="Helvetica-Bold", vw="Helvetica", size=10):
    c.setFont(lw, size); c.drawString(x, y, label)
    c.setFont(vw, size); c.drawString(x + 1.55 * inch, y, value)


# --------------------------------------------------------------------------- #
# G1  multi-page invoice, alternate labels, extra fields, table over two pages
# --------------------------------------------------------------------------- #

def g1_invoice_multipage(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER)

    c.setFont("Helvetica-Bold", 16)
    c.drawString(1 * inch, H - 1 * inch, "Northstar Fabrication Ltd.")
    c.setFont("Helvetica", 9)
    c.drawString(1 * inch, H - 1.18 * inch, "Unit 7, Kilbride Industrial Estate, Sheffield S9 2XT")
    c.setFont("Helvetica-Bold", 13)
    c.drawRightString(W - 1 * inch, H - 1 * inch, "SALES INVOICE")
    c.setFont("Helvetica", 9)
    c.drawRightString(W - 1 * inch, H - 1.18 * inch, "Page 1 of 2")

    y = H - 1.75 * inch
    # alternate labels throughout: "Our Ref", "Bill Date", "Your Order"
    for label, value in [("Our Ref:", "NF/24518"), ("Bill Date:", "11 April 2026"),
                         ("Your Order:", "PO-2026-0091"), ("Terms:", "30 days net"),
                         ("Currency:", "GBP")]:
        _label_value(c, 1 * inch, y, label, value)
        y -= 15

    y -= 10
    c.setFont("Helvetica-Bold", 10); c.drawString(1 * inch, y, "Invoice To")
    c.setFont("Helvetica", 10)
    c.drawString(1 * inch, y - 14, "Northwind Manufacturing LLC")
    c.drawString(1 * inch, y - 27, "2100 Lakeshore Drive, Milwaukee, WI 53202")

    y -= 60
    c.setFont("Helvetica-Bold", 9)
    for x, t in ((1.0, "Item"), (1.6, "Particulars"), (4.9, "Qty"), (5.5, "Rate"), (6.6, "Net")):
        c.drawString(x * inch, y, t)
    c.line(1 * inch, y - 4, W - 1 * inch, y - 4)
    y -= 18

    lines_p1 = [
        ("1", "Mild steel bracket, 6mm, powder coated", "120", "18.40", "2,208.00"),
        ("2", "Stainless dowel pin, M8 x 40", "500", "1.15", "575.00"),
        ("3", "Weld nut, M10 zinc plated", "800", "0.62", "496.00"),
        ("4", "Cable gland, brass, 20mm", "250", "2.05", "512.50"),
    ]
    c.setFont("Helvetica", 9)
    for item, desc, qty, rate, net in lines_p1:
        c.drawString(1.0 * inch, y, item); c.drawString(1.6 * inch, y, desc)
        c.drawString(4.9 * inch, y, qty)
        c.drawRightString(6.3 * inch, y, rate); c.drawRightString(W - 1 * inch, y, net)
        y -= 14
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(1 * inch, y - 10, "continued overleaf ...")
    c.showPage()

    # page 2 - remainder of the table and the totals block
    c.setFont("Helvetica-Bold", 10)
    c.drawString(1 * inch, H - 1 * inch, "Northstar Fabrication Ltd. - Our Ref NF/24518")
    c.setFont("Helvetica", 9)
    c.drawRightString(W - 1 * inch, H - 1 * inch, "Page 2 of 2")

    y = H - 1.5 * inch
    c.setFont("Helvetica-Bold", 9)
    for x, t in ((1.0, "Item"), (1.6, "Particulars"), (4.9, "Qty"), (5.5, "Rate"), (6.6, "Net")):
        c.drawString(x * inch, y, t)
    c.line(1 * inch, y - 4, W - 1 * inch, y - 4)
    y -= 18
    c.setFont("Helvetica", 9)
    for item, desc, qty, rate, net in [
        ("5", "Hex bolt, M12 x 60, grade 8.8", "300", "0.94", "282.00"),
        ("6", "Carriage and packing", "1", "85.00", "85.00"),
    ]:
        c.drawString(1.0 * inch, y, item); c.drawString(1.6 * inch, y, desc)
        c.drawString(4.9 * inch, y, qty)
        c.drawRightString(6.3 * inch, y, rate); c.drawRightString(W - 1 * inch, y, net)
        y -= 14

    # extra fields with no schema slot: discount, VAT breakdown, carriage
    y -= 24
    c.line(4.9 * inch, y + 12, W - 1 * inch, y + 12)
    for label, value, bold in [("Goods total", "4,158.50", False),
                               ("Settlement discount 2.5%", "-103.96", False),
                               ("Net after discount", "4,054.54", False),
                               ("VAT @ 20%", "810.91", False),
                               ("Amount Payable", "4,865.45", True)]:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 9)
        c.drawRightString(6.3 * inch, y, f"{label}:")
        c.drawRightString(W - 1 * inch, y, value)
        y -= 15
    c.showPage()
    c.save()


# --------------------------------------------------------------------------- #
# G2  scanned invoice, PAID stamp + handwriting, NO purchase order reference
# --------------------------------------------------------------------------- #

def g2_invoice_stamped_source(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(1 * inch, H - 1 * inch, "BAYSIDE ELECTRICAL SUPPLY")
    c.setFont("Helvetica", 9)
    c.drawString(1 * inch, H - 1.16 * inch, "881 Harbour Way, Oakland CA 94607  ·  (510) 555-0142")

    y = H - 1.7 * inch
    c.setFont("Helvetica-Bold", 12); c.drawString(1 * inch, y, "INVOICE")
    y -= 24
    # NOTE: deliberately no PO / order reference anywhere on this document
    for label, value in [("Invoice #", "BES-77310"), ("Date", "03/22/2026"),
                         ("Account", "NWM-4471"), ("Terms", "Due on receipt")]:
        _label_value(c, 1 * inch, y, label, value)
        y -= 15

    y -= 20
    c.setFont("Helvetica-Bold", 9)
    c.drawString(1 * inch, y, "Qty   Description")
    c.drawRightString(W - 1 * inch, y, "Amount")
    c.line(1 * inch, y - 4, W - 1 * inch, y - 4)
    y -= 18
    c.setFont("Helvetica", 9)
    for qty, desc, amt in [("12", "THHN wire, 12 AWG, 500ft spool", "1,428.00"),
                           ("4", "Panelboard, 42-circuit, 225A", "3,196.00"),
                           ("40", "EMT conduit, 3/4in x 10ft", "318.00")]:
        c.drawString(1 * inch, y, qty); c.drawString(1.55 * inch, y, desc)
        c.drawRightString(W - 1 * inch, y, amt)
        y -= 14

    y -= 20
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(6.3 * inch, y, "TOTAL")
    c.drawRightString(W - 1 * inch, y, "$4,942.00")

    # PAID stamp: rotated outlined box over the body
    c.saveState()
    c.translate(4.7 * inch, 3.5 * inch); c.rotate(14)
    c.setStrokeColor(colors.HexColor("#8B1A1A")); c.setLineWidth(2.5)
    c.rect(-0.95 * inch, -0.3 * inch, 1.9 * inch, 0.62 * inch)
    c.setFillColor(colors.HexColor("#8B1A1A")); c.setFont("Helvetica-Bold", 25)
    c.drawCentredString(0, -0.1 * inch, "PAID")
    c.restoreState()

    # handwritten-style annotation (oblique, rotated, off-baseline)
    c.saveState()
    c.translate(4.6 * inch, 3.0 * inch); c.rotate(-6)
    c.setFillColor(colors.HexColor("#1F3A93")); c.setFont("Helvetica-Oblique", 13)
    c.drawString(0, 0, "ck #4471  4/2")
    c.restoreState()
    c.showPage(); c.save()


# --------------------------------------------------------------------------- #
# G3  blanket purchase agreement - not called a "purchase order", no single total
# --------------------------------------------------------------------------- #

def g3_blanket_po(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(W / 2, H - 1 * inch, "BLANKET PURCHASE AGREEMENT")
    c.setFont("Helvetica", 9)
    c.drawCentredString(W / 2, H - 1.2 * inch, "Vertex Aerospace Components Inc.  /  Northwind Manufacturing LLC")

    y = H - 1.8 * inch
    for label, value in [("Agreement No.", "BPA-4471-26"), ("Effective", "2026-01-15"),
                         ("Expires", "2026-12-31"), ("Issued By", "Northwind Manufacturing LLC"),
                         ("Supplier", "Vertex Aerospace Components Inc."),
                         ("Release Method", "Against written release only")]:
        _label_value(c, 1 * inch, y, label, value)
        y -= 16

    y -= 18
    c.setFont("Helvetica-Bold", 9)
    for x, t in ((1.0, "Line"), (1.5, "Part / Description"), (4.6, "Est. Qty"),
                 (5.4, "Unit"), (6.3, "Line Value")):
        c.drawString(x * inch, y, t)
    c.line(1 * inch, y - 4, W - 1 * inch, y - 4)
    y -= 18
    c.setFont("Helvetica", 9)
    # no single "total committed" line - the cap is expressed as Maximum Commitment below
    for line, desc, qty, unit, val in [
        ("001", "VX-3300 titanium fastener set", "2,000", "68.00", "136,000.00"),
        ("002", "VX-3312 locking collar", "1,500", "22.50", "33,750.00"),
        ("003", "VX-9000 inspection service, per lot", "40", "760.00", "30,400.00"),
    ]:
        c.drawString(1.0 * inch, y, line); c.drawString(1.5 * inch, y, desc)
        c.drawString(4.6 * inch, y, qty)
        c.drawRightString(5.9 * inch, y, unit); c.drawRightString(W - 1 * inch, y, val)
        y -= 14

    y -= 26
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Maximum Commitment:")
    c.drawRightString(W - 1 * inch, y, "USD 200,150.00")
    y -= 30
    c.setFont("Helvetica", 8)
    c.drawString(1 * inch, y, "Releases against this agreement may not in aggregate exceed the Maximum")
    c.drawString(1 * inch, y - 11, "Commitment without a written amendment signed by both parties.")
    c.showPage(); c.save()


# --------------------------------------------------------------------------- #
# G4  scanned PO with a handwritten amendment over the printed amount
# --------------------------------------------------------------------------- #

def g4_po_amended_source(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(1 * inch, H - 1 * inch, "PURCHASE ORDER")
    c.setFont("Helvetica", 9)
    c.drawRightString(W - 1 * inch, H - 1 * inch, "Harlow Components GmbH")

    y = H - 1.7 * inch
    for label, value in [("Order No.", "HC-2026-8802"), ("Dated", "2026-03-05"),
                         ("Ordered By", "Northwind Manufacturing LLC"),
                         ("Deliver To", "Dock 4, Milwaukee WI")]:
        _label_value(c, 1 * inch, y, label, value, size=11)
        y -= 18

    y -= 20
    c.setFont("Helvetica", 11)
    c.drawString(1 * inch, y, "1   Precision ground shaft, 25mm x 400mm     Qty 150")
    y -= 40
    c.setFont("Helvetica-Bold", 13)
    c.drawString(1 * inch, y, "Order Value:")
    c.drawRightString(W - 1 * inch, y, "EUR 18,750.00")

    # handwritten amendment: strike through the printed value, write a new one above
    c.setStrokeColor(colors.HexColor("#1F3A93")); c.setLineWidth(1.6)
    c.line(W - 2.35 * inch, y + 5, W - 0.95 * inch, y + 7)
    c.saveState()
    c.translate(W - 2.5 * inch, y + 22); c.rotate(-3)
    c.setFillColor(colors.HexColor("#1F3A93")); c.setFont("Helvetica-Oblique", 13)
    c.drawString(0, 0, "19,400.00  DO 6/3")
    c.restoreState()

    y -= 50
    c.setFont("Helvetica", 8)
    c.drawString(1 * inch, y, "Amendments must be countersigned. See attached change note.")
    c.showPage(); c.save()


# --------------------------------------------------------------------------- #
# G5 / G6  retail receipts - NOT in the taxonomy
# --------------------------------------------------------------------------- #

def _receipt_canvas(path: Path, width_in: float = 3.2, height_in: float = 7.0):
    return canvas.Canvas(str(path), pagesize=(width_in * inch, height_in * inch))


def g5_receipt(path: Path) -> None:
    w, h = 3.2 * inch, 7.0 * inch
    c = _receipt_canvas(path)
    y = h - 0.5 * inch
    c.setFont("Helvetica-Bold", 11); c.drawCentredString(w / 2, y, "OFFICE DEPOT #2214")
    c.setFont("Helvetica", 7)
    for line in ["4410 W Layton Ave", "Greenfield, WI 53220", "(414) 555-0199"]:
        y -= 11; c.drawCentredString(w / 2, y, line)
    y -= 18
    c.drawString(0.25 * inch, y, "04/02/2026  14:32   Reg 04  Trn 8871")
    y -= 8; c.line(0.25 * inch, y, w - 0.25 * inch, y); y -= 14

    c.setFont("Helvetica", 8)
    for desc, amt in [("COPY PAPER 8.5X11 10RM", "54.99"), ("TONER HP 26A BLACK", "89.49"),
                      ("BINDER CLIPS MED 144CT", "12.79"), ("LEGAL PAD 12PK", "18.99")]:
        c.drawString(0.25 * inch, y, desc); c.drawRightString(w - 0.25 * inch, y, amt)
        y -= 12
    y -= 6; c.line(0.25 * inch, y, w - 0.25 * inch, y); y -= 14
    for label, amt, bold in [("SUBTOTAL", "176.26", False), ("TAX 5.5%", "9.69", False),
                             ("TOTAL", "185.95", True), ("VISA ****3318", "185.95", False)]:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 9 if bold else 8)
        c.drawString(0.25 * inch, y, label); c.drawRightString(w - 0.25 * inch, y, amt)
        y -= 12
    y -= 14
    c.setFont("Helvetica", 7)
    c.drawCentredString(w / 2, y, "RETURNS WITHIN 30 DAYS WITH RECEIPT")
    c.showPage(); c.save()


def g6_receipt_thermal_source(path: Path) -> None:
    w, h = 2.8 * inch, 6.0 * inch
    c = canvas.Canvas(str(path), pagesize=(w, h))
    y = h - 0.4 * inch
    c.setFont("Courier-Bold", 9); c.drawCentredString(w / 2, y, "FUEL STOP 41")
    c.setFont("Courier", 7)
    for line in ["I-94 EXIT 310", "PUMP 6   DIESEL"]:
        y -= 10; c.drawCentredString(w / 2, y, line)
    y -= 16
    for label, amt in [("GALLONS", "38.412"), ("PRICE/GAL", "4.099"),
                       ("FUEL TOTAL", "157.45"), ("CARD", "FLEET *7781")]:
        c.drawString(0.2 * inch, y, label); c.drawRightString(w - 0.2 * inch, y, amt)
        y -= 11
    y -= 12
    c.drawCentredString(w / 2, y, "04/03/26 07:14")
    c.showPage(); c.save()


# --------------------------------------------------------------------------- #
# G7  vendor terms workbook - horizontal layout, different sheet, MULTIPLE vendors
# --------------------------------------------------------------------------- #

def g7_vendor_terms(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Supplier Terms"     # not "Vendors"
    bold = Font(bold=True)

    ws["A1"] = "APPROVED SUPPLIER TERMS - FY2026"
    ws["A1"].font = Font(bold=True, size=12)

    # horizontal: one vendor per ROW, attributes across columns
    headers = ["Supplier Code", "Supplier", "Agreed Rate (USD)", "Rate Basis",
               "Valid From", "Settlement", "Approver"]
    for j, head in enumerate(headers, start=1):
        ws.cell(row=3, column=j, value=head).font = bold

    rows = [
        ("SUP-0098", "Harlow Components GmbH",        124.00, "per unit", "2026-02-01", "45 days",  "D. Okafor"),
        ("SUP-0101", "Vertex Aerospace Components",    68.00, "per unit", "2026-01-15", "Net 60",   "D. Okafor"),
        ("SUP-0112", "Northstar Fabrication Ltd.",     18.40, "per unit", "2026-03-01", "30 days",  "R. Bennett"),
    ]
    for i, row in enumerate(rows, start=4):
        for j, value in enumerate(row, start=1):
            cell = ws.cell(row=i, column=j, value=value)
            if j == 3:
                cell.number_format = '"$"#,##0.00'

    for col, width in zip("ABCDEFG", (16, 32, 18, 12, 12, 12, 14)):
        ws.column_dimensions[col].width = width
    wb.save(path)


# --------------------------------------------------------------------------- #
# G8  genuinely unsupported document type
# --------------------------------------------------------------------------- #

def g8_safety_datasheet(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(1 * inch, H - 1 * inch, "SAFETY DATA SHEET")
    c.setFont("Helvetica", 9)
    c.drawString(1 * inch, H - 1.2 * inch, "In accordance with OSHA HCS 29 CFR 1910.1200")

    y = H - 1.7 * inch
    sections = [
        ("SECTION 1: IDENTIFICATION",
         ["Product identifier: Isopropyl Alcohol 99%",
          "Recommended use: Industrial degreasing and cleaning",
          "Supplier: Meridian Chemical Partners, Toledo OH",
          "Emergency telephone: CHEMTREC 1-800-424-9300"]),
        ("SECTION 2: HAZARDS IDENTIFICATION",
         ["Flammable liquid, Category 2",
          "Serious eye irritation, Category 2A",
          "Signal word: DANGER",
          "H225 Highly flammable liquid and vapour"]),
        ("SECTION 4: FIRST-AID MEASURES",
         ["Inhalation: Move to fresh air. If breathing is difficult, give oxygen.",
          "Skin contact: Wash with soap and water. Remove contaminated clothing.",
          "Eye contact: Rinse cautiously with water for several minutes."]),
        ("SECTION 9: PHYSICAL AND CHEMICAL PROPERTIES",
         ["Appearance: Clear colourless liquid",
          "Flash point: 12 degC (closed cup)",
          "Boiling point: 82.6 degC",
          "Relative density: 0.785 at 20 degC"]),
    ]
    for title, lines in sections:
        c.setFont("Helvetica-Bold", 10); c.drawString(1 * inch, y, title); y -= 15
        c.setFont("Helvetica", 9)
        for line in lines:
            c.drawString(1.15 * inch, y, line); y -= 12
        y -= 10
    c.showPage(); c.save()


# --------------------------------------------------------------------------- #
# Scan simulation (same degradation approach as the controlled fixtures)
# --------------------------------------------------------------------------- #

def scan(src: Path, out: Path, *, dpi: int, blur: float, noise: int,
         jpeg: int, rotate: float) -> None:
    doc = pymupdf.open(src)
    pix = doc[0].get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    page_rect = doc[0].rect
    doc.close()

    rng = random.Random(SEED)
    if rotate:
        img = img.rotate(rotate, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        px = img.load()
        for _ in range(int(img.width * img.height * noise / 1000)):
            x, y = rng.randrange(img.width), rng.randrange(img.height)
            v = rng.randint(0, 255)
            px[x, y] = (v, v, v)

    buf = io.BytesIO()
    img.convert("L").save(buf, format="JPEG", quality=jpeg, optimize=True)
    pdf = pymupdf.open()
    page = pdf.new_page(width=page_rect.width, height=page_rect.height)
    page.insert_image(page_rect, stream=buf.getvalue())
    pdf.save(out)
    pdf.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    g1_invoice_multipage(OUT / "G1_invoice_northstar_multipage.pdf")

    g2_invoice_stamped_source(BUILD / "g2_src.pdf")
    scan(BUILD / "g2_src.pdf", OUT / "G2_invoice_stamped_scan.pdf",
         dpi=250, blur=0.6, noise=3, jpeg=55, rotate=0.5)

    g3_blanket_po(OUT / "G3_blanket_purchase_agreement.pdf")

    g4_po_amended_source(BUILD / "g4_src.pdf")
    scan(BUILD / "g4_src.pdf", OUT / "G4_po_amended_scan.pdf",
         dpi=200, blur=0.9, noise=4, jpeg=40, rotate=0.7)

    g5_receipt(OUT / "G5_receipt_office_supply.pdf")

    g6_receipt_thermal_source(BUILD / "g6_src.pdf")
    scan(BUILD / "g6_src.pdf", OUT / "G6_receipt_thermal_scan.pdf",
         dpi=200, blur=1.0, noise=6, jpeg=35, rotate=1.1)

    g7_vendor_terms(OUT / "G7_supplier_terms_horizontal.xlsx")
    g8_safety_datasheet(OUT / "G8_safety_data_sheet.pdf")

    for p in sorted(OUT.iterdir()):
        print(f"  wrote {p.name}")


if __name__ == "__main__":
    main()
