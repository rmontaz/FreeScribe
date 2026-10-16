"""
sessions.py

The session model behind the workspace sidebar.

A *session* is one patient encounter: the transcript, any extra context the
clinician typed in, the generated note and any further documents (referral
letter, patient summary ...) produced from it.

Sessions replace the older "temporary note history" list of
``(timestamp, transcript, note)`` tuples. The on-disk format stays a JSON list
and every entry still carries the three original keys (``timestamp``,
``user_message``, ``response_text``), so history written by earlier versions
loads here and history written here still loads in earlier versions.

This module has no UI or encryption dependencies of its own: the caller passes
``encrypt`` / ``decrypt`` callables, which keeps it easy to test.

This software is released under the AGPL-3.0 license.
"""

import re
import datetime as _dt
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

LEGACY_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
UNTITLED = "Untitled session"


def _now() -> _dt.datetime:
    return _dt.datetime.now().replace(microsecond=0)


def _parse_time(value) -> Optional[_dt.datetime]:
    if not value or not isinstance(value, str):
        return None
    for parser in (_dt.datetime.fromisoformat, lambda v: _dt.datetime.strptime(v, LEGACY_TIMESTAMP_FORMAT)):
        try:
            return parser(value)
        except (ValueError, TypeError):
            continue
    return None


def format_clock(moment: _dt.datetime) -> str:
    """``2:05 PM`` – written by hand because ``%-I`` is not portable to Windows."""
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


# eq=False: two documents are the same only if they are the same object
@dataclass(eq=False)
class Document:
    """An additional document generated for a session."""

    title: str
    text: str = ""
    template: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: str = field(default_factory=lambda: _now().isoformat())

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "text": self.text,
                "template": self.template, "created": self.created}

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        return cls(
            title=str(data.get("title") or data.get("template") or "Document"),
            text=str(data.get("text") or ""),
            template=str(data.get("template") or ""),
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            created=str(data.get("created") or _now().isoformat()),
        )


# eq=False: sessions are compared by identity, which also keeps them hashable
@dataclass(eq=False)
class Session:
    """One encounter."""

    title: str = ""
    transcript: str = ""
    context: str = ""
    note: str = ""
    template: str = ""
    duration: float = 0.0
    documents: List[Document] = field(default_factory=list)
    # Chat with the AI about this encounter: {"role": "user" | "assistant" | "event", "text", "time"}.
    # "event" entries record things done in the session (a form filled) for the chat to know about.
    chat: List[dict] = field(default_factory=list)
    # The patient's details as the EMR has them (see services.patient_details), e.g. from
    # the OSCAR chart through the Chrome extension; used to fill demographic boxes on forms
    patient: Dict[str, str] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: str = field(default_factory=lambda: _now().isoformat())
    updated: str = field(default_factory=lambda: _now().isoformat())

    # ------------------------------------------------------------ properties
    @property
    def created_at(self) -> _dt.datetime:
        return _parse_time(self.created) or _now()

    @property
    def display_title(self) -> str:
        return self.title.strip() or UNTITLED

    @property
    def is_empty(self) -> bool:
        """True when nothing worth keeping has been entered yet."""
        return not (self.title.strip() or self.transcript.strip() or self.context.strip()
                    or self.note.strip() or self.documents or self.chat or self.patient)

    @property
    def has_output(self) -> bool:
        return bool(self.transcript.strip() or self.note.strip() or self.documents)

    def preview(self, limit: int = 80) -> str:
        """A one-line summary for the sidebar."""
        for source in (self.note, self.transcript, self.context):
            for line in source.splitlines():
                cleaned = line.strip().strip("#*-• ").strip()
                if len(cleaned) > 3 and not cleaned.endswith(":"):
                    return cleaned if len(cleaned) <= limit else cleaned[:limit - 1].rstrip() + "…"
        return ""

    def subtitle(self, now: Optional[_dt.datetime] = None) -> str:
        """Time (and recording length) shown under the title in the sidebar."""
        now = now or _now()
        moment = self.created_at
        if moment.date() == now.date() or (now.date() - moment.date()).days == 1:
            text = format_clock(moment)
        elif moment.year == now.year:
            text = f"{moment.strftime('%b')} {moment.day}, {format_clock(moment)}"
        else:
            text = f"{moment.strftime('%b')} {moment.day}, {moment.year}"
        if self.duration >= 1:
            text += f"  ·  {format_duration(self.duration)}"
        return text

    def touch(self) -> None:
        self.updated = _now().isoformat()

    def find_document(self, document_id: str) -> Optional[Document]:
        for document in self.documents:
            if document.id == document_id:
                return document
        return None

    def matches(self, query: str) -> bool:
        query = query.strip().lower()
        if not query:
            return True
        haystacks = [self.display_title, self.transcript, self.note, self.context, self.template]
        haystacks.extend(document.text for document in self.documents)
        haystacks.extend(document.title for document in self.documents)
        return any(query in (text or "").lower() for text in haystacks)

    # ---------------------------------------------------------- serialisation
    def to_dict(self) -> dict:
        legacy_label = self.title.strip() or self.created_at.strftime(LEGACY_TIMESTAMP_FORMAT)
        return {
            # Keys understood by earlier releases of the client.
            "timestamp": legacy_label,
            "user_message": self.transcript,
            "response_text": self.note,
            # Session fields.
            "id": self.id,
            "title": self.title,
            "created": self.created,
            "updated": self.updated,
            "context": self.context,
            "template": self.template,
            "duration": round(float(self.duration or 0), 1),
            "documents": [document.to_dict() for document in self.documents],
            "chat": [dict(message) for message in self.chat],
            "patient": dict(self.patient),
        }

    def add_chat(self, role: str, text: str) -> dict:
        """Append a chat entry and return it."""
        message = {"role": role, "text": text, "time": _now().isoformat()}
        self.chat.append(message)
        self.touch()
        return message

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        if not isinstance(data, dict):
            raise ValueError("Session entry must be an object")
        label = data.get("timestamp")
        label = label if isinstance(label, str) else ""
        created = _parse_time(data.get("created")) or _parse_time(label)
        if "title" in data:
            title = str(data.get("title") or "")
        else:
            # Entry written by an earlier release: the label is either the original
            # timestamp or a name the user gave the note by renaming it.
            title = "" if _parse_time(label) else label
        documents = []
        for raw in data.get("documents") or []:
            if isinstance(raw, dict):
                documents.append(Document.from_dict(raw))
        chat = []
        for raw in data.get("chat") or []:
            if isinstance(raw, dict) and raw.get("role") in ("user", "assistant", "event") and raw.get("text"):
                chat.append({"role": raw["role"], "text": str(raw["text"]), "time": str(raw.get("time") or "")})
        try:
            duration = float(data.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        created_text = (created or _now()).isoformat()
        return cls(
            title=title,
            transcript=str(data.get("user_message") or data.get("transcript") or ""),
            context=str(data.get("context") or ""),
            note=str(data.get("response_text") or data.get("note") or ""),
            template=str(data.get("template") or ""),
            duration=duration,
            documents=documents,
            chat=chat,
            patient={str(k): str(v) for k, v in (data.get("patient") or {}).items()
                     if isinstance(v, (str, int, float)) and str(v).strip()}
            if isinstance(data.get("patient"), dict) else {},
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            created=created_text,
            updated=str(data.get("updated") or created_text),
        )


class SessionStore:
    """Ordered collection of sessions, newest first."""

    def __init__(self):
        self.sessions: List[Session] = []

    # -------------------------------------------------------------- mutation
    def new(self) -> Session:
        session = Session()
        self.sessions.insert(0, session)
        return session

    def add(self, session: Session) -> Session:
        self.sessions.insert(0, session)
        return session

    def remove(self, session_id: str) -> Optional[Session]:
        for index, session in enumerate(self.sessions):
            if session.id == session_id:
                return self.sessions.pop(index)
        return None

    def clear(self) -> None:
        self.sessions = []

    def prune_empty(self, keep: Optional[Session] = None) -> None:
        """Drop sessions that never received any content (except ``keep``)."""
        self.sessions = [s for s in self.sessions if s is keep or not s.is_empty]

    # --------------------------------------------------------------- queries
    def get(self, session_id: str) -> Optional[Session]:
        for session in self.sessions:
            if session.id == session_id:
                return session
        return None

    def __len__(self) -> int:
        return len(self.sessions)

    def __iter__(self):
        return iter(self.sessions)

    def visible(self, query: str = "", current: Optional[Session] = None) -> List[Session]:
        """Sessions to list: non-empty ones (plus the current one) matching ``query``."""
        result = []
        for session in self.sessions:
            if session.is_empty and session is not current:
                continue
            if session.matches(query):
                result.append(session)
        return result

    def grouped(self, query: str = "", current: Optional[Session] = None,
                now: Optional[_dt.datetime] = None) -> List[Tuple[str, List[Session]]]:
        """Visible sessions bucketed under date headings, newest first."""
        now = now or _now()
        ordered = sorted(self.visible(query, current), key=lambda s: s.created_at, reverse=True)
        groups: List[Tuple[str, List[Session]]] = []
        for session in ordered:
            label = self._bucket(session.created_at, now)
            if groups and groups[-1][0] == label:
                groups[-1][1].append(session)
            else:
                groups.append((label, [session]))
        return groups

    @staticmethod
    def _bucket(moment: _dt.datetime, now: _dt.datetime) -> str:
        days = (now.date() - moment.date()).days
        if days <= 0:
            return "Today"
        if days == 1:
            return "Yesterday"
        if days < 7:
            return "Previous 7 days"
        if moment.year == now.year:
            return moment.strftime("%B")
        return moment.strftime("%B %Y")

    # ----------------------------------------------------------- persistence
    def to_json(self) -> str:
        return json.dumps([s.to_dict() for s in self.sessions if not s.is_empty], indent=2, ensure_ascii=False)

    def load_json(self, text: str) -> int:
        """Replace the contents with sessions parsed from ``text``. Returns how many loaded."""
        data = json.loads(text) if text and text.strip() else []
        if not isinstance(data, list):
            raise ValueError("Session history must be a list")
        loaded = []
        for entry in data:
            try:
                loaded.append(Session.from_dict(entry))
            except (ValueError, TypeError):
                continue  # skip a damaged entry rather than lose the rest
        self.sessions = loaded
        return len(loaded)

    def save(self, path: str, encrypt: Callable[[str], str]) -> None:
        """Encrypt and write the history atomically, so a crash mid-write cannot corrupt it."""
        payload = self.to_json()
        has_sessions = any(not s.is_empty for s in self.sessions)
        data = encrypt(payload) if has_sessions else ""
        directory = os.path.dirname(os.path.abspath(path))
        handle, temp_path = tempfile.mkstemp(prefix=".notes_", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(data)
            os.replace(temp_path, path)
        except BaseException:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

    def load(self, path: str, decrypt: Callable[[str], str]) -> int:
        """Read and decrypt the history. A missing or empty file is simply an empty history."""
        try:
            with open(path, "r", encoding="utf-8") as file:
                encrypted = file.read()
        except FileNotFoundError:
            self.sessions = []
            return 0
        if not encrypted.strip():
            self.sessions = []
            return 0
        return self.load_json(decrypt(encrypted))


# ------------------------------------------------------------- prompt helpers
CONTEXT_HEADING = "Additional context provided by the clinician (not spoken during the visit):"

DETAIL_LEVELS = {
    "Brief": "Keep the document brief: short phrases and only the clinically essential points, without "
             "repetition. Still list every symptom the patient said no to as a pertinent negative.",
    "Standard": "",
    "Detailed": "Be thorough: include every clinically relevant detail that was mentioned, "
                "with pertinent negatives, while staying strictly within what was said.",
}
DEFAULT_DETAIL_LEVEL = "Standard"


_SENTENCE = re.compile(r"[^.?!\n]+[.?!]?")
_NO_ANSWER = re.compile(r"^\s*(no|nope|none|never|not really|nah|no,? (?:none|never|not at all))\b[\s,.!]*", re.IGNORECASE)
_WELLNESS = re.compile(r"\b(okay|ok|alright|all right|well|fine|good|better|normal|enough|able|managing|coping)\b",
                       re.IGNORECASE)
_NO_BUT = re.compile(r"^\s*no\b[\s,]*(but|except|only|just|well)\b", re.IGNORECASE)


def find_denials(transcript: str, limit: int = 15) -> List[str]:
    """
    Questions in ``transcript`` that the patient answered with a plain "no"
    ("Any chest pain?" followed by "No."). A "no, but ..." is left out because it
    is not a clean negative. The note prompt lists these so a small model does not
    drop them.
    """
    sentences = [m.group(0).strip() for m in _SENTENCE.finditer(transcript or "") if m.group(0).strip()]
    found = []
    for question, answer in zip(sentences, sentences[1:]):
        if not question.endswith("?") or len(question) > 140:
            continue
        # "Are you sleeping okay?" - "No" is a problem, not a denial
        if _WELLNESS.search(question):
            continue
        if _NO_ANSWER.match(answer) and not _NO_BUT.match(answer):
            if question not in found:
                found.append(question)
        if len(found) >= limit:
            break
    return found


_QUESTION_LEAD = re.compile(
    r"^(?:and\s+|so\s+|okay,?\s+|ok,?\s+)?(?:(?:do|did|have|has|are|were|is|was)\s+(?:you|there|it)\s+"
    r"(?:had\s+|have\s+|having\s+|noticed\s+|been\s+|get\s+|got\s+|getting\s+|feel\s+|felt\s+)?)?"
    r"(?:any\s+|anything\s+like\s+)?", re.IGNORECASE)
_STOP = {"any", "anything", "anywhere", "you", "your", "with", "when", "have", "had", "been", "from", "that", "this",
         "there", "like", "about", "some", "ever", "recently", "lately", "today", "the", "and", "for", "are", "did"}


def denial_phrase(question: str) -> str:
    """'Any blood when you cough?' -> 'blood when coughing'-style short phrase for the note."""
    phrase = _QUESTION_LEAD.sub("", question.strip().rstrip("?").strip())
    phrase = re.sub(r"\b(?:anywhere|at all|lately|recently)\b", "", phrase, flags=re.IGNORECASE)
    # "when you cough" -> "when coughing"
    phrase = re.sub(r"\bwhen you (\w+)\b",
                    lambda m: "when " + (m.group(1)[:-1] if m.group(1).endswith("e") else m.group(1)) + "ing",
                    phrase, flags=re.IGNORECASE)
    phrase = re.sub(r"\byour\b", "their", phrase, flags=re.IGNORECASE)
    phrase = re.sub(r"\byou\b", "the patient", phrase, flags=re.IGNORECASE)
    phrase = re.sub(r"\s+", " ", phrase).strip(" ,")
    return phrase[:1].lower() + phrase[1:] if phrase else question.strip()


def _key_words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP]


def missing_denials(note: str, transcript: str) -> List[str]:
    """
    Things the patient said no to that the note does not mention at all, as short
    phrases ("chest pain", "blood when the patient cough"). Small models sometimes
    drop pertinent negatives; the clinician is offered these to add.
    """
    note_words = set(_key_words(note or ""))
    missing = []
    for question in find_denials(transcript):
        words = _key_words(denial_phrase(question))
        # Mentioned when one of its main words is in the note (stems: "vomit" in "vomiting")
        if words and not any(w in note_words or any(n.startswith(w[:5]) for n in note_words) for w in words):
            missing.append(denial_phrase(question))
    return missing


def add_denials_to_note(note: str, phrases: List[str]) -> str:
    """Adds 'Denies: ...' after the Subjective section (or at the end when there is none)."""
    if not phrases:
        return note
    line = "Denies (from the transcript): " + "; ".join(phrases) + "."
    lines = (note or "").split("\n")
    start = next((i for i, l in enumerate(lines) if re.match(r"^\W*(subjective|history|hpi)\b", l, re.IGNORECASE)), None)
    if start is None:
        return (note.rstrip() + "\n\n" + line) if note.strip() else line
    end = next((i for i in range(start + 1, len(lines))
                if re.match(r"^\W*(objective|exam|physical|assessment|plan)\b", lines[i], re.IGNORECASE)), len(lines))
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, line)
    return "\n".join(lines)


def compose_source(transcript: str, context: str = "") -> str:
    """Combine the transcript and the clinician's context into the text sent for note generation."""
    transcript = (transcript or "").strip()
    context = (context or "").strip()
    if not context:
        return transcript
    return f"{transcript}\n\n{CONTEXT_HEADING}\n{context}"


def compose_revision_prompt(instruction: str, document: str, transcript: str = "", context: str = "") -> str:
    """Prompt asking the model to revise ``document`` following the clinician's ``instruction``."""
    parts = [
        "You are helping a clinician revise a clinical document. Apply the clinician's instruction to the "
        "document below. Use only information found in the document, the visit transcript or the additional "
        "context. Do not invent clinical facts, values or findings. Return only the complete revised document, "
        "with no commentary before or after it.",
        f"Clinician's instruction: {instruction.strip()}",
        f"Document:\n{(document or '').strip()}",
    ]
    if (transcript or "").strip():
        parts.append(f"Visit transcript:\n{transcript.strip()}")
    if (context or "").strip():
        parts.append(f"{CONTEXT_HEADING}\n{context.strip()}")
    return "\n\n".join(parts)
