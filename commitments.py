# Roll No: cert-aai-2026-06-0034
"""
commitments.py - dates the mailbox has quietly put on the owner's calendar
(Part 7, pane 3).

A commitment is an obligation with a time attached, and it is only worth
anything if you can see where it came from, so every one of them carries the
message ids it was built from and those ids are checked against the store
before the dashboard will show them.

Three things this file does that a regex alone would not:

  * resolves relative dates across messages. m040 says the board deck is due
    "two days before the board review" and never says when that is; m038 says
    the review is the 18th. The deck lands on the 16th, cited to [m040, m038].
  * merges restatements. The launch date is stated in m026 and repeated in
    m036; that is one commitment citing both, not two.
  * surfaces collisions rather than listing them politely. Two things at the
    same hour, or one that breaks a stored preference, come out as conflicts.

"Today" is config.TODAY (2026-09-10, the morning after the last message in the
inbox), so weekday phrases like "Wednesday at 2:00pm" resolve the same way on
every run.
"""

import re
from datetime import datetime, timedelta

import config
import mailstore
import memory_store

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}
WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

EVENT_WORDS = re.compile(
    r"\b(review|meeting|call|demo|1:1|appointment|launch|deadline|due|deck|renew|renews|"
    r"expires|sign|signature|submit|flight|check-in|offsite|board|deposit|respond|slot)\b", re.I)

TIME_RE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)\b", re.I)

# A time somebody is asking us to accept, as opposed to one already fixed.
# Only a proposal can break a preference: the board review on the 18th is at
# 10:00 and the owner does not take meetings before 11:00, but that meeting is
# already in the diary and interrupting him about it would be noise.
PROPOSAL = re.compile(
    r"(does .{0,25}work|work on your side|can we (move|do)|could you do|can you do|"
    r"are you free|any chance|shall we)", re.I)


def today():
    return datetime.fromisoformat(config.TODAY)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------
def _time_in(text):
    found = TIME_RE.search(text)
    if not found:
        return None
    hour = int(found.group(1))
    minute = int(found.group(2) or 0)
    if found.group(3).lower() == "pm" and hour < 12:
        hour += 12
    if found.group(3).lower() == "am" and hour == 12:
        hour = 0
    return hour, minute


DEADLINE_CLAUSE = re.compile(r"\b(?:by|before|due)\s+((?:the\s+)?[^,.;!?]{2,40})", re.I)
MOVED_TO = re.compile(
    r"\bfrom\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.{0,20}?"
    r"\bto\s+((monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.{0,20})", re.I)


def parse_when(text, base=None):
    """
    Best single date/time in a piece of text, or None.

    Returns (datetime, has_time, phrase).

    Two bits of English that a plain date regex gets wrong and this does not:
      "move our 1:1 from Thursday to Wednesday at 2:00pm"  -> Wednesday
      "review the minutes ahead of the meeting on the 18th and flag
       corrections by Monday"                              -> Monday
    A deadline stated with "by" wins over a date mentioned in passing.
    """
    base = base or today()
    clock = _time_in(text)

    moved = MOVED_TO.search(text)
    if moved:
        return parse_when(moved.group(2), base)

    deadline = DEADLINE_CLAUSE.search(text)
    if deadline:
        inner = _scan(deadline.group(1), base, _time_in(deadline.group(0)) or clock)
        if inner:
            return inner

    return _scan(text, base, clock)


def _scan(text, base, clock):

    # "September 15", "Sep 22"
    found = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b",
                      text, re.I)
    if found:
        month = MONTHS[found.group(1)[:3].lower()]
        day = int(found.group(2))
        year = base.year + (1 if month < base.month else 0)
        return _build(year, month, day, clock, found.group(0))

    # "the 18th"
    found = re.search(r"\bthe (\d{1,2})(st|nd|rd|th)\b", text, re.I)
    if found:
        day = int(found.group(1))
        month, year = base.month, base.year
        if day < base.day:          # a day already past means next month
            month += 1
            if month > 12:
                month, year = 1, year + 1
        return _build(year, month, day, clock, found.group(0))

    # "month-end" / "before month-end"
    if re.search(r"month[- ]end|end of the month", text, re.I):
        month, year = base.month, base.year
        last = (datetime(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1))
        return _build(last.year, last.month, last.day, clock, "month-end")

    # "Wednesday at 2:00pm", "by Friday"
    found = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text, re.I)
    if found:
        target = WEEKDAYS[found.group(1).lower()]
        ahead = (target - base.weekday()) % 7
        ahead = ahead or 7
        when = base + timedelta(days=ahead)
        return _build(when.year, when.month, when.day, clock, found.group(0))

    return None


def _build(year, month, day, clock, phrase):
    hour, minute = clock if clock else (0, 0)
    try:
        return datetime(year, month, day, hour, minute), bool(clock), phrase
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def _subject(message):
    subject = re.sub(r"^(re|fwd):\s*", "", message.get("subject", ""), flags=re.I).strip()
    return subject or message.id


def _title(message, phrase=None):
    """
    A commitment in a nine-message thread called "Re: Launch week -- kickoff"
    needs a better label than its subject line, so the title is the sentence
    the date actually appeared in.
    """
    body = " ".join(message["body"].split())
    if phrase:
        for sentence in re.split(r"(?<=[.?!])\s+", body):
            if phrase.lower() in sentence.lower():
                return sentence[:90]
    return _subject(message)


def _topic_words(text):
    return set(w for w in re.findall(r"[a-z]{4,}", text.lower())
               if w not in ("this", "that", "with", "have", "from", "your", "will", "before"))


def extract():
    """
    Every dated obligation in the mailbox, as a list of dicts:

        {"title", "when", "when_text", "has_time", "cited": [ids], "quote"}
    """
    found = []
    relatives = []

    for message in mailstore.all_messages():
        text = message.text
        if not EVENT_WORDS.search(text):
            continue

        relative = re.search(
            r"\b(\w+)\s+days?\s+before\s+(?:the\s+)?([a-z\s]{3,30})", message["body"], re.I)
        if relative:
            relatives.append((message, relative))
            continue

        parsed = parse_when(text)
        if not parsed:
            continue
        when, has_time, phrase = parsed
        found.append({
            "title": _title(message, phrase),
            "subject": _subject(message),
            "when": when,
            "when_text": phrase,
            "has_time": has_time,
            "proposed": bool(PROPOSAL.search(message["body"])),
            "cited": [message.id],
            "quote": mailstore.quote_for(message.id, 140),
            "from": message.sender,
        })

    merged = _merge(found)

    # Second pass: dates expressed relative to another commitment.
    for message, relative in relatives:
        count = relative.group(1).lower()
        days = WORD_NUMBERS.get(count, int(count) if count.isdigit() else None)
        topic = _topic_words(relative.group(2))
        if days is None or not topic:
            continue
        candidates = sorted(
            ((len(topic & _topic_words(item["title"] + " " + item.get("subject", ""))), item)
             for item in merged),
            key=lambda pair: -pair[0])
        if not candidates or candidates[0][0] == 0:
            continue
        anchor = candidates[0][1]
        due = (anchor["when"] - timedelta(days=days)).replace(hour=0, minute=0)
        merged.append({
            "title": _title(message, None),
            "subject": _subject(message),
            "when": due,
            "when_text": "%s days before %s" % (days, anchor.get("subject", anchor["title"])),
            "has_time": False,
            "proposed": False,
            "cited": sorted(set([message.id] + anchor["cited"])),
            "quote": mailstore.quote_for(message.id, 140),
            "from": message.sender,
            "derived_from": anchor["cited"],
        })

    merged.sort(key=lambda c: c["when"])
    return [c for c in merged if _citations_ok(c)]


def _merge(items):
    """Two messages describing the same thing at the same time are one commitment."""
    out = []
    for item in items:
        for existing in out:
            same_time = existing["when"] == item["when"]
            same_topic = (_topic_words(existing.get("subject", "")) &
                          _topic_words(item.get("subject", "")))
            if same_time and same_topic:
                existing["cited"] = sorted(set(existing["cited"] + item["cited"]))
                break
        else:
            out.append(item)
    return out


def _citations_ok(commitment):
    ok, unknown = mailstore.cited_ids_are_real(commitment["cited"])
    if not ok:
        print("  ! dropping a commitment citing unknown message(s): %s" % unknown)
    return ok


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------
def conflicts(items):
    """
    Collisions worth interrupting somebody about:
      - two timed commitments at the same moment
      - a timed commitment that breaks a stored preference
    """
    out = []
    timed = [c for c in items if c["has_time"]]
    for i, first in enumerate(timed):
        for second in timed[i + 1:]:
            if first["when"] == second["when"]:
                out.append({
                    "kind": "double-booked",
                    "when": first["when"],
                    "what": [first["title"], second["title"]],
                    "cited": sorted(set(first["cited"] + second["cited"])),
                    "detail": "two commitments at %s" % first["when"].strftime("%a %d %b %H:%M"),
                })
    for item in [c for c in timed if c.get("proposed")]:
        allowed, why = memory_store.applies_to_time(item["when"])
        if not allowed:
            out.append({
                "kind": "breaks-preference",
                "when": item["when"],
                "what": [item["title"]],
                "cited": item["cited"],
                "detail": "%s at %s, but %s" % (
                    item["title"], item["when"].strftime("%a %d %b %H:%M"), why),
            })
    return out
