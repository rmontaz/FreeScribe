"""
note_text.py

Turns the markdown that AI models like to write (**bold**, * bullets, # headings)
into plain text, so a note reads cleanly in FreeScribe and pastes cleanly into an EMR.

This software is released under the AGPL-3.0 license.
"""

import re


def plain_note(note: str) -> str:
    """
    The note as plain text: markdown marks are removed, headings become plain
    lines and bullets become "- ".
    """
    text = (note or "").replace("\r\n", "\n")
    text = re.sub(r"^```\w*\s*$", "", text, flags=re.MULTILINE)        # code fences
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)  # # Heading
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)                       # **bold**
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)   # *italic*
    text = re.sub(r"^([ \t]*)[*•+][ \t]+", r"\1- ", text, flags=re.MULTILINE)  # bullets as "- "
    text = re.sub(r"^\s*\*+\s*$", "", text, flags=re.MULTILINE)        # lines that are only *
    text = re.sub(r"\*\*", "", text)                                   # unpaired ** left over
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
