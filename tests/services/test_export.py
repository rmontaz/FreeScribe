"""Tests for exporting notes as text, Word and PDF.

This software is released under the AGPL-3.0 license.
"""
import re
import zipfile
from xml.dom import minidom

import pytest

from services import export

NOTE = """**Subjective:**
- Two weeks of cough — worse at night, 38.2°C
- Fever resolved; **none currently**

## Objective
Crackles at the right base. (Parentheses) and back\\slash.

Plan:
1. Chest X-ray today
2. Amoxicillin for 5 days
"""


class TestParseBlocks:
    def test_structure(self):
        kinds = [(kind, marker) for kind, _, marker in export.parse_blocks(NOTE)]
        assert kinds == [("heading", ""), ("bullet", "•"), ("bullet", "•"), ("blank", ""),
                         ("heading", ""), ("text", ""), ("blank", ""),
                         ("heading", ""), ("bullet", "1."), ("bullet", "2.")]

    def test_heading_text_is_cleaned(self):
        headings = [text for kind, text, _ in export.parse_blocks(NOTE) if kind == "heading"]
        assert headings == ["Subjective:", "Objective", "Plan:"]

    def test_sentence_with_a_colon_is_not_a_heading(self):
        assert export.parse_blocks("Medications: lisinopril 10 mg")[0][0] == "text"
        assert export.parse_blocks("Re: Mrs. Alvarez")[0][0] == "text"

    def test_blank_lines_collapse_and_trailing_blanks_go(self):
        assert [kind for kind, _, _ in export.parse_blocks("a\n\n\n\nb\n\n\n")] == ["text", "blank", "text"]

    def test_empty_input(self):
        assert export.parse_blocks("") == []
        assert export.parse_blocks(None) == []


class TestFilename:
    def test_illegal_characters_are_removed(self):
        name = export.default_filename('Jane: Doe/"x"', "SOAP Note")
        assert not re.search(r'[\\/:*?"<>|]', name)
        assert "Jane" in name and "SOAP Note" in name

    def test_untitled_session(self):
        assert export.default_filename("", "Note").startswith("Session - Note - ")


class TestText:
    def test_contents(self, tmp_path):
        path = tmp_path / "note.txt"
        export.export(str(path), NOTE, title="Jane — SOAP Note", subtitle="March 4, 2025")
        text = path.read_text(encoding="utf-8")
        assert text.startswith("Jane — SOAP Note\nMarch 4, 2025\n\n")
        assert "38.2°C" in text and text.endswith("2. Amoxicillin for 5 days\n")

    def test_unknown_extension_falls_back_to_text(self, tmp_path):
        path = tmp_path / "note.md"
        export.export(str(path), "hello")
        assert path.read_text(encoding="utf-8") == "hello\n"


class TestDocx:
    def test_is_a_well_formed_package(self, tmp_path):
        path = tmp_path / "note.docx"
        export.export(str(path), NOTE, title="Jane", subtitle="March 4, 2025")
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml",
                    "word/_rels/document.xml.rels"} <= names
            for name in names:
                minidom.parseString(archive.read(name))  # raises if any part is not valid XML
            body = archive.read("word/document.xml").decode("utf-8")
        assert "Two weeks of cough" in body and "38.2°C" in body
        assert 'w:val="Heading2"' in body and 'w:val="ListItem"' in body
        assert "**" not in body          # bold markers became real bold runs
        assert "<w:b/>" in body
        assert "1.  Chest X-ray today" in body

    def test_markup_and_control_characters_are_escaped(self, tmp_path):
        path = tmp_path / "note.docx"
        export.export(str(path), "BP < 120 & HR > 60 \x07 done", title="A & B")
        with zipfile.ZipFile(path) as archive:
            document = minidom.parseString(archive.read("word/document.xml"))
        text = "".join(node.firstChild.data for node in document.getElementsByTagName("w:t") if node.firstChild)
        assert "BP < 120 & HR > 60  done" in text and "A & B" in text


class TestPdf:
    def read(self, path):
        return path.read_bytes()

    def test_structure(self, tmp_path):
        path = tmp_path / "note.pdf"
        export.export(str(path), NOTE, title="Jane", subtitle="March 4, 2025")
        data = self.read(path)
        assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
        assert b"/Type /Catalog" in data and b"/BaseFont /Helvetica-Bold" in data
        assert b"Two weeks of cough" in data
        assert b"Page 1 of 1" in data

    def test_cross_reference_table_points_at_the_objects(self, tmp_path):
        path = tmp_path / "note.pdf"
        export.export(str(path), NOTE, title="Jane")
        data = self.read(path)
        start = int(re.search(rb"startxref\n(\d+)\n", data).group(1))
        assert data[start:start + 4] == b"xref"
        entries = re.findall(rb"(\d{10}) 00000 n ", data[start:])
        assert entries
        for number, offset in enumerate(entries, start=1):
            assert data[int(offset):].startswith(b"%d 0 obj" % number)

    def test_stream_lengths_are_exact(self, tmp_path):
        path = tmp_path / "note.pdf"
        export.export(str(path), NOTE)
        data = self.read(path)
        for match in re.finditer(rb"<< /Length (\d+) >>\nstream\n", data):
            length = int(match.group(1))
            assert data[match.end() + length:match.end() + length + 10] == b"\nendstream"

    def test_long_notes_flow_onto_more_pages(self, tmp_path):
        path = tmp_path / "long.pdf"
        export.export(str(path), "\n".join(f"Line {i} of a long note." for i in range(200)))
        data = self.read(path)
        pages = int(re.search(rb"/Count (\d+)", data).group(1))
        assert pages >= 4
        assert b"Page %d of %d" % (pages, pages) in data

    def test_special_characters_do_not_break_the_file(self, tmp_path):
        path = tmp_path / "special.pdf"
        export.export(str(path), "(a) \\ b) café — “quoted” 中文 " + "x" * 400)
        data = self.read(path)
        assert b"\\(a\\) \\\\ b\\)" in data                # delimiters escaped
        assert b"caf\\351" in data                          # WinAnsi octal escape
        assert data.rstrip().endswith(b"%%EOF")

    def test_lines_fit_the_page_width(self):
        spans = [("word " * 80, False)]
        for line in export._wrap_spans(spans, 11.0, 468.0):
            width = sum(export._pdf_width(fragment, 11.0, bold) for fragment, bold in line)
            assert width <= 468.0 + 0.01
