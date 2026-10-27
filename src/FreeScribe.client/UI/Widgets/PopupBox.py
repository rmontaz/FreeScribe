import tkinter as tk
from tkinter import Toplevel, ttk
from UI.Theme import Theme, Palette

class PopupBox:
    """
    A class to create a popup dialog box with a customizable message and two buttons.

    :param parent: The parent window for the popup dialog.
    :param title: The title of the popup window (default: "Message").
    :param message: The message displayed in the popup (default: "Message text").
    :param button_text_1: The text for the first button (default: "OK").
    :param button_text_2: The text for the second button (default: "Cancel").
    :param button_1_callback: Callback function for the first button (default: None).
    :param button_2_callback: Callback function for the second button (default: None).
    """

    def __init__(self, 
                 parent, 
                 title="Message", 
                 message="Message text", 
                 button_text_1="OK", 
                 button_text_2="Cancel",
                 button_text_3=None,
                 delete_window_button_action=1,
                 button_1_callback=None, 
                 button_2_callback=None,
                 on_open=None):
        """
        Initialize the PopupBox instance and create the dialog window.

        :param parent: The parent widget for the dialog.
        :param title: The title of the dialog window.
        :param message: The message to be displayed in the dialog.
        :param button_text_1: The text label for the first button.
        :param button_text_2: The text label for the second button.
        :param button_1_callback: Optional callback function for the first button.
        :param button_2_callback: Optional callback function for the second button.
        """
        # Stores the response indicating which button was clicked
        self.response = None
        # Create a top-level window for the popup
        self.dialog = Toplevel(parent)
        # Make the exit button behave like the specified button
        delete_window_action = None
        if delete_window_button_action == 1:
            delete_window_action = self.on_button_1
        elif delete_window_button_action == 2:
            delete_window_action = self.on_button_2
        elif delete_window_button_action == 3:
            delete_window_action = self.on_button_3
        
        if delete_window_action:
            self.dialog.protocol("WM_DELETE_WINDOW", delete_window_action)
        # Set the window title
        self.dialog.title(title)
        
        self.dialog.configure(bg=Palette.BG)

        # Create and pack the message label
        label = tk.Label(self.dialog, text=message, wraplength=Theme.px(360), justify="left", anchor="w",
                         bg=Palette.BG, fg=Palette.TEXT, font=Theme.font("medium"))
        label.pack(padx=Theme.px(24), pady=(Theme.px(22), Theme.px(8)), fill="x")

        # Create and pack a frame to hold the buttons
        button_frame = tk.Frame(self.dialog, bg=Palette.BG)
        button_frame.pack(padx=Theme.px(24), pady=(Theme.px(10), Theme.px(18)), anchor="e")

        # Create and pack the first button
        button_1 = ttk.Button(button_frame, text=button_text_1, command=self.on_button_1)
        button_1.pack(side=tk.LEFT, padx=(0, Theme.px(8)))

        # Create and pack the second button
        button_2 = ttk.Button(button_frame, text=button_text_2, command=self.on_button_2)
        button_2.pack(side=tk.RIGHT)

        if button_text_3:
            # Create and pack the third button
            button_3 = ttk.Button(button_frame, text=button_text_3, command=self.on_button_3)
            button_3.pack(side=tk.RIGHT, padx=(0, Theme.px(8)))

        # Update dialog to calculate required height
        self.dialog.update()
        window_height = self.dialog.winfo_reqheight()
        window_width = self.dialog.winfo_reqwidth()

        # Disable window resizing
        self.dialog.resizable(False, False)

        # Center the dialog relative to the parent window
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()

        center_x = parent_x + (parent_width // 2) - (window_width // 2)
        center_y = parent_y + (parent_height // 2) - (window_height // 2)

        # Set final geometry with calculated height
        self.dialog.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")

        # Configure the dialog as a modal window
        self.dialog.transient(parent)
        self.dialog.grab_set()
        if on_open is not None:
            on_open(self)       # e.g. so the question can also be answered from elsewhere
        parent.wait_window(self.dialog)

    def on_button_1(self):
        """
        Handle the event when the first button is clicked.
        Sets the response to 'button_1' and closes the dialog.
        """
        self.response = "button_1"
        self.dialog.destroy()

    def on_button_2(self):
        """
        Handle the event when the second button is clicked.
        Sets the response to 'button_2' and closes the dialog.
        """
        self.response = "button_2"
        self.dialog.destroy()

    def on_button_3(self):
        """
        Handle the event when the third button is clicked.
        Sets the response to 'button_3' and closes the dialog.
        """
        self.response = "button_3"
        self.dialog.destroy()