"""
activity_log.py

The activity log: a record, kept on this computer, of what FreeScribe did with a visit's text.
It lists every AI request and reply, every change to a note or document (before and after),
dictation, notes put into OSCAR and forms filled.

Entries are stored encrypted, one line each, with the same key as the saved sessions. Writing to
the log must never stop the action it records, so every public method swallows its own errors.
The log holds patient information: the window warns before exporting it.

This module has no UI. ``install()`` is called once at start-up with the file and the encrypt and
decrypt functions; until then (and in tests) the log is switched off and records nothing.

This software is released under the AGPL-3.0 license.
"""

import contextlib
import csv
import datetime as _dt
import json
import os
import tempfile
import threading
import uuid
from typing import Callable, Dict, Iterable, List, Optional

KINDS = {
    "ai": "AI request",
    "edit": "Edit",
    "dictation": "Dictation",
    "oscar": "Put into OSCAR",
    "form": "Form",
}
PERIODS = (("all", "All time", None), ("today", "Today", 1), ("week", "Last 7 days", 7), ("month", "Last 30 days", 30))
DEFAULT_RETENTION_DAYS = 90
RETENTION_CHOICES = (7, 30, 90, 180, 365)
HAND_EDIT_PAUSE_SECONDS = 60

_local = threading.local()


def _now() -> _dt.datetime:
    return _dt.datetime.now().replace(microsecond=0)


@contextlib.contextmanager
def purpose(name: str, **details):
    """What the AI requests made inside this block are for (for example "Writing a note")."""
    previous = getattr(_local, "purpose", None)
    _local.purpose = (name, details)
    try:
        yield
    finally:
        _local.purpose = previous


def current_purpose():
    return getattr(_local, "purpose", None) or ("AI request", {})


class ActivityLog:
    """:param encrypt: / :param decrypt: turn one line of text into its stored form and back"""

    def __init__(self, path: Optional[str] = None, encrypt: Optional[Callable[[str], str]] = None,
                 decrypt: Optional[Callable[[str], str]] = None, settings=None,
                 session_info: Optional[Callable[[], tuple]] = None):
        self.path = path
        self._encrypt, self._decrypt = encrypt, decrypt
        self.settings = settings                      # anything with get(key, default) and set(key, value)
        self.session_info = session_info              # () -> (session id, session title) of the open session
        self._entries: Optional[List[dict]] = None
        self._lock = threading.RLock()
        self.suspended = path is None                 # nothing is recorded until install() gives a file

    # ------------------------------------------------------------ settings
    @property
    def enabled(self) -> bool:
        if self.settings is None:
            return True
        return bool(self.settings.get("activity_log_enabled", True))

    def set_enabled(self, value: bool) -> None:
        if self.settings is not None:
            self.settings.set("activity_log_enabled", bool(value))

    @property
    def retention_days(self) -> int:
        days = DEFAULT_RETENTION_DAYS
        if self.settings is not None:
            try:
                days = int(self.settings.get("activity_log_days", DEFAULT_RETENTION_DAYS))
            except (TypeError, ValueError):
                days = DEFAULT_RETENTION_DAYS
        return max(1, days)

    def set_retention_days(self, days: int) -> int:
        """Changes how long entries are kept; older ones are deleted now. Returns how many were removed."""
        if self.settings is not None:
            self.settings.set("activity_log_days", max(1, int(days)))
        return self.prune()

    # ------------------------------------------------------------ writing
    def record(self, kind: str, title: str, session=None, **fields) -> Optional[dict]:
        """Add an entry. ``session`` is (id, title), or None for the open session. Never raises."""
        try:
            if self.suspended or not self.enabled or self.path is None:
                return None
            if session is None and self.session_info is not None:
                try:
                    session = self.session_info()
                except Exception:
                    session = None
            session_id, session_title = session if session else ("", "")
            entry = {"id": uuid.uuid4().hex[:12], "at": _now().isoformat(), "kind": kind, "title": str(title)[:200],
                     "session": session_id or "", "session_title": session_title or ""}
            entry.update({key: value for key, value in fields.items() if value not in (None, "")})
            with self._lock:
                self._load()
                self._entries.append(entry)
                self._append_line(entry)
            return entry
        except Exception:
            return None

    def record_ai(self, prompt: str, reply: str = "", seconds: float = 0.0, error: str = "", session=None) -> None:
        """One AI request: exactly what was sent, what came back, and how long it took."""
        name, details = current_purpose()
        extra = {key: value for key, value in details.items() if key != "session"}
        self.record("ai", name, session=session or details.get("session"), prompt=prompt, reply=reply,
                    seconds=round(float(seconds or 0), 1), error=error, **extra)

    def _append_line(self, entry: dict) -> None:
        line = self._encrypt(json.dumps(entry, ensure_ascii=False))
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(line + "\n")

    # ------------------------------------------------------------ reading
    def _load(self) -> None:
        if self._entries is not None:
            return
        entries = []
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                for line in file:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(self._decrypt(line))
                    except Exception:
                        continue                      # one damaged line must not hide the rest
                    if isinstance(entry, dict):
                        entries.append(entry)
        except FileNotFoundError:
            pass
        self._entries = entries

    def entries(self) -> List[dict]:
        """Every entry, newest first."""
        try:
            with self._lock:
                self._load()
                return sorted(self._entries, key=lambda e: e.get("at", ""), reverse=True)
        except Exception:
            return []

    def count(self) -> int:
        return len(self.entries())

    @staticmethod
    def matches(entry: dict, text: str = "", kinds: Optional[Iterable[str]] = None, days: Optional[int] = None,
                today: Optional[_dt.datetime] = None) -> bool:
        if kinds and entry.get("kind") not in set(kinds):
            return False
        if days:
            now = today or _now()
            try:
                at = _dt.datetime.fromisoformat(entry.get("at", ""))
            except ValueError:
                return False
            limit = now.replace(hour=0, minute=0, second=0) - _dt.timedelta(days=days - 1)
            if at < limit:
                return False
        needle = (text or "").strip().lower()
        if needle:
            haystack = " ".join(str(value) for value in entry.values()).lower()
            return needle in haystack
        return True

    def query(self, text: str = "", kinds=None, days: Optional[int] = None) -> List[dict]:
        return [e for e in self.entries() if self.matches(e, text, kinds, days)]

    # ------------------------------------------------------------ keeping it tidy
    def prune(self, today: Optional[_dt.datetime] = None) -> int:
        """Delete entries older than the retention period. Returns how many were removed."""
        try:
            with self._lock:
                if self.path is None:
                    return 0
                self._load()
                limit = (today or _now()) - _dt.timedelta(days=self.retention_days)
                keep = []
                for entry in self._entries:
                    try:
                        if _dt.datetime.fromisoformat(entry.get("at", "")) >= limit:
                            keep.append(entry)
                    except ValueError:
                        keep.append(entry)
                removed = len(self._entries) - len(keep)
                if removed:
                    self._entries = keep
                    self._rewrite()
                return removed
        except Exception:
            return 0

    def clear(self) -> None:
        try:
            with self._lock:
                self._entries = []
                self._rewrite()
        except Exception:
            pass

    def _rewrite(self) -> None:
        if self.path is None:
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        handle, temp = tempfile.mkstemp(prefix=".activity_", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                for entry in sorted(self._entries, key=lambda e: e.get("at", "")):
                    file.write(self._encrypt(json.dumps(entry, ensure_ascii=False)) + "\n")
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.remove(temp)

    # ------------------------------------------------------------ export
    COLUMNS = ("at", "kind", "title", "session_title", "session", "prompt", "reply", "seconds", "error",
               "template", "instruction", "tab", "by", "before", "after", "text", "chart", "source")

    @classmethod
    def export_csv(cls, entries: List[dict], path: str) -> int:
        with open(path, "w", encoding="utf-8-sig", newline="") as file:        # BOM: Excel opens it as UTF-8
            writer = csv.writer(file)
            writer.writerow(cls.COLUMNS)
            for entry in entries:
                writer.writerow([_cell(entry.get(column, "")) for column in cls.COLUMNS])
        return len(entries)

    @staticmethod
    def export_json(entries: List[dict], path: str) -> int:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(entries, file, ensure_ascii=False, indent=2)
        return len(entries)


def _cell(value) -> str:
    """Spreadsheets run text that starts with = + - @ as a formula; a leading quote keeps it text."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


class HandEdits:
    """
    Typing by hand becomes one entry per burst: the text before the first key and after the last,
    saved after a pause, or when ``flush()`` is called (switching session, asking the AI, closing the app).
    """

    def __init__(self, log: ActivityLog, schedule: Optional[Callable[[int, Callable], object]] = None,
                 cancel: Optional[Callable[[object], None]] = None):
        self.log = log
        self._schedule, self._cancel = schedule, cancel
        self._bursts: Dict[tuple, dict] = {}
        self._timer = None

    def typed(self, session, tab: str, before: str, after: str) -> None:
        """``session`` is (id, title); ``tab`` names the note or document; both texts are the whole text."""
        try:
            key = (session[0], tab)
            burst = self._bursts.get(key)
            if burst is None:
                self._bursts[key] = {"session": session, "tab": tab, "before": before, "after": after}
            else:
                burst["after"] = after
            self._arm()
        except Exception:
            pass

    def _arm(self) -> None:
        if self._schedule is None:
            return
        if self._timer is not None and self._cancel is not None:
            self._cancel(self._timer)
        self._timer = self._schedule(HAND_EDIT_PAUSE_SECONDS * 1000, self.flush)

    def flush(self) -> None:
        try:
            bursts, self._bursts = list(self._bursts.values()), {}
            self._timer = None
            for burst in bursts:
                if burst["before"] != burst["after"]:
                    self.log.record("edit", f"Typed in the {burst['tab']}", session=burst["session"], by="hand",
                                    tab=burst["tab"], before=burst["before"], after=burst["after"])
        except Exception:
            pass


# The one log the application uses. Switched off until install() is called.
log = ActivityLog()


def install(path: str, encrypt, decrypt, settings=None, session_info=None, suspended: bool = False) -> ActivityLog:
    """Point the shared log at its file. ``suspended`` keeps it off (the installer self-test)."""
    log.path, log._encrypt, log._decrypt = path, encrypt, decrypt
    log.settings, log.session_info = settings, session_info
    log._entries = None
    log.suspended = suspended
    if not suspended:
        log.prune()
    return log
