"""The server model list in Settings: overlapping refreshes, a slow or stopped server, switching to and from
the local model, and never saving "Loading models..." as the model. Runs a real Tk window in a subprocess."""
import os
import subprocess
import sys

import pytest

CLIENT = os.path.join(os.path.dirname(__file__), "..", "..", "src", "FreeScribe.client")
SCENARIO = os.path.join(os.path.dirname(__file__), "model_list_scenario.py")


def _tk_available():
    try:
        import tkinter
        tkinter.Tk().destroy()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _tk_available(), reason="needs a display for Tk")
def test_model_list_scenarios():
    out = subprocess.run([sys.executable, SCENARIO, os.path.abspath(CLIENT)], capture_output=True, text=True, timeout=120)
    text = out.stdout + out.stderr
    assert "FAILURES: 0" in text, "\n".join(l for l in text.splitlines() if "PASS" in l or "FAIL" in l)
