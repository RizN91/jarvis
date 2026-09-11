"""Make a test's own output survive being captured.

WHY THIS EXISTS
---------------
Every test here prints `PASS`/`FAIL` lines, and several print the text they
actually inserted - which includes `café`, `€12.50`, an em dash and CJK.

When stdout is a console, CPython writes through WriteConsoleW and Unicode is
fine. When stdout is a PIPE - which is what happens the moment any agent, CI
step or `>` redirect captures the run - CPython falls back to the locale
encoding, cp1252 on this machine, and the first non-Latin-1 character raises
`UnicodeEncodeError` in the middle of the test.

That is not hypothetical: `tests/test_insert_integration.py` died exactly there,
16 assertions in, leaving Notepad open and the remaining checks (the Enter
policy, clipboard restore, undo, every refusal path) unrun. The traceback blamed
`encodings/cp1252.py`, which looks like a test bug and is easy to wave away.

`use_utf8_stdout()` forces UTF-8 with `errors="replace"` on both streams, so the
text is readable when the pipe can take it and degrades to `?` when it cannot -
but the test always runs to its last assertion.
"""

from __future__ import annotations

import sys


def use_utf8_stdout() -> None:
    """Reconfigure stdout/stderr so non-ASCII output can never abort a test."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # Already-wrapped or non-reconfigurable streams (pytest's capture
            # objects, for one) are left exactly as they are.
            pass
