# Roll No: cert-aai-2026-06-0034
"""
trace.py - the run log.

Every interesting thing that happens is appended to trace.jsonl as one JSON
object per line. Each event carries the capability that caused it, so the
manifest can point a marker at "events tagged cap=R5" and they can grep for it:

    {"ts": "...", "cap": "R5", "kind": "refusal", "msg_id": "m024", ...}

Kinds used in this project:
    run_start, read, decision, rule_hit, model_call, draft, no_grounding,
    gate, action, refusal, preference, commitment, conflict, run_end

It is deliberately dumb - append-only, no rotation, no framework. The value is
that the dashboard, the manifest evidence and the Final Report all point at the
same file.
"""

import json
import os
from datetime import datetime

import config

_RUN_ID = None


def start_run(cap, argv=None, fresh=False):
    """Begin a run. fresh=True truncates the trace so a demo starts clean."""
    global _RUN_ID
    _RUN_ID = datetime.now().strftime("%Y%m%d-%H%M%S")
    if fresh and os.path.exists(config.TRACE_FILE):
        os.remove(config.TRACE_FILE)
    event(cap, "run_start", argv=argv or [], provider=config.PROVIDER)
    return _RUN_ID


def event(cap, kind, **fields):
    """Append one event. Never raises - a broken log must not kill a run."""
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "run": _RUN_ID,
        "cap": cap,
        "kind": kind,
    }
    record.update(fields)
    try:
        os.makedirs(os.path.dirname(config.TRACE_FILE), exist_ok=True)
        with open(config.TRACE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:  # pragma: no cover
        print("  (trace write failed: %s)" % exc)
    return record


def end_run(cap, **fields):
    event(cap, "run_end", **fields)


def read_events(cap=None, kind=None):
    """Read the trace back. Used by X-capabilities and the 'why' output."""
    if not os.path.exists(config.TRACE_FILE):
        return []
    out = []
    with open(config.TRACE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cap and rec.get("cap") != cap:
                continue
            if kind and rec.get("kind") != kind:
                continue
            out.append(rec)
    return out


# --- console helpers -------------------------------------------------------
def rule(title=""):
    if not title:
        return "-" * 74
    return "-- %s %s" % (title, "-" * max(0, 70 - len(title)))


def banner(title):
    print()
    print(rule(title))
