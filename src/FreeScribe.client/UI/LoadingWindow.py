import tkinter as tk
from tkinter import ttk
from utils.log_config import logger
import UI.Helpers
from UI.Theme import Theme, Palette
from UI.Art import Artwork
from UI.Widgets.Controls import Button
import time
import threading

class LoadingWindow:
    """
    A class to create and manage an animated processing popup window.

    This class creates a popup window with an animated progress bar to indicate
    ongoing processing and a cancel button to abort the operation.

    :param parent: The parent window for this popup
    :type parent: tk.Tk or tk.Toplevel or None
    :param title: The title of the popup window
    :type title: str
    :param initial_text: The initial text to display in the popup
    :type initial_text: str
    :param on_cancel: Callback function to execute when cancel is pressed
    :type on_cancel: callable or None
    :param note_text: Optional note text to display below the initial text
    :type note_text: str or None

    :ivar popup: The main popup window
    :type popup: tk.Toplevel
    :ivar popup_label: The label widget containing the text
    :type popup_label: tk.Label
    :ivar cancelled: Flag indicating if the operation was cancelled
    :type cancelled: bool

    Example
    -------
    >>> root = tk.Tk()
    >>> processing = LoadingWindow(root, title="Processing", initial_text="Loading", note_text="Note: This may take some time.")
    >>> # Do some work here
    >>> if not processing.cancelled:
    ...     # Complete the operation
    >>> processing.destroy()
    """

    MACOS_SIZE = (300, 125)
    WINDOWS_SIZE = (280, 115)
    NOTE_OFFSET = (80, 75)

    def __init__(
        self,
        parent=None,
        title="Processing",
        initial_text="Loading",
        on_cancel=None,
        note_text=None,
    ):
        """
        Initialize the processing popup window.

        :param parent: Parent window for this popup
        :type parent: tk.Tk or tk.Toplevel or None
        :param title: Title of the popup window
        :type title: str
        :param initial_text: Initial text to display
        :type initial_text: str
        :param on_cancel: Callback function to execute when cancel is pressed
        :type on_cancel: callable or None
        :param note_text: Optional note text to display below the initial text
        :type note_text: str or None
        """
        self.title = title
        self.initial_text = initial_text
        self.note_text = note_text
        self.parent = parent
        self.on_cancel = on_cancel
        self.cancelled = False
        self.ui_built = False
        self._close_requested = threading.Event()
        self._close_job = None
        self._destroyed = False
    
        self._startup = getattr(parent, "_startup_coordinator", None)
        self._in_splash = bool(self._startup and self._startup.attach(self))
        if self.parent and not self._in_splash:
            self.parent.after(0, self.build_ui)

    def build_ui(self):
        if self._close_requested.is_set():
            return
        try:
            logger.debug("Building LoadingWindow UI")
            self.popup = tk.Toplevel(self.parent)
            self.popup.title(self.title)
            self.popup.configure(bg=Palette.BG)
            # Works on every platform (a bare .ico is not accepted on Linux)
            UI.Helpers.set_window_icon(self.popup)

            self.artwork = Artwork(self.popup, 'splash-universe', height=130, themed=False, cover=True, fade=True)
            self.artwork.configure(width=Theme.px(380))
            self.artwork.pack(fill='x')
            body = tk.Frame(self.popup, bg=Palette.BG)
            body.pack(fill="both", expand=True, padx=Theme.px(24), pady=(Theme.px(20), Theme.px(16)))

            # Use label and progress bar
            self.label = tk.Label(body, text=self.initial_text, bg=Palette.BG, fg=Palette.TEXT,
                                  font=Theme.font("medium", "semibold"), justify="left", anchor="w",
                                  wraplength=Theme.px(320))
            self.label.pack(fill="x")
            self.progress = ttk.Progressbar(body, mode='indeterminate', length=Theme.px(320))
            self.progress.pack(pady=(Theme.px(12), Theme.px(4)), fill='x')
            self.progress.start()

            # Add note text if provided
            if self.note_text:
                self.note_label = tk.Label(
                    body,
                    text=self.note_text,
                    wraplength=Theme.px(320),
                    justify="left",
                    anchor="w",
                    font=Theme.font("small"),
                    bg=Palette.BG,
                    fg=Palette.TEXT_MUTED,
                )
                self.note_label.pack(fill="x", pady=(Theme.px(6), 0))

            # Add cancel button
            self.cancel_button = Button(
                body, text="Cancel", variant="neutral", command=self._handle_cancel
            )
            self.cancel_button.pack(anchor="e", pady=(Theme.px(14), 0))

            if self.parent:
                # Center the popup window on the parent window
                self.parent.update_idletasks()
                self.popup.update_idletasks()
                x = (
                    self.parent.winfo_x()
                    + (self.parent.winfo_width() - self.popup.winfo_reqwidth()) // 2
                )
                y = (
                    self.parent.winfo_y()
                    + (self.parent.winfo_height() - self.popup.winfo_reqheight()) // 2
                )
                self.popup.geometry(f"+{max(0, x)}+{max(0, y)}")
                self.popup.transient(self.parent)
                
                # Disable the parent window
                UI.Helpers.disable_parent_window(self.parent, self.popup)

            # Not Resizable
            self.popup.resizable(False, False)

            # Disable closing of the popup manually
            self.popup.protocol("WM_DELETE_WINDOW", lambda: None)
            
            logger.debug("LoadingWindow UI built successfully")
            self.ui_built = True
            self._poll_close()
        except Exception:
            logger.exception("Error creating LoadingWindow")
            # Enable the window on exception
            if self.parent:
                UI.Helpers.enable_parent_window(self.parent, self.popup)
            raise

    def set_text(self, text):
        """Change the message shown in the window. Safe to call from any thread."""
        self.initial_text = text

        def apply():
            label = getattr(self, "label", None)
            if label is not None and label.winfo_exists():
                label.configure(text=text)

        if self.parent and not self._in_splash:
            try:
                self.parent.after(0, apply)
            except RuntimeError:
                pass  # main loop no longer running

    def _handle_cancel(self):
        """
        Internal method to handle cancel button press.
        Sets the cancelled flag and calls the user-provided callback if any.
        """
        self.cancelled = True
        if callable(self.on_cancel):
            try:
                self.on_cancel()
            except Exception:
                logger.exception("Error in on_cancel callback")
                self.destroy()

        self.destroy()

    def _poll_close(self):
        if self._destroyed:
            return
        if self._close_requested.is_set():
            self._destroy_ui()
        else:
            self._close_job = self.popup.after(80, self._poll_close)

    def _destroy_ui(self):
        if self._destroyed or not self.ui_built:
            return
        self._destroyed = True
        try:
            if self._close_job is not None:
                self.popup.after_cancel(self._close_job)
                self._close_job = None
            self.progress.stop()
            if self.parent:
                UI.Helpers.enable_parent_window(self.parent, self.popup)
            self.popup.destroy()
        except tk.TclError:
            pass  # The parent may already have closed.

    def destroy(self):
        """Worker-safe cancellation; only the UI thread calls Tk."""
        self._close_requested.set()
        if threading.current_thread() is threading.main_thread():
            self._destroy_ui()
