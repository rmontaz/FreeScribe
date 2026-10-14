import tkinter as tk
from tkinter import ttk
from utils.log_config import logger
import UI.Helpers
from UI.Theme import Theme, Palette
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
    
        if self.parent:
            self.parent.after(0, self.build_ui)

    def build_ui(self):
        try:
            logger.debug("Building LoadingWindow UI")
            self.popup = tk.Toplevel(self.parent)
            self.popup.title(self.title)
            self.popup.configure(bg=Palette.BG)
            # Works on every platform (a bare .ico is not accepted on Linux)
            UI.Helpers.set_window_icon(self.popup)

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
            self.cancel_button = ttk.Button(
                body, text="Cancel", command=self._handle_cancel
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

        if self.parent:
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

    def destroy(self):
        """
        Clean up and destroy the popup window.

        This method performs the following cleanup operations:
        1. Stops the progress bar animation
        2. Re-enables the parent window
        3. Destroys the popup window

        Note
        ----
        This method should be called when you want to close the popup window,
        rather than destroying the window directly.

        Example
        -------
        >>> popup = LoadingWindow()
        >>> # Do some processing
        >>> popup.destroy()  # Properly clean up and close the window
        """
        def _destroy_ui_main_thread():
            if self.popup:
                # Enable the parent window
                if self.parent:
                    logger.debug("Enabling parent window")
                    UI.Helpers.enable_parent_window(self.parent, self.popup)

                if hasattr(self, 'progress') and self.progress:
                    logger.debug("Stopping progress bar animation")
                    if self.progress.winfo_exists():
                        # Stop the progress bar animation
                        self.progress.stop()

                if self.popup.winfo_exists():
                    logger.debug("Destroying popup window")
                    self.popup.destroy()

        def _wait_for_ui_and_destroy():
            start_time = time.time()
            logger.debug("Waiting for LoadingWindow UI to be built")
            while not self.ui_built:
                elapsed_time = time.time() - start_time
                if int(elapsed_time) % 2 == 0 or elapsed_time < 1:
                    logger.info(f"Waiting for LoadingWindowUI to build (elapsed={elapsed_time}s, built={self.ui_built})")
                time.sleep(0.1)
                
            # Schedule the UI destruction on the main thread
            logger.debug("LoadingWindow UI is built, proceeding to destroy it")
            self.popup.after(0, _destroy_ui_main_thread)

        # Run the destroy logic in a separate thread to make it non-blocking
        destroy_thread = threading.Thread(target=_wait_for_ui_and_destroy, daemon=True)
        destroy_thread.start()

