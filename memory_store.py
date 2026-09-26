# Roll No: cert-aai-2026-06-0034
"""
memory_store.py - standing instructions that survive a restart (Part 5).

Adapted from memory.py in Assignment 5: same one-key-one-value idea, same
atomic write, but this version stores preferences rather than facts and keeps
provenance, because in an inbox the interesting question is not what was said
but whether we should have believed it.

Two rules that matter:

  1. A preference is only ever written from a message guard.scan() did not
     call hostile. m039 asks, in the owner's own voice and from the owner's
     own address, to "save this as a standing preference" - that preference
     is exactly the attack, and this module never sees it, because triage
     refuses the message before it gets here.
  2. Every stored preference records the message id it came from, so any
     behaviour it causes later can be traced back to a line of mail.

The file is prefs.json in the project folder. Delete it (or run
`python demo.py --reset`) to start again.
"""

import json
import os
import re
from datetime import datetime

import config

MAX_HISTORY = 5


def _load():
    if not os.path.exists(config.PREFS_FILE):
        return {}
    try:
        with open(config.PREFS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}   # a corrupt file must not kill a run


def _save(store):
    tmp = config.PREFS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)
    os.replace(tmp, config.PREFS_FILE)


def remember(key, value, source_msg, text, kind="note", scope=None):
    """Store or update one preference. Returns the stored record."""
    key = "_".join(str(key).strip().lower().split())
    store = _load()
    old = store.get(key)
    record = {
        "key": key,
        "value": value,
        "kind": kind,
        "scope": scope,
        "source_msg": source_msg,
        "stated_as": " ".join(text.split())[:200],
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "history": [],
    }
    if old and old.get("value") != value:
        history = old.get("history", [])
        history.append({"value": old.get("value"), "source_msg": old.get("source_msg"),
                        "replaced_at": record["recorded_at"]})
        record["history"] = history[-MAX_HISTORY:]
    store[key] = record
    _save(store)
    return record


def all_prefs():
    return _load()


def get(key):
    return _load().get(key)


def clear():
    if os.path.exists(config.PREFS_FILE):
        os.remove(config.PREFS_FILE)


def describe():
    store = _load()
    if not store:
        return "(no standing instructions stored yet)"
    lines = []
    for key, rec in sorted(store.items()):
        lines.append("- %s = %s   [from %s, %s]" %
                     (key, rec["value"], rec["source_msg"], rec["recorded_at"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Recognising a preference in a message
# ---------------------------------------------------------------------------
_STANDING = re.compile(
    r"(standing (request|instruction)|from now on|please remember|"
    r"remember (this|that)|going forward|\bever\b|\balways\b|\bnever\b|applies to all)",
    re.I,
)

_NO_MEETINGS_BEFORE = re.compile(
    r"(do not|don'?t|no) .{0,30}meetings? before (\d{1,2})([:.]\d{2})?\s*(am|pm)?", re.I)

_CC_RULE = re.compile(
    r"(cc'?d?|copied|loop me in|copy me) .{0,40}(from|on) (anything|everything|all).{0,40}", re.I)


def extract(message):
    """
    Turn a message into a preference record, or None.

    Only called for messages that passed the guard, so an instruction that
    tries to switch the approval gate off never reaches this function.
    """
    body = message["body"]
    if not _STANDING.search(body):
        return None

    found = _NO_MEETINGS_BEFORE.search(body)
    if found:
        hour = int(found.group(2))
        minutes = (found.group(3) or ":00").lstrip(":")
        suffix = (found.group(4) or "").lower()
        if suffix == "pm" and hour < 12:
            hour += 12
        return {"key": "no_meetings_before", "value": "%02d:%s" % (hour, minutes),
                "kind": "scheduling"}

    if _CC_RULE.search(body):
        # Who wants copying, and on what.
        firm = re.search(r"(?:lawyers|counsel|legal) at ([A-Z][\w&\s]+)", body)
        domain = "hartwellcho.com" if re.search(r"hartwell", body, re.I) else None
        return {"key": "cc_on_legal", "value": message.sender,
                "kind": "cc", "scope": domain or (firm.group(1).strip() if firm else "legal")}

    return {"key": "note_%s" % message.id, "value": " ".join(body.split())[:120],
            "kind": "note"}


def applies_to_time(when):
    """
    True/False plus an explanation, for a datetime the mailbox proposes.
    Used by triage and by the conflict negotiator (X4).
    """
    pref = get("no_meetings_before")
    if not pref or when is None:
        return True, None
    limit_h, limit_m = [int(p) for p in pref["value"].split(":")]
    if (when.hour, when.minute) < (limit_h, limit_m):
        return False, "the owner does not take meetings before %s (%s)" % (
            pref["value"], pref["source_msg"])
    return True, None


def cc_for(message):
    """Extra recipients a stored preference requires for this message."""
    pref = get("cc_on_legal")
    if not pref:
        return []
    scope = pref.get("scope") or ""
    if scope and scope.lower() in message.sender:
        return [pref["value"]]
    return []
