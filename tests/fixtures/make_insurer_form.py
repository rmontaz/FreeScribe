"""
Generates insurer_form.pdf: a small disability form with the layouts that real
insurer and government forms use and that FreeScribe once misread.

- Yes / No buttons whose printed neighbours are misleading ("•", "Date expected")
- buttons stored as 0, 1, 2, 3 whose words are drawn inside an embedded drawing
  (a Form XObject), as on the CPP disability medical report
- a rating grid where one row stores its first option under the row's own name
- tick boxes that share a question and store what they mean ("Workplace Issues")
- two tick boxes that are one question (Male / Female), as on Sun Life's form
- a table with numbered rows, clinician details and identifiers

Run from the repository root (needs reportlab, a development-only dependency):

    python tests/fixtures/make_insurer_form.py

This software is released under the AGPL-3.0 license.
"""

import os

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "insurer_form.pdf")


def draw(path):
    c = canvas.Canvas(path, pagesize=letter)
    form = c.acroForm
    _, height = letter
    c.setFont("Helvetica", 10)
    y = height - 72

    def text_field(label, name, width=250, tooltip=None):
        nonlocal y
        c.drawString(72, y + 4, label)
        kwargs = {"tooltip": tooltip} if tooltip else {}
        form.textfield(name=name, x=330, y=y, width=200, height=18, borderWidth=1, **kwargs)
        y -= 28

    text_field("Patient name:", "patient_name")
    text_field("Date of birth (dd/mm/yyyy):", "dob")
    text_field("Group plan number:", "group_no")
    text_field("Date last worked (dd/mm/yyyy):", "last_worked")

    # Yes / No stored as such, with misleading printed neighbours
    c.drawString(72, y + 3, "Treated for this before?")
    form.radio(name="treated_before", value="Yes", x=260, y=y, size=12, selected=False)
    c.drawString(276, y + 3, "•")
    form.radio(name="treated_before", value="No", x=320, y=y, size=12, selected=False)
    c.drawString(336, y + 3, "Date expected")
    y -= 28

    # Options stored as 0..3, words drawn inside a Form XObject
    c.drawString(72, y + 3, "Condition is likely to:")
    # The drawing is placed with a transform, so its words only land beside the
    # buttons once that transform is applied
    c.beginForm("prognosis_words")
    for x, word in [(276, "improve"), (356, "deteriorate"), (446, "remain the same")]:
        c.drawString(x, 3, word)
    c.endForm()
    c.saveState()
    c.translate(0, y)
    c.doForm("prognosis_words")
    c.restoreState()
    for i, x in enumerate((260, 340, 430)):
        form.radio(name="prognosis", value=str(i), x=x, y=y, size=12, selected=False)
    y -= 36

    # Rating grid; the "Memory" row stores "No impact" as "Memory"
    for x, word in [(260, "No impact"), (340, "Mild"), (420, "Severe")]:
        c.drawString(x, y + 3, word)
    y -= 22
    for row in ("Energy", "Mood", "Memory"):
        c.drawString(72, y + 3, row)
        for x, value in [(260, "No impact"), (340, "Mild"), (420, "Severe")]:
            stored = row if (row == "Memory" and value == "No impact") else value
            form.radio(name=f"grid_{row.lower()}", value=stored, x=x, y=y, size=12, selected=False)
        y -= 22
    y -= 10

    # Tick boxes that share a question (their states are renamed below)
    c.drawString(72, y + 3, "Contributing factors:")
    for i, (x, word) in enumerate([(260, "Workplace Issues"), (400, "Alcohol / Drug Abuse")]):
        form.checkbox(name=f"factor_{i}", x=x, y=y, size=12, checked=False,
                      tooltip="Please indicate all factors that may complicate recovery:")
        c.drawString(x + 16, y + 3, word)
    y -= 28

    # Two tick boxes that are one question (merged below)
    c.drawString(72, y + 3, "Sex:")
    for i, (x, word) in enumerate([(260, "Male"), (340, "Female")]):
        form.checkbox(name=f"sex_{i}", x=x, y=y, size=12, checked=False)
        c.drawString(x + 16, y + 3, word)
    y -= 34

    # A table with numbered rows
    for row in (1, 2, 3):
        c.drawString(72, y + 4, f"Medication {row}:")
        form.textfield(name=f"med_name_{row}", x=160, y=y, width=150, height=18, borderWidth=1,
                       tooltip=f"Medication Name ({row})")
        form.textfield(name=f"med_start_{row}", x=320, y=y, width=180, height=18, borderWidth=1,
                       tooltip=f"Initial dosage and date started (dd/mm/yyyy) ({row})")
        y -= 26
    y -= 10

    text_field("Attending physician (please print):", "physician_name")
    text_field("Address (street, city, province, postal code):", "physician_address")
    text_field("Telephone number:", "physician_phone")
    text_field("Fax number:", "physician_fax")
    text_field("Date signed (dd/mm/yyyy):", "date_signed")
    c.save()


def rename_states_and_merge(path):
    reader = PdfReader(path)
    writer = PdfWriter(clone_from=reader)
    root_fields = writer._root_object["/AcroForm"]["/Fields"]

    def widget(name):
        for ref in root_fields:
            node = ref.get_object()
            if node.get("/T") == name:
                return ref, node
        raise KeyError(name)

    def rename(node, new_state):
        normal = node["/AP"]["/N"]
        for key in list(normal.keys()):
            if key != "/Off":
                normal[NameObject("/" + new_state)] = normal[key]
                del normal[key]
        down = node["/AP"].get("/D")
        if down is not None:
            for key in list(down.keys()):
                if key != "/Off":
                    down[NameObject("/" + new_state)] = down[key]
                    del down[key]

    rename(widget("factor_0")[1], "Workplace Issues")
    rename(widget("factor_1")[1], "Alcohol / Drug Abuse")

    # Male / Female: one checkbox field with two widgets
    parent = DictionaryObject({
        NameObject("/FT"): NameObject("/Btn"),
        NameObject("/T"): TextStringObject("sex"),
        NameObject("/V"): NameObject("/Off"),
    })
    parent_ref = writer._add_object(parent)
    kids = ArrayObject()
    for name, state in (("sex_0", "Male"), ("sex_1", "Female")):
        ref, node = widget(name)
        rename(node, state)
        del node["/T"]
        node.pop("/FT", None)
        node.pop("/V", None)
        node[NameObject("/Parent")] = parent_ref
        kids.append(ref)
        root_fields.remove(ref)
    parent[NameObject("/Kids")] = kids
    root_fields.append(parent_ref)

    with open(path, "wb") as handle:
        writer.write(handle)


if __name__ == "__main__":
    draw(PATH)
    rename_states_and_merge(PATH)
    print("wrote", PATH)
