"""GPT-Live Responses function dispatch, bounded and application-approved.

Contract checked against the official live-delegation guide on 2026-09-11.
Output snapshots can be empty: collect output_item.done before response.completed.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import threading
import time
import uuid

from . import tools
from .asyncrun import RUNNER
from ..logsetup import redact


def function_schemas() -> list[dict]:
    # Screenshots need an image-sharing UI; a local filename is not image input.
    return [{"type": "function", "name": s.name, "description": s.description,
             "parameters": {**s.params, "additionalProperties": False}, "strict": False}
            for s in tools.TOOLS.values() if s.name != "screenshot"]


class DelegatedTools:
    def __init__(self, owner):
        self.owner = owner
        self._calls = {}
        self._seen = set()
        self._usage_seen = set()
        self._stopped = threading.Event()
        self._busy = False
        self._used = 0
        self._started = time.monotonic()

    @property
    def busy(self):
        return self._busy

    def cancel(self):
        self._stopped.set()

    def handle(self, outer: dict) -> None:
        event = outer.get("event") or {}
        kind = event.get("type")
        response = event.get("response") or {}
        response_id = event.get("response_id") or response.get("id")
        if kind == "response.created":
            self._busy = True
        elif kind == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call" and item.get("call_id") not in self._seen:
                self._calls.setdefault(response_id, []).append(item)
        elif kind in ("response.completed", "response.failed", "response.incomplete", "response.cancelled"):
            self._record_usage(response)
            calls = self._calls.pop(response_id, [])
            if calls and kind == "response.completed" and not self._stopped.is_set():
                RUNNER.submit(self._execute(calls))
            else:
                self._busy = False

    def _record_usage(self, response):
        rid = response.get("id")
        usage = response.get("usage") or {}
        if not rid or not usage or rid in self._usage_seen:
            return
        self._usage_seen.add(rid)
        model = response.get("model") or self.owner.session.config.delegation["responses"]["model"]
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
        # Response IDs, not the voice-session ID: each response is a new charge.
        self.owner.app.budget.record_backend(model, int(usage.get("input_tokens", 0)),
                                             int(usage.get("output_tokens", 0)),
                                             int(cached), session_id=rid)

    def _run_call(self, item):
        if self._stopped.is_set() or self.owner._stop.is_set() or tools.is_hard_stopped():
            return {"ok": False, "cancelled": True, "detail": "cancelled; nothing ran"}
        name = item.get("name", "")
        if name not in {s["name"] for s in function_schemas()}:
            return {"ok": False, "detail": "unknown or unavailable tool"}
        raw = item.get("arguments") or "{}"
        if len(raw) > 8000:
            return {"ok": False, "detail": "arguments exceed the review limit"}
        try:
            args = json.loads(raw)
        except (ValueError, TypeError):
            return {"ok": False, "detail": "invalid JSON arguments"}
        if not isinstance(args, dict):
            return {"ok": False, "detail": "arguments must be an object"}
        limit = int(self.owner.cfg.get("max_task_tool_calls", 40))
        timeout = float(self.owner.cfg.get("max_task_seconds", 600))
        if (limit > 0 and self._used >= limit) or time.monotonic() - self._started > timeout:
            return {"ok": False, "detail": "session tool/time limit reached"}
        self._used += 1
        self.owner.app.budget.check_can_start(category="backend")
        # Every function call has a native, default-No confirmation, including
        # reads that would disclose local text to the backend. Model text never
        # generates approval tokens. Show the full exact arguments.
        prompt = (name + "\n\n" + json.dumps(args, indent=2, ensure_ascii=False)
                  + "\n\nAllow this exact action and send its result to OpenAI?")
        self.owner.app.overlay.set_state(state="approval", detail="Review the action dialog")
        answer = ctypes.windll.user32.MessageBoxW(None, prompt, "Jarvis: approve action",
                                                 0x24 | 0x100)
        if answer != 6 or self._stopped.is_set() or self.owner._stop.is_set():
            return {"ok": False, "detail": "action declined or cancelled; nothing ran"}
        token = tools.issue_approval(name, args)
        try:
            return tools.call(name, args, approval_token=token).as_dict()
        finally:
            tools.revoke_approval(token)

    async def _execute(self, calls):
        try:
            for item in calls:
                call_id = item.get("call_id")
                if not call_id or call_id in self._seen:
                    continue
                self._seen.add(call_id)
                try:
                    result = await asyncio.to_thread(self._run_call, item)
                except Exception as exc:
                    result = {"ok": False, "detail": redact(str(exc))}
                if self._stopped.is_set() or self.owner.session is None:
                    return
                await self.owner.session._send({
                    "type": "response.item.create", "event_id": uuid.uuid4().hex,
                    "item": {"type": "function_call_output", "call_id": call_id,
                             "output": redact(json.dumps(result, ensure_ascii=False))}})
            if not self._stopped.is_set() and self.owner.session is not None:
                self.owner.app.budget.check_can_start(category="backend")
                await self.owner.session._send({"type": "response.create",
                                                "event_id": uuid.uuid4().hex})
        finally:
            self._busy = False
