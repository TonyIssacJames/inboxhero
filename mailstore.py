# Roll No: cert-aai-2026-06-0034
"""
mailstore.py - the only thing in the project that reads inbox.json.

It gives the rest of the code three services:

  1. lookup by id / by thread, sorted by timestamp
  2. retrieval: thread_walk() first, keyword_search() as the cross-thread
     fallback (Part 3.3 - the choice is justified in CAPABILITIES.md)
  3. citation checking: cited_ids_are_real() and quote_for() exist so that a
     draft or a commitment can never claim a message id that is not in the
     store (Part 3.2 and Part 7).

Assumption about the data format, also stated in the manifest: every message
has id, thread_id, from, to, subject, timestamp, body, unread; ids are unique;
timestamps are naive ISO-8601 local strings. Verified by check_format().
"""

import json
import re
from datetime import datetime

import config

_CACHE = None


class Message(dict):
    """A message is just a dict; this adds the few accessors we keep repeating."""

    @property
    def id(self):
        return self["id"]

    @property
    def sender(self):
        return self["from"].strip().lower()

    @property
    def when(self):
        return datetime.fromisoformat(self["timestamp"])

    @property
    def text(self):
        """Subject and body together - what retrieval and the guard look at."""
        return "%s\n%s" % (self.get("subject", ""), self.get("body", ""))

    def one_line(self, width=58):
        subject = self.get("subject", "")
        if len(subject) > width:
            subject = subject[: width - 1] + "…"
        return "%-5s %-28s %s" % (self.id, self.sender[:28], subject)


def load(path=None):
    """Load and cache the mailbox."""
    global _CACHE
    if _CACHE is None or path:
        with open(path or config.INBOX_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _CACHE = [Message(m) for m in raw]
        _CACHE.sort(key=lambda m: m["timestamp"])
    return _CACHE


def check_format():
    """Validate the assumptions above. Returns (ok, list_of_problems)."""
    required = {"id", "thread_id", "from", "to", "subject", "timestamp", "body", "unread"}
    problems = []
    seen = set()
    for m in load():
        missing = required - set(m.keys())
        if missing:
            problems.append("%s missing %s" % (m.get("id", "?"), sorted(missing)))
        if m["id"] in seen:
            problems.append("duplicate id %s" % m["id"])
        seen.add(m["id"])
        try:
            datetime.fromisoformat(m["timestamp"])
        except ValueError:
            problems.append("%s has an unparseable timestamp" % m["id"])
    return (not problems), problems


def all_messages():
    return load()


def by_id(msg_id):
    for m in load():
        if m["id"] == msg_id:
            return m
    return None


def exists(msg_id):
    return by_id(msg_id) is not None


def thread(thread_id):
    """Every message in a thread, oldest first."""
    return [m for m in load() if m["thread_id"] == thread_id]


def inbound():
    """Messages addressed to the owner (excludes his own sent mail)."""
    return [m for m in load() if config.OWNER in m["to"].lower()]


def sent_by_owner():
    return [m for m in load() if m.sender == config.OWNER and config.OWNER not in m["to"].lower()]


# ---------------------------------------------------------------------------
# Retrieval (Part 3.3)
# ---------------------------------------------------------------------------
def thread_walk(msg_id, before_only=True):
    """
    Primary retrieval: every earlier message in the same thread.

    An inbox already carries its own graph in thread_id, so walking it is
    cheaper and more precise than embeddings and cannot return a message from
    an unrelated conversation.
    """
    msg = by_id(msg_id)
    if msg is None:
        return []
    siblings = thread(msg["thread_id"])
    if before_only:
        siblings = [m for m in siblings if m["timestamp"] < msg["timestamp"]]
    return siblings


_STOP = set("""a an the and or but if then than that this these those is are was were be been
being to of in on for with as at by from it its i you he she we they your my our their me him
her them do does did done can could should would will just so not no yes about into over out up
down re fwd please thanks thank hi hello sam""".split())


def _terms(text):
    words = re.findall(r"[a-z0-9][a-z0-9\-\.]{2,}", text.lower())
    return [w for w in words if w not in _STOP]


def keyword_search(query, exclude_ids=(), limit=5):
    """
    Fallback retrieval for facts that live in a different thread.

    Plain term overlap with a small stop-list. No embeddings: 100 messages of
    a few lines each do not need a vector store, and a scoring rule I can read
    is easier to defend than a similarity number I cannot.
    """
    wanted = set(_terms(query))
    if not wanted:
        return []
    scored = []
    for m in load():
        if m["id"] in exclude_ids:
            continue
        have = set(_terms(m.text))
        overlap = wanted & have
        if overlap:
            scored.append((len(overlap), m["timestamp"], m))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [m for _, _, m in scored[:limit]]


# ---------------------------------------------------------------------------
# Citation checking (Part 3.2, Part 7)
# ---------------------------------------------------------------------------
def cited_ids_are_real(ids):
    """Return (ok, unknown_ids). Nothing may cite a message that is not here."""
    unknown = [i for i in ids if not exists(i)]
    return (not unknown), unknown


def quote_for(msg_id, limit=240):
    """Short quotation used in dashboards so a citation can be eyeballed."""
    m = by_id(msg_id)
    if m is None:
        return ""
    body = " ".join(m["body"].split())
    return body[:limit] + ("…" if len(body) > limit else "")


def summary_counts():
    msgs = load()
    return {
        "total": len(msgs),
        "unread": sum(1 for m in msgs if m["unread"]),
        "threads": len(set(m["thread_id"] for m in msgs)),
        "inbound": len(inbound()),
    }
