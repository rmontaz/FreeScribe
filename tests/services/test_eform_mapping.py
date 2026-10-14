"""
Tests for finding the fields of a PDF form for the OSCAR eForm Mapper.

This software is released under the AGPL-3.0 license.
"""
import base64
import os

import pytest

pytest.importorskip("pymupdf")

from services import eform_mapping as em  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


def payload(name, pages, **extra):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        data = base64.b64encode(handle.read()).decode()
    return {"pdf": "data:application/pdf;base64," + data, "pages": pages, **extra}


# Letter pages rendered 1500 px wide, as the mapper's converter does
LETTER = {"naturalW": 1500, "naturalH": 1941}
SCALE = 1500 / 612


def fields_by_label(result):
    return {f["label"]: f for f in result["fields"]}


class TestDetect:
    def test_checkbox_heavy_form(self):
        result = em.detect(payload("flat_checkbox_form.pdf", [{"page": 1, "pdfPage": 1, **LETTER}]))
        fields = fields_by_label(result)
        kinds = [f["type"] for f in result["fields"]]
        assert kinds.count("checkbox") == 21          # radio circles, ❏ glyphs, line boxes, the test grid
        assert result["summary"]["fields"] == 27
        card = fields["Health card number"]
        assert card["type"] == "text" and card["comb"] == 10
        assert card["oscarTag"] == "hin" and card["name"] == "health_card_number"
        assert fields["Date of birth"]["comb"] == 4 and fields["Date of birth (3)"]["comb"] == 2
        assert fields["Glucose"]["type"] == "checkbox" and fields["Glucose"]["oscarTag"] == ""
        assert {"Sex: Male", "Urgent: No", "Fasting: Yes"} <= set(fields)

    def test_positions_are_in_mapper_pixels(self):
        result = em.detect(payload("flat_checkbox_form.pdf", [{"page": 1, "pdfPage": 1, **LETTER}]))
        glucose = fields_by_label(result)["Glucose"]
        # The square is drawn at x=72pt, 8pt wide, top at 792-(y)… in PDF space
        assert glucose["x"] == pytest.approx(72 * SCALE, abs=1)
        assert glucose["width"] == pytest.approx(8 * SCALE, abs=1)
        assert glucose["height"] == pytest.approx(8 * SCALE, abs=1)
        assert 0 < glucose["y"] < 1941

    def test_pdf_page_is_matched_to_the_mapper_page(self):
        # The converter's single-page mode can load PDF page 1 into mapper page 3
        result = em.detect(payload("flat_intake_form.pdf", [{"page": 3, "pdfPage": 1, **LETTER}]))
        assert result["fields"] and {f["page"] for f in result["fields"]} == {3}

    def test_fillable_pdf_uses_its_own_fields(self):
        result = em.detect(payload("work_absence_form.pdf", [{"page": 1, "pdfPage": 1, **LETTER}]))
        fields = fields_by_label(result)
        assert len(result["fields"]) == 14          # 13 fields, the radio pair as two tick boxes
        assert fields["Patient name"]["oscarTag"] == "patient_name"
        assert fields["Clinician comments"]["type"] == "textarea"
        assert fields["Follow-up required: Yes"]["type"] == "checkbox"
        assert fields["Ontario health card number (10 digits)"]["oscarTag"] == "hin"

    def test_word_processor_form(self):
        result = em.detect(payload("flat_word_table_form.pdf", [{"page": 1, "pdfPage": 1, **LETTER}]))
        fields = fields_by_label(result)
        assert len(result["fields"]) == 15
        assert fields["Date of birth"]["oscarTag"] == "dobc2"
        assert fields["Signature"]["type"] == "signature"
        assert fields["Signature"]["oscarTag"] == "current_user_signature_image"
        assert fields["Reason for referral / goals of treatment"]["type"] == "textarea"

    def test_names_avoid_names_already_used(self):
        result = em.detect(payload("flat_intake_form.pdf", [{"page": 1, "pdfPage": 1, **LETTER}],
                                   takenNames=["patient_name"]))
        assert fields_by_label(result)["Patient name"]["name"] == "patient_name_2"

    def test_only_tags_from_the_mappers_list(self):
        result = em.detect(payload("flat_intake_form.pdf", [{"page": 1, "pdfPage": 1, **LETTER}],
                                   tagCatalog=["patient_name", "today"]))
        tags = {f["oscarTag"] for f in result["fields"]} - {""}
        assert tags <= {"patient_name", "today"} and "patient_name" in tags

    def test_scanned_pdf_is_read_with_ocr(self):
        result = em.detect(payload("scanned_form.pdf", [{"page": 1, "pdfPage": 1, **LETTER}]))
        assert result["summary"]["pagesReadWithOcr"] == 1
        field = fields_by_label(result)["Patient name"]
        assert field["oscarTag"] == "patient_name" and field["source"] == "rule"

    def test_scanned_pdf_without_ocr_data(self, monkeypatch):
        monkeypatch.setattr(em.scan_form, "available", lambda: False)
        with pytest.raises(em.MappingError, match="OCR data"):
            em.detect(payload("scanned_form.pdf", [{"page": 1, "pdfPage": 1, **LETTER}]))

    def test_bad_requests(self):
        with pytest.raises(em.MappingError, match="did not send"):
            em.detect({"pages": [{"page": 1, **LETTER}]})
        with pytest.raises(em.MappingError, match="could not be read"):
            em.detect({"pdf": base64.b64encode(b"not a pdf").decode(), "pages": [{"page": 1, **LETTER}]})
        with pytest.raises(em.MappingError, match="matched"):
            em.detect(payload("flat_intake_form.pdf", [{"page": 1, "pdfPage": 9, **LETTER}]))


class TestRotatedPages:
    def test_page_stored_sideways_matches_the_upright_image(self):
        """A page stored sideways with a rotation flag is shown upright; positions follow what is shown."""
        import pymupdf
        source = pymupdf.open(os.path.join(FIXTURES, "flat_checkbox_form.pdf"))
        doc = pymupdf.open()
        page = doc.new_page(width=792, height=612)
        page.show_pdf_page(page.rect, source, 0, rotate=90)
        page.set_rotation(90)
        data = base64.b64encode(doc.tobytes()).decode()

        rotated = fields_by_label(em.detect({"pdf": data, "pages": [{"page": 1, **LETTER}]}))
        upright = fields_by_label(em.detect(payload("flat_checkbox_form.pdf", [{"page": 1, **LETTER}])))
        assert set(rotated) == set(upright)
        for label in ("Glucose", "Health card number", "Sex: Female"):
            for key in ("x", "y", "width", "height"):
                assert rotated[label][key] == pytest.approx(upright[label][key], abs=2)


class TestTagRules:
    CATALOG = em._catalog_map(None)

    @pytest.mark.parametrize("label, field_type, tag", [
        ("Patient name", "text", "patient_name"), ("Last name", "text", "patient_nameL"),
        ("Given name(s)", "text", "patient_nameF"), ("DOB (yyyy/mm/dd)", "text", "dobc2"),
        ("Health card version code", "text", "hinversion"), ("Cell phone", "text", "cell"),
        ("Patient contact number", "text", "phone"), ("Postal code", "text", "postal"),
        ("Date signed", "date", "today"), ("Date of injury", "date", ""),
        ("Physician name", "text", "current_user"), ("CPSO number", "text", "current_user_cpsid"),
        ("Physician's OHIP billing number", "text", "current_user_ohip_no"),
        ("Patient signature", "signature", ""), ("Clinic fax", "text", "clinic_fax"),
        ("Employer name", "text", ""), ("Emergency contact name", "text", ""),
        ("Hospital name", "text", ""), ("Diagnosis", "text", ""), ("Allergies: Penicillin", "checkbox", ""),
    ])
    def test_rule_tag(self, label, field_type, tag):
        assert em.rule_tag(label, field_type, self.CATALOG) == tag

    def test_model_answers_are_checked(self):
        items = [{"id": 1, "label": "Insured's employer", "type": "text"},
                 {"id": 2, "label": "Preferred pharmacy phone", "type": "text"},
                 {"id": 3, "label": "Mother's maiden name", "type": "text"},
                 {"id": 4, "label": "Patient's preferred language", "type": "text"}]
        items.append({"id": 5, "label": "Preferred name on file", "type": "text"})
        prompts = []
        answers = {"Patient's preferred language": "made_up_tag", "Preferred name on file": "patient_name"}

        def fake_llm(prompt):
            prompts.append(prompt)
            import re
            numbered = re.findall(r'^(\d+)\. "([^"]+)"', prompt, flags=re.MULTILINE)
            return "{" + ", ".join(f'"{n}": "{answers.get(label, "")}"' for n, label in numbered) + "}"

        result = em.suggest_tags(items, None, fake_llm)
        # Fields about other people never reach the model; made-up tags are dropped
        assert result == {5: ("patient_name", "ai")}
        assert len(prompts) == 1 and "Tags you may use" in prompts[0]
        assert "Insured's employer" not in prompts[0] and "pharmacy phone" not in prompts[0]


def test_names():
    taken = set()
    assert em.name_from_label("Patient's last name", "text", taken) == "patient_s_last_name"
    assert em.name_from_label("Patient's last name", "text", taken) == "patient_s_last_name_2"
    assert em.name_from_label("", "checkbox", taken) == "checkbox"
    assert em.name_from_label("1st visit", "text") == "f_1st_visit"
    assert em.is_generic_name("text_7") and em.is_generic_name("checkbox_12") and not em.is_generic_name("dob")


def test_wrong_pdf_for_the_page_image_is_caught():
    landscape = {"naturalW": 1941, "naturalH": 1500}
    with pytest.raises(em.MappingError, match="not the same shape"):
        em.detect(payload("flat_intake_form.pdf", [{"page": 1, "pdfPage": 1, **landscape}]))


def test_checkboxes_get_no_tag():
    catalog = em._catalog_map(None)
    assert em.rule_tag("Sex: Male", "checkbox", catalog) == ""
    assert em.rule_tag("Sex", "text", catalog) == "sex"
