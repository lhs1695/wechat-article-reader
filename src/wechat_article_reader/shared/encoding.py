"""UTF-8 handling for application text streams."""

from __future__ import annotations

import sys


def configure_utf8_stdio() -> None:
    """Reconfigure standard text streams before user-visible I/O."""

    for stream_name, errors in (
        ("stdin", "strict"),
        ("stdout", "backslashreplace"),
        ("stderr", "backslashreplace"),
    ):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors=errors)
            except (OSError, ValueError):
                # Embedded callers may provide streams that cannot be reconfigured.
                continue
