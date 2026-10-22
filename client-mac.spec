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


def _find_tk_library():
    """
    Tk's script folder (the one holding tk.tcl). PyInstaller guesses it from where Tcl is
    and, when the guess is wrong, only warns and builds an app that cannot open a window
    (this happened to the Intel build). Ask Tk itself first, then look in the usual places.
    """
    import glob
    import sys
    try:
        out = subprocess.run(
            [sys.executable, "-c",
             "import tkinter; r = tkinter.Tk(); r.withdraw(); print(r.eval('set tk_library')); r.destroy()"],
            capture_output=True, text=True, timeout=120)
        path = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
        if path and os.path.isfile(os.path.join(path, "tk.tcl")):
            return path
        print(f"client-mac.spec: Tk did not report a usable tk_library ({path!r}; {out.stderr.strip()[-300:]})")
    except Exception as error:  # noqa: BLE001 - fall back to searching
        print(f"client-mac.spec: could not ask Tk for its library: {error}")
    roots = [sys.base_prefix, sys.prefix, "/usr/local", "/opt/homebrew", "/Library/Frameworks"]
    patterns = ["lib/tk8.6", "opt/tcl-tk*/lib/tk8.6", "Cellar/tcl-tk*/*/lib/tk8.6",
                "Frameworks/Tk.framework/Versions/8.6/Resources/Scripts",
                "Tk.framework/Versions/8.6/Resources/Scripts"]
    for root in roots:
        for pattern in patterns:
            for path in sorted(glob.glob(os.path.join(root, pattern))):
                if os.path.isfile(os.path.join(path, "tk.tcl")):
                    return path
    return None


if not any(dest.startswith("_tk_data") for dest, _src, _kind in a.datas):
    tk_library = _find_tk_library()
    if not tk_library:
        raise SystemExit("client-mac.spec: Tk's script folder (tk.tcl) was not found; the app would not open.")
    print(f"client-mac.spec: PyInstaller missed Tk's scripts; adding them from {tk_library}")
    a.datas += Tree(tk_library, prefix="_tk_data", excludes=["demos", "*.lib", "tkConfig.sh"])

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
