"""Audio capture, playback and the local wake-word detector.

``wake`` wraps sherpa-onnx keyword spotting. It is optional by design: when the
model is missing, ``WakeWordDetector.is_available()`` is False and the rest of
the app keeps working push-to-talk only.
"""

from __future__ import annotations

__all__ = ["wake"]
