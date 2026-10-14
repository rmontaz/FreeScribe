"""
chat.py

Builds the prompt for chatting with the AI about one encounter.

Every message is sent together with what FreeScribe knows about the session
(title, date, context, transcript, note, extra documents, forms filled) and the
recent conversation. Local models have small context windows, so the material
is shortened to a character budget: the transcript first, then the documents,
then the oldest turns of the conversation. The newest message is always kept.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

INSTRUCTIONS = (
    "You are FreeScribe's assistant, helping a clinician with one patient encounter. Answer the clinician's "
    "last message. Use the encounter information below when it is relevant, and say so plainly when the "
    "answer is not in it. Never invent clinical facts, values or findings about this patient. Your answers "
    "support the clinician's judgement and do not replace it. Keep answers short and practical, in plain text."
)
SHORTENED = " [shortened]"
ROLE_LABELS = {"user": "Clinician", "assistant": "Assistant", "event": "FreeScribe"}

# The smallest share of the budget kept for each part when something has to give
MIN_TRANSCRIPT = 600
MIN_DOCUMENT = 300


def _cut(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    if limit <= len(SHORTENED) + 20:
        return ""
    return text[:limit - len(SHORTENED)].rsplit(" ", 1)[0] + SHORTENED


def encounter_text(session, transcript_limit: Optional[int] = None, document_limit: Optional[int] = None) -> str:
    """The encounter information block for ``session``."""
    parts = []
    head = []
    if session.title.strip():
        head.append(f"Session: {session.title.strip()}")
    head.append(f"Date: {session.created_at.strftime('%Y-%m-%d')}")
    parts.append("\n".join(head))
    if session.context.strip():
        parts.append(f"Additional context from the clinician:\n{session.context.strip()}")
    if session.note.strip():
        parts.append(f"Note:\n{session.note.strip()}")
    for document in session.documents:
        text = document.text if document_limit is None else _cut(document.text, document_limit)
        if text:
            parts.append(f"{document.title}:\n{text}")
    transcript = session.transcript.strip()
    if transcript:
        if transcript_limit is not None:
            transcript = _cut(transcript, transcript_limit)
        if transcript:
            parts.append(f"Visit transcript:\n{transcript}")
    return "\n\n".join(parts)


def _turn(message: dict) -> str:
    return f"{ROLE_LABELS.get(message.get('role'), 'Note')}: {(message.get('text') or '').strip()}"


def compose_chat_prompt(session, history: List[dict], question: str, budget: int = 12000) -> Tuple[str, dict]:
    """
    The prompt for ``question``, given the session and the earlier ``history``.

    Returns ``(prompt, report)``; the report says what was shortened or left out,
    for the log and the tests.
    """
    question = (question or "").strip()
    report = {"transcript_shortened": False, "documents_shortened": False, "turns_dropped": 0}
    turns = [_turn(m) for m in history if (m.get("text") or "").strip()]

    def build(transcript_limit=None, document_limit=None, keep_turns=None):
        kept = turns if keep_turns is None else turns[len(turns) - keep_turns:] if keep_turns else []
        sections = [INSTRUCTIONS, "Encounter information:\n<<<\n"
                    + encounter_text(session, transcript_limit, document_limit) + "\n>>>"]
        if kept:
            sections.append("Conversation so far:\n" + "\n".join(kept))
        sections.append(f"Clinician: {question}\nAssistant:")
        return "\n\n".join(sections)

    prompt = build()
    if len(prompt) <= budget:
        return prompt, report

    # 1. Shorten the transcript
    over = len(prompt) - budget
    transcript = session.transcript.strip()
    if transcript:
        limit = max(MIN_TRANSCRIPT, len(transcript) - over)
        report["transcript_shortened"] = True
        prompt = build(transcript_limit=limit)
        if len(prompt) <= budget:
            return prompt, report

    # 2. Shorten the extra documents
    t_limit = MIN_TRANSCRIPT if transcript else None
    if session.documents:
        report["documents_shortened"] = True
        prompt = build(transcript_limit=t_limit, document_limit=MIN_DOCUMENT)
        if len(prompt) <= budget:
            return prompt, report
    d_limit = MIN_DOCUMENT if session.documents else None

    # 3. Drop the oldest turns, keeping as many recent ones as fit
    for keep in range(len(turns) - 1, -1, -1):
        prompt = build(transcript_limit=t_limit, document_limit=d_limit, keep_turns=keep)
        report["turns_dropped"] = len(turns) - keep
        if len(prompt) <= budget:
            return prompt, report

    # 4. Still too long (a very long note or context): drop the transcript entirely
    prompt = build(transcript_limit=0, document_limit=d_limit, keep_turns=0)
    return prompt[:budget], report


def form_event_text(form_name: str, saved_as: str, values: List[Tuple[str, str]], limit: int = 40) -> str:
    """The chat entry recorded when a form is filled and saved."""
    filled = [(label, value) for label, value in values if value]
    lines = [f"Filled the form “{form_name}” and saved it as “{saved_as}”."]
    if filled:
        lines.append("Values entered:")
        for label, value in filled[:limit]:
            value = " ".join(str(value).split())
            lines.append(f"- {label}: {value[:200]}")
        if len(filled) > limit:
            lines.append(f"- …and {len(filled) - limit} more")
    else:
        lines.append("No fields were filled.")
    return "\n".join(lines)
