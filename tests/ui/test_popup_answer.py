"""A pop-up question can be answered from outside the pop-up (the browser extension does this).

This software is released under the AGPL-3.0 license.
"""
import pytest

tk = pytest.importorskip("tkinter")


def test_popup_answered_from_outside():
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk cannot open a display: {error}")
    from UI.Theme import Theme
    from UI.Widgets.PopupBox import PopupBox
    Theme.init(root)
    opened = []

    def on_open(popup):
        opened.append(popup)
        root.after(200, popup.on_button_2)      # what an answer from the extension does

    popup = PopupBox(root, title="Write the note anyway?", message="Short", button_text_1="Don't",
                     button_text_2="Write it", on_open=on_open)
    assert opened and popup.response == "button_2" and not popup.dialog.winfo_exists()
    root.destroy()
