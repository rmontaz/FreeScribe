"""Replayable illustrated introduction to the existing workspace workflow."""
import tkinter as tk
from UI.Art import Artwork
from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Button

STEPS = (
    ('tour-1', 'Capture the conversation', 'Start a session, check your microphone, and choose Start transcribing. You can also upload audio or paste a transcript.'),
    ('tour-2', 'Review your note', 'Choose a note template, then review the generated text for accuracy. Context, Transcript and Note keep each part of the visit accessible.'),
    ('tour-3', 'Bring it into your workflow', 'Copy your reviewed note, or open Chrome extension from Tools to set up the connection to OSCAR.'),
    ('done', 'Ready for your next visit', 'Choose New session to begin. You can return to this tour from Help at any time.'),
)


def show_tour(root):
    existing = getattr(root, '_freescribe_tour', None)
    if existing is not None and existing.winfo_exists():
        existing.lift()
        return
    window = tk.Toplevel(root, bg=Palette.BG)
    root._freescribe_tour = window
    window.title('FreeScribe · Quick tour')
    window.geometry('560x500')
    window.minsize(480, 440)
    window.transient(root)
    content = tk.Frame(window, bg=Palette.BG)
    content.pack(fill='both', expand=True)
    state = [0]
    def render():
        for child in content.winfo_children():
            child.destroy()
        index = state[0]
        asset, title, body = STEPS[index]
        Artwork(content, asset, height=220).pack(fill='x', pady=(16, 4))
        tk.Label(content, text=title, font=Theme.font('title', 'semibold'), bg=Palette.BG, fg=Palette.TEXT).pack(pady=10)
        tk.Label(content, text=body, wraplength=430, justify='left', font=Theme.font('base'), bg=Palette.BG, fg=Palette.TEXT_MUTED).pack(padx=28, fill='x')
        nav = tk.Frame(content, bg=Palette.BG)
        nav.pack(side='bottom', fill='x', padx=28, pady=22)
        if index:
            Button(nav, text='Back', command=lambda: move(-1), variant='neutral').pack(side='left')
        tk.Label(nav, text=f'{index+1} / {len(STEPS)}', bg=Palette.BG, fg=Palette.TEXT_FAINT).pack(side='left', padx=12)
        Button(nav, text='Done' if index == len(STEPS)-1 else 'Next', variant='primary', command=window.destroy if index == len(STEPS)-1 else lambda: move(1)).pack(side='right')
    def move(delta):
        state[0] += delta
        render()
    render()
