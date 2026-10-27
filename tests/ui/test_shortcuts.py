"""The app's keyboard shortcuts: names, keys pressed on each system, clashes, changing them.

This software is released under the AGPL-3.0 license.
"""
import types

import pytest

tk = pytest.importorskip("tkinter")

from UI import Shortcuts  # noqa: E402


def key(keysym, state=0, char="", widget=None):
    return types.SimpleNamespace(keysym=keysym, state=state, char=char, widget=widget)


@pytest.mark.parametrize("spec,expected", [
    ("alt+r", "Alt+R"), ("Shift+Alt+d", "Alt+Shift+D"), ("cmd+shift+d", "Cmd+Shift+D"), ("F8", "F8"),
    ("r", ""), ("Shift+R", ""), ("Ctrl+Enter", ""), ("Foo+R", ""), ("", ""),
])
def test_normalize(spec, expected):
    assert Shortcuts.normalize(spec) == expected


def test_labels():
    assert Shortcuts.label("Alt+Shift+D", mac=False) == "Alt+Shift+D"
    assert Shortcuts.label("Alt+R", mac=True) == "⌥R"
    assert Shortcuts.label("Cmd+Shift+D", mac=True) == "⇧⌘D"


@pytest.mark.parametrize("event,system,expected", [
    (key("r", 0x8, "r"), "x11", "Alt+R"),
    (key("r", 0x20000, "r"), "win32", "Alt+R"),
    (key("D", 0x5, "\x04"), "x11", "Ctrl+Shift+D"),
    (key("partialderivative", 0x10, "∂"), "aqua", "Alt+D"),          # Option+D on a Mac
    (key("perthousand", 0x11, "‰"), "aqua", "Alt+Shift+R"),          # Option+Shift+R
    (key("d", 0x8 | 0x1, "D"), "aqua", "Cmd+Shift+D"),
    (key("F8"), "x11", "F8"),
    (key("r", 0, "r"), "x11", ""),                                   # plain typing
    (key("Alt_L", 0x8), "x11", ""),
])
def test_keys_pressed(event, system, expected):
    assert Shortcuts.from_event(event, system) == expected


def test_clashes():
    assert Shortcuts.problem({"record": "Alt+R", "pause": "Alt+P", "dictate": ""}) == ""
    assert "both" in Shortcuts.problem({"record": "Alt+R", "pause": "alt+r"})


@pytest.fixture(scope="module")
def root():
    try:
        window = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk cannot open a display: {error}")
    from UI.Theme import Theme
    Theme.init(window)
    yield window
    window.destroy()


def test_shortcuts_run_and_can_be_changed_or_turned_off(root):
    ran = []
    Shortcuts.install(root, {a: (lambda a=a: ran.append(a)) for a in Shortcuts.ACTIONS}, {"dictate": "Ctrl+Shift+D"})
    text = tk.Text(root)
    text.pack()
    root.update()
    text.focus_force()
    root.update()
    assert Shortcuts._on_key(key("r", 0x8 if Shortcuts.system() == "x11" else 0x20000, "r", text)) == "break"
    assert Shortcuts._on_key(key("d", 0x8, "d", text)) is None or Shortcuts.system() != "x11"   # Alt+D is not dictate now
    assert Shortcuts._on_key(key("D", 0x5, "\x04", text)) == "break"
    assert ran[0] == "record" and ran[-1] == "dictate"
    Shortcuts.apply({"record": ""})
    assert Shortcuts._on_key(key("r", 0x8, "r", text)) is None
    assert Shortcuts.hint("Start or stop transcribing", "record") == "Start or stop transcribing"
    Shortcuts.apply(None)
    assert Shortcuts.key_of("record") == "Alt+R"
    text.destroy()


def test_option_character_is_taken_back_out_of_the_text(root):
    text = tk.Text(root)
    text.insert("1.0", "Plan∂")              # what Option+D typed on a Mac before the shortcut was seen
    Shortcuts._take_back_typed_character(key("partialderivative", 0x10, "∂", text))
    assert text.get("1.0", "end-1c") == "Plan"
    entry = tk.Entry(root)
    entry.insert(0, "Plan")                  # an ordinary letter is never removed
    Shortcuts._take_back_typed_character(key("n", 0, "n", entry))
    assert entry.get() == "Plan"
    text.destroy()
    entry.destroy()


def test_shortcuts_window(root):
    from UI.ShortcutsWindow import ShortcutsWindow
    Shortcuts.apply(None)
    saved = []
    win = ShortcutsWindow(root, saved.append)
    root.update()
    win._listen("dictate")
    assert Shortcuts.paused and "Press the keys" in win.rows["dictate"][0].cget("text")
    win._on_key(key("Control_L", 0x4))                   # a lone modifier: still waiting
    assert win.listening == "dictate"
    win._on_key(key("c", 0x4, "\x03"))                    # Ctrl+C is for copying: refused
    assert win.listening == "dictate" and "editing" in win.message.cget("text")
    win._on_key(key("F8"))
    assert win.values["dictate"] == "F8" and not Shortcuts.paused
    win._listen("pause")
    win._on_key(key("F8"))                                # the same keys twice: can't save
    assert not win.save_button.enabled and "both" in win.message.cget("text")
    win._set("pause", "")                                 # turn pause's shortcut off instead
    assert win.save_button.enabled
    win.save()
    assert saved == [{"record": "Alt+R", "pause": "", "dictate": "F8"}]


def test_a_real_key_press_reaches_the_shortcut(root):
    if Shortcuts.system() != "x11":
        pytest.skip("the state bits below are X11's")
    ran = []
    Shortcuts.install(root, {"record": lambda: ran.append("record"), "pause": lambda: None,
                             "dictate": lambda: ran.append("dictate")}, None)
    text = tk.Text(root)
    text.pack()
    text.insert("1.0", "Note")
    root.update()
    text.focus_force()
    root.update()
    text.event_generate("<KeyPress>", keysym="d", state=0x8)      # Alt+D
    text.event_generate("<KeyPress>", keysym="x")                 # ordinary typing still works
    root.update()
    assert ran == ["dictate"]
    assert text.get("1.0", "end-1c").endswith("x") and "d" not in text.get("1.0", "end-1c")[4:]
    text.destroy()
