# Roll No: cert-aai-2026-06-0034
"""
gate.py - the lock on everything that cannot be undone (Part 4).

Classification used throughout the project (also in CAPABILITIES.md):

  reversible   draft, label, archive, defer, flag, note
               All of these write to files this project owns. Re-running a
               command overwrites them and nothing outside the project has
               seen them, so undoing means running it again.

  irreversible send, delete
               send writes a file into outbox/, which in this assignment is
               what "the message has left" means; once a recipient has it,
               nothing here can take it back.
               delete is irreversible BY DESIGN, not by accident: the mock
               store has no trash, and a delete is the one action an attacker
               most wants (m024 asks for exactly that). So this project never
               deletes anything at all. The action exists in the manifest, is
               classified irreversible, and every attempt to use it is
               refused and logged.

Two gates, and both are on by default:

  --dry-run (the default) prints what it would do and writes nothing to outbox/
  --live                  asks y/n per action at the terminal
  --live --yes            approves non-interactively, recorded as such in the
                          log, for scripted runs

Where the line is drawn: only sends are gated one at a time, and only sends.
Archiving, labelling and deferring happen without asking. The reasoning, and
what it costs, is in CAPABILITIES.md under "Escalation line".
"""

import json
import os
from datetime import datetime

import config
import trace

_LOG = []


class Gate:
    def __init__(self, dry_run=True, auto_yes=False, cap="-"):
        self.dry_run = dry_run
        self.auto_yes = auto_yes
        self.cap = cap
        self.sent = 0
        self.would_send = 0
        self.refused = 0
        # This gate's own actions. _LOG below is process-wide and feeds
        # gate_log.json; this list is what pending() reports, so a dashboard
        # built later in the same process (R6 after R3 under --all) shows the
        # actions IT proposed rather than every action the process has taken.
        self._entries = []

    # -- the only way to cause an effect -----------------------------------
    def propose(self, action, target, why, payload=None, cap=None):
        """
        Ask for permission to do one thing. Returns a decision dict.

        Nothing else in this project writes to outbox/ or removes a message,
        so this function is the complete list of ways inboxHero can touch the
        outside world.
        """
        cap = cap or self.cap
        payload = payload or {}

        if action in config.REVERSIBLE_ACTIONS:
            return self._record(cap, action, target, why, "auto", "done",
                                note="reversible action, no approval needed")

        if action == "delete":
            self.refused += 1
            return self._record(cap, action, target, why, "policy", "refused",
                                note="this system never deletes mail; flagged and left in place")

        if action != "send":
            return self._record(cap, action, target, why, "policy", "refused",
                                note="unknown action")

        if self.dry_run:
            self.would_send += 1
            return self._record(cap, action, target, why, "dry-run", "would-do",
                                note="dry run: nothing written to outbox/",
                                preview=payload.get("body", "")[:400],
                                to=payload.get("to"))

        approved = self.auto_yes or self._ask(action, target, why, payload)
        if not approved:
            self.refused += 1
            return self._record(cap, action, target, why,
                                "human" if not self.auto_yes else "--yes",
                                "declined", to=payload.get("to"))

        path = self._write_outbox(target, payload)
        self.sent += 1
        return self._record(cap, action, target, why,
                            "--yes" if self.auto_yes else "human", "done",
                            file=path, to=payload.get("to"))

    # -- helpers -----------------------------------------------------------
    def _ask(self, action, target, why, payload):
        print()
        print("  APPROVAL NEEDED")
        print("    action    : %s" % action)
        print("    message   : %s" % target)
        print("    to        : %s" % payload.get("to", "?"))
        print("    because   : %s" % why)
        body = payload.get("body", "")
        for line in body.splitlines():
            print("    | %s" % line)
        try:
            answer = input("    send this? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        return answer in ("y", "yes")

    def _write_outbox(self, target, payload):
        os.makedirs(config.OUTBOX_DIR, exist_ok=True)
        path = os.path.join(config.OUTBOX_DIR, "%s.txt" % target)
        lines = [
            "To: %s" % payload.get("to", ""),
            "Cc: %s" % ", ".join(payload.get("cc", []) or []),
            "Subject: %s" % payload.get("subject", ""),
            "In-Reply-To: %s" % target,
            "Cited: %s" % ", ".join(payload.get("cited", []) or []),
            "Sent-At: %s" % datetime.now().isoformat(timespec="seconds"),
            "",
            payload.get("body", ""),
        ]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return os.path.relpath(path, os.path.dirname(config.OUTBOX_DIR))

    def _record(self, cap, action, target, why, decided_by, outcome, **extra):
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "cap": cap,
            "action": action,
            "message": target,
            "why_gated": why,
            "decided_by": decided_by,
            "outcome": outcome,
        }
        entry.update(extra)
        _LOG.append(entry)
        self._entries.append(entry)
        trace.event(cap, "gate", **{k: v for k, v in entry.items() if k != "cap"})
        return entry

    # -- reporting ---------------------------------------------------------
    def pending(self):
        """Everything THIS gate wanted to do but did not do alone (pane 1)."""
        return [e for e in self._entries
                if e["action"] in config.IRREVERSIBLE_ACTIONS
                and e["outcome"] in ("would-do", "declined", "refused")]

    def summary(self):
        return {"sent": self.sent, "would_send": self.would_send, "refused": self.refused,
                "mode": "dry-run" if self.dry_run else ("--yes" if self.auto_yes else "approval")}


def log():
    return list(_LOG)


def save_log():
    with open(config.GATE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(_LOG, f, indent=2, ensure_ascii=False)
    return config.GATE_LOG_FILE


def outbox_files():
    if not os.path.isdir(config.OUTBOX_DIR):
        return []
    return sorted(os.listdir(config.OUTBOX_DIR))


def clear_outbox():
    if not os.path.isdir(config.OUTBOX_DIR):
        return
    for name in os.listdir(config.OUTBOX_DIR):
        os.remove(os.path.join(config.OUTBOX_DIR, name))
