"""
ClinicianProfileWindow.py

"Your details": the clinician's name, specialty, licence number and clinic
contact details. Forms get these from here instead of from the AI, which would
otherwise guess them.

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk

import UI.Helpers
from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Button

FIELDS = (
    ("name", "Your name (as you sign forms)"),
    ("specialty", "Specialty"),
    ("licence", "Licence or billing number"),
    ("street", "Clinic street address"),
    ("city", "City"),
    ("province", "Province or state"),
    ("postal_code", "Postal code"),
    ("phone", "Clinic phone"),
    ("fax", "Clinic fax"),
    ("email", "Email for forms"),
)


class ClinicianProfileWindow:
    """
    :param parent: the window to open over
    :param profile: the saved details ({key: value}, keys from :data:`FIELDS`)
    :param on_save: called with the new details when the clinician saves
    """

    def __init__(self, parent, profile, on_save):
        self.on_save = on_save
        bg = Palette.BG
        self.window = tk.Toplevel(parent, bg=bg)
        self.window.title("Your details for forms")
        UI.Helpers.set_window_icon(self.window)
        self.window.transient(parent)
        self.window.resizable(False, False)
        pad = Theme.px(22)
        body = tk.Frame(self.window, bg=bg)
        body.pack(fill="both", expand=True, padx=pad, pady=pad)

        tk.Label(body, text="Your details for forms", bg=bg, fg=Palette.TEXT, anchor="w",
                 font=Theme.font("large", "semibold")).pack(fill="x")
        tk.Label(body, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left", wraplength=Theme.px(440),
                 font=Theme.font("small"),
                 text="FreeScribe puts these into the clinician boxes of a form (name, specialty, address, "
                      "phone, fax), so the AI never has to guess them. They stay on this computer."
                 ).pack(fill="x", pady=(Theme.px(4), Theme.px(12)))

        grid = tk.Frame(body, bg=bg)
        grid.pack(fill="x")
        grid.grid_columnconfigure(1, weight=1)
        self.entries = {}
        for row, (key, label) in enumerate(FIELDS):
            tk.Label(grid, text=label, bg=bg, fg=Palette.TEXT, anchor="w", font=Theme.font("small")).grid(
                row=row, column=0, sticky="w", pady=Theme.px(3), padx=(0, Theme.px(12)))
            entry = tk.Entry(grid, width=34, font=Theme.font("base"), relief="solid", borderwidth=1,
                             highlightthickness=0, bg=Palette.SURFACE, fg=Palette.TEXT,
                             insertbackground=Palette.TEXT)
            entry.insert(0, str((profile or {}).get(key) or ""))
            entry.grid(row=row, column=1, sticky="ew", pady=Theme.px(3))
            self.entries[key] = entry

        buttons = tk.Frame(body, bg=bg)
        buttons.pack(fill="x", pady=(Theme.px(16), 0))
        Button(buttons, text="Save", variant="primary", command=self.save).pack(side="right")
        Button(buttons, text="Cancel", variant="neutral", command=self.window.destroy).pack(
            side="right", padx=(0, Theme.px(8)))
        self.window.bind("<Escape>", lambda e: self.window.destroy())
        self.window.bind("<Return>", lambda e: self.save())
        self.entries["name"].focus_set()

    def values(self):
        return {key: entry.get().strip() for key, entry in self.entries.items()}

    def save(self):
        details = self.values()
        self.window.destroy()
        if callable(self.on_save):
            self.on_save(details)
