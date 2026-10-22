# -*- mode: python ; coding: utf-8 -*-

import os
import subprocess

# Determine the path to ffmpeg
ffmpeg_path = subprocess.check_output(['which', 'ffmpeg']).decode().strip()

a = Analysis(
    ['src/FreeScribe.client/client.py'],
    pathex=[],
    binaries=[(ffmpeg_path, 'ffmpeg')],
    datas=[('./src/FreeScribe.client/whisper-assets', 'faster_whisper/assets'), ('./src/FreeScribe.client/markdown', 'markdown'), ('./src/FreeScribe.client/assets', 'assets')],
    hiddenimports=['en_core_web_md', 'en_core_sci_md'],
    hookspath=['./scripts/hooks'],
    hooksconfig={},
    runtime_hooks=['./scripts/runtime_hooks/hook-runtime.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)



# PyInstaller only warns when it cannot find Tk's scripts, and the app then cannot open a
# window. Stop the build instead. (The scripts must come from the same Tk that Python's
# tkinter was built with, so they are never added from elsewhere.)
if not any(dest.startswith("_tk_data") for dest, _src, _kind in a.datas):
    raise SystemExit("client-mac.spec: PyInstaller did not collect Tk's scripts (_tk_data); "
                     "this Python's tkinter cannot be packaged. Use a Python with its own Tcl/Tk 8.6.")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FreeScribe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    icon=['src/FreeScribe.client/assets/logo.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FreeScribe',
)
app = BUNDLE(
    coll,
    name='FreeScribe.app',
    icon='src/FreeScribe.client/assets/logo.ico',
    bundle_identifier='com.clinicianfocus.freescribe',
    info_plist={'NSMicrophoneUsageDescription':'This app requires access to the microphone to capture audio.'},
)
