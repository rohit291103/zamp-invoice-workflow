"""Generate the test invoice set.

Each vendor gets a visually different layout, because "handles messy vendor
formats" is only a real claim if the test set is actually messy. One invoice is
rendered as a rotated, noisy page image with no text layer at all.

    python scripts/generate_invoices.py
"""
from __future__ import annotations

import io
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "data" / "invoices"
W, H = A4
random.seed(7)


def _c(path: Path) -> canvas.Canvas:
    OUT.mkdir(parents=True, exist_ok=True)
    return canvas.Canvas(str(path), pagesize=A4)


def _money(v: float) -> str:
    return f"{v:,.2f}"


# --------------------------------------------------------------------------
# Layout A - clean US style with a ruled table. (Kestrel)
# --------------------------------------------------------------------------
def layout_kestrel(path: Path, invoice_no: str, date: str, po: str | None,
                   items: list[tuple[str, float, float]], tax_rate: float,
                   banner: str | None = None) -> None:
    c = _c(path)
    y = H - 30 * mm
    c.setFont("Helvetica-Bold", 20)
    c.drawString(20 * mm, y, "Kestrel Office Supplies LLC")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y - 6 * mm, "1120 Marlin Avenue, Suite 300, Austin TX 78701")
    c.drawString(20 * mm, y - 10 * mm, "EIN 84-2915530   ·   ap@kestrelsupplies.example")

    if banner:
        c.setFont("Helvetica-Bold", 11)
        c.drawRightString(W - 20 * mm, y, banner)

    y -= 22 * mm
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20 * mm, y, "INVOICE")
    c.setFont("Helvetica", 10)
    y -= 8 * mm
    c.drawString(20 * mm, y, f"Invoice #: {invoice_no}")
    c.drawString(90 * mm, y, f"Invoice Date: {date}")
    y -= 5 * mm
    if po:
        c.drawString(20 * mm, y, f"PO Number: {po}")
    c.drawString(90 * mm, y, "Terms: Net 15")

    y -= 12 * mm
    c.setFillColorRGB(.93, .93, .93)
    c.rect(20 * mm, y - 2 * mm, W - 40 * mm, 7 * mm, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 9)
    for x, t in ((22, "Description"), (110, "Qty"), (130, "Unit Price"), (165, "Amount")):
        c.drawString(x * mm, y, t)

    c.setFont("Helvetica", 9)
    subtotal = 0.0
    for desc, qty, unit in items:
        y -= 6 * mm
        amt = round(qty * unit, 2)
        subtotal += amt
        c.drawString(22 * mm, y, desc)
        c.drawRightString(120 * mm, y, f"{qty:g}")
        c.drawRightString(155 * mm, y, _money(unit))
        c.drawRightString(188 * mm, y, _money(amt))

    tax = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax, 2)
    y -= 10 * mm
    c.line(120 * mm, y + 3 * mm, 188 * mm, y + 3 * mm)
    for label, val, bold in (("Subtotal", subtotal, False),
                             (f"Sales Tax ({tax_rate*100:.2f}%)", tax, False),
                             ("Total Due (USD)", total, True)):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 9)
        c.drawRightString(160 * mm, y, label)
        c.drawRightString(188 * mm, y, _money(val))
        y -= 6 * mm

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(20 * mm, 20 * mm, "Remit to Kestrel Office Supplies LLC · Routing 114000093")
    c.save()
    return total


# --------------------------------------------------------------------------
# Layout B - European, VAT shown separately, EUR. (Northwind) Rendered to an
# image so the resulting PDF has no text layer.
# --------------------------------------------------------------------------
def layout_northwind_scanned(path: Path, invoice_no: str, date: str, po: str,
                             net: float, vat_rate: float) -> float:
    font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
    bold_path = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

    def f(size: int, bold: bool = False):
        p = bold_path if (bold and Path(bold_path).exists()) else font_path
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            return ImageFont.load_default()

    img = Image.new("RGB", (1654, 2339), "white")
    d = ImageDraw.Draw(img)
    vat = round(net * vat_rate, 2)
    total = round(net + vat, 2)

    d.text((120, 140), "NORTHWIND LOGISTICS BV", font=f(56, True), fill=(15, 15, 15))
    d.text((120, 215), "Waalhaven Oostzijde 81, 3087 BM Rotterdam", font=f(30), fill=(40, 40, 40))
    d.text((120, 258), "VAT NL812345678B01   KvK 24356789", font=f(30), fill=(40, 40, 40))
    d.line((120, 320, 1530, 320), fill=(120, 120, 120), width=3)

    d.text((120, 380), "FACTUUR / INVOICE", font=f(44, True), fill=(15, 15, 15))
    rows = [("Factuurnummer / Invoice no.", invoice_no),
            ("Factuurdatum / Date", date),
            ("Uw referentie / Your ref.", po),
            ("Betaaltermijn / Terms", "45 dagen netto")]
    y = 470
    for k, v in rows:
        d.text((120, y), k, font=f(30), fill=(70, 70, 70))
        d.text((820, y), v, font=f(30, True), fill=(15, 15, 15))
        y += 52

    y += 40
    d.rectangle((120, y, 1530, y + 60), fill=(238, 238, 238))
    d.text((140, y + 14), "Omschrijving / Description", font=f(30, True), fill=(20, 20, 20))
    d.text((1250, y + 14), "Bedrag EUR", font=f(30, True), fill=(20, 20, 20))
    y += 90
    for desc, amt in (("Zeevracht Rotterdam - Mumbai, week 27-30", net * 0.72),
                      ("Terminal handling & douane-inklaring", net * 0.28)):
        d.text((140, y), desc, font=f(29), fill=(30, 30, 30))
        d.text((1290, y), f"{amt:,.2f}", font=f(29), fill=(30, 30, 30))
        y += 54

    y += 40
    d.line((900, y, 1530, y), fill=(120, 120, 120), width=2)
    y += 20
    for label, val, bold in (("Netto / Net", net, False),
                             (f"BTW / VAT {vat_rate*100:.0f}%", vat, False),
                             ("TOTAAL TE BETALEN / TOTAL DUE", total, True)):
        d.text((900, y), label, font=f(32, bold), fill=(15, 15, 15))
        d.text((1290, y), f"EUR {val:,.2f}", font=f(32, bold), fill=(15, 15, 15))
        y += 56

    d.text((120, 2150), "IBAN NL91ABNA0417164300  ·  BIC ABNANL2A", font=f(26), fill=(70, 70, 70))

    # Make it look like it came off a flatbed: slight skew, blur, grain, grey.
    img = img.rotate(-0.55, resample=Image.BICUBIC, expand=False, fillcolor=(255, 255, 255))
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    px = img.load()
    for _ in range(90000):
        x, yy = random.randrange(img.width), random.randrange(img.height)
        v = random.randint(-28, 12)
        r, g, b = px[x, yy]
        px[x, yy] = (max(0, min(255, r + v)), max(0, min(255, g + v)), max(0, min(255, b + v)))
    img = img.convert("L").convert("RGB")
    img.save(path, "PDF", resolution=200.0)
    return total


# --------------------------------------------------------------------------
# Layout C - terse, PO buried in a free-text reference line, bundled item.
# (Vertex Packaging)
# --------------------------------------------------------------------------
def layout_vertex(path: Path, invoice_no: str, date: str, po: str,
                  total: float, release_note: str) -> float:
    c = _c(path)
    y = H - 28 * mm
    c.setFont("Courier-Bold", 16)
    c.drawString(20 * mm, y, "VERTEX PACKAGING CO")
    c.setFont("Courier", 9)
    for line in ("4400 Industrial Parkway, Toledo OH 43612",
                 "Tax ID 77-3320981   billing@vertexpkg.example"):
        y -= 5 * mm
        c.drawString(20 * mm, y, line)

    y -= 14 * mm
    c.setFont("Courier-Bold", 12)
    c.drawString(20 * mm, y, f"INVOICE {invoice_no}")
    c.setFont("Courier", 10)
    y -= 6 * mm
    c.drawString(20 * mm, y, f"Dated {date}")
    y -= 6 * mm
    # PO reference written as free text, not a labelled field.
    c.drawString(20 * mm, y, f"Ref: {po} / {release_note}")

    y -= 14 * mm
    c.setFont("Courier", 10)
    c.drawString(20 * mm, y, "Corrugated shipping boxes, assorted sizes")
    y -= 5 * mm
    c.drawString(20 * mm, y, "(quantities and unit pricing per master agreement)")

    y -= 16 * mm
    c.setFont("Courier-Bold", 13)
    # Tax is embedded, not broken out - deliberately.
    c.drawString(20 * mm, y, f"AMOUNT PAYABLE  USD {_money(total)}")
    c.setFont("Courier", 8)
    y -= 6 * mm
    c.drawString(20 * mm, y, "Amount is tax-inclusive. Net 30 from invoice date.")
    c.save()
    return total


# --------------------------------------------------------------------------
# Layout D - dense industrial, GST embedded in the rate. (Acme Steel)
# --------------------------------------------------------------------------
def layout_acme(path: Path, invoice_no: str | None, date: str, po: str | None,
                tonnes: float, rate: float) -> float:
    c = _c(path)
    total = round(tonnes * rate, 2)
    y = H - 25 * mm
    c.setFont("Helvetica-Bold", 17)
    c.drawString(18 * mm, y, "ACME STEEL WORKS PVT. LTD.")
    c.setFont("Helvetica", 8)
    for line in ("Plot 42, MIDC Industrial Area, Taloja, Navi Mumbai 410208, India",
                 "GSTIN 29AABCA1234F1Z5   ·   exports@acmesteel.example   ·   +91 22 4455 9900"):
        y -= 4.5 * mm
        c.drawString(18 * mm, y, line)

    y -= 10 * mm
    c.setLineWidth(1.2)
    c.line(18 * mm, y, W - 18 * mm, y)
    y -= 8 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(18 * mm, y, "TAX INVOICE (EXPORT)")

    y -= 8 * mm
    c.setFont("Helvetica", 9)
    if invoice_no:
        c.drawString(18 * mm, y, f"Invoice No.: {invoice_no}")
    else:
        # The realistic failure: the field is on the form but never filled in.
        c.drawString(18 * mm, y, "Invoice No.: ____________")
    c.drawString(105 * mm, y, f"Dated: {date}")
    y -= 5 * mm
    c.drawString(18 * mm, y, f"Buyer's Order Ref.: {po}" if po
                 else "Buyer's Order Ref.: as per contract")
    c.drawString(105 * mm, y, "Despatch: Nhava Sheva → Houston")

    y -= 12 * mm
    c.setFont("Helvetica-Bold", 8)
    for x, t in ((19, "Sl"), (28, "Description of Goods"), (105, "HSN"),
                 (125, "Qty (MT)"), (150, "Rate/MT"), (175, "Amount USD")):
        c.drawString(x * mm, y, t)
    c.line(18 * mm, y - 1.5 * mm, W - 18 * mm, y - 1.5 * mm)

    c.setFont("Helvetica", 8)
    y -= 7 * mm
    c.drawString(19 * mm, y, "1")
    c.drawString(28 * mm, y, "Hot-rolled steel plates, 12mm, IS 2062 E250")
    c.drawString(105 * mm, y, "7208.51")
    c.drawRightString(145 * mm, y, f"{tonnes:g}")
    c.drawRightString(170 * mm, y, _money(rate))
    c.drawRightString(192 * mm, y, _money(total))

    y -= 10 * mm
    c.setFont("Helvetica-Oblique", 7.5)
    c.drawString(28 * mm, y, "Rate is inclusive of IGST at 0% (export under LUT). No separate tax component.")

    y -= 12 * mm
    c.line(120 * mm, y + 4 * mm, 192 * mm, y + 4 * mm)
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(170 * mm, y, "TOTAL")
    c.drawRightString(192 * mm, y, f"USD {_money(total)}")

    y -= 10 * mm
    c.setFont("Helvetica", 8)
    c.drawString(18 * mm, y, f"Amount in words: US Dollars {total:,.0f} only.")
    c.save()
    return total


# --------------------------------------------------------------------------
# Layout E - modern SaaS-ish. (Halcyon / Brightline)
# --------------------------------------------------------------------------
def layout_modern(path: Path, company: str, addr: str, taxid: str,
                  invoice_no: str, date: str, po: str, lines: list[tuple[str, float]],
                  currency: str = "USD") -> float:
    c = _c(path)
    total = round(sum(a for _, a in lines), 2)
    c.setFillColorRGB(.11, .13, .18)
    c.rect(0, H - 42 * mm, W, 42 * mm, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 19)
    c.drawString(20 * mm, H - 22 * mm, company)
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, H - 29 * mm, addr)
    c.drawString(20 * mm, H - 34 * mm, f"Tax ID {taxid}")
    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(W - 20 * mm, H - 24 * mm, "INVOICE")

    c.setFillColorRGB(0, 0, 0)
    y = H - 58 * mm
    c.setFont("Helvetica", 10)
    for label, val in (("Invoice number", invoice_no), ("Issued", date),
                       ("Purchase order", po), ("Payment terms", "Net 30")):
        c.setFillColorRGB(.4, .4, .45)
        c.drawString(20 * mm, y, label)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(65 * mm, y, val)
        y -= 6 * mm

    y -= 8 * mm
    c.setFont("Helvetica-Bold", 9)
    c.setFillColorRGB(.4, .4, .45)
    c.drawString(20 * mm, y, "DESCRIPTION")
    c.drawRightString(W - 20 * mm, y, f"AMOUNT ({currency})")
    c.setFillColorRGB(0, 0, 0)
    c.setLineWidth(.6)
    y -= 2 * mm
    c.line(20 * mm, y, W - 20 * mm, y)
    c.setFont("Helvetica", 10)
    for desc, amt in lines:
        y -= 7 * mm
        c.drawString(20 * mm, y, desc)
        c.drawRightString(W - 20 * mm, y, _money(amt))

    y -= 12 * mm
    c.setFont("Helvetica-Bold", 13)
    c.drawString(20 * mm, y, "Total due")
    c.drawRightString(W - 20 * mm, y, f"{currency} {_money(total)}")
    c.save()
    return total


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    made: list[tuple[str, str, float]] = []

    # 01 - happy path. Exact PO match, approved vendor, clean text PDF.
    t = layout_kestrel(OUT / "01_happy_path.pdf", "INV-KS-8841", "2026-08-28", "PO-4403",
                       [("Copy paper A4 80gsm, 10-ream carton", 40, 42.50),
                        ("Whiteboard markers, box of 12", 25, 18.00),
                        ("Nitrile gloves, box of 100", 30, 24.00),
                        ("Desk organiser, matte black", 14, 20.00)],
                       tax_rate=0.0)
    made.append(("01_happy_path.pdf", "Happy path - exact PO match", t))

    # 02 - EDGE 1. Scan with no text layer, forces the vision path.
    t = layout_northwind_scanned(OUT / "02_edge_scanned_freight.pdf", "NW-3310",
                                 "2026-08-22", "PO-4402", net=10247.93, vat_rate=0.21)
    made.append(("02_edge_scanned_freight.pdf", "EDGE 1 - scanned image, no text layer", t))

    # 03 - EDGE 2. Three releases against one partial-billing PO of 27,000.
    for name, no, date, amt, note in (
            ("03a_split_release_1.pdf", "INV-VP-771", "2026-07-08", 12000.00, "release 1 of 3"),
            ("03b_split_release_2.pdf", "INV-VP-782", "2026-08-05", 10500.00, "release 2 of 3"),
            ("03c_split_release_3.pdf", "INV-VP-795", "2026-09-02", 6200.00, "release 3 of 3")):
        t = layout_vertex(OUT / name, no, date, "PO-4404", amt, note)
        made.append((name, f"EDGE 2 - split billing {note}", t))

    # 04 - EDGE 3. Same invoice number and amount as 01, re-issued on a
    # different layout so the file hash will not catch it.
    t = layout_modern(OUT / "04_edge_duplicate_resubmit.pdf",
                      "Kestrel Office Supplies LLC",
                      "1120 Marlin Avenue, Suite 300, Austin TX 78701", "84-2915530",
                      "INV-KS-8841", "2026-09-04", "PO-4403",
                      [("Office consumables - HQ replenishment (statement copy)", 3150.00)])
    made.append(("04_edge_duplicate_resubmit.pdf", "EDGE 3 - duplicate under a new layout", t))

    # 05 - EDGE 4. No PO reference and the invoice number was never filled in.
    t = layout_acme(OUT / "05_edge_no_po_no_number.pdf", None, "2026-08-30", None,
                    tonnes=40, rate=1215.00)
    made.append(("05_edge_no_po_no_number.pdf", "EDGE 4 - no PO ref, no invoice number", t))

    # 06 - blocked vendor.
    t = layout_modern(OUT / "06_blocked_vendor.pdf", "Halcyon IT Services",
                      "77 Beacon Street, Boston MA 02108", "61-4409922",
                      "HAL-5510", "2026-08-19", "PO-4407",
                      [("Engineering laptops, 20 units @ 1,940.00", 38800.00),
                       ("3-year onsite warranty, 20 units", 2400.00)])
    made.append(("06_blocked_vendor.pdf", "Rule demo - blocked vendor", t))

    # 07 - minor overage, inside the 2% tolerance band.
    t = layout_acme(OUT / "07_tolerance_minor_overage.pdf", "ASW-2026-0442", "2026-09-01",
                    "PO-4408", tonnes=5, rate=1304.00)
    made.append(("07_tolerance_minor_overage.pdf", "Rule demo - within tolerance", t))
    # 6,520 against a 6,400 PO: +120, inside the 128 tolerance band (2% of 6,400).

    # 08 - vendor on hold.
    t = layout_modern(OUT / "08_vendor_on_hold.pdf", "Brightline Consulting Group",
                      "210 Wacker Drive, Chicago IL 60606", "45-7781203",
                      "BL-220", "2026-08-15", "PO-4405",
                      [("Advisory retainer - August 2026", 15000.00)])
    made.append(("08_vendor_on_hold.pdf", "Rule demo - vendor on hold", t))

    # 09 - EDGE 5. Same PO and same amount as the happy path, but a brand new
    # invoice number and date. Duplicate detection cannot see it (the number is
    # genuinely new) and the amount is exactly on the PO, so tolerance is happy.
    # Only a PO-level check catches it.
    t = layout_kestrel(OUT / "09_edge_same_po_new_number.pdf", "INV-KS-8907", "2026-09-05",
                       "PO-4403",
                       [("Office consumables - HQ replenishment", 1, 2870.00),
                        ("Delivery and handling", 1, 280.00)],
                       tax_rate=0.0)
    made.append(("09_edge_same_po_new_number.pdf", "EDGE 5 - same PO, new invoice number", t))

    print(f"Wrote {len(made)} invoices to {OUT}\n")
    for name, desc, total in made:
        print(f"  {name:38s} {total:>12,.2f}   {desc}")


if __name__ == "__main__":
    main()
