"""Deterministic recording primitives for scripted and real agent code."""

from .core import EVENT_KINDS, Instrumentor, Recorder, canonical_json, event_bytes

__all__ = ["EVENT_KINDS", "Instrumentor", "Recorder", "canonical_json", "event_bytes"]
__version__ = "0.1.0"
