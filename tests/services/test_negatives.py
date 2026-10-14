"""
Tests for finding what the patient said no to, and the check that the note mentions it.

This software is released under the AGPL-3.0 license.
"""
from services.sessions import add_denials_to_note, denial_phrase, find_denials, missing_denials

VISIT = ("So you've had headaches? Yes, for a week. Any vision changes? No. Any nausea or vomiting? No. "
         "Weakness or numbness anywhere? No. Are you sleeping okay? Not really, I wake up a lot. "
         "Night sweats? No, but I feel tired. Any blood when you cough? No.\nDo you smoke?\nNever.")


def test_finds_plain_no_answers_only():
    assert find_denials(VISIT) == ["Any vision changes?", "Any nausea or vomiting?",
                                   "Weakness or numbness anywhere?", "Any blood when you cough?", "Do you smoke?"]
    # "Are you sleeping okay? Not really" is a problem, "No, but ..." is not a clean negative


def test_phrases():
    assert denial_phrase("Any vision changes?") == "vision changes"
    assert denial_phrase("Have you had any fever?") == "fever"
    assert denial_phrase("Any blood when you cough?") == "blood when coughing"
    assert denial_phrase("Is it the worst headache of your life?") == "the worst headache of their life"


def test_missing_from_the_note():
    note = "Subjective:\nHeadaches for a week. Denies vision changes and vomiting.\n\nPlan:\nReview."
    assert missing_denials(note, VISIT) == ["weakness or numbness", "blood when coughing", "smoke"]
    assert missing_denials("", "") == []


def test_added_under_subjective():
    note = "**Subjective:**\nHeadaches for a week.\n\n**Objective:**\nBP 130/80."
    added = add_denials_to_note(note, ["fever", "chest pain"])
    assert added == ("**Subjective:**\nHeadaches for a week.\nDenies (from the transcript): fever; chest pain.\n\n"
                     "**Objective:**\nBP 130/80.")
    assert add_denials_to_note("Plain note.", ["fever"]).endswith("\n\nDenies (from the transcript): fever.")
    assert add_denials_to_note(note, []) == note
