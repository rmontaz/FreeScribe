"""
ChatWindow.py

The chat panel docked on the right of the main window: a conversation with the
AI about the open session. Each message is sent with the session's context,
transcript, note, documents and the forms filled in it; the conversation is
kept with the session. The panel follows whichever session is open.

The panel only shows the conversation and collects messages; client.py sends
them to the model (with the usual identifier scrubbing and review).

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk

from UI.Icons import icon
from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Button, ScrollFrame

SUBTITLE = "Knows this visit's note, transcript, documents and forms"
FOOTNOTE = "Names and numbers are hidden from the AI and put back in its reply."
EMPTY = "Ask about this visit, for example “What follow-up was planned?” or “Write a short note for her employer”."
FORM_TINT, FORM_TILE = "#7E3C0E", "#FEEBDD"


class ChatPanel:
    """
    :param parent: the frame the panel lives in
    :param on_send: called with (session, text)
    :param on_clear: called with the session when the user clears its conversation
    :param on_close: called when the panel is closed
    """

    WIDTH = 330

    def __init__(self, parent, on_send, on_clear=None, on_close=None):
        self.on_send = on_send
        self.on_clear = on_clear
        self.on_close = on_close
        self.session = None
        self.busy = {}            # session id -> status text while a reply is on its way
        self.errors = {}          # session id -> last error
        self._wrap = 260

        bg = Palette.SURFACE
        self.frame = tk.Frame(parent, bg=bg, width=Theme.px(self.WIDTH))
        self.frame.pack_propagate(False)
        self._build(bg)

    # ------------------------------------------------------------------ layout
    def _build(self, bg):
        header = tk.Frame(self.frame, bg=bg)
        header.pack(fill="x", padx=Theme.px(16), pady=(Theme.px(16), Theme.px(10)))
        self.close_button = Button(header, icon_name="close", variant="ghost", size="sm", surface=bg,
                                   command=self.close, tooltip="Close the chat")
        self.close_button.pack(side="right", anchor="n")
        self.clear_button = Button(header, icon_name="trash", variant="ghost", size="sm", surface=bg,
                                   command=self._clear, tooltip="Clear this session's chat")
        self.clear_button.pack(side="right", anchor="n")
        titles = tk.Frame(header, bg=bg)
        titles.pack(side="left", fill="x", expand=True)
        self.title_label = tk.Label(titles, text="Chat", bg=bg, fg=Palette.TEXT, anchor="w",
                                    font=Theme.font("medium", "semibold"))
        self.title_label.pack(fill="x")
        tk.Label(titles, text=SUBTITLE, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left",
                 font=Theme.font("small"), wraplength=Theme.px(self.WIDTH - 110)).pack(fill="x")
        tk.Frame(self.frame, bg=Palette.BORDER, height=1).pack(fill="x")

        # Bottom first so the conversation takes the remaining height
        bottom = tk.Frame(self.frame, bg=bg)
        bottom.pack(side="bottom", fill="x", padx=Theme.px(16), pady=(Theme.px(8), Theme.px(14)))
        self.status = tk.Label(bottom, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left",
                               font=Theme.font("small"), wraplength=Theme.px(self.WIDTH - 40))
        self.status.pack(fill="x", pady=(0, Theme.px(4)))
        border = tk.Frame(bottom, bg=Palette.BORDER_STRONG, padx=1, pady=1)
        border.pack(fill="x")
        box = tk.Frame(border, bg=Palette.BG)
        box.pack(fill="x")
        self.send_button = Button(box, icon_name="send", variant="primary", size="sm", pill=True,
                                  surface=Palette.BG, command=self.send, tooltip="Send (Enter)")
        self.send_button.pack(side="right", anchor="s", padx=Theme.px(6), pady=Theme.px(6))
        self.entry = tk.Text(box, height=3, wrap="word", relief="flat", bd=0, font=Theme.font("base"),
                             padx=Theme.px(10), pady=Theme.px(8), bg=Palette.BG, fg=Palette.TEXT,
                             insertbackground=Palette.TEXT, highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<KP_Enter>", self._on_return)
        tk.Label(bottom, text=FOOTNOTE, bg=bg, fg=Palette.TEXT_FAINT, anchor="w", justify="left",
                 font=Theme.font("micro"), wraplength=Theme.px(self.WIDTH - 40)).pack(fill="x", pady=(Theme.px(6), 0))

        self.log = ScrollFrame(self.frame, bg=bg)
        self.log.pack(fill="both", expand=True, padx=(Theme.px(14), Theme.px(4)), pady=(Theme.px(8), 0))
        self.log.canvas.bind("<Configure>", self._on_log_resize, add="+")

    def _on_log_resize(self, event):
        wrap = max(140, event.width - Theme.px(70))
        if abs(wrap - self._wrap) > 8:
            self._wrap = wrap
            self.refresh()

    # ------------------------------------------------------------------ content
    def set_session(self, session):
        """Show the conversation of ``session`` (the panel follows the open session)."""
        self.session = session
        self.refresh()

    def refresh(self, session_id=None):
        """Redraw the conversation (only when ``session_id`` is the one shown, if given)."""
        session = self.session
        if session is None or (session_id is not None and session_id != session.id):
            return
        body = self.log.body
        for child in body.winfo_children():
            child.destroy()
        bg = Palette.SURFACE
        if not session.chat:
            tk.Label(body, text=EMPTY, bg=bg, fg=Palette.TEXT_FAINT, anchor="w", justify="left",
                     font=Theme.font("base"), wraplength=self._wrap).pack(fill="x", pady=Theme.px(10))
        for message in session.chat:
            self._message(body, message)
        busy = self.busy.get(session.id)
        if busy is not None:
            tk.Label(body, text="FreeScribe AI is writing…", bg=bg, fg=Palette.TEXT_MUTED, anchor="w",
                     font=Theme.font("small")).pack(fill="x", pady=(Theme.px(10), Theme.px(4)))
        error = self.errors.get(session.id)
        self.status.configure(text=error or busy or "", fg=Palette.DANGER if error else Palette.TEXT_MUTED)
        self.send_button.set_enabled(busy is None)
        self.clear_button.set_enabled(bool(session.chat) and busy is None)
        self.log.scroll_to_bottom()

    def _message(self, body, message):
        bg = Palette.SURFACE
        role, text = message.get("role", "user"), (message.get("text") or "").strip()
        row = tk.Frame(body, bg=bg)
        row.pack(fill="x", pady=(Theme.px(6), Theme.px(4)), padx=(0, Theme.px(10)))
        if role == "user":
            bubble = tk.Label(row, text=text, bg=Palette.TEXT, fg=Palette.TEXT_ON_PRIMARY, justify="left",
                              anchor="w", font=Theme.font("base"), wraplength=self._wrap,
                              padx=Theme.px(12), pady=Theme.px(8))
            bubble.pack(side="right")
        elif role == "assistant":
            holder = tk.Frame(row, bg=bg)
            holder.pack(side="left", fill="x")
            border = tk.Frame(holder, bg=Palette.BORDER, padx=1, pady=1)
            border.pack(anchor="w")
            tk.Label(border, text=text, bg=Palette.BG, fg=Palette.TEXT, justify="left", anchor="w",
                     font=Theme.font("base"), wraplength=self._wrap, padx=Theme.px(12),
                     pady=Theme.px(8)).pack()
            Button(holder, text="Copy", icon_name="copy", variant="ghost", size="xs", surface=bg,
                   command=lambda t=text: self._copy(t), tooltip="Copy this reply").pack(anchor="w")
        else:
            card = tk.Frame(row, bg=FORM_TILE)
            card.pack(fill="x")
            tk.Label(card, image=icon("form", 16, FORM_TINT), bg=FORM_TILE).pack(
                side="left", anchor="n", padx=(Theme.px(10), Theme.px(8)), pady=Theme.px(9))
            tk.Label(card, text=text, bg=FORM_TILE, fg=FORM_TINT, justify="left", anchor="w",
                     font=Theme.font("small"), wraplength=self._wrap - Theme.px(20)).pack(
                side="left", fill="x", pady=Theme.px(8), padx=(0, Theme.px(10)))

    def _copy(self, text):
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
            self.status.configure(text="Copied to the clipboard.", fg=Palette.TEXT_MUTED)
        except tk.TclError:
            pass

    def set_busy(self, session_id, busy: bool, status: str = ""):
        if busy:
            self.busy[session_id] = status or "Sending…"
            self.errors.pop(session_id, None)
        else:
            self.busy.pop(session_id, None)
        self.refresh(session_id)
        if not busy and status and self.session is not None and self.session.id == session_id:
            self.status.configure(text=status, fg=Palette.TEXT_MUTED)

    def show_error(self, session_id, message: str):
        self.busy.pop(session_id, None)
        self.errors[session_id] = message
        self.refresh(session_id)

    # ------------------------------------------------------------------ actions
    def text(self) -> str:
        return self.entry.get("1.0", "end-1c").strip()

    def send(self):
        message = self.text()
        if not message or self.session is None or self.session.id in self.busy:
            return
        self.entry.delete("1.0", "end")
        self.errors.pop(self.session.id, None)
        self.on_send(self.session, message)

    def _on_return(self, event):
        if event.state & 0x0001:     # Shift: new line
            return None
        self.send()
        return "break"

    def _clear(self):
        session = self.session
        if session is None or session.id in self.busy or not session.chat:
            return
        if callable(self.on_clear):
            self.on_clear(session)
        self.refresh()

    def focus(self):
        self.entry.focus_set()

    def exists(self) -> bool:
        try:
            return bool(self.frame.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        if callable(self.on_close):
            self.on_close()
