"""
Generates patient_form.pdf: a form with section headings, the patient's own
boxes, and boxes about other people (employer, witness, attending physician)
that must not get the patient's details.

Run from the repository root (needs reportlab, a development-only dependency):

    python tests/fixtures/make_patient_form.py

This software is released under the AGPL-3.0 license.
"""

import os

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "patient_form.pdf")


def draw(path):
    c = canvas.Canvas(path, pagesize=letter)
    form = c.acroForm
    _, height = letter
    y = height - 60

    def heading(text):
        nonlocal y
        c.setFont("Helvetica-Bold", 11)
        c.drawString(72, y, text)
        c.setFont("Helvetica", 9)
        y -= 26

    def text_field(label, name):
        nonlocal y
        c.drawString(72, y + 4, label)
        form.textfield(name=name, x=330, y=y, width=220, height=16, borderWidth=1)
        y -= 24

    heading("Section 1 - Patient information")
    text_field("Name (Last, First, Middle Initial)", "name")
    text_field("Date of birth (dd/mm/yyyy)", "dob")
    text_field("Health card number", "hin")
    text_field("Address (Street, City, Province, Postal Code)", "address")
    text_field("Home phone number", "phone")
    text_field("Employer's name", "employer")
    c.drawString(72, y + 3, "Sex:")
    for x, value in ((330, "M"), (400, "F")):
        form.radio(name="sex", value=value, x=x, y=y, size=12, selected=False)
        c.drawString(x + 16, y + 3, {"M": "Male", "F": "Female"}[value])
    y -= 30

    heading("2. Medications (please attach a separate list if needed)")
    text_field("Medication name", "med")
    text_field("3. If yes, please indicate when the patient will return", "return_when")

    heading("Section 4 - Witness")
    text_field("First name of witness", "witness_first")
    text_field("Telephone number", "witness_phone")

    heading("Section 5 - Attending physician")
    text_field("Name of attending physician", "physician")
    text_field("Telephone number", "physician_phone")
    c.save()


if __name__ == "__main__":
    draw(PATH)
    print("wrote", PATH)
