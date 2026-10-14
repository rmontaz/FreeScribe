"""
ChatWindow.py

A chat with the AI about one encounter. Each message is sent with the
session's context, transcript, note, documents and the forms filled in it;
the conversation is kept with the session.

The window only shows the conversation and collects messages; client.py sends
them to the model (with the usual identifier scrubbing and review).

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk

import UI.Helpers
from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Button, Scrollbar

INTRO = ("Ask about this encounter or anything else. FreeScribe sends your message together with this "
         "session's context, transcript, note, documents and filled forms. Answers can be wrong; "
         "check anything you rely on.")
NAMES = {"user": "You", "assistant": "FreeScribe AI", "event": "FreeScribe"}


class ChatWindow:
    """
    :param session: the session this chat belongs to (its ``chat`` list is shown)
    :param on_send: called with the message text
    :param on_clear: called when the user clears the conversation
    :param on_close: called when the window closes
    """

    def __init__(self, parent, session, on_send, on_clear=None, on_close=None):
        self.session = session
        self.on_send = on_send
        self.on_clear = on_clear
        self.on_close = on_close
        self.busy = False

        self.window = tk.Toplevel(parent)
        self.window.configure(bg=Palette.BG)
        UI.Helpers.set_window_icon(self.window)
        self.window.minsize(Theme.px(420), Theme.px(420))
        width, height = Theme.px(560), Theme.px(680)
        parent.update_idletasks()
        x = parent.winfo_rootx() + max(0, parent.winfo_width() - width - Theme.px(40))
        y = parent.winfo_rooty() + Theme.px(40)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.refresh()
        self.entry.focus_set()

    # ------------------------------------------------------------------ layout
    def _build(self):
        pad = Theme.px(18)
        header = tk.Frame(self.window, bg=Palette.BG)
        header.pack(fill="x", padx=pad, pady=(Theme.px(14), Theme.px(6)))
        self.title_label = tk.Label(header, bg=Palette.BG, fg=Palette.TEXT, anchor="w",
                                    font=Theme.font("large", "semibold"))
        self.title_label.pack(side="left", fill="x", expand=True)
        self.clear_button = Button(header, text="Clear chat", variant="ghost", size="sm", icon_name="trash",
                                   command=self._clear)
        self.clear_button.pack(side="right")

        intro = tk.Label(self.window, text=INTRO, bg=Palette.BG, fg=Palette.TEXT_MUTED, anchor="w",
                         justify="left", font=Theme.font("small"))
        intro.pack(fill="x", padx=pad)
        intro.bind("<Configure>", lambda e: intro.configure(wraplength=max(100, e.width)))
        tk.Frame(self.window, bg=Palette.BORDER, height=1).pack(fill="x", padx=pad, pady=(Theme.px(10), 0))

        # Bottom first so the conversation takes the remaining height
        bottom = tk.Frame(self.window, bg=Palette.BG)
        bottom.pack(side="bottom", fill="x", padx=pad, pady=(Theme.px(8), Theme.px(14)))
        self.status = tk.Label(bottom, bg=Palette.BG, fg=Palette.TEXT_MUTED, anchor="w", font=Theme.font("small"))
        self.status.pack(fill="x", pady=(0, Theme.px(4)))
        row = tk.Frame(bottom, bg=Palette.BG)
        row.pack(fill="x")
        # The button is packed first so the text box cannot squeeze it out
        self.send_button = Button(row, text="Send", variant="primary", icon_name="send", command=self.send)
        self.send_button.pack(side="right", padx=(Theme.px(8), 0), anchor="s")
        border = tk.Frame(row, bg=Palette.BORDER_STRONG, padx=1, pady=1)
        border.pack(side="left", fill="x", expand=True)
        self.entry = tk.Text(border, height=3, wrap="word", relief="flat", bd=0, font=Theme.font("medium"),
                             padx=Theme.px(8), pady=Theme.px(6), bg=Palette.BG, fg=Palette.TEXT,
                             insertbackground=Palette.TEXT, highlightthickness=0)
        self.entry.pack(fill="x")
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<KP_Enter>", self._on_return)
        tk.Label(bottom, text="Enter to send · Shift+Enter for a new line", bg=Palette.BG,
                 fg=Palette.TEXT_FAINT, anchor="w", font=Theme.font("micro")).pack(fill="x", pady=(Theme.px(4), 0))

        body = tk.Frame(self.window, bg=Palette.BG)
        body.pack(fill="both", expand=True, padx=(pad, Theme.px(8)), pady=(Theme.px(6), 0))
        self.log = tk.Text(body, wrap="word", relief="flat", bd=0, bg=Palette.BG, fg=Palette.TEXT,
                           font=Theme.font("medium"), padx=Theme.px(4), pady=Theme.px(6), highlightthickness=0,
                           cursor="arrow", spacing1=Theme.px(1), spacing3=Theme.px(1))
        scroll = Scrollbar(body, command=self.log.yview, surface=Palette.BG)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("name_user", font=Theme.font("small", "semibold"), foreground=Palette.PRIMARY,
                               spacing1=Theme.px(12))
        self.log.tag_configure("name_assistant", font=Theme.font("small", "semibold"), foreground=Palette.TEXT,
                               spacing1=Theme.px(12))
        self.log.tag_configure("name_event", font=Theme.font("small", "semibold"), foreground=Palette.TEXT_MUTED,
                               spacing1=Theme.px(12))
        self.log.tag_configure("text_user", lmargin1=0, lmargin2=0)
        self.log.tag_configure("text_assistant", lmargin1=0, lmargin2=0)
        self.log.tag_configure("text_event", foreground=Palette.TEXT_MUTED, font=Theme.font("small"))
        self.log.tag_configure("pending", foreground=Palette.TEXT_FAINT, font=Theme.font("small"),
                               spacing1=Theme.px(12))
        self.log.tag_configure("empty", foreground=Palette.TEXT_FAINT, font=Theme.font("base"),
                               spacing1=Theme.px(12))
        # Read-only, but text can still be selected and copied
        self.log.bind("<Key>", self._log_key)

    # ------------------------------------------------------------------ content
    def refresh(self):
        """Redraw the conversation from ``session.chat``."""
        self.title_label.configure(text=f"Chat · {self.session.display_title}")
        self.window.title(f"Chat – {self.session.display_title}")
        self.log.delete("1.0", "end")
        if not self.session.chat:
            self.log.insert("end", "No messages yet. Try “Summarise this visit for the patient” or "
                                   "“What follow-up was planned?”", "empty")
        for message in self.session.chat:
            role = message.get("role", "user")
            self.log.insert("end", NAMES.get(role, role) + "\n", f"name_{role}")
            self.log.insert("end", message.get("text", "").strip() + "\n", f"text_{role}")
        if self.busy:
            self.log.insert("end", "FreeScribe AI is thinking…\n", "pending")
        self.log.see("end")
        self.clear_button.set_enabled(bool(self.session.chat) and not self.busy)

    def set_busy(self, busy: bool, status: str = ""):
        self.busy = bool(busy)
        self.send_button.set_enabled(not self.busy)
        self.status.configure(text=status, fg=Palette.TEXT_MUTED)
        self.refresh()

    def show_error(self, message: str):
        self.busy = False
        self.send_button.set_enabled(True)
        self.refresh()
        self.status.configure(text=message, fg=Palette.DANGER)

    # ------------------------------------------------------------------ actions
    def text(self) -> str:
        return self.entry.get("1.0", "end-1c").strip()

    def send(self):
        message = self.text()
        if not message or self.busy:
            return
        self.entry.delete("1.0", "end")
        self.on_send(message)

    def _on_return(self, event):
        if event.state & 0x0001:     # Shift: new line
            return None
        self.send()
        return "break"

    def _log_key(self, event):
        # Allow copying and moving around; block typing into the conversation
        if (event.state & 0x0004 or event.state & 0x0008 or event.state & 0x0010) and event.keysym.lower() in ("c", "a"):
            return None
        if event.keysym in ("Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End"):
            return None
        return "break"

    def _clear(self):
        if self.busy or not self.session.chat:
            return
        if callable(self.on_clear):
            self.on_clear()
        self.refresh()

    def lift(self):
        self.window.deiconify()
        self.window.lift()
        self.entry.focus_set()

    def exists(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        if self.exists():
            self.window.destroy()
        if callable(self.on_close):
            self.on_close()
