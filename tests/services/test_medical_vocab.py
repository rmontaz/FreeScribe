"""
Tests for the medical vocabulary prompt, the drug-name check and the per-visit language.

This software is released under the AGPL-3.0 license.
"""
import pytest

from services.medical_vocab import (BASE_TERMS, PROMPT_CHAR_LIMIT, DrugLexicon, VisitLanguage,
                                    build_speech_prompt, is_prompt_echo, split_terms)


@pytest.fixture(scope="module")
def lexicon():
    return DrugLexicon.load()       # the bundled lists


def test_bundled_lists_load(lexicon):
    assert len(lexicon) > 8000
    for name in ("amlodipine", "metformin", "salbutamol", "Lipitor", "Ozempic", "insulin glargine"):
        assert name in lexicon


def test_near_misses_are_suggested(lexicon):
    assert lexicon.suggestions("amlodapine")[0] == "amlodipine"
    assert lexicon.suggestions("Metforman") == ["metformin"]
    assert lexicon.suggestions("lisinoprill") == ["lisinopril"]
    assert lexicon.suggestions("Ozempick") == ["Ozempic"]
    # known drugs and ordinary words are left alone
    for word in ("amlodipine", "ability", "patient", "breathing", "tablets", "allergies", "symptoms"):
        assert lexicon.suggestions(word) == [], word


def test_flags_in_text(lexicon):
    text = "Started amlodapine 5 mg daily. Continue metformin. Breathing is better."
    flags = lexicon.flags(text)
    assert [(text[s:e], options[0]) for s, e, _w, options in flags] == [("amlodapine", "amlodipine")]
    assert lexicon.flags(text, ignore=["Amlodapine"]) == []


def test_finds_drugs_in_context(lexicon):
    context = "Meds: Amlodipine 5 mg, insulin glargine 10 units at night, Lipitor. Lives with Maria."
    assert lexicon.find_in_text(context) == ["amlodipine", "insulin glargine", "Lipitor"]


def test_speech_prompt_puts_the_visit_words_last_and_fits(lexicon):
    prompt = build_speech_prompt("On apixaban and metformin. Name: Maria Lopez", ["Holter", "spirometry"], lexicon)
    assert prompt.startswith("Clinical visit") and prompt.endswith("Holter, spirometry, apixaban, metformin.")
    assert "Maria" not in prompt and "Lopez" not in prompt      # only drug names come from the context
    assert "chest pain" in prompt
    many = [f"term{i}" for i in range(200)]
    long_prompt = build_speech_prompt("", many, lexicon)
    assert len(long_prompt) <= PROMPT_CHAR_LIMIT
    assert BASE_TERMS[0] not in long_prompt and "term199" in long_prompt   # general terms give way first


def test_own_terms_parsing():
    assert split_terms("Holter, spirometry\nEntresto; ") == ["Holter", "spirometry", "Entresto"]
    assert split_terms("None") == [] and split_terms("") == []


def test_prompt_echo_is_dropped_but_real_speech_kept(lexicon):
    prompt = build_speech_prompt("", [], lexicon)
    assert is_prompt_echo("Clinical visit between a clinician and a patient.", prompt)
    assert is_prompt_echo(" shortness of breath, chest pain, blood pressure", prompt)
    assert not is_prompt_echo("I have chest pain and shortness of breath.", prompt)
    assert not is_prompt_echo("Yes.", prompt)


def test_visit_language_is_kept_once_clear():
    visit = VisitLanguage()
    assert visit.observe("fr", 0.95, 2.0) is None          # too short to trust
    assert visit.observe("es", 0.5, 10.0) is None          # not confident
    assert visit.observe("fr", 0.9, 8.0) == "fr"
    assert visit.observe("en", 0.99, 20.0) == "fr"         # kept for the rest of the visit
    visit.reset()
    assert visit.language is None
