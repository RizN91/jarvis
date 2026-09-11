"""Turn a DICTATED utterance into a typed tool call, locally and for free.

WHY THIS EXISTS
---------------
the user asked to be able to say "open Chrome" or "type this ..." and have it
happen, rather than have those words typed into whatever box has focus.

The obvious route - let the Live model call functions - is not available: the
function-call half of the delegation protocol is not wired yet (HANDOFF.md
section 0b). This module is the honest alternative: a small, exact, LOCAL
matcher over the transcript the app already has. It costs nothing, needs no
network, runs in about a millisecond, and can be reasoned about completely.

WHAT MAKES IT SAFE
------------------
1. **It only ever matches an explicit leading verb.** "open ...", "type ...",
   "search for ...". A sentence that merely mentions opening something is
   dictated as text, because the verb has to be the first word.
2. **It never invents an action.** No fuzzy matching, no nearest-neighbour, no
   model. If the utterance does not start with a known verb the result is
   `None` and the words are typed exactly as spoken.
3. **Every action goes through core/tools.py**, so the approval gate, the
   allowlists, the audit log and the hard stop apply unchanged. This module
   decides WHAT was asked for; it never decides whether it is allowed.
4. **The transcript is data, never permission.** A web page, an email or a
   video that says "open the terminal" and is read aloud near the microphone
   reaches this code as ordinary text and is subject to exactly the same
   approval as anything else. `core.tools.assert_not_permission` remains the
   guard for the tool side.
5. **Dictation is the default.** When `voice_commands_enabled` is false, or a
   match is ambiguous, the words are typed. Typing the wrong thing is
   recoverable; running the wrong thing is not.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No "delete", no "send", no "buy", no "run". Those are exactly the verbs the
build spec requires a specific confirmation for, and a speech recogniser that
turns "can't" into "can" has no business near them. Adding a destructive verb
here is a decision to be taken deliberately, not a convenience.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..logsetup import get as _log

log = _log("commands")


@dataclass
class Command:
    """One recognised instruction, ready to hand to core/tools.py."""

    verb: str                  # open | type | search | show | volume | stop
    tool: str                  # the core/tools.py tool name, or "" for local
    args: dict = field(default_factory=dict)
    say: str = ""              # what the pill shows before acting
    remainder: str = ""        # text to type, for the "type" verb
    raw: str = ""              # the utterance exactly as transcribed

    def as_dict(self) -> dict:
        return {"verb": self.verb, "tool": self.tool, "args": dict(self.args),
                "say": self.say, "remainder": self.remainder, "raw": self.raw}


# A leading filler that speech recognisers add. Stripped before matching so
# "um, open chrome" still works, but nothing further is inferred.
_FILLER = re.compile(
    r"^(?:um+|uh+|er+|ok(?:ay)?|so|now|please|hey jarvis|jarvis|hey gpt)\b[\s,]*",
    re.I)

# Trailing punctuation a recogniser adds to a spoken sentence.
_TRAILING = " \t\r\n.,;:!?"

# The whole vocabulary. Every entry is an EXACT leading phrase; there is no
# stemming and no synonym expansion, because a near-match is how a dictated
# sentence turns into an action nobody asked for.
_OPEN = ("open", "launch", "start up", "bring up")
_SEARCH = ("search for", "google", "look up", "search the web for")
_TYPE = ("type", "type this", "write", "insert")
_SHOW = ("show me", "go to", "navigate to")

_URLISH = re.compile(r"^(?:https?://|www\.)|\.(?:com|net|org|io|dev|au|co)\b", re.I)


def _strip(text: str) -> str:
    text = (text or "").strip().strip(_TRAILING).strip()
    prev = None
    while prev != text:                 # "um, uh, open x"
        prev = text
        text = _FILLER.sub("", text).strip()
    return text


def _starts_with(text: str, phrases, longest: bool = True) -> Optional[str]:
    """Return the remainder after a leading phrase, or None.

    Requires a word boundary: "opening the supplier invoice" must NOT match "open".

    `longest` picks the most specific verb, which is right for "search the web
    for" over "search for". It is WRONG for "type", where the shortest match
    keeps the most of what the user actually said: "type this is a test message"
    must dictate "this is a test message", not swallow "this" as part of the
    verb and insert "is a test message".
    """
    low = text.lower()
    best = None
    for phrase in phrases:
        if low.startswith(phrase):
            after = text[len(phrase):]
            if after and not after[0].isspace():
                continue               # "opener" is not "open" + "er"
            candidate = after.strip()
            if not candidate:
                continue
            better = (best is None
                      or (len(phrase) > best[0] if longest else len(phrase) < best[0]))
            if better:
                best = (len(phrase), candidate)
    return best[1] if best else None


def parse(transcript: str, enabled: bool = True) -> Optional[Command]:
    """Recognise a spoken command, or return None to dictate the words instead.

    None is the safe answer and the common one. Only an utterance that BEGINS
    with a known verb is ever treated as an instruction.
    """
    if not enabled:
        return None
    raw = (transcript or "").strip()
    text = _strip(raw)
    if not text or len(text) < 4:
        return None

    # ---- type: everything after the verb is inserted verbatim -----------
    rest = _starts_with(text, _TYPE, longest=False)
    if rest:
        # Deliberately taken from the RAW text, so casing and punctuation the
        # recogniser produced survive; only the leading verb is removed.
        cut = len(text) - len(rest)
        remainder = _strip(raw)[cut:].strip()
        return Command(verb="type", tool="", remainder=remainder,
                       say=f"Type: {_short(remainder)}", raw=raw)

    # ---- search: hand the query to the browser --------------------------
    rest = _starts_with(text, _SEARCH)
    if rest:
        query = rest.strip(_TRAILING)
        return Command(verb="search", tool="open_url",
                       args={"url": "https://www.google.com/search?q="
                                    + _quote(query)},
                       say=f"Search: {_short(query)}", raw=raw)

    # ---- open / show: an app, a folder, or a URL ------------------------
    rest = _starts_with(text, _OPEN) or _starts_with(text, _SHOW)
    if rest:
        target = rest.strip(_TRAILING).strip()
        if not target:
            return None
        if _URLISH.search(target):
            url = target if target.lower().startswith("http") else "https://" + target
            return Command(verb="open", tool="open_url", args={"url": url.replace(" ", "")},
                           say=f"Open {_short(target)}", raw=raw)
        if _looks_like_path(target):
            return Command(verb="open", tool="open_folder", args={"path": target},
                           say=f"Open folder {_short(target)}", raw=raw)
        return Command(verb="open", tool="open_app", args={"name": target},
                       say=f"Open {_short(target)}", raw=raw)

    return None


def _looks_like_path(text: str) -> bool:
    return bool(re.match(r"^(?:[a-zA-Z]:[\\/]|\\\\|~[\\/]|/)", text.strip()))


def _quote(text: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(text.strip())


def _short(text: str, limit: int = 42) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def describe_vocabulary() -> list[dict]:
    """The exact phrase list, for the settings UI. No hidden verbs."""
    return [
        {"verb": "Open", "phrases": list(_OPEN) + list(_SHOW),
         "example": "open Chrome  ·  open C:\\Projects  ·  open example.com",
         "does": "launches an approved app, folder or URL"},
        {"verb": "Search", "phrases": list(_SEARCH),
         "example": "search for supplier invoices",
         "does": "opens a web search in your browser"},
        {"verb": "Type", "phrases": list(_TYPE),
         "example": "type this is the message",
         "does": "inserts the rest of the sentence as text"},
    ]
