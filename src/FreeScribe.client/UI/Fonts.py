"""Load the bundled Inter typeface for this process only (nothing is installed on the system).

Inter is licensed under the SIL Open Font License; the licence text sits next to the font files.
Registration is best effort: if it fails, the theme falls back to the system font.
"""
import ctypes
import ctypes.util
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

FILES = ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf")


def _folder():
    folder = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    if not folder.is_dir():
        from utils.file_utils import get_file_path
        folder = Path(get_file_path("assets", "fonts"))
    return folder


def register() -> bool:
    """Make the bundled fonts available to Tk. Call before the first Tk window is created."""
    folder = _folder()
    paths = [folder / name for name in FILES if (folder / name).is_file()]
    if not paths:
        return False
    loaded = 0
    for path in paths:
        try:
            if _register_one(str(path)):
                loaded += 1
        except Exception as error:  # fonts are cosmetic
            logger.debug("Could not load font %s: %s", path.name, error)
    return loaded > 0


def _register_one(path: str) -> bool:
    if sys.platform == "win32":
        # FR_PRIVATE: only this process sees the font
        return bool(ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0))
    if sys.platform == "darwin":
        core_foundation = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
        core_text = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreText"))
        core_foundation.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
        core_foundation.CFURLCreateFromFileSystemRepresentation.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
        url = core_foundation.CFURLCreateFromFileSystemRepresentation(None, path.encode(), len(path.encode()), False)
        core_text.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        core_text.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
        return bool(core_text.CTFontManagerRegisterFontsForURL(url, 1, None))  # 1 = this process
    library = ctypes.util.find_library("fontconfig")
    if not library:
        return False
    fontconfig = ctypes.cdll.LoadLibrary(library)
    fontconfig.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    fontconfig.FcConfigAppFontAddFile.restype = ctypes.c_int
    return bool(fontconfig.FcConfigAppFontAddFile(None, path.encode()))
