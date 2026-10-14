"""Tests for the built-in note templates and how they merge with a user's own.

This software is released under the AGPL-3.0 license.
"""
import json

import pytest

pytest.importorskip("tkinter")

from UI.NoteStyleSelector import NoteStyleSelector  # noqa: E402


@pytest.fixture
def styles_file(tmp_path, monkeypatch):
    path = tmp_path / "note_styles.json"
    monkeypatch.setattr(NoteStyleSelector, "_styles_path", str(path))
    monkeypatch.setattr(NoteStyleSelector, "_styles_loaded", False, raising=False)
    monkeypatch.setattr(NoteStyleSelector, "style_data", {})
    monkeypatch.setattr(NoteStyleSelector, "style_options", [])
    monkeypatch.setattr(NoteStyleSelector, "current_style", NoteStyleSelector.DEFAULT_STYLE)
    return path


def test_first_run_offers_every_built_in_template(styles_file):
    names = NoteStyleSelector.template_names()
    assert names == list(NoteStyleSelector._get_builtin_styles())
    assert names[0] == "SOAP Note - Default"
    assert json.loads(styles_file.read_text(encoding="utf-8"))["current_style"] == "SOAP Note - Default"


def test_default_soap_prompt_is_unchanged(styles_file):
    default = NoteStyleSelector._get_default_styles()["SOAP Note - Default"]
    assert NoteStyleSelector._get_builtin_styles()["SOAP Note - Default"] == default
    assert default["pre_prompt"].startswith("AI, please transform the following conversation into a concise SOAP note.")


def test_every_template_forbids_invented_content(styles_file):
    for name, prompts in NoteStyleSelector._get_builtin_styles().items():
        if name == NoteStyleSelector.NO_NOTE_STYLE:
            continue      # sends nothing to the model
        assert "do not make assumptions" in prompts["post_prompt"].lower(), name
        assert prompts["pre_prompt"].rstrip().endswith("Here's the conversation:"), name


def test_existing_file_keeps_custom_templates_and_gains_the_built_ins(styles_file):
    custom = {"pre_prompt": "My pre", "post_prompt": "My post"}
    styles_file.write_text(json.dumps({
        "styles": {"SOAP Note - Default": NoteStyleSelector._get_default_styles()["SOAP Note - Default"],
                   "My Clinic Note": custom},
        "current_style": "My Clinic Note"}), encoding="utf-8")
    names = NoteStyleSelector.template_names()
    assert names[-1] == "My Clinic Note" and "Referral Letter" in names
    assert NoteStyleSelector.current_style == "My Clinic Note"
    assert NoteStyleSelector.style_data["My Clinic Note"] == custom
    assert not NoteStyleSelector.is_protected("My Clinic Note")
    assert NoteStyleSelector.is_protected("Referral Letter")


def test_user_template_sharing_a_built_in_name_stays_editable(styles_file):
    mine = {"pre_prompt": "My own referral wording", "post_prompt": ""}
    styles_file.write_text(json.dumps({"styles": {"Referral Letter": mine}, "current_style": "Referral Letter"}),
                           encoding="utf-8")
    NoteStyleSelector.ensure_loaded()
    assert NoteStyleSelector.style_data["Referral Letter"] == mine          # not overwritten
    assert not NoteStyleSelector.is_protected("Referral Letter")


def test_selection_falls_back_when_the_saved_template_is_gone(styles_file):
    styles_file.write_text(json.dumps({"styles": {}, "current_style": "Deleted Template"}), encoding="utf-8")
    NoteStyleSelector.ensure_loaded()
    assert NoteStyleSelector.current_style == "SOAP Note - Default"


def test_corrupt_file_falls_back_to_the_built_ins(styles_file):
    styles_file.write_text("{not json", encoding="utf-8")
    assert "SOAP Note - Default" in NoteStyleSelector.template_names()


def test_select_style(styles_file):
    assert NoteStyleSelector.select_style("Progress Note")
    assert json.loads(styles_file.read_text(encoding="utf-8"))["current_style"] == "Progress Note"
    assert not NoteStyleSelector.select_style("No Such Template")
    assert NoteStyleSelector.current_style == "Progress Note"


def test_no_note_generation_is_built_in_and_listed_last(styles_file):
    builtins = list(NoteStyleSelector._get_builtin_styles())
    assert builtins[0] == NoteStyleSelector.DEFAULT_STYLE
    assert builtins[-1] == NoteStyleSelector.NO_NOTE_STYLE
    assert NoteStyleSelector.is_protected(NoteStyleSelector.NO_NOTE_STYLE)


def test_default_template_is_saved_and_used_at_start(styles_file):
    NoteStyleSelector.ensure_loaded()
    assert NoteStyleSelector.set_default("Progress Note")
    NoteStyleSelector.select_style("Referral Letter")
    NoteStyleSelector._styles_loaded = False
    NoteStyleSelector.ensure_loaded()                       # next launch
    assert NoteStyleSelector.default_style == "Progress Note"
    assert NoteStyleSelector.current_style == "Progress Note"
    assert not NoteStyleSelector.set_default("No such template")


def test_older_files_keep_the_last_template_as_default(styles_file):
    import json
    with open(styles_file, "w") as f:
        json.dump({"styles": {}, "current_style": "History & Physical"}, f)
    NoteStyleSelector._styles_loaded = False
    NoteStyleSelector.ensure_loaded()
    assert NoteStyleSelector.default_style == NoteStyleSelector.current_style == "History & Physical"
