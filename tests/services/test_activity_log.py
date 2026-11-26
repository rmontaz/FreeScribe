import datetime as dt
import json

from services import activity_log as al


class Settings(dict):
    def set(self, key, value):
        self[key] = value


def make(tmp_path, **kw):
    # a reversible stand-in for the real encryption
    enc = lambda text: text[::-1].encode().hex()
    dec = lambda text: bytes.fromhex(text).decode()[::-1]
    return al.ActivityLog(str(tmp_path / "log.txt"), enc, dec, kw.get("settings", Settings()),
                          kw.get("session_info", lambda: ("s1", "Sample patient A")))


def test_entries_are_stored_encrypted_one_per_line_and_reload(tmp_path):
    log = make(tmp_path)
    log.suspended = False
    log.record("ai", "Writing a note", prompt="hello patient", reply="a note", seconds=2.34)
    log.record("dictation", "Dictated in the Note tab", text="hi")
    raw = (tmp_path / "log.txt").read_text().splitlines()
    assert len(raw) == 2 and "hello patient" not in "".join(raw)
    again = make(tmp_path)
    again.suspended = False
    entries = again.entries()
    assert [e["kind"] for e in entries] == ["dictation", "ai"] or len(entries) == 2
    assert {e["session"] for e in entries} == {"s1"}


def test_off_or_not_installed_records_nothing(tmp_path):
    assert al.ActivityLog().record("ai", "x") is None
    log = make(tmp_path)
    log.suspended = False
    log.set_enabled(False)
    assert log.record("ai", "x") is None and log.count() == 0


def test_recording_never_raises(tmp_path):
    log = make(tmp_path)
    log.suspended = False
    log.path = str(tmp_path / "missing-folder" / "log.txt")
    assert log.record("ai", "x") is None


def test_purpose_labels_ai_requests_per_thread(tmp_path):
    log = make(tmp_path)
    log.suspended = False
    with al.purpose("Writing a note", template="SOAP Note"):
        log.record_ai("p", "r", 1.0)
    log.record_ai("p2", "", 0.5, error="timeout")
    first, second = sorted(log.entries(), key=lambda e: e["at"] + e["id"])[:2] if False else (None, None)
    titles = {e["title"] for e in log.entries()}
    assert titles == {"Writing a note", "AI request"}
    note = [e for e in log.entries() if e["title"] == "Writing a note"][0]
    assert note["template"] == "SOAP Note" and note["prompt"] == "p"


def test_retention_deletes_old_entries_and_follows_the_setting(tmp_path):
    settings = Settings()
    log = make(tmp_path, settings=settings)
    log.suspended = False
    old = (dt.datetime.now() - dt.timedelta(days=120)).replace(microsecond=0).isoformat()
    log._load()
    log._entries.append({"id": "old", "at": old, "kind": "ai", "title": "old"})
    log.record("ai", "new")
    assert log.prune() == 1
    assert [e["title"] for e in log.entries()] == ["new"]
    assert log.retention_days == 90
    log._entries.append({"id": "mid", "at": (dt.datetime.now() - dt.timedelta(days=40)).isoformat(), "kind": "ai", "title": "mid"})
    assert log.set_retention_days(30) == 1


def test_search_and_filters(tmp_path):
    log = make(tmp_path)
    log.suspended = False
    log.record("ai", "Chat", prompt="chest pain", reply="x")
    log.record("oscar", "Put into OSCAR", chart="Sample chart", text="note")
    assert [e["kind"] for e in log.query("CHEST")] == ["ai"]
    assert [e["kind"] for e in log.query(kinds=["oscar"])] == ["oscar"]
    assert len(log.query(days=1)) == 2


def test_clear_and_exports(tmp_path):
    log = make(tmp_path)
    log.suspended = False
    log.record("ai", "=cmd", prompt="+1", reply="a,b\n\"c\"")
    csv_path, json_path = tmp_path / "e.csv", tmp_path / "e.json"
    assert log.export_csv(log.entries(), str(csv_path)) == 1
    text = csv_path.read_text(encoding="utf-8-sig")
    assert "'=cmd" in text and "'+1" in text          # spreadsheet formulas are kept as text
    assert log.export_json(log.entries(), str(json_path)) == 1
    assert json.loads(json_path.read_text())[0]["title"] == "=cmd"
    log.clear()
    assert log.count() == 0 and (tmp_path / "log.txt").read_text() == ""


def test_hand_edits_become_one_entry_per_burst(tmp_path):
    log = make(tmp_path)
    log.suspended = False
    jobs = []
    edits = al.HandEdits(log, schedule=lambda ms, fn: jobs.append((ms, fn)) or len(jobs), cancel=lambda job: None)
    s = ("s1", "Sample patient A")
    edits.typed(s, "Note", "a", "ab")
    edits.typed(s, "Note", "ab", "abc")
    assert log.count() == 0 and jobs[-1][0] == 60000
    edits.flush()
    entry = log.entries()[0]
    assert (entry["before"], entry["after"], entry["by"]) == ("a", "abc", "hand")
    edits.typed(s, "Note", "x", "x")          # typed and deleted again: nothing to record
    edits.flush()
    assert log.count() == 1
