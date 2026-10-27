"""Only one FreeScribe runs: the running copy is found however it was started, and a held lock always wins.

This software is released under the AGPL-3.0 license.
"""
import os
import subprocess
import sys
import time

import pytest

CLIENT_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "FreeScribe.client")


def test_finds_a_copy_started_as_python_client_py_from_its_folder(tmp_path):
    from utils.OneInstance import OneInstance
    # A stand-in "client.py" in the real client folder's place is not possible, so start a process whose
    # command line and working folder look exactly like the Mac launcher's: python client.py, from the folder
    fake_dir = tmp_path / "FreeScribe.client"
    fake_dir.mkdir()
    (fake_dir / "client.py").write_text("import time\ntime.sleep(30)\n")
    proc = subprocess.Popen([sys.executable, "client.py"], cwd=str(fake_dir))
    try:
        time.sleep(0.5)
        import psutil
        p = psutil.Process(proc.pid)
        assert OneInstance._runs_client(p, p.cmdline(), str(fake_dir / "client.py"))
        assert not OneInstance._runs_client(p, p.cmdline(), os.path.join(CLIENT_DIR, "client.py"))
        assert not OneInstance._runs_client(p, [sys.executable, "other.py"], str(fake_dir / "client.py"))
    finally:
        proc.kill()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="lock files are for Mac and Linux")
def test_held_lock_means_already_running_even_without_a_process(monkeypatch):
    from utils import OneInstance as module
    shown = []
    monkeypatch.setattr(module.messagebox, "showinfo", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(module.tk, "Tk", lambda: type("T", (), {"withdraw": lambda s: None, "destroy": lambda s: None})())
    monkeypatch.setattr(module.OneInstance, "bring_to_front", classmethod(lambda cls, name: True))
    monkeypatch.setattr(module.OneInstance, "get_running_instance_pids", lambda self: [])
    check = "linux_check_instance" if sys.platform.startswith("linux") else "macos_check_instance"
    monkeypatch.setattr(module, check, lambda: (None, True))
    assert module.OneInstance("FreeScribe", "freescribe-client").is_running() is True
    assert shown and "already open" in shown[0][1]
