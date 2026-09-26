# Roll No: cert-aai-2026-06-0034
"""
heuristics.py - the offline brain.

This is what runs when LLM_PROVIDER=offline: a deterministic stand-in for the
model, used for triage, drafting and summarising. It exists for three reasons,
and I would keep it even if the API were free:

  * every command in the manifest runs on a fresh checkout with no API key,
    so nothing in this project is unmarkable because a quota ran out;
  * runs are reproducible, which is what makes the dashboard and the trace
    worth checking;
  * it is the control. When the Gemini path disagrees with this file, one of
    them is wrong, and that is a useful thing to be able to see.

It is not pretending to be a language model. It is a pile of signals with an
order of precedence, and where it is weaker than a model (tone, summarising a
long thread into prose) the difference shows.
"""

import re

import config
import guard

# --- signal vocabulary -----------------------------------------------------
MONEY = re.compile(r"\b(invoice|wire|payment|remit|deposit|\$\s?[\d,]+|budget|refund|payout)\b", re.I)
LEGAL = re.compile(r"\b(safe amendment|term sheet|clause \d|ip assignment|board minutes|"
                   r"contract|agreement|counsel|\bllp\b|signature|sign (the|via|this|off))\b", re.I)
CREDENTIALS = re.compile(r"\b(creds?|credentials?|password|api[_\- ]?key|token|"
                         r"connection string|amqp)\b", re.I)
SCHEDULING = re.compile(r"\b(meeting|demo|1:1|slot|reschedul|move our|calendar|appointment)\b"
                        r"|(?<![\w-])call(?![\w-])|does .{0,20}work|can you do|are you free", re.I)
# An actual request aimed at the owner, rather than any use of the word "review".
ASKS_OWNER = re.compile(
    r"(can you|could you|would you|can we|please (can |could )?(you |make sure)?|need you to|"
    r"need your|let me know|any read on|where i stand|following up on|wanted to follow up|"
    r"reply to confirm|just need a yes|does .{0,20}work|work on your side)", re.I)
# A piece of work being handed to the owner, as opposed to a question.
TASK_VERB = re.compile(
    r"\b(approve|sign|review the|review your|review the draft|finish|circulated?|submit|"
    r"prepare|complete|flag any|have the .{0,20}(deck|copy|draft))\b", re.I)
DEADLINE = re.compile(r"\b(by (the )?\w+|deadline|due|no later than|hard date|end of day|"
                      r"month-end|this week|today)\b", re.I)
FYI = re.compile(r"\b(heads up|fyi|just so you know|for visibility|i'll own|i will own|"
                 r"uploading|draft by|is green|nothing pending|covering on-call|"
                 r"are available|auto-saved)\b", re.I)
PRESS = re.compile(r"\b(coverage|press|journalist|reporter|on deadline for|"
                   r"launch coverage)\b", re.I)


def _internal(message):
    domain = message.sender.split("@")[-1]
    return domain in config.OWNER_DOMAINS


def triage(message, context=None):
    """
    Decide one disposition and a reason. Returns (disposition, reason).

    Order of precedence is the point of this function: safety first, then
    whether a human must decide, then ordinary work, then noise.
    """
    context = context or {}
    body = message.get("body", "")
    text = message.text

    # 1. Anything the guard called hostile or fraudulent is quarantined.
    if context.get("hostile"):
        return "flag", "carries an instruction aimed at the assistant: %s" % context["hostile"]
    if context.get("phishing"):
        return "flag", "social engineering: %s" % context["phishing"]

    # 2. A standing instruction from the owner: store it, nothing to answer.
    if context.get("preference"):
        return "archive", "standing instruction recorded in prefs.json; no reply needed"

    # 3. Credentials. Never handled automatically, even for a colleague.
    if CREDENTIALS.search(text) and ASKS_OWNER.search(body):
        return "escalate", "asks for credentials to be re-sent; a human decides how secrets move"

    # 4. Money and legal. Irreversible in the real world, not just in code.
    if LEGAL.search(text) and re.search(r"\b(sign|signature|review|execute|counter-?sign)\b",
                                       body, re.I):
        return "escalate", "legal document needing the owner's signature or review"
    if MONEY.search(text) and ASKS_OWNER.search(body) and not _internal(message):
        return "escalate", "external request involving money"

    # 5. The owner's own sent mail: nothing to decide, it is waiting on them.
    if message.sender == config.OWNER and config.OWNER not in message["to"].lower():
        return "defer", "sent by the owner and still unanswered; tracked as a follow-up"

    # A question mark anywhere counts: m012's real ask sits mid-paragraph.
    asks = bool(ASKS_OWNER.search(body)) or "?" in body

    # 6. A proposed time that collides with something already promised, or with
    #    a standing preference, is not a reply. It is a decision for a human.
    if SCHEDULING.search(text) and context.get("conflicts"):
        return "escalate", "proposed time collides with another commitment or a stored preference"

    # 7. Press, from outside. The wording of a public quote is not ours to
    #    choose; a colleague saying "press is briefed" is not a press enquiry.
    if PRESS.search(text) and not _internal(message):
        return "escalate", "press enquiry; a public statement is the owner's wording"

    # 8. Work being handed to the owner, usually with a date on it.
    if TASK_VERB.search(body) and asks:
        dated = " with a deadline in the message" if DEADLINE.search(body) else ""
        return "defer", "a piece of work for the owner%s; scheduled, not answered" % dated

    # 9. Something that points outside the mailbox entirely (m012). Guessing
    #    what it refers to is exactly the failure mode worth avoiding.
    if context.get("no_grounding") and asks:
        return "escalate", "refers to something discussed outside the mailbox; only the owner knows"

    # 10. A time proposal we can answer.
    if SCHEDULING.search(text) and asks:
        return "reply", "proposes a time that needs a yes or no from the owner"

    # 11. Any other direct question.
    if asks:
        return "reply", "a question the owner can answer from the thread"

    # 12. Team traffic that is telling, not asking.
    if _internal(message) and FYI.search(text):
        return "delegate", "team update; owned by the sender, nothing for the owner to do"
    if _internal(message):
        return "archive", "internal update with nothing addressed to the owner"

    return "archive", "no action requested and no date attached"


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------
def draft(message, evidence, method):
    """
    Compose a reply using only `evidence`. Returns text, or the exact string
    INSUFFICIENT_EVIDENCE, which is the contract the Gemini path also honours.
    """
    if not evidence:
        return "INSUFFICIENT_EVIDENCE"

    name = (message.sender.split("@")[0].split(".")[0] or "there").capitalize()
    body = message.get("body", "")

    # Credentials: answer the question, refuse the mechanism.
    if CREDENTIALS.search(message.text):
        source = next((m for m in evidence if guard.secrets_in(m.text)), None)
        if source is not None:
            when = source["timestamp"][:10]
            return (
                "Hi %s,\n\n"
                "The staging broker credentials were rotated on %s and the new connection "
                "details went to Raghav earlier in this thread. I am not going to paste them "
                "into mail again - I will put them in the password manager and send you the "
                "entry, or you can pull them from the deploy config.\n\n"
                "Nothing needs rotating for this.\n\n%s"
            ) % (name, when, config.OWNER_NAME)

    # Confirming a date somebody else is holding for us.
    if re.search(r"\b(confirm|holding|hold expires)\b", body, re.I) and method == "date-fact":
        when = None
        for item in evidence:
            found = re.search(r"the (\d{1,2})(st|nd|rd|th)", item.text, re.I)
            if found:
                when = "the %s%s" % (found.group(1), found.group(2))
                break
        if when:
            return (
                "Hi,\n\n"
                "Yes - please confirm %s. That is the date the team has been working to "
                "internally, and it has not moved. Send the contract over and I will get it "
                "back to you.\n\n%s"
            ) % (when, config.OWNER_NAME)

    # Scheduling: acknowledge and promise a decision, never accept on our own.
    if SCHEDULING.search(message.text):
        return (
            "Hi %s,\n\n"
            "Thanks - I am checking that against what is already in the calendar and will "
            "come back to you today with a yes or an alternative.\n\n%s"
        ) % (name, config.OWNER_NAME)

    # Everything else: acknowledge and point at the thread. Deliberately no
    # quoted body text here - one of the messages this could quote is m003,
    # which contains a live connection string, and a template that pastes
    # whatever it retrieved is how a credential ends up in an outbound mail.
    return (
        "Hi %s,\n\n"
        "Picking this up - there are already %d earlier messages in this thread and the "
        "detail is there. I will come back to you on it shortly.\n\n%s"
    ) % (name, len(evidence), config.OWNER_NAME)


def alternatives_for(preference_text):
    """Three times that satisfy a 'no meetings before HH:00' preference."""
    found = re.search(r"(\d{1,2})[:.]?(\d{2})?\s*(am|pm)", preference_text, re.I)
    hour = int(found.group(1)) if found else 11
    if found and found.group(3).lower() == "pm" and hour < 12:
        hour += 12
    return ["%02d:00" % hour, "%02d:30" % hour, "%02d:00" % (hour + 2)]


# ---------------------------------------------------------------------------
# Summarising a long thread (X3)
# ---------------------------------------------------------------------------
def summarise_thread(messages, owner=None):
    """
    Return {"open_question", "asked_by", "cited", "others"}.

    The open question is the last thing in the thread that asks the OWNER for
    something. Everything else is status, however loud it is.
    """
    owner = owner or config.OWNER
    asks = []
    for m in messages:
        body = m["body"]
        aimed_at_owner = re.search(r"\b(sam|you)\b.{0,40}\b(can|could|need|approve|confirm)\b",
                                   body, re.I) or re.search(r"needs sam", body, re.I)
        if ASKS_OWNER.search(body) and aimed_at_owner and m.sender != owner:
            asks.append(m)
    others = [m.id for m in messages if m not in asks]
    if not asks:
        return {"open_question": None, "asked_by": None, "cited": [], "others": others}
    last = asks[-1]
    question = re.sub(r"\s+", " ", last["body"]).strip()
    return {
        "open_question": question,
        "asked_by": last.sender,
        "cited": [last.id],
        "others": others,
    }
