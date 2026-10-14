"""
This software is released under the AGPL-3.0 license
Copyright (c) 2023-2024 Braedon Hendy

Further updates and packaging added in 2024 through the ClinicianFOCUS initiative,
a collaboration with Dr. Braedon Hendy and Conestoga College Institute of Applied
Learning and Technology as part of the CNERG+ applied research project,
Unburdening Primary Healthcare: An Open-Source AI Clinician Partner Platform".
Prof. Michael Yingbull (PI), Dr. Braedon Hendy (Partner),
and Research Students - Software Developer Alex Simko, Pemba Sherpa (F24), and Naitik Patel.

"""

import ctypes
import io
import sys
import gc
import os
import logging
from pathlib import Path
import wave
import threading
import json
import datetime
import re
import time
import queue
import atexit
import torch
import pyaudio
import requests
import pyperclip
import speech_recognition as sr  # python package is named speechrecognition
import scrubadub
import numpy as np
import tkinter as tk
import math
import traceback
import asyncio
from tkinter import filedialog
import tkinter.messagebox as messagebox
import librosa
from faster_whisper import WhisperModel
from UI.MainWindowUI import MainWindowUI
from UI.SettingsWindow import SettingsWindow
from UI.SettingsConstant import SettingsKeys, FeatureToggle
from UI.LoadingWindow import LoadingWindow
from UI.ImageWindow import ImageWindow
from Model import ModelManager
from utils.ip_utils import is_private_ip
from utils.file_utils import get_file_path, get_resource_path
from utils.OneInstance import OneInstance
from utils.utils import get_application_version
import utils.AESCryptoUtils as AESCryptoUtils
import utils.audio
import utils.AESCryptoUtils as AESCryptoUtils
import utils.system
from UI.Widgets.MicrophoneTestFrame import MicrophoneTestFrame
from utils.windows_utils import remove_min_max, add_min_max
from WhisperModel import TranscribeError
from UI.Widgets.PopupBox import PopupBox
from UI.RecordingsManager import RecordingsManager
from UI.ScrubWindow import ScrubWindow
from utils.log_config import logger
from Model import ModelStatus
from services.whisper_hallucination_cleaner import hallucination_cleaner
from utils.whisper.WhisperModel import load_stt_model, faster_whisper_transcribe, is_whisper_valid, is_whisper_lock, load_model_with_loading_screen, unload_stt_model, get_model_from_settings, WhisperModelStatus, get_whisper_model, set_whisper_model
from services.factual_consistency import find_factual_inconsistency
import utils.arg_parser
from services.whisper_hallucination_cleaner import hallucination_cleaner, load_hallucination_cleaner_model
from utils.log_config import logger
from UI.NoteStyleSelector import NoteStyleSelector
from utils.network.base import NetworkConfig
from utils.network.openai_client import OpenAIClient
from urllib.parse import urlparse
from UI.Theme import Theme
from UI.Workspace import Workspace
from services.sessions import (SessionStore, DETAIL_LEVELS, DEFAULT_DETAIL_LEVEL, compose_source,
                               compose_revision_prompt)
from services import form_filler
from UI.FormFillWindow import FormFillWindow
from services.chat import compose_chat_prompt, form_event_text
from services.dictation import Dictation, to_wav_bytes
from services.medical_vocab import (DrugLexicon, VisitLanguage, build_speech_prompt, is_prompt_echo,
                                    split_terms)
from utils.ui_state import UIState

# parse command line arguments
utils.arg_parser.parse_args()

APP_NAME = 'AI Medical Scribe'  # Application name
if utils.system.is_windows():
    APP_TASK_MANAGER_NAME = 'freescribe-client.exe'
else:
    APP_TASK_MANAGER_NAME = 'FreeScribe'

logger.info(f"{APP_NAME=} {APP_TASK_MANAGER_NAME=} {get_application_version()=}")

# check if another instance of the application is already running.
# if false, create a new instance of the application
# if true, exit the current instance
app_manager = OneInstance(APP_NAME, APP_TASK_MANAGER_NAME)

if app_manager.is_running():
    # Another instance is running
    sys.exit(1)
else:
    # No other instance is running, or we successfully terminated the other instance
    root = tk.Tk()
    root.title(APP_NAME)
    # Colours, fonts and widget styling shared by every window
    Theme.init(root)

if utils.system.is_macos():
    utils.system.install_macos_ssl_certificates()


def delete_temp_file(filename):
    """
    Deletes a temporary file if it exists.

    Args:
        filename (str): The name of the file to delete.
    """
    file_path = get_resource_path(filename)
    if os.path.exists(file_path):
        try:
            logger.info(f"Deleting temporary file: {filename}")
            os.remove(file_path)
        except OSError as e:
            logger.exception(f"Error deleting temporary file {filename}: {e}")

def on_closing():
    delete_temp_file('recording.wav')
    delete_temp_file('realtime.wav')

    # Keep the session history only if the user chose to store it; otherwise
    # make sure nothing from this run is left on disk.
    try:
        if storing_notes_locally():
            save_notes_history()
        else:
            wipe_notes_history_file()
    except Exception as e:
        logger.exception(f"Error finalising session history on exit: {e}")

    # Cancel any pending async operations
    try:
        loop = asyncio.get_running_loop()
        if loop and not loop.is_closed():
            # Cancel all pending tasks
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
    except RuntimeError:
        pass  # No running loop

    app_manager.cleanup()


# Register the cleanup function to be called on exit
atexit.register(on_closing)

# Every encounter (transcript, context, note, extra documents) lives in a session.
session_store = SessionStore()
notes_save_job = None
# How long the last save of the session history took, in seconds
notes_save_duration = 0.0


def notes_history_path():
    """Path of the encrypted session history file."""
    return get_resource_path('notes_history.txt')


def storing_notes_locally():
    """Whether the user has chosen to keep sessions on disk (encrypted)."""
    return bool(app_settings.editable_settings[SettingsKeys.STORE_NOTES_LOCALLY.value])


def enable_notes_history(event=None):
    """
    Enables the 'Store Notes Locally' setting in the application settings.
    Sessions already on disk are loaded and the ones from this run are kept from now on.
    """
    load_notes_history()
    save_notes_history()
    workspace.set_storage_mode(True)

root.bind("<<EnableNoteHistory>>", enable_notes_history)

def disable_notes_history(event=None):
    """
    Disables the 'Store Notes Locally' setting in the application settings.
    Clears all existing sessions and updates the UI accordingly.
    """
    clear_all_notes()
    workspace.set_storage_mode(False)

root.bind("<<DisableNoteHistory>>", disable_notes_history)

def load_notes_history():
    """
    Loads the saved sessions from the local encrypted history file and shows them in the sidebar.
    Sessions created during this run are kept alongside the loaded ones.
    """
    notes_file_path = notes_history_path()
    in_memory = [session for session in session_store.sessions if not session.is_empty]
    try:
        count = session_store.load(notes_file_path, AESCryptoUtils.AESCryptoUtilsClass.decrypt)
        logger.info(f"{count} saved sessions loaded from {notes_file_path}")
    except Exception as e:
        logger.exception(f"Error loading saved sessions: {e}")
        session_store.sessions = []
        # Do not let the next save silently overwrite a history that could not be read.
        try:
            backup_path = f"{notes_file_path}.unreadable-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
            os.replace(notes_file_path, backup_path)
            logger.warning(f"Unreadable session history moved to {backup_path}")
        except OSError as backup_error:
            logger.exception(f"Could not back up unreadable session history: {backup_error}")

    known_ids = {session.id for session in session_store.sessions}
    for session in reversed(in_memory):
        if session.id not in known_ids:
            session_store.sessions.insert(0, session)

    workspace.reload_sessions()


def wipe_notes_history_file():
    """Empties the session history file."""
    notes_file_path = notes_history_path()
    try:
        if os.path.exists(notes_file_path):
            with open(notes_file_path, 'w') as file:
                file.write("")  # Write an empty string to clear the file
            logger.info(f"Session history file cleared: {notes_file_path}")
    except Exception as e:
        logger.exception(f"Error clearing session history file: {e}")


def clear_all_notes():
    """
    Clears all sessions from the UI and the history file.
    """
    session_store.clear()
    wipe_notes_history_file()
    workspace.reload_sessions()


def on_sessions_cleared():
    """Called after the user deleted every session from the sidebar."""
    wipe_notes_history_file()


def schedule_notes_save():
    """Saves the sessions shortly after the last edit, when local storage is enabled."""
    global notes_save_job
    if not storing_notes_locally():
        return
    if notes_save_job is not None:
        try:
            root.after_cancel(notes_save_job)
        except tk.TclError:
            pass

    def action():
        global notes_save_job, notes_save_duration
        notes_save_job = None
        if storing_notes_locally():
            started = time.monotonic()
            save_notes_history()
            notes_save_duration = time.monotonic() - started

    # The whole history is written each time, so with a very long history wait
    # longer between saves rather than interrupting typing.
    delay_ms = int(max(1500, min(30000, notes_save_duration * 20000)))
    notes_save_job = root.after(delay_ms, action)


def run_on_ui(function, *args, **kwargs):
    """Runs ``function`` on the Tk main thread. Safe to call from worker threads."""
    root.after(0, lambda: function(*args, **kwargs))


def report_error(message):
    """Shows an error to the user in a dismissible banner above the session."""
    logger.error(message)
    run_on_ui(lambda: workspace.show_warning(message, closable=True, kind="error"))


def set_transcript_status(text=None):
    """Shows a temporary hint in the empty transcript area (e.g. while audio is processed)."""
    run_on_ui(lambda: workspace.set_transcript_status(text))


def safe_set_transcription_box(text, callback=None):
    """
    Safely sets the transcript of the current session from any thread.

    Args:
        text (str): The transcribed text.
        callback (callable, optional): Function to call after text is updated.
    """
    def update_text():
        workspace.set_transcript_result(text)
        if callback:
            callback()
    root.after(0, update_text)

# This runs before on_closing, if not confirmed, nothing should be changed
def confirm_exit_and_destroy():
    """Show confirmation dialog before exiting the application.

    Warns when a recording is still running, and when sessions are not being
    stored locally (in which case they are cleared on exit). If the user
    confirms, the window closes. If canceled, the application remains open.

    .. note::
        This function is bound to the window's close button (WM_DELETE_WINDOW protocol).

    :returns: None
    :rtype: None
    """
    if is_recording:
        if not messagebox.askokcancel(
                "Confirm Exit",
                "A recording is in progress.\n\n"
                "If you exit now, the audio that has not been transcribed yet will be lost.\n\n"
                "Do you still want to exit?"
        ):
            return

    try:
        workspace.flush_edits()
    except Exception as e:
        logger.exception(f"Error flushing edits before exit: {e}")

    has_sessions = any(not session.is_empty for session in session_store.sessions)
    if has_sessions and not storing_notes_locally():
        if not messagebox.askokcancel(
                "Confirm Exit",
                "Warning: Your sessions are not stored and will be cleared when the app closes.\n\n"
                "Please make sure you have copied or exported your important notes "
                "before closing.\n\n"
                "Do you still want to exit?"
        ):
            return

    try:
        if current_view == "full" and root.wm_state() == "normal":
            ui_state.set("window_size", f"{root.winfo_width()}x{root.winfo_height()}")
    except Exception as e:
        logger.exception(f"Error saving window size: {e}")

    root.destroy()


# remind user notes will be gone after exiting
root.protocol("WM_DELETE_WINDOW", confirm_exit_and_destroy)

# settings logic
app_settings = SettingsWindow()

if app_settings.editable_settings[SettingsKeys.ENABLE_FILE_LOGGER.value]:
    utils.log_config.add_file_handler(utils.log_config.logger, format=utils.log_config.AESEncryptedFormatter())

#  create our ui elements and settings config
window = MainWindowUI(root, app_settings)

app_settings.set_main_window(window)

# Interface preferences that are not application settings (level of detail, window size ...)
ui_state = UIState()

user_message = []
current_view = "full"
username = "user"
botname = "Assistant"
num_lines_to_keep = 20
uploaded_file_path = None
is_recording = False
recording_thread = None
is_realtimeactive = False
audio_data = []
frames = []
is_paused = False
is_flashing = False
use_aiscribe = True
is_gpt_button_active = False
p = pyaudio.PyAudio()
audio_queue = queue.Queue()
CHUNK = 512
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
silent_warning_duration = 0
# The session a note is currently being generated for
generation_session_id = None

# Application flags
is_audio_processing_realtime_canceled = threading.Event()
is_audio_processing_whole_canceled = threading.Event()
cancel_await_thread = threading.Event()


# Thread tracking variables
REALTIME_TRANSCRIBE_THREAD_ID = None
GENERATION_THREAD_ID = None
# The Thread objects behind the IDs above. Thread IDs are recycled by the operating
# system, so a remembered ID may later belong to an unrelated thread; kill_thread()
# uses this to leave such threads alone.
tracked_threads = {}


def track_thread(thread):
    """Remembers ``thread`` so kill_thread() can tell if it has already finished. Returns its ID."""
    tracked_threads[thread.ident] = thread
    return thread.ident


# ---------------------------------------------------------------- speech vocabulary
visit_language = VisitLanguage()      # the language kept for the visit being transcribed (Auto-detect)
_drug_lexicon = None


def get_drug_lexicon():
    """Drug names from Health Canada's Drug Product Database and RxNorm (loaded once)."""
    global _drug_lexicon
    if _drug_lexicon is None:
        try:
            _drug_lexicon = DrugLexicon.load(get_file_path('assets', 'medical', 'drug_names.txt'),
                                             get_file_path('assets', 'medical', 'common_lookalikes.txt'))
        except Exception as e:
            logger.exception(f"Drug names could not be loaded: {e}")
            _drug_lexicon = DrugLexicon()
    return _drug_lexicon


def find_drug_flags(text):
    """Words in ``text`` that look like a misheard drug name, when the check is on."""
    if not app_settings.editable_settings.get(SettingsKeys.DRUG_NAME_CHECK.value, True):
        return []
    try:
        return get_drug_lexicon().flags(text)
    except Exception as e:
        logger.exception(f"Drug-name check failed: {e}")
        return []


def speech_options():
    """(language, prompt) for the speech-to-text model during the current visit."""
    settings = app_settings.editable_settings
    code = str(settings.get(SettingsKeys.WHISPER_LANGUAGE_CODE.value, "")).strip()
    language = None if code in SettingsWindow.AUTO_DETECT_LANGUAGE_CODES else code
    if language is None:
        language = visit_language.language
    own = split_terms(str(settings.get(SettingsKeys.MEDICAL_VOCABULARY.value, "") or ""))
    legacy = str(settings.get(SettingsKeys.WHISPER_INITIAL_PROMPT.value, "") or "").strip()
    if legacy and legacy not in SettingsWindow.AUTO_DETECT_LANGUAGE_CODES:
        own = [legacy] + own
    session = workspace.current if 'workspace' in globals() else None
    context = session.context if session is not None else ""
    prompt = build_speech_prompt(context, own, get_drug_lexicon())
    return language, prompt


def transcribe_local(audio):
    """Local Whisper with the visit's language and vocabulary; keeps the language once it is clear."""
    language, prompt = speech_options()
    info = {}
    text = faster_whisper_transcribe(audio, app_settings=app_settings, language=language, prompt=prompt, info=info)
    if language is None and info.get("language"):
        if visit_language.observe(info["language"], info.get("language_probability", 0.0), info.get("seconds", 0.0)):
            logger.info(f"Transcription language kept for this visit: {visit_language.language}")
            run_on_ui(workspace._refresh_meta)
    if is_prompt_echo(text, prompt):
        logger.info("Dropped a piece of transcript that only repeated the vocabulary prompt")
        return ""
    return text


def get_prompt(formatted_message):

    sampler_order = app_settings.editable_settings["sampler_order"]
    if isinstance(sampler_order, str):
        sampler_order = json.loads(sampler_order)
    return {
        "prompt": f"{formatted_message}\n",
        "use_story": app_settings.editable_settings["use_story"],
        "use_memory": app_settings.editable_settings["use_memory"],
        "use_authors_note": app_settings.editable_settings["use_authors_note"],
        "use_world_info": app_settings.editable_settings["use_world_info"],
        "max_context_length": int(app_settings.editable_settings["max_context_length"]),
        "max_length": int(app_settings.editable_settings["max_length"]),
        "rep_pen": float(app_settings.editable_settings["rep_pen"]),
        "rep_pen_range": int(app_settings.editable_settings["rep_pen_range"]),
        "rep_pen_slope": float(app_settings.editable_settings["rep_pen_slope"]),
        "temperature": float(app_settings.editable_settings["temperature"]),
        "tfs": float(app_settings.editable_settings["tfs"]),
        "top_a": float(app_settings.editable_settings["top_a"]),
        "top_k": int(app_settings.editable_settings["top_k"]),
        "top_p": float(app_settings.editable_settings["top_p"]),
        "typical": float(app_settings.editable_settings["typical"]),
        "sampler_order": sampler_order,
        "singleline": app_settings.editable_settings["singleline"],
        "frmttriminc": app_settings.editable_settings["frmttriminc"],
        "frmtrmblln": app_settings.editable_settings["frmtrmblln"]
    }


def threaded_check_stt_model():
    """
    Starts a new thread to check the status of the speech-to-text (STT) model loading process.

    A separate thread is spawned to run the `double_check_stt_model_loading` function,
    which monitors the loading of the STT model. The function waits for the task to be completed and
    handles cancellation if requested.
    """
    # Create a Boolean variable to track if the task is done/canceled
    task_done_var = tk.BooleanVar(value=False)
    task_cancel_var = tk.BooleanVar(value=False)

    # Start a new thread to run the double_check_stt_model_loading function
    stt_thread = threading.Thread(target=double_check_stt_model_loading, args=(task_done_var, task_cancel_var))
    stt_thread.start()

    # Wait for the task_done_var to be set to True (indicating task completion)
    root.wait_variable(task_done_var)

    # Check if the task was canceled via task_cancel_var
    if task_cancel_var.get():
        logger.debug("double checking canceled")
        return False
    return True


def threaded_toggle_recording(button=None, resume=False):
    """Starts or stops recording on a worker thread.

    :param button: Unused; kept for callers that still pass the button.
    :param resume: When starting, continue in the current session instead of opening a new one.
    """
    # quick fix: prevents the button being clicked repeatedly in short time, avoid UI freeze
    workspace.record_button.set_enabled(False)
    workspace.compact_record.set_enabled(False)
    root.after(1000, lambda: (workspace.record_button.set_enabled(True), workspace.compact_record.set_enabled(True)))

    ready_flag = threaded_check_stt_model()
    # there is no point start recording if we are using local STT model and it's not ready
    # if user chooses to cancel the double check process, we need to return and not start recording
    if not ready_flag:
        return
    thread = threading.Thread(target=toggle_recording, kwargs={"resume": resume})
    thread.start()


def double_check_stt_model_loading(task_done_var, task_cancel_var):
    logger.info(f"*** Double Checking STT model - Model Current Status: {is_whisper_valid()}")
    stt_loading_window = None
    try:
        if is_recording:
            logger.info("*** Recording in progress, skipping double check")
            return
        if not app_settings.editable_settings[SettingsKeys.LOCAL_WHISPER.value]:
            logger.info("*** Local Whisper is disabled, skipping double check")
            return
        if is_whisper_valid():
            logger.info("*** STT model already loaded, skipping double check")
            return
        # if using local whisper and model is not loaded, when starting recording
        if is_whisper_lock():
            model_name = app_settings.editable_settings[SettingsKeys.WHISPER_MODEL.value].strip()
            stt_loading_window = LoadingWindow(root, "Loading Speech to Text model",
                                               f"Loading {model_name} model. Please wait.",
                                               on_cancel=lambda: task_cancel_var.set(True))
            timeout = 300
            time_start = time.monotonic()
            # wait until the other loading thread is done
            while True:
                time.sleep(0.1)
                if task_cancel_var.get():
                    # user cancel
                    logger.debug(f"user canceled after {time.monotonic() - time_start} seconds")
                    return
                if time.monotonic() - time_start > timeout:
                    messagebox.showerror("Error",
                                         f"Timed out while loading local Speech to Text model after {timeout} seconds.")
                    task_cancel_var.set(True)
                    return
                if not is_whisper_lock():
                    break
            stt_loading_window.destroy()
            stt_loading_window = None
        # double check
        if is_whisper_valid():
            # mandatory loading, synchronous
            t = load_model_with_loading_screen(root=root, app_settings=app_settings)
            t.join()

    except Exception as e:
        logger.exception(str(e))
        messagebox.showerror("Error",
                             f"An error occurred while loading Speech to Text model synchronously {type(e).__name__}: {e}")
    finally:
        logger.info(f"*** Double Checking STT model Complete - Model Current Status: {is_whisper_valid()}")
        if stt_loading_window:
            stt_loading_window.destroy()
        task_done_var.set(True)


def threaded_realtime_text():
    thread = threading.Thread(target=realtime_text)
    thread.start()
    return thread


def threaded_handle_message(formatted_message):
    thread = threading.Thread(target=show_edit_transcription_popup, args=(formatted_message,))
    thread.start()
    return thread


def threaded_send_audio_to_server():
    thread = threading.Thread(target=send_audio_to_server)
    thread.start()
    return thread


def toggle_pause():
    def action():
        global is_paused
        # Pausing only makes sense while recording (un-pausing is always allowed)
        if not is_recording and not is_paused:
            return
        is_paused = not is_paused
        workspace.set_recording_state(is_recording, is_paused)

    root.after(0, action)
    
SILENCE_WARNING_LENGTH = 10 # seconds, warn the user after 10s of no input something might be wrong

def open_microphone_stream():
    """
    Opens an audio stream from the selected microphone.

    This function retrieves the index of the selected microphone from the
    MicrophoneTestFrame and attempts to open an audio stream using the pyaudio
    library. If successful, it returns the stream object and None. In case of
    an error (either OSError or IOError), it logs the error message and returns
    None along with the error object.

    Returns:
        tuple: A tuple containing the stream object (or None if an error occurs)
               and the error object (or None if no error occurs).
    """

    try:
        selected_index = MicrophoneTestFrame.get_selected_microphone_index()
        stream = p.open(
            format=FORMAT,
            channels=1,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
            input_device_index=int(selected_index))

        return stream, None
    except (OSError, IOError) as e:
        # Log the error message
        # TODO System logger
        logger.exception(f"An error occurred opening the stream({type(e).__name__}): {e}")
        return None, e


def record_audio():
    """
    Records audio from the selected microphone, processes the audio to detect silence,
    and manages the recording state.

    Global Variables:
        is_paused (bool): Indicates whether the recording is paused.
        frames (list): List of audio data frames.
        audio_queue (queue.Queue): Queue to store recorded audio chunks.

    Returns:
        None: The function does not return a value. It interacts with global variables.
    """
    global is_paused, frames, audio_queue, silent_warning_duration

    try:
        recording_id = f"{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        current_chunk = []
        silent_duration = 0
        record_duration = 0
        minimum_silent_duration = float(app_settings.editable_settings["Real Time Silence Length"])
        minimum_audio_duration = float(app_settings.editable_settings["Real Time Audio Length"])

        stream, stream_exception = open_microphone_stream()

        if stream is None:
            logger.error(f"An error occurred while trying to record audio: {stream_exception}")
            # Stop the recording and tell the user, from the UI thread
            run_on_ui(lambda: (
                clear_application_press(),
                messagebox.showerror("Error", f"An error occurred while trying to record audio: {stream_exception}")))
        
        audio_data_leng = 0
        while is_recording and stream is not None:
            if not is_paused:
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
                # Check for silence
                audio_buffer = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768

                # convert the setting from str to float
                try:
                    speech_prob_threshold = float(
                        app_settings.editable_settings[SettingsKeys.SILERO_SPEECH_THRESHOLD.value])
                except ValueError:
                    # default it to value in DEFAULT_SETTINGS_TABLE on invalid error
                    speech_prob_threshold = app_settings.DEFAULT_SETTINGS_TABLE[SettingsKeys.SILERO_SPEECH_THRESHOLD.value]
                    logger.info(f"Invalid value for SILERO_SPEECH_THRESHOLD: {app_settings.editable_settings[SettingsKeys.SILERO_SPEECH_THRESHOLD.value]}. Defaulting to {speech_prob_threshold}")

                if is_silent(audio_buffer, speech_prob_threshold ):
                    silent_duration += CHUNK / RATE
                    silent_warning_duration += CHUNK / RATE
                else:
                    silent_duration = 0
                    silent_warning_duration = 0
                    audio_data_leng += CHUNK / RATE

                current_chunk.append(data)
                
                record_duration += CHUNK / RATE

                # Check if we need to warn if silence is long than warn time
                root.after(0, lambda: check_silence_warning(silent_warning_duration))

                # 1 second of silence at the end so we dont cut off speech
                if silent_duration >= minimum_silent_duration and audio_data_leng > 1.5  and record_duration > minimum_audio_duration:
                    if app_settings.editable_settings[SettingsKeys.WHISPER_REAL_TIME.value] and current_chunk:
                        padded_audio = utils.audio.pad_audio_chunk(current_chunk, pad_seconds=0.5)
                        audio_queue.put(b''.join(padded_audio))
                    
                    if app_settings.editable_settings[SettingsKeys.STORE_RECORDINGS_LOCALLY.value]:
                        # Encrypt the audio chunk and save it to a file
                        utils.audio.encrypt_audio_chunk(b''.join(current_chunk), filepath=recording_id)

                    # Carry over the last .1 seconds of audio to the next one so next speech does not start abruptly or in middle of a word
                    carry_over_chunk = current_chunk[-int(0.1 * RATE / CHUNK):]
                    current_chunk = [] 
                    current_chunk.extend(carry_over_chunk)

                    # reset the variables and state holders for realtime audio processing
                    audio_data_leng = 0
                    silent_duration = 0
                    record_duration = 0
            else:
                # Add a small delay to prevent high CPU usage
                time.sleep(0.01)


        # Send any remaining audio chunk when recording stops
        if current_chunk:
            last_chunk = b''.join(current_chunk)
            audio_queue.put(last_chunk)
            if app_settings.editable_settings[SettingsKeys.STORE_RECORDINGS_LOCALLY.value]:
                utils.audio.encrypt_audio_chunk(last_chunk, filepath=recording_id)
    except Exception as e:
        # Log the error message
        # TODO System logger
        # For now general catch on any problems
        logger.exception(f"An error occurred: {e}")
    finally:
        if stream:
            stream.stop_stream()
            stream.close()
        audio_queue.put(None)

        # If the warning bar is displayed, remove it
        if window.warning_bar is not None:
            root.after(0, lambda: window.destroy_warning_bar())


def check_silence_warning(silence_duration):
    """Check if silence warning should be displayed."""

    # Check if we need to warn if silence is long than warn time
    if silence_duration >= SILENCE_WARNING_LENGTH and window.warning_bar is None and not is_paused:
        if current_view in ["full", "transcribe"]:
            window.create_warning_bar(f"No audio input detected for {SILENCE_WARNING_LENGTH} seconds. Please check and ensure your microphone input device is working.", closeButton=False)
        elif current_view == "minimal":
            window.create_warning_bar(f"🔇No audio for {SILENCE_WARNING_LENGTH}s.", closeButton=False)
    elif silence_duration <= SILENCE_WARNING_LENGTH and window.warning_bar is not None:
        # If the warning bar is displayed, remove it
        window.destroy_warning_bar()


silero, _silero = torch.hub.load(repo_or_dir='snakers4/silero-vad', model='silero_vad')


def is_silent(data, threshold: float = 0.65):
    """Check if audio chunk contains speech using Silero VAD"""
    # Convert audio data to tensor and ensure correct format
    audio_tensor = torch.FloatTensor(data)
    if audio_tensor.dim() == 2:
        audio_tensor = audio_tensor.mean(dim=1)

    # Get speech probability
    speech_prob = silero(audio_tensor, 16000).item()
    return speech_prob < threshold


def realtime_text():
    global is_realtimeactive, audio_queue
    # Incase the user starts a new recording while this one the older thread is finishing.
    # This is a local flag to prevent the processing of the current audio chunk
    # if the global flag is reset on new recording
    local_cancel_flag = False
    if not is_realtimeactive:
        is_realtimeactive = True
        # this is the text that will be used to process intents
        intent_text = ""

        while True:
            #  break if canceled
            if is_audio_processing_realtime_canceled.is_set():
                local_cancel_flag = True
                break

            audio_data = audio_queue.get()
            if audio_data is None:
                break
            if app_settings.editable_settings[SettingsKeys.WHISPER_REAL_TIME.value] == True:
                logger.info("Real Time Audio to Text")
                audio_buffer = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768
                if app_settings.editable_settings[SettingsKeys.LOCAL_WHISPER.value] == True:
                    logger.info(f"Local Real Time Whisper {audio_queue.qsize()=}")
                    if not is_whisper_valid():

                        report_error("Local Whisper model not loaded. Please check your settings.")
                        break
                    result = None
                    try:
                        result = transcribe_local(audio_buffer)
                        if app_settings.editable_settings[SettingsKeys.ENABLE_HALLUCINATION_CLEAN.value] and FeatureToggle.HALLUCINATION_CLEANING:
                            result = hallucination_cleaner.clean_text(result)
                    except Exception as e:
                        logger.exception(f"Error: {e}")
                        report_error(f"Part of the audio could not be transcribed: {e}")

                    if result is not None and not local_cancel_flag and not is_audio_processing_realtime_canceled.is_set():
                        update_gui(result)
                        intent_text = result
                else:
                    logger.info("Remote Real Time Whisper")
                    buffer = io.BytesIO()
                    with wave.open(buffer, 'wb') as wf:
                        wf.setnchannels(CHANNELS)
                        wf.setsampwidth(p.get_sample_size(FORMAT))
                        wf.setframerate(RATE)
                        wf.writeframes(audio_data)

                    buffer.seek(0)  # Reset buffer position

                    files = {'audio': buffer}

                    headers = {
                        "Authorization": f"Bearer {app_settings.editable_settings[SettingsKeys.WHISPER_SERVER_API_KEY.value]}"
                    }

                    body = {
                        "use_translate": app_settings.editable_settings[SettingsKeys.USE_TRANSLATE_TASK.value],
                    }

                    language, prompt = speech_options()
                    if language:
                        body["language_code"] = language
                    body['initial_prompt'] = prompt

                    try:
                        verify = not app_settings.editable_settings[SettingsKeys.S2T_SELF_SIGNED_CERT.value]

                        logger.info("Sending audio to server")
                        logger.info("File informaton")
                        logger.info(f"File Size: {len(buffer.getbuffer())} bytes")

                        response = requests.post(app_settings.editable_settings[SettingsKeys.WHISPER_ENDPOINT.value], headers=headers,files=files, verify=verify, data=body)
                            
                        logger.info(f"Response from whisper with status code: {response.status_code}")

                        if response.status_code == 200:
                            text = response.json()['text']
                            if app_settings.editable_settings[SettingsKeys.ENABLE_HALLUCINATION_CLEAN.value] and FeatureToggle.HALLUCINATION_CLEANING:
                                text = hallucination_cleaner.clean_text(text)
                            if not local_cancel_flag and not is_audio_processing_realtime_canceled.is_set():
                                update_gui(text)
                                intent_text = text
                        else:
                            report_error(f"The speech-to-text server returned an error (HTTP Status {response.status_code}): {response.text}")
                    except Exception as e:
                        report_error(f"Part of the audio could not be transcribed: {e}")
                        logger.exception(f"Error: {e}")
                    finally:
                        # close buffer. we dont need it anymore
                        buffer.close()
                # Process intents
                if FeatureToggle.INTENT_ACTION:
                    try:
                        logger.debug(f"Processing intents for text: {intent_text}")
                        window.get_text_intents(intent_text)
                    except Exception as e:
                        logger.exception(f"Error processing intents: {e}")
            audio_queue.task_done()

        # unload thestt model on low mem mode
        if app_settings.is_low_mem_mode():
            unload_stt_model()  
    else:
        is_realtimeactive = False


def update_gui(text):
    """Appends a piece of live transcription to the current session's transcript."""
    def action(text):
        if text and text.strip():
            workspace.append_transcript(text.strip() + '\n')
    root.after(0, lambda: action(text))

def save_audio():
    global frames
    if frames:
        with wave.open(get_resource_path("recording.wav"), 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
        frames = []  # Clear recorded data

    if app_settings.editable_settings[SettingsKeys.WHISPER_REAL_TIME.value] == True and is_audio_processing_realtime_canceled.is_set(
    ) is False:
        # The transcript is already complete; write the note unless only transcribing
        if not is_transcribe_only():
            send_and_receive()
    elif app_settings.editable_settings[SettingsKeys.WHISPER_REAL_TIME.value] == False and is_audio_processing_whole_canceled.is_set() is False:
        # Transcribe the recording; send_audio_to_server skips the note when only transcribing
        threaded_send_audio_to_server()


def toggle_recording(resume=False):
    global is_recording, recording_thread, audio_queue, current_view, REALTIME_TRANSCRIBE_THREAD_ID, frames, silent_warning_duration

    # Reset the cancel flags going into a fresh recording
    if not is_recording:
        is_audio_processing_realtime_canceled.clear()
        is_audio_processing_whole_canceled.clear()
        if not resume:
            visit_language.reset()

    if is_paused:
        toggle_pause()

    realtime_thread = threaded_realtime_text()

    if not is_recording:
        #load the stt model for transcription
        if not is_whisper_valid() and app_settings.is_low_mem_mode():
            loading_screen = LoadingWindow(root, "Loading Speech to Text model", "Loading Speech to Text model. Please wait.")
            load_stt_model(app_settings=app_settings)
            loading_screen.destroy()
            
        disable_recording_ui_elements()

        # A session that already has a transcript or note is kept as it is: recording
        # starts a new session unless the user asked to resume the current one.
        run_on_ui(lambda: workspace.begin_capture(resume=resume))

        REALTIME_TRANSCRIBE_THREAD_ID = track_thread(realtime_thread)
        
        if not app_settings.editable_settings[SettingsKeys.WHISPER_REAL_TIME.value]:
            set_transcript_status("Recording\u2026 Real-time transcription is turned off, so the transcript will appear when you stop transcribing.")
        else:
            set_transcript_status("Listening\u2026 The transcript will appear here as the conversation goes on.")

        is_recording = True

        # reset frames before new recording so old data is not used
        frames = []
        silent_warning_duration = 0
        recording_thread = threading.Thread(target=record_audio)
        recording_thread.start()

        run_on_ui(lambda: workspace.set_recording_state(True, False))
    else:
        enable_recording_ui_elements()
        run_on_ui(lambda: workspace.set_recording_state(False, False))
        is_recording = False
        if recording_thread and recording_thread.is_alive():
            recording_thread.join()  # Ensure the recording thread is terminated

        if app_settings.editable_settings[SettingsKeys.WHISPER_REAL_TIME.value] and not is_audio_processing_realtime_canceled.is_set(
        ):
            def cancel_realtime_processing(thread_id):
                """Cancels any ongoing audio processing.

                Sets the global flag to stop audio processing operations.
                """
                global REALTIME_TRANSCRIBE_THREAD_ID

                try:
                    kill_thread(thread_id)
                except Exception as e:
                    logger.exception(f"An error occurred: {e}")
                finally:
                    REALTIME_TRANSCRIBE_THREAD_ID = None

                # empty the queue
                while not audio_queue.empty():
                    audio_queue.get()
                    audio_queue.task_done()

            loading_window = LoadingWindow(
                root,
                "Processing Audio",
                "Processing Audio. Please wait.",
                on_cancel=lambda: (
                    cancel_processing(),
                    cancel_realtime_processing(REALTIME_TRANSCRIBE_THREAD_ID)))

            try:
                timeout_length = int(app_settings.editable_settings[SettingsKeys.AUDIO_PROCESSING_TIMEOUT_LENGTH.value])
            except ValueError:
                # default to 3minutes
                timeout_length = 180
                logger.info(f"Invalid value for AUDIO_PROCESSING_TIMEOUT_LENGTH: {app_settings.editable_settings[SettingsKeys.AUDIO_PROCESSING_TIMEOUT_LENGTH.value]}. Defaulting to {timeout_length} seconds")

            timeout_timer = 0.0
            while audio_queue.empty() is False and timeout_timer < timeout_length:
                # break because cancel was requested
                if is_audio_processing_realtime_canceled.is_set():
                    break
                # increment timer
                timeout_timer += 0.1
                # round to 10 decimal places, account for floating point errors
                timeout_timer = round(timeout_timer, 10)

                # check if we should print a message every 5 seconds
                if timeout_timer % 5 == 0:
                    logger.info(f"Waiting for audio processing to finish. Timeout after {timeout_length} seconds. Timer: {timeout_timer}s")

                # Wait for 100ms before checking again, to avoid busy waiting
                time.sleep(0.1)

            loading_window.destroy()

            realtime_thread.join()

        set_transcript_status(None)
        save_audio()

        logger.info("*** Recording Stopped")

def disable_recording_ui_elements():
    def action():
        window.disable_settings_menu()
        workspace.set_locked(True)
        mic_test.set_mic_test_state(False)
    root.after(0, action)

def enable_recording_ui_elements():
    def action():
        window.enable_settings_menu()
        workspace.set_locked(False)
        mic_test.set_mic_test_state(True)

    root.after(0, action)
        

def cancel_processing():
    """Cancels any ongoing audio processing.

    Sets the global flag to stop audio processing operations.
    """
    logger.info("Processing canceled.")

    if app_settings.editable_settings[SettingsKeys.WHISPER_REAL_TIME.value]:
        is_audio_processing_realtime_canceled.set()  # Flag to terminate processing
    else:
        is_audio_processing_whole_canceled.set()  # Flag to terminate processing


def clear_application_press():
    """Stops any recording or processing in progress and clears transient status.

    The content of the session (transcript, note, documents) is left untouched.
    """
    reset_recording_status()  # Reset recording-related variables
    clear_all_text_fields()  # Clear transient status in the UI


def reset_recording_status():
    """Resets all recording-related variables and stops any active recording.

    Handles cleanup of recording state by:
        - Checking if recording is active
        - Canceling any processing
        - Stopping the recording thread
    """
    global is_recording, frames, audio_queue, REALTIME_TRANSCRIBE_THREAD_ID, GENERATION_THREAD_ID
    if is_recording:  # Only reset if currently recording
        cancel_processing()  # Stop any ongoing processing
        threaded_toggle_recording()  # Stop the recording thread

    # kill the generation thread if active
    if REALTIME_TRANSCRIBE_THREAD_ID:
        # Exit the current realtime thread
        try:
            kill_thread(REALTIME_TRANSCRIBE_THREAD_ID)
        except Exception as e:
            logger.exception(f"An error occurred: {e}")
        finally:
            REALTIME_TRANSCRIBE_THREAD_ID = None

    if GENERATION_THREAD_ID:
        try:
            kill_thread(GENERATION_THREAD_ID)
        except Exception as e:
            logger.exception(f"An error occurred: {e}")
        finally:
            GENERATION_THREAD_ID = None


def clear_all_text_fields():
    """Clears transient status shown in the UI (processing hints and the busy indicator)."""
    set_transcript_status(None)
    stop_flashing()  # Stop the busy indicator


def send_audio_to_server():
    """
    Sends an audio file to either a local or remote Whisper server for transcription.

    Global Variables:
    ----------------
    uploaded_file_path : str
        The path to the uploaded audio file. If `None`, the function defaults to
        'recording.wav'.

    Parameters:
    -----------
    None

    Returns:
    --------
    None

    Raises:
    -------
    ValueError
        If the `app_settings.editable_settings[SettingsKeys.LOCAL_WHISPER.value]` flag is not a boolean.
    FileNotFoundError
        If the specified audio file does not exist.
    requests.exceptions.RequestException
        If there is an issue with the HTTP request to the remote server.
    """

    global uploaded_file_path
    current_thread_id = threading.current_thread().ident
    # Set once the transcript has been handed over for note generation
    handed_off = False

    def cancel_whole_audio_process(thread_id):
        global GENERATION_THREAD_ID

        is_audio_processing_whole_canceled.clear()

        try:
            kill_thread(thread_id)
        except Exception as e:
            logger.exception(f"An error occurred: {e}")
        finally:
            GENERATION_THREAD_ID = None
            clear_application_press()
            stop_flashing()

    loading_window = LoadingWindow(
        root,
        "Processing Audio",
        "Processing Audio. Please wait.",
        on_cancel=lambda: (
            cancel_processing(),
            cancel_whole_audio_process(current_thread_id)))

    # Check if SettingsKeys.LOCAL_WHISPER is enabled in the editable settings
    if app_settings.editable_settings[SettingsKeys.LOCAL_WHISPER.value] == True:
        # Inform the user that SettingsKeys.LOCAL_WHISPER.value is being used for transcription
        logger.info(f"Using {SettingsKeys.LOCAL_WHISPER.value} for transcription.")

        # Let the user know the audio is being processed
        set_transcript_status("Transcribing audio\u2026 Please wait.")
        try:
            if utils.system.is_macos():
                # Load the audio file to send for transcription
                file_to_send, sr = librosa.load(uploaded_file_path, sr=RATE, mono=True)
                delete_file = False
                uploaded_file_path = None
            else:
                # Determine the file to send for transcription
                file_to_send = uploaded_file_path or get_resource_path('recording.wav')
                delete_file = False if uploaded_file_path else True
                uploaded_file_path = None


            # load stt model for transcription
            if not is_whisper_valid() and app_settings.is_low_mem_mode():
                model_id = get_model_from_settings(app_settings=app_settings)
                model_load_window = LoadingWindow(root, 
                title = "Speech to Text model", 
                initial_text = f"Loading Speech to Text model({model_id}). Please wait.")
                load_thread = load_stt_model(app_settings=app_settings)
                load_thread.join()
                model_load_window.destroy()

            # Transcribe the audio file using the loaded model
            transcription_error = None
            try:
                result = transcribe_local(file_to_send)
                if app_settings.editable_settings[SettingsKeys.ENABLE_HALLUCINATION_CLEAN.value] and FeatureToggle.HALLUCINATION_CLEANING:
                    result = hallucination_cleaner.clean_text(result)
            except Exception as e:
                result = None
                transcription_error = f"The audio could not be transcribed ({type(e).__name__}): {e}"
                logger.exception(f"An error occurred: {e}\n \n {traceback.format_exc()}")
            finally:
                if app_settings.is_low_mem_mode():
                    unload_stt_model()

            transcribed_text = result

            # done with file clean up
            if delete_file is True and os.path.exists(file_to_send) :
                os.remove(file_to_send)

            # check if canceled, if so do not update the UI
            if not is_audio_processing_whole_canceled.is_set():
                if transcription_error:
                    # Never create a note from an error message
                    set_transcript_status(None)
                    report_error(transcription_error)
                elif is_transcribe_only():
                    # Transcribe Only Mode or the "No Note Generation" template: keep the transcript, write no note
                    safe_set_transcription_box(transcribed_text)
                else:
                    handed_off = True
                    safe_set_transcription_box(transcribed_text, send_and_receive)
        except Exception as e:
            logger.exception(f"An error occurred: {e}")
            set_transcript_status(None)
            report_error(f"An error occurred while transcribing the audio: {e}")
        finally:
            loading_window.destroy()

    else:
        # Inform the user that Remote Whisper is being used for transcription
        logger.info("Using Remote Whisper for transcription.")

        # Let the user know the audio is being processed
        set_transcript_status("Transcribing audio\u2026 Please wait.")

        delete_file = False if uploaded_file_path else True

        # Determine the file to send for transcription
        if uploaded_file_path:
            file_to_send = uploaded_file_path
            uploaded_file_path = None
        else:
            file_to_send = get_resource_path('recording.wav')

        # Open the audio file in binary mode
        with open(file_to_send, 'rb') as f:
            files = {'audio': f}

            # Add the Bearer token to the headers for authentication
            headers = {
                "Authorization": f"Bearer {app_settings.editable_settings[SettingsKeys.WHISPER_SERVER_API_KEY.value]}"
            }

            body = {
                "use_translate": app_settings.editable_settings[SettingsKeys.USE_TRANSLATE_TASK.value],
            }

            language, prompt = speech_options()
            if language:
                body["language_code"] = language
            body['initial_prompt'] = prompt

            try:
                verify = not app_settings.editable_settings[SettingsKeys.S2T_SELF_SIGNED_CERT.value]

                logger.info("Sending audio to server")
                logger.info("File informaton")
                logger.info(f"File: {file_to_send}")
                logger.info(f"File Size: {os.path.getsize(file_to_send)}")

                # Send the request without verifying the SSL certificate
                response = requests.post(
                    app_settings.editable_settings[SettingsKeys.WHISPER_ENDPOINT.value], headers=headers, files=files, verify=verify, data=body)

                logger.info(f"Response from whisper with status code: {response.status_code}")

                response.raise_for_status()

                # check if canceled, if so do not update the UI
                if not is_audio_processing_whole_canceled.is_set():
                    # Update the UI with the transcribed text
                    transcribed_text = response.json()['text']
                    if app_settings.editable_settings[SettingsKeys.ENABLE_HALLUCINATION_CLEAN.value] and FeatureToggle.HALLUCINATION_CLEANING:
                        transcribed_text = hallucination_cleaner.clean_text(transcribed_text)
                        try:
                            transcribed_text = hallucination_cleaner.clean_text(transcribed_text)
                            logger.debug(f"remote Cleaned result: {transcribed_text}")
                        except Exception as e:
                            # ignore the error as it should not break the transcription
                            logger.exception(f"remote Error during hallucination cleaning: {str(e)}")
                    if is_transcribe_only():
                        # Transcribe Only Mode or the "No Note Generation" template: keep the transcript, write no note
                        safe_set_transcription_box(transcribed_text)
                    else:
                        handed_off = True
                        safe_set_transcription_box(transcribed_text, send_and_receive)
            except Exception as e:
                logger.exception(f"An error occurred: {e}")
                # Display an error message to the user (never create a note from it)
                set_transcript_status(None)
                report_error(f"An error occurred while transcribing the audio: {e}")
            finally:
                # done with file clean up
                f.close()
                if os.path.exists(file_to_send) and delete_file:
                    os.remove(file_to_send)
                loading_window.destroy()
    if not handed_off:
        stop_flashing()


def kill_thread(thread_id):
    """
    Terminate a thread with a given thread ID.

    This function forcibly terminates a thread by raising a `SystemExit` exception in its context.
    **Use with caution**, as this method is not safe and can lead to unpredictable behavior,
    including corruption of shared resources or deadlocks.

    :param thread_id: The ID of the thread to terminate.
    :type thread_id: int
    :raises ValueError: If the thread ID is invalid.
    :raises SystemError: If the operation fails due to an unexpected state.
    """
    tracked = tracked_threads.get(thread_id)
    if tracked is not None and not tracked.is_alive():
        # The thread we meant has finished; its ID may now belong to another thread.
        logger.info(f"*** Thread with ID {thread_id} has already finished, nothing to kill")
        tracked_threads.pop(thread_id, None)
        return

    logger.info(f"*** Attempting to kill thread with ID: {thread_id}")
    # Call the C function `PyThreadState_SetAsyncExc` to asynchronously raise
    # an exception in the target thread's context.
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(thread_id),  # The thread ID to target (converted to `long`).
        ctypes.py_object(SystemExit)  # The exception to raise in the thread.
    )

    # Check the result of the function call.
    if res == 0:
        # If 0 is returned, the thread ID is invalid.
        raise ValueError(f"Invalid thread ID: {thread_id}")
    elif res > 1:
        # If more than one thread was affected, something went wrong.
        # Reset the state to prevent corrupting other threads.
        ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, None)
        raise SystemError("PyThreadState_SetAsyncExc failed")
    logger.info(f"*** Killed thread with ID: {thread_id}")


def send_and_receive():
    """Creates the note for the current session. Safe to call from any thread."""
    root.after(0, start_note_generation)


def start_note_generation():
    """Collects the transcript and context of the current session and starts note generation."""
    global user_message, generation_session_id
    workspace.flush_edits()
    session = workspace.current
    transcript = session.transcript.strip() if session else ""
    if not transcript:
        stop_flashing()
        workspace.show_tab("transcript")
        workspace.toast("There is no transcript to create a note from yet", icon_name="info")
        return

    user_message = transcript
    generation_session_id = session.id
    workspace.hide_warning(kind="error")
    start_flashing("Creating note")
    # Context the clinician typed in is sent along with the transcript
    threaded_handle_message(compose_source(transcript, session.context))


def save_notes_history():
    """
    Saves the sessions to a local file in encrypted JSON format.
    """
    notes_file_path = notes_history_path()
    try:
        session_store.save(notes_file_path, AESCryptoUtils.AESCryptoUtilsClass.encrypt)
        logger.info(f"Sessions saved to {notes_file_path}")
    except Exception as e:
        logger.exception(f"Error saving sessions: {e}")


def is_llm_error(response_text):
    """Whether the model call failed (the network client reports failures as 'Error: ...' text)."""
    if not isinstance(response_text, str) or not response_text.strip():
        return True
    # The built-in model reports failures as "(SomeError): message"
    if re.match(r"\(\w*(Error|Exception)\w*\): ", response_text):
        return True
    return response_text.startswith("Error: ")


def describe_llm_error(response_text):
    """A user-facing description of a failed model call."""
    if not isinstance(response_text, str) or not response_text.strip():
        return "The AI returned an empty response."
    if response_text.startswith("Error: "):
        response_text = response_text[len("Error: "):]
    return response_text.strip() or "The AI request failed."


def update_gui_with_response(response_text):
    def action(text):
        if is_llm_error(text):
            # Never store an error message as if it were a note
            if "Operation cancelled" not in str(text):  # the user cancelled; nothing to report
                report_error(f"The note could not be created. {describe_llm_error(text)}")
            return

        workspace.apply_note(generation_session_id, text.strip(), NoteStyleSelector.current_style)
        if storing_notes_locally():
            save_notes_history()

        copied = True
        try:
            # copy/paste may be disabled in sandbox environment
            pyperclip.copy(text.strip())
        except Exception as e:
            copied = False
            logger.warning(str(e))
        workspace.toast("Note ready and copied to clipboard" if copied else "Note ready")
    stop_flashing()
    
    root.after(0, lambda: action(response_text))


def send_text_to_api(edited_text, cancel_event):
    """
    Sends text to API using httpx AsyncClient in a cancellable async format.
    
    Args:
        edited_text (str): The text to send to the API
        cancel_event (threading.Event): Event to signal cancellation
        
    Returns:
        str: The response text or 'Error' if cancelled/failed
    """
    network_config = NetworkConfig(
        host=app_settings.editable_settings[SettingsKeys.LLM_ENDPOINT.value],
        api_key=app_settings.OPENAI_API_KEY,
        verify_ssl=not app_settings.editable_settings["AI Server Self-Signed Certificates"],
        timeout=180.0,  # Set a reasonable timeout for the request
        connect_timeout=10.0,
    )

    llm_client = OpenAIClient(config=network_config)

    generated_response = llm_client.send_chat_completion_sync(
        text=edited_text,
        model=app_settings.editable_settings[SettingsKeys.LOCAL_LLM_MODEL.value],
        threading_cancel_event=cancel_event,
        temperature=float(app_settings.editable_settings["temperature"]),
        top_p=float(app_settings.editable_settings["top_p"]),
        top_k=int(app_settings.editable_settings["top_k"]),
        stream=False,
    )

    return generated_response
         
# The built-in model handles one request at a time (the chat can be used while a note is written)
local_model_lock = threading.Lock()


def send_text_to_localmodel(edited_text):
    with local_model_lock:
        return _send_text_to_localmodel(edited_text)


def _send_text_to_localmodel(edited_text):
    # Send prompt to local model and get response
    if ModelManager.local_model is None:
        ModelManager.setup_model(app_settings=app_settings, root=root)

        timer = 0
        while ModelManager.local_model is None and timer < 30:
            timer += 0.1
            time.sleep(0.1)
       
    response  = ModelManager.local_model.generate_response(
        edited_text,
        temperature=float(app_settings.editable_settings["temperature"]),
        top_p=float(app_settings.editable_settings["top_p"]),
        repeat_penalty=float(app_settings.editable_settings["rep_pen"]),
    )

    if app_settings.is_low_mem_mode():
        ModelManager.unload_model()

    return response

def screen_input_with_llm(conversation):
    """
    Send a conversation to a large language model (LLM) for prescreening.
    :param conversation: A string containing the conversation to be screened.
    :return: A boolean indicating whether the conversation is valid.
    """
    # Define the chunk size (number of words per chunk)
    words_per_chunk = 60  # Adjust this value based on your results
    # Split the conversation into words
    words = conversation.split()
    # Split the words into chunks
    chunks = [' '.join(words[i:i + words_per_chunk]) for i in range(0, len(words), words_per_chunk)]
    logger.info(f"Total chunks count: {len(chunks)}")
    return any(process_chunk(chunk) for chunk in chunks)


def process_chunk(chunk):
    """
    Process a chunk of the conversation using the LLM.
    """
    prompt = (
        "Analyze the following conversation and determine if it is a valid doctor-patient conversation. "
        "A valid conversation involves a discussion between a healthcare provider and a patient about medical concerns, "
        "symptoms, diagnoses, treatments, or health management. It may include:\n"
        "- Descriptions of symptoms or health issues.\n"
        "- Discussions about medications, treatments, or follow-up plans.\n"
        "- Questions and answers related to the patient's health.\n"
        "- Casual or conversational tones, as long as the topic is medically relevant.\n\n"
        "If the conversation is unrelated to healthcare, lacks medical context, or appears to be non-medical, "
        "it is not a valid doctor-patient conversation.\n\n"
        "Return only one word: 'True' if the conversation is valid, or 'False' if it is not. "
        "Do not provide explanations, additional formatting, or any text other than 'True' or 'False'.\n\n"
        "Here is the conversation:\n"
    )
    # Send the prompt and chunk to the LLM for evaluation
    prescreen = send_text_to_chatgpt(f"{prompt}{chunk}")
    # Check if the response from the LLM is 'true' (case-insensitive)
    return prescreen.strip().lower() == "true"


def has_more_than_50_words(text: str) -> bool:
    # Split the text into words using whitespace as the delimiter
    words = text.split()
    # Print the number of words
    logger.info(f"Number of words: {len(words)}")
    # Check if the number of words is greater than 50
    return len(words) > 50


def display_screening_popup():
    """
    Display a popup window to inform the user of invalid input and offer options.

    :return: A boolean indicating the user's choice:
             - False if the user clicks 'Cancel'.
             - True if the user clicks 'Process Anyway!'.
    """
    # Create and display the popup window
    popup_result = PopupBox(
        parent=root,
        title="Invalid Input",
        message=(
            "Input has been flagged as invalid. Please ensure the input is a conversation with more than "
            "50 words between a doctor and a patient. Unexpected results may occur from the AI."
        ),
        button_text_1="Cancel",
        button_text_2="Process Anyway!"
    )

    # Return based on the button the user clicks
    if popup_result.response == "button_1":
        return False
    elif popup_result.response == "button_2":
        return True


def screen_input(user_message):
    """
    Screen the user's input message based on the application's settings.

    :param user_message: The message to be screened.
    :return: A boolean indicating whether the input is valid and accepted for further processing.
    """
    validators = []
    if app_settings.editable_settings[SettingsKeys.Enable_Word_Count_Validation.value]:
        validators.append(has_more_than_50_words)

    if app_settings.editable_settings[SettingsKeys.Enable_AI_Conversation_Validation.value]:
        validators.append(screen_input_with_llm)

    return all(validator(user_message) for validator in validators)


def threaded_screen_input(user_message, screen_return):
    """
    Screen the user's input message based on the application's settings in a separate thread.

    :param user_message: The message to be screened.
    :param screen_return: A boolean variable to store the result of the screening.
    """
    input_return = screen_input(user_message)
    screen_return.set(input_return)

def send_text_to_chatgpt(edited_text, cancel_event=None): 
    if app_settings.editable_settings[SettingsKeys.LOCAL_LLM.value]:
        return send_text_to_localmodel(edited_text)
    else:
        # send_text_to_api is already synchronous and handles async internally
        return send_text_to_api(edited_text, cancel_event)
    
def generate_note(formatted_message, cancel_event):
    """Generate a note from the formatted message.
    
    This function processes the input text and generates a medical note or AI response
    based on application settings. It supports pre-processing, post-processing, and
    factual consistency verification.
    
    :param formatted_message: The transcribed conversation text to generate a note from
    :type formatted_message: str
    
    :returns: True if note generation was successful, False otherwise
    :rtype: bool
    
    .. note::
        The behavior of this function depends on several application settings:
        - If 'use_aiscribe' is True, it generates a structured medical note
        - If 'Use Pre-Processing' is enabled, it first generates a list of facts
        - If 'Use Post-Processing' is enabled, it refines the generated note
        - Factual consistency verification is performed on the final note
    """
    try:
        summary = None
        current_prompt_info = NoteStyleSelector.get_current_prompt_info()
        # The chosen level of detail is added to the template's closing instructions
        post_prompt = f"{current_prompt_info.post_prompt} {get_detail_instruction()}".strip()
        if use_aiscribe:
            # If pre-processing is enabled
            if app_settings.editable_settings[SettingsKeys.USE_PRE_PROCESSING.value]:
                #Generate Facts List
                list_of_facts = send_text_to_chatgpt(f"{app_settings.editable_settings['Pre-Processing']} {formatted_message}", cancel_event)
                
                #Make a note from the facts
                medical_note = send_text_to_chatgpt(f"{current_prompt_info.pre_prompt} {list_of_facts} {post_prompt}", cancel_event)

                # If post-processing is enabled check the note over
                if app_settings.editable_settings["Use Post-Processing"]:
                    post_processed_note = send_text_to_chatgpt(f"{app_settings.editable_settings['Post-Processing']}\nFacts:{list_of_facts}\nNotes:{medical_note}", cancel_event)
                    summary = post_processed_note
                    update_gui_with_response(post_processed_note)
                else:
                    summary = medical_note
                    update_gui_with_response(medical_note)

            else: # If pre-processing is not enabled then just generate the note
                medical_note = send_text_to_chatgpt(f"{current_prompt_info.pre_prompt} {formatted_message} {post_prompt}", cancel_event)

                if app_settings.editable_settings["Use Post-Processing"]:
                    post_processed_note = send_text_to_chatgpt(f"{app_settings.editable_settings['Post-Processing']}\nNotes:{medical_note}", cancel_event)
                    update_gui_with_response(post_processed_note)
                    summary = post_processed_note
                else:
                    update_gui_with_response(medical_note)
                    summary = medical_note
        else: # do not generate note just send text directly to AI 
            ai_response = send_text_to_chatgpt(formatted_message, cancel_event)
            update_gui_with_response(ai_response)
            summary = ai_response

        if not is_llm_error(summary):
            check_and_warn_about_factual_consistency(formatted_message, summary)
            
        return True
    except Exception as e:
        logger.exception(f"An error occurred: {e}")
        report_error(f"An error occurred while creating the note: {e}")
        return False


def get_detail_instruction():
    """The extra prompt instruction for the level of detail chosen in the template menu."""
    return DETAIL_LEVELS.get(ui_state.get("detail_level", DEFAULT_DETAIL_LEVEL), "")

def check_and_warn_about_factual_consistency(formatted_message: str, medical_note: str) -> None:
    """Verify and warn about potential factual inconsistencies in generated medical notes.

    This function checks the consistency between the original conversation and the generated
    medical note using multiple verification methods. If inconsistencies are found, a warning 
    dialog is shown to the user.

    :param formatted_message: The original transcribed conversation text
    :type formatted_message: str
    :param medical_note: The generated medical note to verify
    :type medical_note: str
    :returns: None

    .. note::
        The verification is only performed if factual consistency checking is enabled
        in the application settings.

    .. warning::
        Even if no inconsistencies are found, this does not guarantee the note is 100% accurate.
        Always review generated notes carefully.
    """
    # Verify factual consistency
    if not app_settings.editable_settings[SettingsKeys.FACTUAL_CONSISTENCY_VERIFICATION.value] or not FeatureToggle.FACTS_CHECK:
        return
        
    inconsistent_entities = find_factual_inconsistency(formatted_message, medical_note)
    logger.info(f"Inconsistent entities: {inconsistent_entities}")
    
    if inconsistent_entities:
        entities = '\n'.join(f'- {entity}' for entity in inconsistent_entities)
        warning_message = (
            "Heads-up: Potential inconsistencies detected in the generated note:\n\n"
            "Entities not in original conversation found:\n"
            f"{entities}"
            "\n\nPlease review the note for accuracy."
        )
        messagebox.showwarning("Factual Consistency Heads-up", warning_message)


def scrub_for_llm(text):
    """Removes identifiers from text before it is sent to a model."""
    scrubbed_message = scrubadub.clean(text)

    pattern = r'\b\d{10}\b'     # Any 10 digit number, looks like OHIP
    return re.sub(pattern, '{{OHIP}}', scrubbed_message)


def needs_phi_review():
    """Whether the user must review the scrubbed text before it leaves this machine or network."""
    stays_private = app_settings.editable_settings[SettingsKeys.LOCAL_LLM.value] or is_private_ip(
        app_settings.editable_settings[SettingsKeys.LLM_ENDPOINT.value])
    return not (stays_private and not app_settings.editable_settings["Show Scrub PHI"])


def show_edit_transcription_popup(formatted_message):
    cleaned_message = scrub_for_llm(formatted_message)

    if not needs_phi_review():
        generate_note_thread(cleaned_message)
        return

    def on_proceed(edited_text):
        thread = threading.Thread(target=generate_note_thread, args=(edited_text,))
        thread.start()

    def on_cancel():
        stop_flashing()

    ScrubWindow(root, cleaned_message, on_proceed, on_cancel)


def generate_note_thread(text: str):
    """
    Generate a note from the given text and update the GUI with the response.

    :param text: The text to generate a note from.
    :type text: str
    """
    global GENERATION_THREAD_ID

    GENERATION_THREAD_ID = None

    cancel_event = threading.Event()

    def cancel_note_generation(thread_id, screen_thread):
        """Cancels any ongoing note generation.

        Sets the global flag to stop note generation operations.
        """
        global GENERATION_THREAD_ID

        try:
            # Set the cancellation event first
            cancel_event.set()
            
            if thread_id:
                kill_thread(thread_id)

            # check if screen thread is active before killing it
            if screen_thread and screen_thread.is_alive():
                kill_thread(screen_thread.ident)
                
            # Let the user know; the existing note (if any) is left as it was
            run_on_ui(lambda: workspace.toast("Note generation cancelled", icon_name="info"))
        except Exception as e:
            logger.exception(f"An error occurred: {e}")
        finally:
            GENERATION_THREAD_ID = None
            stop_flashing()

    # Track the screen input thread
    screen_thread = None
    # The return value from the screen input thread
    screen_return = tk.BooleanVar()

    loading_window = LoadingWindow(root, "Screening Input Text", "Ensuring input is valid. Please wait.", on_cancel=lambda: (cancel_note_generation(GENERATION_THREAD_ID, screen_thread)))
    
    # screen input in its own thread so we can cancel it
    screen_thread = threading.Thread(target=threaded_screen_input, args=(text, screen_return))
    screen_thread.start()
    # wait for the thread to join/cancel so we can continue
    screen_thread.join()

    # Check if the screen input was canceled or force overridden by the user
    if screen_return.get() is False:
        loading_window.destroy()

        # display the popup
        if display_screening_popup() is False:
            stop_flashing()
            return
    
    loading_window.destroy()
    loading_window = LoadingWindow(root, "Generating Note.", "Generating Note. Please wait.", on_cancel=lambda: (cancel_note_generation(GENERATION_THREAD_ID, screen_thread)))

    def generate_note_with_cleanup(text, cancel_event):
        """Wrapper function that ensures proper cleanup."""
        try:
            return generate_note(text, cancel_event)
        except Exception as e:
            logger.exception(f"Error in generate_note: {e}")
            return False
        finally:
            # Ensure the cancel event is set to stop any ongoing operations
            cancel_event.set()

    thread = threading.Thread(target=generate_note_with_cleanup, args=(text, cancel_event))
    thread.start()
    GENERATION_THREAD_ID = track_thread(thread)

    def check_thread_status(thread, loading_window):
        if thread.is_alive():
            root.after(500, lambda: check_thread_status(thread, loading_window))
        else:
            loading_window.destroy()
            stop_flashing()
    root.after(500, lambda: check_thread_status(thread, loading_window))


def run_llm_task(prompt_text, title, message, on_success):
    """
    Sends a prompt to the model with the same safeguards as note generation
    (identifier scrubbing, optional review before sending, a cancellable progress
    window) and calls ``on_success(text)`` on the UI thread with the reply.

    :param prompt_text: The full prompt to send.
    :param title: Title of the progress window, also shown as the busy indicator.
    :param message: Message shown in the progress window.
    :param on_success: Called with the model's reply when it succeeds.
    """
    cleaned_prompt = scrub_for_llm(prompt_text)

    def start(text):
        start_flashing(title)
        threading.Thread(target=_llm_task_thread, args=(text, title, message, on_success), daemon=True).start()

    if needs_phi_review():
        ScrubWindow(root, cleaned_prompt, start, stop_flashing)
    else:
        start(cleaned_prompt)


def _llm_task_thread(text, title, message, on_success):
    cancel_event = threading.Event()
    state = {"cancelled": False, "worker": None}
    result = {}

    def cancel():
        state["cancelled"] = True
        cancel_event.set()
        worker = state["worker"]
        try:
            if worker is not None and worker.is_alive():
                kill_thread(worker.ident)
        except Exception as e:
            logger.exception(f"An error occurred: {e}")
        finally:
            stop_flashing()
            run_on_ui(lambda: workspace.toast("Cancelled", icon_name="info"))

    loading_window = LoadingWindow(root, title, message, on_cancel=cancel)

    def work():
        try:
            result["text"] = send_text_to_chatgpt(text, cancel_event)
        except Exception as e:
            logger.exception(f"An error occurred: {e}")
            result["error"] = str(e)

    worker = threading.Thread(target=work)
    state["worker"] = worker
    worker.start()
    worker.join()

    loading_window.destroy()
    stop_flashing()
    if state["cancelled"]:
        return
    if "error" in result:
        report_error(f"The request could not be completed: {result['error']}")
    elif is_llm_error(result.get("text")):
        report_error(f"The request could not be completed. {describe_llm_error(result.get('text'))}")
    else:
        run_on_ui(on_success, result["text"].strip())


def ask_assistant(instruction, target):
    """Asks the model to revise the note or document described by ``target``."""
    session = session_store.get(target.get("session_id"))
    prompt = compose_revision_prompt(
        instruction,
        target.get("text", ""),
        session.transcript if session else "",
        session.context if session else "")

    def on_success(text):
        workspace.apply_revision(target, text)
        if storing_notes_locally():
            save_notes_history()

    workspace.hide_warning(kind="error")
    run_llm_task(prompt, "Updating", "Applying your request. Please wait.", on_success)


def generate_document(template_name):
    """Creates an additional document (referral letter, patient summary ...) for the current session."""
    workspace.flush_edits()
    session = workspace.current
    transcript = session.transcript.strip() if session else ""
    if not transcript:
        workspace.toast("There is no transcript to create a document from yet", icon_name="info")
        return

    prompt_info = NoteStyleSelector.get_prompt_info(template_name)
    title = template_name.replace(" - Default", "")
    prompt = (f"{prompt_info.pre_prompt} {compose_source(transcript, session.context)} "
              f"{prompt_info.post_prompt} {get_detail_instruction()}").strip()
    session_id = session.id

    def on_success(text):
        workspace.add_document(session_id, title, template_name, text)
        if storing_notes_locally():
            save_notes_history()
        workspace.toast(f"{title} created")

    workspace.hide_warning(kind="error")
    run_llm_task(prompt, f"Creating {title}", f"Creating {title}. Please wait.", on_success)


def form_source_budget():
    """How many characters of visit information fit in one form-filling prompt."""
    if app_settings.editable_settings[SettingsKeys.LOCAL_LLM.value]:
        try:
            window_tokens = int(app_settings.editable_settings[SettingsKeys.LOCAL_LLM_CONTEXT_WINDOW.value])
        except (TypeError, ValueError):
            window_tokens = 4096
        # Leave room for the instructions, the field list and the answers (~1,300 tokens)
        return max(2000, int((window_tokens - 1300) * 3.5))
    return 40000


def fill_form():
    """Fills a PDF form the user picks with information from the current session."""
    workspace.flush_edits()
    session = workspace.current
    if session is None:
        return
    has_content = bool(session.transcript.strip() or session.note.strip() or session.context.strip())

    pdf_path = filedialog.askopenfilename(title="Choose a PDF form to fill",
                                          filetypes=(("PDF forms", "*.pdf"),))
    if not pdf_path:
        return
    try:
        prepared = form_filler.prepare_form(pdf_path)
    except form_filler.FormError as error:
        messagebox.showinfo("Fill PDF form", str(error))
        return
    fields = prepared.fields
    if not has_content:
        # Nothing for the AI to work from yet: open the form to be filled by hand
        # Nothing for the AI to work from yet: the form opens blank, ready to fill by hand or by talking
        _show_form_review(prepared, {}, session.title, session.id, from_session=False)
        return

    source = form_filler.compose_source(
        title=session.title, note=session.note, context=session.context, transcript=session.transcript,
        visit_date=session.created_at.date(), max_chars=form_source_budget())
    # Identifiers become placeholders the model copies; the real values are put back afterwards
    hidden_source, mapping = form_filler.pseudonymize(source)
    asked = [f for f in fields if f.needs_answer]
    form_title = os.path.splitext(os.path.basename(pdf_path))[0]
    session_title = session.title
    session_id = session.id

    def start(text):
        start_flashing("Filling form")
        threading.Thread(target=_fill_form_thread,
                         args=(prepared, asked, text, mapping, form_title, session_title, session_id),
                         daemon=True).start()

    workspace.hide_warning(kind="error")
    if not asked:
        _show_form_review(prepared, {}, session_title, session_id)
    elif needs_phi_review():
        ScrubWindow(root, hidden_source, start, stop_flashing)
    else:
        start(hidden_source)


def _ask_form_values(asked, source, mapping, form_title, cancel_event=None, progress=None):
    """Asks the model for ``asked`` fields from ``source``; returns ({field name: value}, problems)."""
    values, problems = {}, []
    done = 0
    for group, prompt in form_filler.build_prompts(asked, source, form_title):
        if cancel_event is not None and cancel_event.is_set():
            break
        if progress is not None:
            progress(done, len(group), len(asked))
        try:
            reply = send_text_to_chatgpt(prompt, cancel_event)
        except Exception as e:
            logger.exception(f"Form filling request failed: {e}")
            problems.append(str(e))
            reply = ""
        if reply and is_llm_error(reply):
            problems.append(describe_llm_error(reply))
            reply = ""
        answers = form_filler.parse_answers(reply or "", len(group))
        if reply and not answers:
            logger.warning(f"Could not read form answers from the model reply: {reply[:300]!r}")
        for number, field in enumerate(group, 1):
            value = form_filler.restore(answers.get(number, ""), mapping)
            values[field.name] = form_filler.normalize(field, value)
        done += len(group)
    return values, problems


def _fill_form_thread(prepared, asked, source, mapping, form_title, session_title, session_id=None):
    cancel_event = threading.Event()
    state = {"cancelled": False, "worker": None}
    values, problems = {}, []

    def cancel():
        state["cancelled"] = True
        cancel_event.set()
        worker = state["worker"]
        try:
            if worker is not None and worker.is_alive():
                kill_thread(worker.ident)
        except Exception as e:
            logger.exception(f"An error occurred: {e}")
        finally:
            stop_flashing()
            run_on_ui(lambda: workspace.toast("Cancelled", icon_name="info"))

    loading_window = LoadingWindow(root, "Filling form", "Reading the form…", on_cancel=cancel,
                                   note_text=f"{len(asked)} fields to fill. Nothing is saved until you review it.")

    def work():
        found, issues = _ask_form_values(
            asked, source, mapping, form_title, cancel_event,
            progress=lambda done, size, total: loading_window.set_text(
                f"Filling fields {done + 1}–{done + size} of {total}…"))
        values.update(found)
        problems.extend(issues)

    worker = threading.Thread(target=work)
    state["worker"] = worker
    worker.start()
    worker.join()

    loading_window.destroy()
    stop_flashing()
    if state["cancelled"]:
        return
    if problems and not any(values.values()):
        report_error(f"The form could not be filled. {problems[0]}")
        return
    run_on_ui(_show_form_review, prepared, values, session_title, session_id)


def _show_form_review(prepared, values, session_title, session_id=None, from_session=True):
    review = {}
    form_title = os.path.splitext(os.path.basename(prepared.original_path))[0]

    def on_saved(path):
        workspace.toast(f"Saved {os.path.basename(path)}")
        session = session_store.get(session_id) if session_id else None
        window = review.get("window")
        if session is None or window is None:
            return
        # The chat knows which forms were filled and with what
        entered = []
        saved = window.saved_values
        for field, value in ((f, saved.get(f.name, "")) for f in window.fields):
            if field.kind == form_filler.CHECKBOX:
                value = "ticked" if value == "yes" else ""
            elif field.kind in (form_filler.RADIO, form_filler.CHOICE):
                value = field.option_label(value) if value else ""
            entered.append((field.label, value))
        session.add_chat("event", form_event_text(os.path.basename(prepared.original_path),
                                                  os.path.basename(path), entered))
        refresh_chat_window(session.id)
        if storing_notes_locally():
            save_notes_history()

    review["window"] = FormFillWindow(root, prepared.original_path, prepared.fields, values,
                                      session_title=session_title, on_saved=on_saved,
                                      fill_path=prepared.fill_path, converted=prepared.converted,
                                      talk=FormTalk(prepared, form_title, session_id), from_session=from_session)


def transcribe_audio(audio):
    """Transcribes in-memory audio (16 kHz float32) with the speech-to-text set in Settings."""
    if app_settings.editable_settings[SettingsKeys.LOCAL_WHISPER.value]:
        low_memory = app_settings.is_low_mem_mode()
        if not is_whisper_valid() and low_memory:
            load_stt_model(app_settings=app_settings).join()
        if not is_whisper_valid():
            raise RuntimeError("The speech-to-text model is still loading. Try again in a moment.")
        try:
            text = transcribe_local(audio)
        finally:
            if low_memory:
                unload_stt_model()
    else:
        settings = app_settings.editable_settings
        headers = {"Authorization": f"Bearer {settings[SettingsKeys.WHISPER_SERVER_API_KEY.value]}"}
        body = {"use_translate": settings[SettingsKeys.USE_TRANSLATE_TASK.value]}
        language, prompt = speech_options()
        if language:
            body["language_code"] = language
        body["initial_prompt"] = prompt
        files = {"audio": ("dictation.wav", to_wav_bytes(audio, RATE), "audio/wav")}
        response = requests.post(settings[SettingsKeys.WHISPER_ENDPOINT.value], headers=headers, files=files,
                                 data=body, verify=not settings[SettingsKeys.S2T_SELF_SIGNED_CERT.value])
        if response.status_code != 200:
            raise RuntimeError(f"The speech-to-text server returned an error (HTTP {response.status_code}).")
        text = response.json().get("text", "")
    if app_settings.editable_settings[SettingsKeys.ENABLE_HALLUCINATION_CLEAN.value] and FeatureToggle.HALLUCINATION_CLEANING:
        text = hallucination_cleaner.clean_text(text)
    return (text or "").strip()


class FormTalk:
    """Talk to fill: the clinician talks through a form and the AI fills it from what was said."""

    def __init__(self, prepared, form_title, session_id):
        self.prepared = prepared
        self.form_title = form_title
        self.session_id = session_id
        self.dictation = Dictation(open_microphone_stream, chunk=CHUNK, rate=RATE)

    def start(self):
        if is_recording:
            raise RuntimeError("The microphone is in use for the visit. Stop transcribing first.")
        self.dictation.start()

    def level(self):
        return self.dictation.level

    def elapsed(self):
        return self.dictation.elapsed

    def cancel(self):
        self.dictation.cancel()

    def finish(self, on_text, on_error):
        audio = self.dictation.stop()
        if audio.size < RATE // 2:
            on_error("That was too short. Choose Start talking and say the details for the form.")
            return

        def work():
            try:
                text = transcribe_audio(audio)
            except Exception as e:
                logger.exception(f"Dictation could not be transcribed: {e}")
                run_on_ui(on_error, f"What you said could not be transcribed: {e}")
                return
            run_on_ui(on_text, text)

        threading.Thread(target=work, daemon=True).start()

    def fill(self, text, on_values, on_error):
        workspace.flush_edits()
        session = session_store.get(self.session_id) if self.session_id else None
        source = form_filler.compose_source(
            title=session.title if session else "", note=session.note if session else "",
            context=session.context if session else "", transcript=session.transcript if session else "",
            visit_date=session.created_at.date() if session else None, max_chars=form_source_budget(),
            dictation=text)
        hidden, mapping = form_filler.pseudonymize(source)
        asked = [f for f in self.prepared.fields if f.needs_answer]
        if not asked:
            on_values({})
            return

        def start(prompt_source):
            def work():
                values, problems = _ask_form_values(asked, prompt_source, mapping, self.form_title)
                if problems and not any(values.values()):
                    run_on_ui(on_error, f"The form could not be filled. {problems[0]}")
                else:
                    run_on_ui(on_values, values)
            threading.Thread(target=work, daemon=True).start()

        if needs_phi_review():
            ScrubWindow(root, hidden, start, lambda: on_error("Not sent to the AI."))
        else:
            start(hidden)


# ------------------------------------------------------------------- chat
def refresh_chat_window(session_id):
    workspace.refresh_chat(session_id)


def _clear_chat(session):
    if messagebox.askyesno("Clear chat", "Delete this session's chat? This cannot be undone."):
        session.chat = []
        session.touch()
        if storing_notes_locally():
            save_notes_history()


def open_chat():
    """Shows or hides the chat panel for the open session."""
    workspace.flush_edits()
    if workspace.current is None:
        return
    workspace.toggle_chat(on_send=send_chat_message, on_clear=_clear_chat)


def send_chat_message(session, text):
    """Sends a chat message with the session as context and shows the reply."""
    panel = workspace.chat_panel
    workspace.flush_edits()
    history = list(session.chat)
    session.add_chat("user", text)
    if panel is not None:
        panel.refresh(session.id)
    if storing_notes_locally():
        save_notes_history()

    prompt, report = compose_chat_prompt(session, history, text, budget=form_source_budget())
    if any(report.values()):
        logger.info(f"Chat prompt shortened to fit: {report}")
    # Identifiers become placeholders the model copies; the real values are put back in its reply
    hidden, mapping = form_filler.pseudonymize(prompt)

    def start(prompt_text):
        if panel is not None:
            panel.set_busy(session.id, True)
        threading.Thread(target=_chat_thread, args=(session, prompt_text, mapping), daemon=True).start()

    def cancelled():
        if panel is not None:
            panel.set_busy(session.id, False, "Not sent.")

    if needs_phi_review():
        ScrubWindow(root, hidden, start, cancelled)
    else:
        start(hidden)


def _chat_thread(session, prompt_text, mapping):
    try:
        reply = send_text_to_chatgpt(prompt_text)
        error = describe_llm_error(reply) if is_llm_error(reply) else None
    except Exception as e:
        logger.exception(f"Chat request failed: {e}")
        reply, error = "", str(e)

    def finish():
        panel = workspace.chat_panel
        if error:
            if panel is not None:
                panel.show_error(session.id, f"No reply: {error}")
            else:
                report_error(f"The chat reply could not be created. {error}")
            return
        session.add_chat("assistant", form_filler.restore(reply.strip(), mapping))
        if panel is not None:
            panel.set_busy(session.id, False)
        if storing_notes_locally():
            save_notes_history()

    run_on_ui(finish)


# The bundled OSCAR eForm Mapper, served on this computer only while FreeScribe runs
eform_mapper_server = None


def _detect_eform_fields(payload):
    from services import eform_mapping
    return eform_mapping.detect(payload)


def open_eform_mapper():
    """Opens the bundled OSCAR eForm Mapper in the web browser, connected to FreeScribe."""
    global eform_mapper_server
    mapper_dir = get_file_path('assets', 'eform_mapper')
    if not os.path.isfile(os.path.join(mapper_dir, 'eform_mapper.html')):
        messagebox.showinfo("eForm mapper", "The eForm mapper is not included in this copy of FreeScribe.")
        return
    try:
        if eform_mapper_server is None:
            from services.mapper_server import MapperServer
            try:
                from services.eform_mapping import MappingError
                user_errors = (MappingError,)
            except ImportError:      # PyMuPDF missing: the mapper still works, without "Find fields from PDF"
                user_errors = ()
            eform_mapper_server = MapperServer(
                mapper_dir, {"/api/detect": _detect_eform_fields},
                status=lambda: {"version": get_application_version()},
                user_errors=user_errors)
        url = eform_mapper_server.start()
    except Exception as e:
        logger.exception(f"Could not start the eForm mapper: {e}")
        messagebox.showerror("eForm mapper", f"The eForm mapper could not be started.\n\n{e}")
        return
    import webbrowser
    if not webbrowser.open(url):
        messagebox.showinfo("eForm mapper", f"Open this address in your web browser:\n\n{url}")
    else:
        workspace.toast("eForm mapper opened in your browser")


def upload_file():
    global uploaded_file_path
    file_path = filedialog.askopenfilename(filetypes=(("Audio files", "*.wav *.mp3 *.m4a"),))
    if file_path:
        visit_language.reset()
        uploaded_file_path = file_path
        # The uploaded audio becomes a session of its own
        workspace.begin_capture()
        start_flashing("Transcribing audio")
        threaded_send_audio_to_server()  # Add this line to process the file immediately


def resume_recording():
    """Continues transcribing into the current session instead of starting a new one."""
    if is_recording:
        return
    threaded_toggle_recording(resume=True)


def start_flashing(text="Working"):
    """Shows the busy indicator in the session header."""
    global is_flashing
    is_flashing = True
    root.after(0, lambda: workspace.set_busy(text))


def stop_flashing():
    """Hides the busy indicator."""
    global is_flashing
    is_flashing = False
    root.after(0, lambda: workspace.set_busy(None))


def send_and_flash():
    if is_transcribe_only():
        workspace.toast("Notes are turned off (Transcribe Only Mode or the \u201cNo Note Generation\u201d template)",
                        icon_name="info")
        return
    send_and_receive()


# Initialize variables to store window geometry for switching between views
last_full_position = None
last_minimal_position = None


def toggle_view():
    """
    Toggles the user interface between a full view and a minimal view.

    Full view includes all UI components, while minimal view limits the interface
    to essential controls, reducing screen space usage. The function also manages
    window properties, button states, and binds/unbinds hover events for transparency.
    """

    if current_view == "full":  # Transition to minimal view
        set_minimal_view()

    else:  # Transition back to full view
        set_full_view()


def set_full_view():
    """
    Configures the application to display the full view interface.

    Actions performed:
    - Show the full workspace again.
    - Reset window attributes such as size, transparency, and 'always on top' behavior.
    - Create the Docker status bar.
    - Restore the last known full view geometry if available.

    Global Variables:
    - current_view: Tracks the current interface state ('full' or 'minimal').
    - last_minimal_position: Saves the geometry of the window when switching from minimal view.
    """
    def action():
        global current_view, last_minimal_position, silent_warning_duration

        workspace.show_full()

        window.toggle_menu_bar(enable=True, is_recording=is_recording)

        # Unbind transparency events and reset window properties
        root.unbind('<Enter>')  
        root.unbind('<Leave>')
        root.attributes('-alpha', 1.0)
        root.attributes('-topmost', False)
        root.minsize(Theme.px(Workspace.MIN_SIZE[0]), Theme.px(Workspace.MIN_SIZE[1]))
        current_view = "full"

        #Recreates Silence Warning Bar
        window.destroy_warning_bar()
        check_silence_warning(silence_duration= silent_warning_duration)

        # add the minimal view geometry and remove the last full view geometry
        add_min_max(root)

        # create docker_status bar if enabled
        if app_settings.editable_settings["Use Docker Status Bar"]:
            window.create_docker_status_bar()

        # Save minimal view geometry and restore last full view geometry
        last_minimal_position = root.geometry()
        root.update_idletasks()
        if last_full_position:
            root.geometry(last_full_position)
        else:
            root.geometry(f"{Theme.px(Workspace.DEFAULT_SIZE[0])}x{Theme.px(Workspace.DEFAULT_SIZE[1])}")

        # Disable to make the window an app(show taskbar icon)
        # root.attributes('-toolwindow', False)

    root.after(0, action)

def set_minimal_view():
    """
    Configures the application to display the minimal view interface.

    Actions performed:
    - Swap the workspace for the compact recorder (record, pause, timer, level).
    - Bind transparency hover events for better focus.
    - Adjust window attributes such as size, transparency, and 'always on top' behavior.

    Global Variables:
    - current_view: Tracks the current interface state ('full' or 'minimal').
    - last_full_position: Saves the geometry of the window when switching from full view.
    """
    def action():
        global current_view, last_full_position, silent_warning_duration

        compact_width, compact_height = Theme.px(Workspace.COMPACT_SIZE[0]), Theme.px(Workspace.COMPACT_SIZE[1])

        # Save full view geometry before anything changes
        if root.wm_state() == 'zoomed':  # Check if window is maximized
            root.wm_state('normal')       # Restore the window
        last_full_position = root.geometry()

        # Remove all non-essential UI components
        workspace.show_compact()

        window.toggle_menu_bar(enable=False)

        # Update window properties for minimal view
        root.attributes('-topmost', True)
        root.minsize(compact_width, compact_height)  # Smaller minimum size for minimal view
        current_view = "minimal"

        #Recreates Silence Warning Bar
        window.destroy_warning_bar()
        check_silence_warning(silence_duration= silent_warning_duration)

        # Set hover transparency events
        def on_enter(e):
            if e.widget == root:  # Ensure the event is from the root window
                root.attributes('-alpha', 1.0)

        def on_leave(e):
            if e.widget == root:  # Ensure the event is from the root window
                root.attributes('-alpha', 0.70)

        root.bind('<Enter>', on_enter)
        root.bind('<Leave>', on_leave)

        # Destroy and re-create components as needed
        window.destroy_docker_status_bar()

        # Remove the minimal view geometry and save the current full view geometry
        remove_min_max(root)

        # Restore last minimal view geometry
        if last_minimal_position:
            root.geometry(last_minimal_position)
        else:
            root.geometry(f"{compact_width}x{compact_height}")  # Set the window size to the minimal view size
    root.after(0, action)


def is_transcribe_only():
    """
    Whether sessions are recorded and transcribed without writing a note: the
    "Transcribe Only Mode" setting is on, or the "No Note Generation" template is chosen.
    """
    return bool(app_settings.editable_settings.get(SettingsKeys.TRANSCRIBE_ONLY_MODE.value, False)) \
        or NoteStyleSelector.current_style == NoteStyleSelector.NO_NOTE_STYLE


def update_ui_for_transcribe_only_mode(event=None):
    """Shows or hides the note controls when Transcribe Only Mode is switched in the settings."""
    workspace.set_transcribe_only(bool(app_settings.editable_settings.get(SettingsKeys.TRANSCRIBE_ONLY_MODE.value, False)))


def get_model_status():
    """Describes where speech-to-text and note generation run, for the sidebar status lines."""
    settings = app_settings.editable_settings
    rows = []

    if settings[SettingsKeys.LOCAL_WHISPER.value]:
        model_name = str(settings[SettingsKeys.WHISPER_MODEL.value]).strip()
        tooltip = "Speech is transcribed on this computer. Audio does not leave this device."
        if get_whisper_model() == WhisperModelStatus.ERROR:
            state, detail = "error", f"Failed to load ({model_name})"
        elif is_whisper_valid():
            state, detail = "ok", f"On this device ({model_name})"
        elif is_whisper_lock():
            state, detail = "loading", f"Loading {model_name}\u2026"
        else:
            state, detail = "off", f"On this device ({model_name}), loads when needed"
    else:
        endpoint = str(settings[SettingsKeys.WHISPER_ENDPOINT.value])
        state, detail = "remote", f"Server {urlparse(endpoint).hostname or endpoint}"
        tooltip = f"Audio is sent to {endpoint} for transcription."
    rows.append({"label": "Speech-to-text", "detail": detail, "state": state, "tooltip": tooltip})

    if settings[SettingsKeys.LOCAL_LLM.value]:
        model_name = str(settings[SettingsKeys.LOCAL_LLM_MODEL.value]).strip()
        tooltip = "Notes are written on this computer. Transcripts do not leave this device."
        if ModelManager.local_model == ModelStatus.ERROR:
            state, detail = "error", f"Failed to load ({model_name})"
        elif ModelManager.is_llm_valid():
            state, detail = "ok", f"On this device ({model_name})"
        elif app_settings.is_low_mem_mode():
            state, detail = "off", f"On this device ({model_name}), loads when needed"
        else:
            state, detail = "off", f"On this device ({model_name}), not loaded yet"
    else:
        endpoint = str(settings[SettingsKeys.LLM_ENDPOINT.value])
        state, detail = "remote", f"Server {urlparse(endpoint).hostname or endpoint}"
        tooltip = f"Transcripts are sent to {endpoint} to write notes."
    rows.append({"label": "AI notes", "detail": detail, "state": state, "tooltip": tooltip})
    return rows


def get_transcription_language():
    """The transcription language shown in the session header."""
    language = str(app_settings.editable_settings[SettingsKeys.WHISPER_LANGUAGE_CODE.value]).strip()
    if language in SettingsWindow.AUTO_DETECT_LANGUAGE_CODES:
        if visit_language.language:
            return f"Auto-detect: {visit_language.language.upper()}"
        return "Auto-detect"
    return language.upper() if len(language) <= 3 else language


window.load_main_window()

# Tracks the available microphones, the selected one and its live input level
mic_test = MicrophoneTestFrame(parent=root, p=p, app_settings=app_settings, root=root)

# Build the main workspace: session sidebar, recorder, Context / Transcript / Note tabs
workspace = Workspace(
    root,
    session_store,
    actions={
        "toggle_recording": lambda: threaded_toggle_recording(),
        "resume_recording": resume_recording,
        "toggle_pause": toggle_pause,
        "generate": send_and_flash,
        "upload": upload_file,
        "ask": ask_assistant,
        "generate_document": generate_document,
        "fill_form": fill_form,
        "open_chat": open_chat,
        "find_drug_flags": find_drug_flags,
        "open_eform_mapper": open_eform_mapper,
        "toggle_view": toggle_view,
        "open_settings": window.open_settings,
        "help_guide": window.open_help_guide,
        "about": window.open_about,
        "third_party_notices": window.open_third_party_notices,
        "debug_window": window.open_debug_window,
        "manage_recordings": window.open_recordings_manager,
        "manage_logs": window.open_log_window,
        "get_data_options": window.data_options,
        "get_status": get_model_status,
        "get_language": get_transcription_language,
        "sessions_changed": schedule_notes_save,
        "sessions_cleared": on_sessions_cleared,
    },
    ui_state=ui_state,
    mic=mic_test,
    version=get_application_version(),
)
window.attach_workspace(workspace)
workspace.set_storage_mode(storing_notes_locally())

if app_settings.editable_settings["Use Docker Status Bar"]:
    window.create_docker_status_bar()

# Bind Alt+P to pause / resume
root.bind('<Alt-p>', lambda event: toggle_pause())

# Bind Alt+R to start / stop transcribing
root.bind('<Alt-r>', lambda event: workspace.record_button.enabled and threaded_toggle_recording())

# Window size: the last one used if it still fits this screen, otherwise the default
root.minsize(Theme.px(Workspace.MIN_SIZE[0]), Theme.px(Workspace.MIN_SIZE[1]))
initial_size = f"{Theme.px(Workspace.DEFAULT_SIZE[0])}x{Theme.px(Workspace.DEFAULT_SIZE[1])}"
try:
    saved_width, saved_height = (int(value) for value in str(ui_state.get("window_size", "")).split("x"))
    if (Theme.px(Workspace.MIN_SIZE[0]) <= saved_width <= root.winfo_screenwidth()
            and Theme.px(Workspace.MIN_SIZE[1]) <= saved_height <= root.winfo_screenheight()):
        initial_size = f"{saved_width}x{saved_height}"
except (ValueError, TypeError):
    pass
root.geometry(initial_size)

# ram checkj
if utils.system.is_system_low_memory() and not app_settings.is_low_mem_mode():
    logger.warning("System has low memory.")

    popup_box = PopupBox(root, 
    title="Low Memory Warning", 
    message="Your system has low memory. Please consider enabling Low Memory Mode in the settings.",
    button_text_1="Enable",
    button_text_2="Dismiss",
    )

    if popup_box.response == "button_1":
        app_settings.editable_settings[SettingsKeys.USE_LOW_MEM_MODE.value] = True
        app_settings.save_settings_to_file()
        logger.debug("Low Memory Mode enabled.")

if (app_settings.editable_settings['Show Welcome Message']):
    window.show_welcome_message()

#Wait for the UI root to be intialized then load the model. If using local llm.
# Do not load the models if low mem is activated.
if not app_settings.is_low_mem_mode():
    # Wait for the UI root to be intialized then load the model. If using local llm.
    if app_settings.editable_settings[SettingsKeys.LOCAL_LLM.value]:
        def on_cancel_llm_load():
            cancel_await_thread.set()
        root.after(
            100,
            lambda: (
                ModelManager.setup_model(
                    app_settings=app_settings,
                    root=root,
                    on_cancel=on_cancel_llm_load)))

    if app_settings.editable_settings[SettingsKeys.LOCAL_WHISPER.value]:
        # Inform the user that Local Whisper is being used for transcription
        print("Using Local Whisper for transcription.")
        root.after(100, lambda: (load_model_with_loading_screen(root=root, app_settings=app_settings)))

if app_settings.editable_settings[SettingsKeys.ENABLE_HALLUCINATION_CLEAN.value] and FeatureToggle.HALLUCINATION_CLEANING:
    root.after(100, lambda: (
        load_hallucination_cleaner_model(root, app_settings)))

if app_settings.editable_settings[SettingsKeys.ENABLE_HALLUCINATION_CLEAN.value] and FeatureToggle.HALLUCINATION_CLEANING:
    root.after(100, lambda: (
        load_hallucination_cleaner_model(root, app_settings)))

# wait for both whisper and llm to be loaded before unlocking the settings button
def await_models(timeout_length=60):
    """
    Waits until the necessary models (Whisper and LLM) are fully loaded.

    The function checks if local models are enabled based on application settings.
    If a remote model is used, the corresponding flag is set to True immediately,
    bypassing the wait. Otherwise, the function enters a loop that periodically
    checks for model readiness and prints status updates until both models are loaded.

    :return: None
    """

    if not hasattr(await_models, "start_timer"):
        await_models.start_timer = time.time()

    # if we cancel this thread then break out of the loop
    if cancel_await_thread.is_set():
        logger.info("*** Model loading cancelled. Enabling settings bar.")
        #reset the flag
        cancel_await_thread.clear()
        # reset the settings bar
        window.enable_settings_menu()
        # return so the .after() doesnt get called.
        return

    # if we are using remote whisper then we can assume it is loaded and dont wait
    whisper_loaded = (not app_settings.editable_settings[SettingsKeys.LOCAL_WHISPER.value] or is_whisper_valid())

    # if we are not using local llm then we can assume it is loaded and dont wait
    llm_loaded = (not app_settings.editable_settings[SettingsKeys.LOCAL_LLM.value] or ModelManager.is_llm_valid())

    # Check for errors in models
    whisper_error = get_whisper_model() == WhisperModelStatus.ERROR
    llm_error = ModelManager.local_model == ModelStatus.ERROR

    logger.debug("*** Model loading status: ")
    logger.debug(f"Whisper loaded: {whisper_loaded}, Whisper Error Status:{whisper_error}, LLM loaded: {llm_loaded}, LLM Error status: {llm_error}")

    elapsed_time = time.time() - await_models.start_timer
    
    # Check if we should show error dialog (timeout OR any model error)
    should_show_error = (elapsed_time >= timeout_length) or \
        (whisper_error and llm_loaded) or \
        (llm_error and whisper_loaded) or \
        (llm_error and whisper_error)

    # wait for both models to be loaded
    if (not whisper_loaded or not llm_loaded) and not app_settings.is_low_mem_mode():
        if math.floor(elapsed_time) % 5 == 0:
            logger.info(f"Waiting for models to load. Loading timer: {math.floor(elapsed_time)}, Timeout:{timeout_length}")

        if should_show_error:
            # Gather diagnostic information about which models failed
            failed_models = []
            if (not whisper_loaded and app_settings.editable_settings[SettingsKeys.LOCAL_WHISPER.value]) or whisper_error:
                failed_models.append("Whisper (STT)")
            if (not llm_loaded and app_settings.editable_settings[SettingsKeys.LOCAL_LLM.value]) or llm_error:
                failed_models.append("LLM")
            
            failed_models_str = ', '.join(failed_models) if failed_models else 'Unknown'
            
            if elapsed_time >= timeout_length:
                error_message = f"Models failed to load within {timeout_length} seconds."
            else:
                error_message = "One or more models failed to load due to errors."
            
            logger.error(
                f"{error_message} "
                f"Failed models: {failed_models_str}. "
                "Please check your settings."
            )
            
            try:
                messagebox.showerror(
                    "Model Loading Error",
                    f"{error_message}\n\n"
                    f"Failed models: {failed_models_str}\n\n"
                    "The settings menu has been re-enabled. Please check your configuration and try again."
                )
            except Exception as e:
                logger.warning(f"Failed to show error notification dialog: {e}")
            finally:
                # Ensure settings menu is always enabled, regardless of success or failure
                window.enable_settings_menu()
            return

        try:
            # override the lock in case something else tried to edit
            window.disable_settings_menu()
            root.after(1000, await_models)
        except Exception as e:
            logger.exception(f"Error in model loading loop: {e}")
            # Ensure settings menu is enabled if there's an error
            window.enable_settings_menu()
            raise
    else:
        logger.info("*** Models loaded successfully on startup.")

        # if error null out the model
        if ModelManager.local_model == ModelStatus.ERROR:
            ModelManager.local_model = None
        
        if get_whisper_model() == WhisperModelStatus.ERROR:
            set_whisper_model(None)

        window.enable_settings_menu()


root.after(100, await_models)

root.bind("<<LoadSttModel>>", lambda e: load_stt_model(e, app_settings=app_settings))
root.bind("<<UnloadSttModel>>", unload_stt_model)

def generate_note_bind(event, data: np.ndarray):
    """
    Generate a note based on the current user input and update the response display.

    Args:
        event: Optional event parameter for binding to tkinter events.
    """
    loading_window = LoadingWindow(root, "Transcribing Audio", "Transcribing Audio. Please wait.", on_cancel=clear_application_press)
    
    def action():
        clear_application_press()
        # The stored recording becomes a session of its own
        run_on_ui(workspace.begin_capture)

        wav_data = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768

        result = transcribe_local(wav_data)

        update_gui(result)

    wrk_thrd = threading.Thread(target=action)
    wrk_thrd.start()

    def check_thread():
        if wrk_thrd.is_alive():
            root.after(100, check_thread)
        else:
            loading_window.destroy()
            send_and_receive()

    check_thread()

                           
root.bind("<<GenerateNote>>", lambda e: threading.Thread(target=lambda: generate_note_bind(e, RecordingsManager.last_selected_data)).start())

if app_settings.editable_settings[SettingsKeys.STORE_NOTES_LOCALLY.value]:
    # Load the saved sessions and show them in the sidebar
    load_notes_history()

root.bind("<<TranscribeOnlyModeChanged>>", update_ui_for_transcribe_only_mode)
root.after(100, update_ui_for_transcribe_only_mode)

root.mainloop()

p.terminate()
