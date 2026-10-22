"""
MicrophoneTestFrame.py

Keeps track of the available microphones, the one in use, and its live input
level.

Earlier versions of this class also built their own drop-down and volume bar.
The workspace now draws those itself (a microphone picker and a level meter in
the session header), so this class is purely the model behind them: it owns the
monitoring audio stream, exposes ``level`` / ``status_text`` and lets the UI
subscribe to changes with :meth:`add_listener`.

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk
from tkinter import ttk
import pyaudio
import numpy as np
from utils.log_config import logger


class MicrophoneState:
    SELECTED_MICROPHONE_INDEX = None
    SELECTED_MICROPHONE_NAME = None


class MicrophoneTestFrame:
    POLL_INTERVAL_MS = 50
    FRAMES_PER_BUFFER = 1024
    MAX_FRAMES_PER_POLL = 8192

    def __init__(self, parent, p, app_settings, root):
        """
        Initialize the microphone monitor.

        Parameters
        ----------
        parent : tk.Widget
            A widget that outlives the monitor; used to schedule polling.
        p : pyaudio.PyAudio
            The PyAudio instance for audio operations.
        app_settings : SettingsWindow
            Application settings including editable settings.
        root : tk.Tk
            The application root window.
        """
        self.root = root
        self.parent = parent
        self.p = p
        self.app_settings = app_settings
        self.stream = None  # Persistent audio stream
        self.is_stream_active = False  # Track if the stream is active

        self.level = 0.0  # Current input level, 0.0 - 1.0
        self.status_text = "Microphone: Ready"
        self.status_ok = True
        self.enabled = True  # False while recording: the microphone cannot be changed
        self._listeners = []

        # Never laid out; it only anchors the polling timer to the widget tree.
        self.frame = ttk.Frame(self.parent)

        self.mic_list = []
        self.mic_mapping = {}
        self.default_input_index = None

        # Initialize microphone list and settings
        self.initialize_microphones()
        self.refresh_devices()

        # Start volume meter updates
        self.update_volume_meter()

        # Initialize the selected microphone
        self.initialize_selected_microphone()

    # ---------------------------------------------------------------- listeners
    def add_listener(self, callback):
        """Register ``callback()`` to be called when the selection or status changes."""
        self._listeners.append(callback)

    def _notify(self):
        for callback in list(self._listeners):
            try:
                callback()
            except Exception as e:
                logger.exception(f"Microphone listener failed: {e}")

    def _set_status(self, text, ok):
        changed = (text, ok) != (self.status_text, self.status_ok)
        self.status_text, self.status_ok = text, ok
        if changed:
            self._notify()

    # ------------------------------------------------------------------ devices
    def initialize_microphones(self):
        """
        Initialize the list of available microphones.
        """
        self.mic_list = []
        self.mic_mapping = {}  # Maps microphone names to their indices

        try:
            default_input_info = self.p.get_default_input_device_info()
            self.default_input_index = default_input_info['index']
        except IOError as e:
            logger.exception(f"Failed to initialize microphone ({type(e).__name__}): {e}")
            self.default_input_index = None

        try:
            device_count = self.p.get_device_count()
            defaultHostApi = self.p.get_default_host_api_info()
        except (IOError, OSError) as e:
            logger.exception(f"Failed to list audio devices ({type(e).__name__}): {e}")
            return

        for i in range(device_count):
            device_info = self.p.get_device_info_by_index(i)
            if device_info['maxInputChannels'] > 0 and device_info['hostApi'] == defaultHostApi['index']:
                device_name = device_info['name']
                if device_name not in [name for _, name in self.mic_list]:
                    self.mic_list.append((i, device_name))
                    self.mic_mapping[device_name] = i
        # Load the selected microphone from settings if available
        if self.app_settings and "Current Mic" in self.app_settings.editable_settings:
            selected_name = self.app_settings.editable_settings["Current Mic"]
            if selected_name in self.mic_mapping:
                MicrophoneState.SELECTED_MICROPHONE_NAME = selected_name
                MicrophoneState.SELECTED_MICROPHONE_INDEX = self.mic_mapping[selected_name]

    def microphone_names(self):
        """Names of the available input devices."""
        return [name for _, name in self.mic_list]

    @property
    def selected_name(self):
        return MicrophoneState.SELECTED_MICROPHONE_NAME

    def initialize_selected_microphone(self):
        """
        Initialize the selected microphone and open the audio stream.
        """
        if MicrophoneState.SELECTED_MICROPHONE_INDEX is not None:
            self.update_selected_microphone(MicrophoneState.SELECTED_MICROPHONE_INDEX)
        elif self.mic_list:
            self.update_selected_microphone(self.mic_list[0][0])
        self._notify()

    def select_microphone(self, selected_name):
        """
        Switch to the named microphone and remember the choice.
        """
        if not self.enabled:
            return
        if selected_name in self.mic_mapping:
            selected_index = self.mic_mapping[selected_name]
            self.update_selected_microphone(selected_index)
            # save the settings to the file
            self.app_settings.save_settings_to_file()
            self._notify()

    def refresh_devices(self):
        """
        Re-scan the audio devices (microphones may have been plugged in or removed).
        """
        if not self.enabled:
            return  # Ignore while recording

        # Get the current selected microphone name
        current_selected_name = MicrophoneState.SELECTED_MICROPHONE_NAME

        # Release the monitoring stream before PortAudio is restarted
        self._close_stream()

        # Reinitialize PyAudio so that newly connected devices are listed
        if self.p:
            try:
                self.p.terminate()
            except Exception as e:
                logger.exception(f"Error terminating PyAudio: {e}")
        self.p = pyaudio.PyAudio()

        # Reinitialize the microphone list
        self.initialize_microphones()

        # Check if the selected microphone is still available
        if current_selected_name in [name for _, name in self.mic_list]:
            MicrophoneState.SELECTED_MICROPHONE_INDEX = self.mic_mapping[current_selected_name]
            # Reopen the stream with the current selected microphone
            self.reopen_stream()
        elif self.mic_list:
            # If the selected microphone is no longer available, select the first one
            self.update_selected_microphone(self.mic_list[0][0])
        else:
            MicrophoneState.SELECTED_MICROPHONE_INDEX = None
            MicrophoneState.SELECTED_MICROPHONE_NAME = None
            self._set_status("Error: No microphones available", False)
        self._notify()

    def _close_stream(self):
        if self.stream:
            try:
                if self.stream.is_active():
                    self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                logger.exception(f"Error closing stream: {e}")
        self.stream = None
        self.is_stream_active = False
        self.level = 0.0

    def _open_stream(self, device_index):
        self.stream = self.p.open(
            format=pyaudio.paInt16,  # Specifies the format of the audio data. paInt16 means 16-bit int PCM.
            channels=1,             # Specifies the number of channels. 1 for mono, 2 for stereo.
            rate=16000,             # Specifies the sampling rate in Hz. 16000 Hz is a common rate for speech.
            input=True,             # Indicates that this stream will be used for input (recording).
            frames_per_buffer=self.FRAMES_PER_BUFFER,  # Number of samples (per channel) in each buffer.
            input_device_index=device_index  # Specifies the index of the input device to use.
        )
        self.is_stream_active = True

    def update_selected_microphone(self, selected_index):
        """
        Update the selected microphone index and name.

        Parameters
        ----------
        selected_index : int
            The index of the selected microphone.
        """
        if selected_index >= 0:
            try:
                selected_mic = self.p.get_device_info_by_index(selected_index)
                MicrophoneState.SELECTED_MICROPHONE_INDEX = selected_mic["index"]
                MicrophoneState.SELECTED_MICROPHONE_NAME = selected_mic["name"]
                self.app_settings.editable_settings["Current Mic"] = selected_mic["name"]

                # Close existing stream if any, then open one on the selected microphone
                self._close_stream()
                self._open_stream(selected_index)
                self._set_status("Microphone: Connected", True)
            except Exception as e:
                self._set_status("Error: Microphone not found", False)
                logger.exception(f"Failed to open microphone ({type(e).__name__}): {e}")
        else:
            MicrophoneState.SELECTED_MICROPHONE_INDEX = None
            MicrophoneState.SELECTED_MICROPHONE_NAME = None
            self._set_status("Error: No microphone selected", False)

    def reopen_stream(self):
        """
        Reopen the audio stream with the currently selected microphone.
        """
        # Stop and close the existing stream if it is open
        self._close_stream()

        # Open a new stream with the selected microphone
        if MicrophoneState.SELECTED_MICROPHONE_INDEX is not None:
            try:
                self._open_stream(MicrophoneState.SELECTED_MICROPHONE_INDEX)
                self._set_status("Microphone: Connected", True)
            except Exception as e:
                self._set_status("Error: Microphone not found", False)
                logger.exception(f"Failed to open microphone ({type(e).__name__}): {e}")

    # -------------------------------------------------------------------- level
    def update_volume_meter(self):
        """
        Sample the microphone and update ``level``.

        Only audio that is already buffered is read, so this never blocks the
        interface while it waits for the sound card.
        """
        try:
            if not self.frame.winfo_exists():
                return
        except tk.TclError:   # the app is closing; the timer stops quietly
            return

        if not self.is_stream_active or self.stream is None:
            self.level = 0.0
            self.frame.after(self.POLL_INTERVAL_MS, self.update_volume_meter)
            return

        try:
            available = self.stream.get_read_available()
            if available >= 160:  # at least 10 ms of audio
                data = self.stream.read(min(available, self.MAX_FRAMES_PER_POLL), exception_on_overflow=False)
                audio_data = np.frombuffer(data, dtype=np.int16)
                rms = np.sqrt(np.mean(np.square(audio_data.astype(np.float64)))) if audio_data.size else 0.0

                if np.isnan(rms) or rms <= 0:
                    volume = 0
                else:
                    # Adjust the scaling factor to make the meter more sensitive
                    scaling_factor = 1000
                    volume = min(max(int((rms / 32768) * scaling_factor), 0), 100)
                self.level = volume / 100.0

        except OSError as e:
            # Handle both Stream closed and Unanticipated host error
            if e.errno in [-9988, -9999]:
                self._set_status("Error: Microphone disconnected", False)
            else:
                # Handle any other stream errors
                self._set_status("Error: Unknown Error. Check debug log for more.", False)

            logger.exception(f"Error in update_volume_meter({type(e).__name__}): {e}")
            self.is_stream_active = False
            self.stream = None
            self.level = 0.0

        self.frame.after(self.POLL_INTERVAL_MS, self.update_volume_meter)

    @staticmethod
    def get_selected_microphone_index():
        """
        Get the selected microphone index.
        """
        return MicrophoneState.SELECTED_MICROPHONE_INDEX

    def set_mic_test_state(self, enabled):
        """
        Allow or prevent changing the microphone.

        The microphone cannot be switched while a recording is in progress, so the
        picker is disabled for that time. The level meter keeps running either way.

        Args:
            enabled (bool): If True the microphone can be changed, otherwise not.
        """
        enabled = bool(enabled)
        if enabled != self.enabled:
            self.enabled = enabled
            self._notify()

    def __del__(self):
        """
        Clean up resources when the object is destroyed.
        """
        if self.stream:
            try:
                if self.stream.is_active():
                    self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                logger.exception(f"Error closing stream in destructor: {e}")
        if self.p:
            self.p.terminate()
