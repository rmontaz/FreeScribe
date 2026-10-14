import tkinter as tk
from tkinter import ttk
from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Scrollbar

import UI.Helpers
from utils.file_utils import get_file_path

class ScrubWindow:
    def __init__(self, parent, cleaned_message, onProceed, onCancel):
        """
        Initialize the ScrubWindow.

        Args:
            parent (tk.Tk or tk.Toplevel): The parent window.
            cleaned_message (str): The message to be displayed in the text area.
            onProceed (function): The callback function to be called when the Proceed button is clicked.
            onCancel (function): The callback function to be called when the Cancel button is clicked.
        """
        # Create a new top-level window
        popup = tk.Toplevel(parent)
        popup.title("Scrub PHI Prior to GPT")
        popup.configure(bg=Palette.BG)
        popup.grab_set()  # Make the popup modal
        UI.Helpers.set_window_icon(popup)

        tk.Label(popup, text="Review before sending", bg=Palette.BG, fg=Palette.TEXT,
                 font=Theme.font("large", "semibold"), anchor="w").pack(
            fill="x", padx=Theme.px(20), pady=(Theme.px(18), 0))
        tk.Label(popup, bg=Palette.BG, fg=Palette.TEXT_MUTED, justify="left", anchor="w",
                 wraplength=Theme.px(640),
                 text="This text will be sent to an AI server outside your computer or private network. "
                      "Remove or replace anything that identifies the patient, then choose Proceed.").pack(
            fill="x", padx=Theme.px(20), pady=(Theme.px(4), Theme.px(10)))

        # The text to review, in a bordered box with the app's scrollbar
        border = tk.Frame(popup, bg=Palette.BORDER_STRONG, padx=1, pady=1)
        border.pack(padx=Theme.px(20), pady=0, fill="both", expand=True)
        box = tk.Frame(border, bg=Palette.BG)
        box.pack(fill="both", expand=True)
        text_area = tk.Text(box, height=20, width=80, wrap="word", font=Theme.font("medium"), relief="flat", bd=0,
                            padx=Theme.px(12), pady=Theme.px(10), bg=Palette.BG, fg=Palette.TEXT,
                            insertbackground=Palette.TEXT, highlightthickness=0, spacing3=Theme.px(3),
                            selectbackground=Palette.PRIMARY_SOFT_HOVER, selectforeground=Palette.TEXT, undo=True)
        scrollbar = Scrollbar(box, command=text_area.yview, surface=Palette.BG)
        text_area.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", padx=(0, Theme.px(4)), pady=Theme.px(6))
        text_area.pack(side="left", fill="both", expand=True)
        text_area.insert(tk.END, cleaned_message)  # Insert the cleaned message into the text area

        def on_proceed():
            """
            Handle the Proceed button click event.
            """
            edited_text = text_area.get("1.0", tk.END).strip()  # Get the edited text from the text area
            popup.destroy()  # Close the popup window
            onProceed(edited_text)  # Call the onProceed callback with the edited text

        # Create the Proceed button
        proceed_button = ttk.Button(popup, text="Proceed", command=on_proceed, style="Accent.TButton")
        proceed_button.pack(side=tk.RIGHT, padx=(Theme.px(8), Theme.px(20)), pady=Theme.px(14))

        def on_cancel():
            """
            Handle the Cancel button click event.
            """
            popup.destroy()  # Close the popup window
            onCancel()  # Call the onCancel callback

        # Create the Cancel button
        cancel_button = ttk.Button(popup, text="Cancel", command=on_cancel)
        cancel_button.pack(side=tk.RIGHT, pady=Theme.px(14))

        # Closing the window is the same as cancelling
        popup.protocol("WM_DELETE_WINDOW", on_cancel)