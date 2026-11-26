"""Bridge startup loading tasks to the independent splash without moving Tk across threads."""
import json
import os
from pathlib import Path
import threading
import time


class StartupCoordinator:
    def __init__(self, root, marker):
        self.root = root
        self.marker = Path(marker)
        self.active = True
        self.tasks = []
        self.lock = threading.RLock()

    def attach(self, task):
        with self.lock:
            if not self.active:
                return False
            self.tasks.append(task)
            return True

    def finish_when_idle(self, callback):
        """Called after the scheduled startup loaders have had a chance to register."""
        def poll():
            with self.lock:
                self.tasks = [t for t in self.tasks if not t._close_requested.is_set()]
                if self.tasks:
                    task = self.tasks[-1]
                    payload = {'title': task.title, 'text': task.initial_text,
                               'note': task.note_text or '', 'cancellable': callable(task.on_cancel)}
                    cancel = self.marker.with_suffix('.cancel')
                    if cancel.exists():
                        cancel.unlink()
                        if callable(task.on_cancel):
                            task._handle_cancel()
                    status = self.marker.with_suffix('.json')
                    temporary = status.with_suffix('.tmp')
                    temporary.write_text(json.dumps(payload), encoding='utf-8')
                    os.replace(temporary, status)
                    self.root.after(80, poll)
                    return
                self.active = False
                self.marker.touch()
            deadline = time.monotonic() + 5
            def wait_for_splash():
                if self.marker.with_suffix('.closed').exists() or time.monotonic() >= deadline:
                    callback()
                else:
                    self.root.after(80, wait_for_splash)
            wait_for_splash()
        poll()


class StartupScreen:
    """
    The splash for a launch that has no separate launcher (the installed app, or ``client.py`` run
    directly). It sits in its own borderless window until the loading steps that start with the app have
    finished, shows what they are doing, and only then reveals the main window. The loading steps find it
    as ``root._startup_coordinator``, exactly as they find the launcher's coordinator.
    """

    RELEASE_AFTER = 120        # seconds: if a step never finishes, show the app and let the step use its own window

    def __init__(self, root):
        import tkinter as tk
        from UI.Art import StartupSplash
        self.root = root
        self.active = True
        self.tasks = []
        self.lock = threading.RLock()
        self._started = time.monotonic()
        width = min(800, root.winfo_screenwidth() - 60)
        height = min(540, root.winfo_screenheight() - 80)
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.title("Starting FreeScribe")
        self.window.geometry(f"{width}x{height}+{max(0, (root.winfo_screenwidth() - width) // 2)}"
                             f"+{max(0, (root.winfo_screenheight() - height) // 2)}")
        self.art = StartupSplash(self.window)
        self.art.on_cancel = self._cancel
        root.withdraw()                      # the app appears when it is ready, not as an empty frame
        self.window.attributes("-topmost", True)
        self.window.deiconify()
        self.window.lift()
        self.window.update()

    def attach(self, task):
        with self.lock:
            if not self.active:
                return False
            self.tasks.append(task)
            return True

    def _cancel(self):
        with self.lock:
            task = self.tasks[-1] if self.tasks else None
        if task is not None and callable(getattr(task, "on_cancel", None)):
            task._handle_cancel()

    def finish_when_idle(self, callback):
        """Call after the loading steps have had a chance to register; ``callback`` runs once the app is shown."""
        def poll():
            with self.lock:
                self.tasks = [t for t in self.tasks if not t._close_requested.is_set()]
                task = self.tasks[-1] if self.tasks else None
                overdue = time.monotonic() - self._started > self.RELEASE_AFTER
            if task is not None and not overdue:
                try:
                    self.art.set_status(task.title, task.initial_text, task.note_text or "",
                                        callable(task.on_cancel))
                except Exception:
                    pass
                self.root.after(80, poll)
                return
            with self.lock:
                self.active = False
                waiting, self.tasks = self.tasks, []
            for item in waiting:             # still not finished: it shows its own window over the app
                item._in_splash = False
                if item.parent:
                    item.parent.after(0, item.build_ui)
            try:
                self.window.destroy()
            except Exception:
                pass
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            callback()
        poll()
