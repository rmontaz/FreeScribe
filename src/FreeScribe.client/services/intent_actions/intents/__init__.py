"""
Intent recognition module.
"""

from .base import Intent, BaseIntentRecognizer
# The AI recognizer needs pydantic_ai, an optional extra. A missing or broken install of it
# must not stop FreeScribe from starting (the main window only uses the spaCy recognizer).
try:
    from .llm_recognizer import LLMIntentRecognizer, MedicalIntentResult
except Exception as _error:  # noqa: BLE001
    import logging as _logging
    _logging.getLogger(__name__).warning(f"AI intent recognizer unavailable: {_error}")
    LLMIntentRecognizer = MedicalIntentResult = None
from .spacy_recognizer import SpacyIntentRecognizer

__all__ = [
    'Intent',
    'BaseIntentRecognizer',
    'LLMIntentRecognizer',
    'MedicalIntentResult',
    'SpacyIntentRecognizer'
] 