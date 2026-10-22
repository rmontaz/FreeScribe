"""The installed app must not relaunch itself when Python starts a helper process."""
import ast
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[1] / "src" / "FreeScribe.client" / "client.py"


def _freeze_support_line(tree):
    for node in tree.body:
        if isinstance(node, ast.If):
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "freeze_support"):
                    return node.lineno
    return None


def test_freeze_support_runs_before_the_heavy_imports():
    tree = ast.parse(CLIENT.read_text(encoding="utf-8"))
    line = _freeze_support_line(tree)
    assert line is not None, "client.py must call multiprocessing.freeze_support()"
    first_other_import = min(
        node.lineno for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and not (isinstance(node, ast.Import) and [a.name for a in node.names] == ["multiprocessing"]))
    assert line < first_other_import, "freeze_support() must come before every other import"


def test_unattended_runs_skip_start_up_questions():
    text = CLIENT.read_text(encoding="utf-8")
    assert 'os.environ.get("FREESCRIBE_UNATTENDED") == "1"' in text
    assert "and not UNATTENDED:\n    popup_box = PopupBox" in text
    assert "and not UNATTENDED:\n    window.show_welcome_message()" in text
