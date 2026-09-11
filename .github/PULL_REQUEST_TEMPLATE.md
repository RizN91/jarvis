## What this changes

<!-- One or two sentences. The "why" matters more than the "what". -->

## How I proved it

<!-- Paste the real footer lines from the suites you ran. -->

```
tests/test_core_units.py   TOTAL ... PASSED ... FAILED 0
```

- [ ] `python -m compileall -q jarvis tests` exits 0
- [ ] `python -m pyflakes jarvis` shows no **undefined names**
- [ ] I ran the suites that cover what I touched
- [ ] I did **not** run the three billable API tests in a loop

## If this fixes a bug

<!-- Name the bug, and name the test that now covers it. -->

## If this adds a language

- [ ] Strings are short enough that the narrow (860 px) layout does not overflow
      — `tests/test_reference_visuals.py` passes
- [ ] If it is right-to-left, the layout actually mirrors

## Checklist

- [ ] No secrets, keys, tokens or personal data anywhere in the diff
- [ ] No new runtime dependency (or: here is why it is justified)
- [ ] No network listener, no telemetry, no `Set-ExecutionPolicy`
- [ ] Dictated text is still treated as data, never as instructions
- [ ] Nothing synthesises Enter or a submit key
- [ ] Screenshots, if any, are app-surface captures — **not** full-desktop grabs
- [ ] `README.md` / `ARCHITECTURE.md` updated if behaviour changed

## Anything I could not verify

<!--
"Unverified" is a perfectly good answer and much better than an estimate
presented as a measurement. Say so here.
-->
