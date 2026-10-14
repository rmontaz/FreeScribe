"""
Generates the PDF forms used by the form-filler tests.

Run from the repository root (needs reportlab, a development-only dependency):

    python tests/fixtures/make_forms.py

The generated PDFs are committed, so the tests themselves only need pypdf.

This software is released under the AGPL-3.0 license.
"""

import os

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))


def make_fillable(path):
    c = canvas.Canvas(path, pagesize=letter)
    c.setTitle("Work Absence Certificate")
    form = c.acroForm
    width, height = letter

    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 72, "Work Absence Certificate")
    c.setFont("Helvetica", 10)

    y = height - 120

    def label(text, x=72, dy=0):
        c.drawString(x, y + 4 + dy, text)

    # Text fields whose labels sit to the left. Two of them have cryptic field names, so the
    # label has to come from the page text.
    for text, name, tooltip in [
        ("Patient name:", "Text1", None),
        ("Date of birth:", "Text2", None),
        ("Health card number:", "hcn", "Ontario health card number (10 digits)"),
        ("Phone:", "patient_phone", None),
        ("Date of visit:", "visit_date", None),
    ]:
        label(text)
        kwargs = {"tooltip": tooltip} if tooltip else {}
        form.textfield(name=name, x=200, y=y, width=300, height=18, borderWidth=1, **kwargs)
        y -= 32

    # Label above the field
    label("Diagnosis / reason for absence")
    y -= 24
    form.textfield(name="topmostSubform[0].Page1[0].f1_06[0]", x=72, y=y, width=428, height=18, borderWidth=1)
    y -= 36

    label("Unable to work from:")
    form.textfield(name="absence_from", x=200, y=y, width=110, height=18, borderWidth=1)
    c.drawString(320, y + 4, "to:")
    form.textfield(name="absence_to", x=345, y=y, width=110, height=18, borderWidth=1)
    y -= 32

    form.checkbox(name="fit_to_return", x=72, y=y, size=14, buttonStyle="check", checked=False)
    c.drawString(92, y + 3, "Fit to return to full duties")
    y -= 32

    label("Follow-up required:")
    form.radio(name="followup", value="Yes", x=200, y=y, size=14, selected=False)
    c.drawString(218, y + 3, "Yes")
    form.radio(name="followup", value="No", x=260, y=y, size=14, selected=False)
    c.drawString(278, y + 3, "No")
    y -= 32

    label("Restrictions:")
    form.choice(name="restrictions", x=200, y=y, width=200, height=18,
                options=["None", "Light duties", "No lifting over 10 kg", "Work from home"],
                value="None")
    y -= 40

    label("Clinician comments")
    y -= 64
    form.textfield(name="comments", x=72, y=y, width=428, height=56, borderWidth=1,
                   fieldFlags="multiline")
    y -= 36

    label("Clinician signature:")
    form.textfield(name="signature", x=200, y=y, width=300, height=18, borderWidth=1)

    c.showPage()
    c.save()


def make_flat(path):
    c = canvas.Canvas(path, pagesize=letter)
    c.drawString(72, 700, "Patient name: ____________________")
    c.showPage()
    c.save()


def make_flat_intake(path):
    """A printed-style form with no form fields: underscores, drawn lines, boxes and tick boxes."""
    c = canvas.Canvas(path, pagesize=letter)
    c.setTitle("Patient Intake Form")
    width, height = letter
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 72, "Patient Intake Form")
    c.setFont("Helvetica", 10)
    c.setLineWidth(0.75)

    y = height - 120
    c.drawString(72, y, "Patient name: ______________________________________")          # underscores
    y -= 30
    c.drawString(72, y, "Date of birth: _______________     Phone: ____________________")  # two on a line
    y -= 30
    c.drawString(72, y, "Health card number:")                                          # drawn line
    c.line(190, y - 2, 500, y - 2)
    y -= 34

    c.drawString(72, y, "Allergies:")                                                   # tick boxes
    for x, text in [(150, "None known"), (250, "Penicillin"), (340, "Other")]:
        c.rect(x, y - 1, 9, 9)
        c.drawString(x + 14, y, text)
    y -= 34

    c.drawString(72, y, "Reason for visit")                                             # label above a box
    c.rect(72, y - 62, 428, 54)
    y -= 92

    c.drawString(72, y, "Current medications")                                          # label inside a box
    c.rect(66, y - 50, 440, 64)
    y -= 90

    c.drawString(72, y, "Off work from ____________ to ____________")                   # a date range
    y -= 50

    c.line(72, y, 300, y)                                                               # signature line
    c.drawString(72, y - 12, "Clinician signature")
    c.drawString(330, y + 2, "Date: ______________")
    c.showPage()
    c.save()


def make_scanned(path):
    """A page that is only a picture, like a scanned paper form."""
    from io import BytesIO
    from PIL import Image, ImageDraw
    from reportlab.lib.utils import ImageReader

    image = Image.new("RGB", (850, 1100), "white")
    draw = ImageDraw.Draw(image)
    draw.text((100, 100), "Patient name: ____________________", fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    c = canvas.Canvas(path, pagesize=letter)
    c.drawImage(ImageReader(buffer), 0, 0, width=letter[0], height=letter[1])
    c.showPage()
    c.save()


def make_checkbox_heavy(path):
    """
    A requisition-style flat form: a grid of tick boxes, tick-box characters,
    radio circles, boxes drawn as four lines, and rows of character boxes.
    """
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 60, "Laboratory Requisition")
    c.setFont("Helvetica", 9)
    c.setLineWidth(0.6)

    # Health card number: ten character boxes, then a two-box version code
    y = height - 95
    c.drawString(72, y + 3, "Health card number")
    for i in range(10):
        c.rect(170 + i * 13, y, 13, 15)
    c.drawString(310, y + 3, "Version")
    for i in range(2):
        c.rect(345 + i * 13, y, 13, 15)

    # Date of birth as YYYY MM DD box groups
    y -= 28
    c.drawString(72, y + 3, "Date of birth")
    for start, count in ((170, 4), (230, 2), (264, 2)):
        for i in range(count):
            c.rect(start + i * 12, y, 12, 15)

    # Radio circles
    y -= 28
    c.drawString(72, y + 1, "Sex:")
    for x, text in ((110, "Male"), (160, "Female")):
        c.circle(x + 4, y + 4, 4)
        c.drawString(x + 12, y + 1, text)

    # Tick-box characters in the ZapfDingbats font
    y -= 26
    c.drawString(72, y, "Urgent:")
    for x, text in ((120, "Yes"), (165, "No")):
        c.setFont("ZapfDingbats", 9)
        c.drawString(x, y, "o")
        c.setFont("Helvetica", 9)
        c.drawString(x + 11, y, text)

    # Boxes drawn as four separate lines
    y -= 26
    c.drawString(72, y + 1, "Fasting:")
    for x, text in ((125, "Yes"), (175, "No")):
        c.line(x, y, x + 9, y)
        c.line(x, y + 9, x + 9, y + 9)
        c.line(x, y, x, y + 9)
        c.line(x + 9, y, x + 9, y + 9)
        c.drawString(x + 13, y + 1, text)

    # Three columns of tests with drawn squares
    y -= 34
    c.setFont("Helvetica-Bold", 10)
    c.drawString(72, y, "Tests requested")
    c.setFont("Helvetica", 9)
    tests = ["Glucose", "HbA1c", "Creatinine", "Sodium", "Potassium", "ALT", "Albumin", "TSH", "Free T4",
             "CBC", "Ferritin", "B12", "Vitamin D", "Lipid profile", "Urinalysis"]
    for index, name in enumerate(tests):
        column, row = index % 3, index // 3
        x, ty = 72 + column * 160, y - 20 - row * 18
        c.rect(x, ty - 1, 8, 8)
        c.drawString(x + 12, ty, name)

    # A text line after the grid
    y -= 20 + 5 * 18 + 14
    c.drawString(72, y, "Other tests: ____________________________________________")
    c.showPage()
    c.save()


if __name__ == "__main__":
    make_fillable(os.path.join(HERE, "work_absence_form.pdf"))
    make_flat(os.path.join(HERE, "flat_form.pdf"))
    make_flat_intake(os.path.join(HERE, "flat_intake_form.pdf"))
    make_scanned(os.path.join(HERE, "scanned_form.pdf"))
    make_checkbox_heavy(os.path.join(HERE, "flat_checkbox_form.pdf"))
    print("written")
