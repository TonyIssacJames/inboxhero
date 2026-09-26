# Roll No: cert-aai-2026-06-0034
"""
retrieval.py - finding the earlier message that answers this one (Part 3).

Three strategies, tried in this order, and the one that was used is recorded
with the draft so the manifest claim can be checked:

  1. thread-walk   - earlier messages in the same thread. An inbox already
                     stores its own conversation graph in thread_id, so this
                     is exact and free. It answers m008 (the credentials
                     question) from m003.
  2. date-fact     - when a message asks us to confirm "the date you agreed"
                     without saying what it is, look for messages that carry
                     BOTH a date expression and a topic word. This answers
                     m019 (the venue) from m026 and m036, which live in a
                     different thread.
  3. keyword       - plain term overlap, for anything else that needs a
                     cross-thread fact.

No embeddings. A hundred short messages do not need a vector store, and a
score I can read beats a cosine distance I cannot when I have to defend a
citation. Cost matters too: retrieval here makes zero model calls.
"""

import re

import mailstore

DATE_EXPRESSION = re.compile(
    r"\b(the \d{1,2}(st|nd|rd|th)|"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}|"
    r"\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*|"
    r"(mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"month-end|end of (the )?(week|month))\b",
    re.I,
)

# Words that mean "the thing we both know about" - they point outside the
# mailbox, at a conversation that happened somewhere else.
DEIXIS = re.compile(
    r"\b(that|the) (thing|stuff|matter|issue|item)\b|"
    r"\bwe (talked|spoke) about\b|\bas discussed\b|\byou know the one\b",
    re.I,
)

TOPIC_STOP = set("""the and for you your our with that this from have has can could
please thanks just need date time confirm reply send back about""".split())


def topic_terms(message):
    """Content words of a message, used to match it against others."""
    words = re.findall(r"[a-z][a-z\-]{3,}", message.text.lower())
    return [w for w in words if w not in TOPIC_STOP]


def thread_walk(message):
    return mailstore.thread_walk(message.id)


def date_fact(message, limit=6):
    """
    Messages that carry a date AND a topic word from this message, reduced to
    the ones that agree with each other.

    m019 asks us to confirm "the date you locked in with your team" and never
    says what it is. Six messages in the launch thread mention both the launch
    and a date, and they do not all mean the same date: m028 says a draft is
    due Wednesday, m030 says pricing copy by the 12th, m026 and m036 both say
    the launch itself is the 20th. Citing all six would be citing noise, so we
    keep the date the evidence agrees on and cite only the messages that say
    it - which is how this ends up as a two-source answer.
    """
    import commitments  # local import: commitments imports the store, not this

    wanted = set(topic_terms(message))
    hits = []
    for other in mailstore.all_messages():
        if other.id == message.id or other["thread_id"] == message["thread_id"]:
            continue
        if not DATE_EXPRESSION.search(other.text):
            continue
        overlap = wanted & set(topic_terms(other))
        if overlap:
            hits.append((len(overlap), other["timestamp"], other))
    hits.sort(key=lambda t: (-t[0], t[1]))
    hits = hits[:limit]

    groups = {}
    for score, stamp, other in hits:
        parsed = commitments.parse_when(other.text)
        if not parsed:
            continue
        groups.setdefault(parsed[0], []).append((score, other))
    if not groups:
        return [m for _, _, m in hits[:2]]

    best = max(groups.values(), key=lambda g: (len(g), sum(s for s, _ in g)))
    return sorted((m for _, m in best), key=lambda m: m["timestamp"])


# Terms this common carry no information about which message answers which.
_DF = None


def _doc_freq():
    global _DF
    if _DF is None:
        _DF = {}
        for m in mailstore.all_messages():
            for term in set(topic_terms(m)):
                _DF[term] = _DF.get(term, 0) + 1
    return _DF


def rare_terms(message, max_df=6):
    """Content words that only a handful of messages use."""
    df = _doc_freq()
    return set(t for t in topic_terms(message) if df.get(t, 0) <= max_df)


def keyword(message, limit=3):
    return mailstore.keyword_search(message.text, exclude_ids=[message.id], limit=limit)


def refers_outside_inbox(message):
    """
    True when the message points at something that is not in the mailbox at
    all: "that thing we talked about", with no earlier message in its thread
    to resolve it. Part 3.4 says the honest answer here is to draft nothing.
    """
    return bool(DEIXIS.search(message["body"])) and not thread_walk(message)


def gather(message):
    """
    Returns (evidence, method). evidence is a list of Messages, oldest first.
    An empty list means nothing in the mailbox grounds a reply.
    """
    walked = thread_walk(message)
    if walked:
        return walked, "thread-walk"

    if re.search(r"\b(confirm|the date you|date we|as agreed|locked in)\b",
                 message["body"], re.I):
        facts = date_fact(message)
        if facts:
            return sorted(facts, key=lambda m: m["timestamp"]), "date-fact"

    if refers_outside_inbox(message):
        return [], "none"

    hits = keyword(message)
    # A weak overlap is not grounding. Two messages both saying "PaperJet" and
    # "enjoyed" are not about the same thing, so only rare terms count, and
    # three of them are needed before a draft may lean on the match.
    mine = rare_terms(message)
    strong = [m for m in hits if len(mine & rare_terms(m)) >= 3]
    if strong:
        return sorted(strong, key=lambda m: m["timestamp"]), "keyword"
    return [], "none"
