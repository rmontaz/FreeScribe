"""Show a responsive splash before importing the application's audio/ML stack."""
import os
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

READY_ENV = 'FREESCRIBE_STARTUP_READY'


def splash(ready_path):
    import tkinter as tk
    from UI.Theme import Theme
    from UI.Art import StartupSplash
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.title('Starting FreeScribe')
    Theme.init(root)
    width, height = min(800, root.winfo_screenwidth()-60), min(540, root.winfo_screenheight()-80)
    root.geometry(f'{width}x{height}+{max(0,(root.winfo_screenwidth()-width)//2)}+{max(0,(root.winfo_screenheight()-height)//2)}')
    root.resizable(False, False)
    artwork = StartupSplash(root)
    artwork.on_cancel = lambda: Path(ready_path).with_suffix(".cancel").touch()
    root.attributes("-topmost", True)
    root.deiconify()
    root.lift()
    root.update()
    root.geometry(f'{width}x{height}+{max(0,(root.winfo_screenwidth()-width)//2)}+{max(0,(root.winfo_screenheight()-height)//2)}')
    root.after(200, root.lift)
    parent_pid = os.getppid()
    last_status = [None]
    def check_ready():
        if Path(ready_path).exists() or os.getppid() != parent_pid:
            root.destroy()
            Path(ready_path).with_suffix(".closed").touch()
        else:
            try:
                status = json.loads(Path(ready_path).with_suffix(".json").read_text())
                if status != last_status[0]:
                    artwork.set_status(**status)
                    last_status[0] = status
            except (OSError, ValueError):
                pass
            root.after(80, check_ready)
    root.after(80, check_ready)
    root.mainloop()


def launch():
    # The marker has no patient content. Its directory exists only for this launch.
    previous = os.environ.get(READY_ENV)
    with tempfile.TemporaryDirectory(prefix='freescribe-startup-') as folder:
        marker = str(Path(folder)/'ready')
        os.environ[READY_ENV] = marker
        process = None
        try:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--splash', marker])
            runpy.run_path(str(Path(__file__).with_name('client.py')), run_name='__main__')
        finally:
            Path(marker).touch()
            if process is not None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=2)
            if previous is None:
                os.environ.pop(READY_ENV, None)
            else:
                os.environ[READY_ENV] = previous


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--splash':
        splash(sys.argv[2])
    else:
        launch()
