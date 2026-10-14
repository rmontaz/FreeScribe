import tkinter as tk
from tkinter import ttk
from UI.SettingsConstant import SettingsKeys
import UI.Helpers
import UI.MainWindow as mw
from UI.ImageWindow import ImageWindow
from UI.SettingsConstant import FeatureToggle
from UI.SettingsWindowUI import SettingsWindowUI
from UI.MarkdownWindow import MarkdownWindow
from UI.Widgets.ActionResultsWindow import ActionResultsWindow
from services.intent_actions.manager import IntentActionManager
from utils.file_utils import get_file_path
from UI.DebugWindow import DebugPrintWindow
from UI.RecordingsManager import RecordingsManager
from UI.LogWindow import LogWindow
from UI.SettingsWindow import SettingsWindow
from utils.log_config import logger
from pathlib import Path

DOCKER_CONTAINER_CHECK_INTERVAL = 10000  # Interval in milliseconds to check the Docker container status
DOCKER_DESKTOP_CHECK_INTERVAL = 10000  # Interval in milliseconds to check the Docker Desktop status

class MainWindowUI:
    """
    This class handles the user interface (UI) for the main application window, 
    including the Docker status bar for managing the LLM and Whisper containers.

    :param root: The root Tkinter window.
    :param settings: The application settings passed to control the containers' behavior.
    """
    
    def __init__(self, root: tk.Tk, settings: SettingsWindow):
        """
        Initialize the MainWindowUI class.

        :param root: The Tkinter root window.
        :param settings: The application settings used to control container states.
        """
        self.root = root  # Tkinter root window
        self.docker_status_bar = None  # Docker status bar frame
        self.is_status_bar_enabled = False  # Flag to indicate if the Docker status bar is enabled
        self.app_settings = settings  # Application settings
        self.logic = mw.MainWindow(self.app_settings)  # Logic to control the container behavior
        self.setting_window = SettingsWindowUI(self.app_settings, self, self.root)  # Settings window
        UI.Helpers.set_window_icon(self.root)
        self.debug_window_open = False  # Flag to indicate if the debug window is open

        self.warning_bar = None # Warning bar
        self.workspace = None  # The main workspace UI, attached once it has been built
        self.menu_bar = None  # Retained for compatibility; navigation now lives in the workspace sidebar
        
        if FeatureToggle.INTENT_ACTION:
            # Initialize intent action system
            maps_dir = Path(get_file_path('assets', 'maps'))
            maps_dir.mkdir(parents=True, exist_ok=True)
            self.intent_manager = IntentActionManager(
                maps_dir,
                self.app_settings.editable_settings.get("Google Maps API Key", "")
            )
            self.action_window = ActionResultsWindow(self.root)
            self.action_window.hide()  # Hide initially

        self.current_docker_status_check_id = None  # ID for the current Docker status check
        self.current_container_status_check_id = None  # ID for the current container status check

    def attach_workspace(self, workspace):
        """
        Connect the main workspace so that warnings, status bars and the settings
        lock are shown inside it.

        :param workspace: The :class:`UI.Workspace.Workspace` instance.
        """
        self.workspace = workspace

    def load_main_window(self):
        """
        Load the main window of the application.
        Brings the window to the front. Settings, help and data management are
        reached from the workspace sidebar rather than a menu bar.
        """
        self._bring_to_focus()

    def _bring_to_focus(self):
        """
        Bring the main window to focus.
        """
        self.root.lift()  # Lift the window to the top
        self.root.attributes('-topmost', True)  # Set the window to be always on top
        self.root.focus_force()  # Force focus on the window
        self.root.after_idle(self.root.attributes, '-topmost', False)  # Reset the always on top attribute after idle

    def create_docker_status_bar(self):
        """
        Create a Docker status bar to display the status of the LLM and Whisper containers.
        Adds start and stop buttons for each container.
        """
        # if not feature do not do this
        if FeatureToggle.DOCKER_STATUS_BAR is not True:
            return
        
        if self.docker_status_bar is not None:
            return

        # Create the frame for the Docker status bar, placed at the bottom of the window
        host = self.workspace.status_host if self.workspace is not None else self.root
        self.docker_status_bar = tk.Frame(host, bd=1, relief=tk.SUNKEN)
        if self.workspace is not None:
            self.docker_status_bar.pack(fill='x')
        else:
            self.docker_status_bar.grid(row=4, column=0, columnspan=14, sticky='nsew')

        # Add a label to indicate Docker container status section
        self.docker_status = tk.Label(self.docker_status_bar, text="Docker Container Status:", padx=10)
        self.docker_status.pack(side=tk.LEFT)

        # Add LLM container status label
        llm_status = tk.Label(self.docker_status_bar, text="LLM Container Status:", padx=10)
        llm_status.pack(side=tk.LEFT)

        # Add status dot for LLM (default: red)
        llm_dot = tk.Label(self.docker_status_bar, text='●', fg='red')
        self.logic.container_manager.update_container_status_icon(llm_dot, self.app_settings.editable_settings["LLM Container Name"])
        llm_dot.pack(side=tk.LEFT)

        # Add Whisper server status label
        whisper_status = tk.Label(self.docker_status_bar, text="Whisper Server Status:", padx=10)
        whisper_status.pack(side=tk.LEFT)

        # Add status dot for Whisper (default: red)
        whisper_dot = tk.Label(self.docker_status_bar, text='●', fg='red')
        self.logic.container_manager.update_container_status_icon(whisper_dot, self.app_settings.editable_settings["Whisper Container Name"])
        whisper_dot.pack(side=tk.LEFT)

        # Start button for Whisper container with a command to invoke the start method from logic
        start_whisper_button = tk.Button(self.docker_status_bar, text="Start Whisper", command=lambda: self.logic.start_whisper_container(whisper_dot, self.app_settings))
        start_whisper_button.pack(side=tk.RIGHT)

        # Start button for LLM container with a command to invoke the start method from logic
        start_llm_button = tk.Button(self.docker_status_bar, text="Start LLM", command=lambda: self.logic.start_LLM_container(llm_dot, self.app_settings))
        start_llm_button.pack(side=tk.RIGHT)

        # Stop button for Whisper container with a command to invoke the stop method from logic
        stop_whisper_button = tk.Button(self.docker_status_bar, text="Stop Whisper", command=lambda: self.logic.stop_whisper_container(whisper_dot, self.app_settings))
        stop_whisper_button.pack(side=tk.RIGHT)

        # Stop button for LLM container with a command to invoke the stop method from logic
        stop_llm_button = tk.Button(self.docker_status_bar, text="Stop LLM", command=lambda: self.logic.stop_LLM_container(llm_dot, self.app_settings))
        stop_llm_button.pack(side=tk.RIGHT)

        self.is_status_bar_enabled = True
        self._background_availbility_docker_check()
        self._background_check_container_status(llm_dot, whisper_dot)

    def create_warning_bar(self, text, closeButton=True):
        """
        Show a warning banner at the top of the workspace, for example to tell the
        user that no audio is being picked up by the microphone.

        :param text: The message to display.
        :param closeButton: Whether the user can dismiss the banner.
        """
        if self.workspace is not None:
            self.workspace.show_warning(text, closable=closeButton, kind="silence")
        # Any non-None value marks the warning as visible for callers that check it.
        self.warning_bar = text

    def destroy_warning_bar(self):
        """
        Remove the warning banner if it is showing.
        """
        if self.warning_bar is not None:
            if self.workspace is not None:
                self.workspace.hide_warning(kind="silence")
            self.warning_bar = None

    def disable_docker_ui(self):
        """
        Disable the Docker status bar UI elements.
        """
        
        if FeatureToggle.DOCKER_STATUS_BAR is not True:
            return

        
        self.is_status_bar_enabled = False
        self.docker_status.config(text="(Docker not found)")
        if self.docker_status_bar is not None:
            for child in self.docker_status_bar.winfo_children():
                child.configure(state='disabled')

    def enable_docker_ui(self):
        """
        Enable the Docker status bar UI elements.
        """

        if FeatureToggle.DOCKER_STATUS_BAR is not True:
            return
        
        self.is_status_bar_enabled = True
        self.docker_status.config(text="Docker Container Status: ")
        if self.docker_status_bar is not None:
            for child in self.docker_status_bar.winfo_children():
                child.configure(state='normal')

    def destroy_docker_status_bar(self):
        """
        Destroy the Docker status bar if it exists.
        """
        if FeatureToggle.DOCKER_STATUS_BAR is not True:
            return
            
        if self.docker_status_bar is not None:
            self.docker_status_bar.destroy()
            self.docker_status_bar = None

        # cancel the check loop as the bar no longer exists and it is waster resources.
        if self.current_docker_status_check_id is not None:
            self.root.after_cancel(self.current_docker_status_check_id)
            self.current_docker_status_check_id = None
        
        if self.current_container_status_check_id is not None:
            self.root.after_cancel(self.current_container_status_check_id)
            self.current_container_status_check_id = None

    def toggle_menu_bar(self, enable: bool, is_recording: bool = False):
        """
        Re-apply the settings lock after switching between the full and compact views.

        :param enable: True when the full view (with its navigation) is shown.
        :type enable: bool
        :param is_recording: Whether a recording is in progress.
        :type is_recording: bool
        """
        if enable and is_recording:
            self.disable_settings_menu()

    def disable_settings_menu(self):
        """
        Disable access to Settings (while recording or while models are loading).
        """
        def action():
            if self.workspace is not None:
                self.workspace.set_settings_enabled(False)
        self.root.after(0, action)  # Schedule the action to run after the current event loop

    def enable_settings_menu(self):
        """
        Re-enable access to Settings.
        """
        def action():
            if self.workspace is not None:
                self.workspace.set_settings_enabled(True)

        self.root.after(0, action)  # Schedule the action to run after the current event loop

    def open_settings(self):
        """Open the settings window."""
        self.setting_window.open_settings_window()

    def open_help_guide(self):
        """Show the annotated guide to the main window."""
        ImageWindow(self.root, "Help Guide", get_file_path('assets', 'help.png'), width=1250, height=858)

    def open_about(self):
        """Show the About page."""
        self._show_md_content(get_file_path('markdown', 'help', 'about.md'), 'About')

    def open_debug_window(self):
        """Open the debug output window."""
        DebugPrintWindow(self)

    def open_recordings_manager(self):
        """Open the stored recordings manager."""
        RecordingsManager(self.root)

    def open_log_window(self):
        """Open the log manager."""
        LogWindow(self.root)

    def data_options(self):
        """
        Which data managers are available, based on the current settings.

        :returns: ``{"recordings": bool, "logs": bool}``
        :rtype: dict
        """
        settings = self.app_settings.editable_settings
        return {
            "recordings": bool(settings[SettingsKeys.STORE_RECORDINGS_LOCALLY.value]),
            "logs": bool(settings[SettingsKeys.ENABLE_FILE_LOGGER.value]),
        }

    def _show_md_content(self, file_path: str, title: str, show_checkbox: bool = False):
        """
        Private method to display help/about information.
        Display help information in a message box.
        This method shows a message box with information about the application when the About option is selected from the Help menu.
        """

        # Callback function called when the window is closed
        def on_close(checkbox_state):
            self.setting_window.settings.editable_settings['Show Welcome Message'] = not checkbox_state
            self.setting_window.settings.save_settings_to_file()
        
        # Create a MarkdownWindow to display the content
        MarkdownWindow(self.root, title, file_path, 
                  callback=on_close if show_checkbox else None)
            

    def _on_help_window_close(self, help_window, dont_show_again: tk.BooleanVar):
        """
        Private method to handle the closing of the help window.
        Updates the 'Show Welcome Message' setting based on the checkbox state.
        """
        self.setting_window.settings.editable_settings['Show Welcome Message'] = not dont_show_again.get()
        self.setting_window.settings.save_settings_to_file()
        help_window.destroy()
    
    def show_welcome_message(self):
        """
        Private method to display a welcome message.
        Display a welcome message when the application is launched.
        This method shows a welcome message in a message box when the application is launched.
        """
        self._show_md_content(get_file_path('markdown','welcome.md'), 'Welcome', True)
    

    def _background_availbility_docker_check(self):
        """
        Check if the Docker client is available in the background.

        This method is intended to be run in a separate thread to periodically
        check the availability of the Docker client.
        """
        logger.info("Checking Docker availability in the background...")
        if self.logic.container_manager.check_docker_availability():
            # Enable the Docker status bar UI elements if not enabled
            if not self.is_status_bar_enabled:
                self.enable_docker_ui()
            logger.info("Docker client is available.")
        else:
            # Disable the Docker status bar UI elements if not disabled
            if self.is_status_bar_enabled:
                self.disable_docker_ui()

            logger.info("Docker client is not available.")

        self.current_docker_status_check_id = self.root.after(DOCKER_DESKTOP_CHECK_INTERVAL, self._background_availbility_docker_check)

    def _background_check_container_status(self, llm_dot, whisper_dot):
        """
        Check the status of Docker containers in the background.

        This method is intended to be run in a separate thread to periodically
        check the status of the LLM and Whisper containers.
        """
        if self.is_status_bar_enabled:
            self.logic.container_manager.set_status_icon_color(llm_dot, self.logic.check_llm_containers())
            self.logic.container_manager.set_status_icon_color(whisper_dot, self.logic.check_whisper_containers())
            self.current_container_status_check_id = self.root.after(DOCKER_CONTAINER_CHECK_INTERVAL, self._background_check_container_status, llm_dot, whisper_dot)
    
    def get_text_intents(self, text: str) -> None:
        """
        Process transcribed text for intents and execute actions.
        
        :param text: Transcribed text to process
        """
        text = text.strip()
        if not text:
            return
        # Process text through intent manager
        results = self.intent_manager.process_text(text)
        logger.debug(f"Results: {results}")

        if results:
            logger.debug("Showing action window")
            # Show action window and add results
            self.action_window.show()
            # self.action_window.clear()
            self.action_window.add_results(results)
            
    def close_action_window(self) -> None:
        """Close the action results window."""
        self.action_window.hide()


