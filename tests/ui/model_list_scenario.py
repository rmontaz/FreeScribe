"""Settings model list scenario (used by test_model_list.py): real Tk widgets and a fake /v1/models server."""
import json, random, sys, threading, time, tkinter as tk
from tkinter import ttk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, sys.argv[1])
import types
from unittest import mock
import importlib.abc, importlib.util
class _Stub(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    ROOTS = ("llama_cpp", "spacy", "torch", "faster_whisper", "transformers", "presidio_analyzer", "presidio_anonymizer",
             "whisper", "nltk", "sklearn", "onnxruntime", "ctranslate2")
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in self.ROOTS:
            return importlib.util.spec_from_loader(name, self, is_package=True)
    def create_module(self, spec):
        m = mock.MagicMock(); m.__path__ = []; m.__spec__ = spec; return m
    def exec_module(self, module): pass
sys.meta_path.insert(0, _Stub())
for name in ():
    if name not in sys.modules:
        try: __import__(name)
        except Exception: sys.modules[name] = mock.MagicMock()
from UI.SettingsConstant import SettingsKeys
from UI.SettingsWindow import SettingsWindow
from UI.SettingsWindowUI import SettingsWindowUI

STATE = {"up": True, "models": ["llama3.1:8b", "gemma3:4b", "qwen2.5:7b"], "slow": (0.0, 0.6)}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        time.sleep(random.uniform(*STATE["slow"]))
        if not STATE["up"]:
            self.send_response(502); self.end_headers(); return
        b = json.dumps({"data": [{"id": m} for m in STATE["models"]]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_port}/v1"

root = tk.Tk()
frame = tk.Frame(root); frame.pack()
s = SettingsWindow.__new__(SettingsWindow)
s.OPENAI_API_KEY = "x"
local = tk.IntVar(value=0)
ep = tk.Entry(frame); ep.insert(0, URL)
s.editable_settings = {SettingsKeys.LOCAL_LLM_MODEL.value: "gemma3:4b", "AI Server Self-Signed Certificates": False}
s.editable_settings_entries = {SettingsKeys.LOCAL_LLM.value: local, SettingsKeys.LLM_ENDPOINT.value: ep}

ui = SettingsWindowUI.__new__(SettingsWindowUI)
ui.settings = s
ui.models_drop_down = ttk.Combobox(frame, values=["gemma3:4b", "Custom"], state="readonly")
ui.models_drop_down.grid(row=2, column=1)
ui.models_drop_down.bind("<<ComboboxSelected>>", ui.on_model_selection_change)
ui.custom_model_entry = tk.Entry(frame); ui.custom_model_entry.insert(0, "gemma3:4b")
ui.models_drop_down.set("gemma3:4b")
s.editable_settings_entries[SettingsKeys.LOCAL_LLM_MODEL.value] = ui.models_drop_down
saves = []
def fake_save(close_window=True):
    # what the real save writes: get_selected_model(), then the general loop over the entries
    m = ui.get_selected_model()
    s.editable_settings[SettingsKeys.LOCAL_LLM_MODEL.value] = m
    v = s.editable_settings_entries[SettingsKeys.LOCAL_LLM_MODEL.value].get()
    from UI.SettingsWindow import PLACEHOLDER_MODELS
    if v.strip() and v not in PLACEHOLDER_MODELS:
        s.editable_settings[SettingsKeys.LOCAL_LLM_MODEL.value] = v
    saves.append(s.editable_settings[SettingsKeys.LOCAL_LLM_MODEL.value])
ui.save_settings = fake_save
ui._get_local_model_options = lambda: ["./models/gemma-2-2b-it-Q8_0.gguf", "Custom"]

def wait(sec):
    end = time.time() + sec
    while time.time() < end:
        root.update(); time.sleep(0.01)
def visible(): return bool(ui.models_drop_down.winfo_manager())
fails = 0
def check(name, ok, detail=""):
    global fails
    fails += not ok
    print(("PASS " if ok else "FAIL ") + name, detail)

def scenario():
    global fails
    # 1. Open in server mode
    threading.Thread(target=s.update_models_dropdown, args=(ui.models_drop_down,), daemon=True).start()
    yield (1.2)
    check("open: list shown, saved model selected", visible() and ui.models_drop_down.get() == "gemma3:4b",
          f"{ui.models_drop_down.get()} {list(ui.models_drop_down['values'])}")
    # 2. Refresh rapidly 15 times, with random server delays
    for _ in range(15):
        ui._refresh_models(); yield (random.uniform(0, 0.08))
    yield (1.5)
    check("15 quick refreshes: ends on saved model, visible", visible() and ui.models_drop_down.get() == "gemma3:4b",
          ui.models_drop_down.get())
    check("no placeholder ever saved", not any(x in ("Loading models...", "Failed to load models", "Custom", "") for x in saves), str(set(saves)))
    # 3. Pick another model, refresh
    ui.models_drop_down.set("qwen2.5:7b"); ui.models_drop_down.event_generate("<<ComboboxSelected>>"); yield (0.1)
    ui._refresh_models(); yield (1.2)
    check("chosen model kept after refresh", ui.models_drop_down.get() == "qwen2.5:7b" and s.editable_settings[SettingsKeys.LOCAL_LLM_MODEL.value] == "qwen2.5:7b")
    # 4. Server down
    STATE["up"] = False
    ui._refresh_models(); yield (1.2)
    check("server down: saved model kept and shown", visible() and ui.models_drop_down.get() == "qwen2.5:7b", list(ui.models_drop_down["values"]))
    ui._refresh_models(); yield (1.2)
    check("server down: save keeps the model", s.editable_settings[SettingsKeys.LOCAL_LLM_MODEL.value] == "qwen2.5:7b")
    STATE["up"] = True
    # 5. Saved model the server doesn't list (typed by hand)
    ui.models_drop_down.set("Custom"); ui.models_drop_down.event_generate("<<ComboboxSelected>>"); yield (0.1)
    ui.custom_model_entry.delete(0, "end"); ui.custom_model_entry.insert(0, "my-finetune:latest")
    ui._refresh_models(); yield (1.2)
    check("custom name kept, list visible again", visible() and ui.models_drop_down.get() == "my-finetune:latest"
          and "llama3.1:8b" in ui.models_drop_down["values"], list(ui.models_drop_down["values"]))
    # 6. Save while the list is still loading (slow server)
    STATE["slow"] = (1.0, 1.0)
    s.editable_settings[SettingsKeys.LOCAL_LLM_MODEL.value] = "llama3.1:8b"; ui.models_drop_down.set("Loading models...")
    fake_save()
    check("save during loading keeps the real model", saves[-1] == "llama3.1:8b", saves[-1])
    STATE["slow"] = (0.0, 0.6)
    # 7. Switch to local while a server list is on its way: the late server list must not land
    STATE["slow"] = (0.8, 0.8)
    threading.Thread(target=s.update_models_dropdown, args=(ui.models_drop_down,), daemon=True).start()
    yield (0.2)
    local.set(1); s.cancel_model_requests()
    opts = ui._get_local_model_options(); ui.models_drop_down["values"] = opts; ui.models_drop_down.set(opts[0])
    yield (1.5)
    check("local list stays after late server reply", ui.models_drop_down.get().endswith(".gguf"), list(ui.models_drop_down["values"]))
    # 8. Back to server: local file name not carried over
    STATE["slow"] = (0.0, 0.2)
    s.editable_settings[SettingsKeys.LOCAL_LLM_MODEL.value] = "./models/gemma-2-2b-it-Q8_0.gguf"
    local.set(0)
    threading.Thread(target=s.update_models_dropdown, args=(ui.models_drop_down,), daemon=True).start()
    yield (1.0)
    check("back to server: a server model chosen, no .gguf in list", ui.models_drop_down.get() == "llama3.1:8b"
          and not any(v.endswith(".gguf") for v in ui.models_drop_down["values"]), list(ui.models_drop_down["values"]))
    # 9. Window closed mid-fetch: no error
    STATE["slow"] = (0.5, 0.5)
    err = []
    def run():
        try: s.update_models_dropdown(ui.models_drop_down)
        except Exception as e: err.append(repr(e))
    t = threading.Thread(target=run, daemon=True); t.start(); yield (0.1)
    frame.destroy(); yield (0.8); None
    check("closing the window mid-fetch raises nothing", not err, err)
    print(f"FAILURES: {fails}"); root.quit()

gen = scenario()
def step():
    try:
        delay = next(gen)
    except StopIteration:
        return
    root.after(int((delay or 0) * 1000), step)
root.after(10, step)
root.mainloop()
