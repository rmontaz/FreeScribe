"""FreeScribe keeps the extension folder Chrome loads the same as its own copy."""
import json
import os

from services import extension_files as ef


def make(folder, version, files):
    os.makedirs(folder / "icons", exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps({"version": version}))
    for name, text in files.items():
        (folder / name).write_text(text)


def test_refresh_only_an_existing_folder_and_only_when_different(tmp_path):
    source, target = tmp_path / "app", tmp_path / "home" / ef.FOLDER_NAME
    make(source, "1.5.0", {"panel.js": "new", "voice.js": "v"})
    assert ef.refresh(str(source), str(target)) is False          # never set up from there: left alone
    assert not target.exists()
    make(target, "1.3.0", {"panel.js": "old", "daysheet.js": "gone in the new version"})
    (target / ".DS_Store").write_text("finder")
    assert ef.version_of(str(target)) == "1.3.0"
    assert ef.refresh(str(source), str(target)) is True
    assert ef.version_of(str(target)) == "1.5.0"
    assert (target / "panel.js").read_text() == "new" and (target / "voice.js").exists()
    assert not (target / "daysheet.js").exists()
    assert ef.refresh(str(source), str(target)) is False          # already the same
    (target / "panel.js").write_text("wen")                        # same size, other content
    assert ef.differs(str(source), str(target))


def test_create_for_the_extension_window(tmp_path):
    source, target = tmp_path / "app", tmp_path / "new"
    make(source, "1.5.0", {"panel.js": "x"})
    assert ef.refresh(str(source), str(target), create=True) and ef.version_of(str(target)) == "1.5.0"


def test_status_reports_the_shipped_extension():
    from services import browser_control as bc
    state = bc.AppState(recording=False, paused=False)
    control = bc.VisitControl(state=lambda: state, start=lambda: None, stop=lambda: None, pause=lambda: None,
                              resume=lambda: None, add_context=lambda t: None, extension_version="1.5.0")
    assert control.status()["extension_version"] == "1.5.0"
