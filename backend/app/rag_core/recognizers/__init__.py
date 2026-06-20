"""Document recognizers (OCR / text extraction) — config-driven registry.

Mirrors the rag_core component pattern (see ``rag_core/llm.py`` and the original
lawer_assistant ``rag_core``): each recognizer is a discriminated-union config
(``type`` + ``params``) resolved to a live instance by :func:`get_recognizer`.

Submodules are imported explicitly (``from app.rag_core.recognizers.factory
import get_recognizer``) rather than re-exported here, so importing the
abstraction (:mod:`base`) never drags in the concrete recognizers / their heavy
deps (httpx, langchain). This keeps the import graph acyclic with
``services/document_extraction.py``, which depends only on :mod:`base`.
"""
