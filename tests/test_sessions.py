"""A session remembers its note template and each time its note went into the EMR."""
from services.sessions import Session


def test_inserts_are_kept_and_saved():
    s = Session(title="Lee, Ann", note="S: back pain", template="SOAP Note - Default")
    assert s.inserted_at == ""
    entry = s.record_insert("Lee, Ann")
    assert s.inserted_at == entry["at"] and entry["chart"] == "Lee, Ann"
    again = Session.from_dict(s.to_dict())
    assert again.inserts == s.inserts and again.template == "SOAP Note - Default"
    assert Session.from_dict({"title": "old entry"}).inserts == []
