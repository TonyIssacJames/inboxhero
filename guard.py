# Roll No: cert-aai-2026-06-0034
"""
guard.py - the trust boundary (Part 6).

Everything in the mailbox is text a stranger can write. This module is the one
place that decides what that text is allowed to be, and it does so before any
model sees it and without asking a model anything. Three jobs:

  1. scan()            - does this message contain an instruction aimed at the
                         assistant that asks for something it should not do?
  2. phishing_scan()   - is this message trying to defraud the human?
  3. wrap_untrusted()  - fence message text so that when it does reach a model
                         it arrives labelled as quoted data.

The part worth arguing about is how (1) decides.

A naive detector treats "this message talks to the assistant" as hostile. That
is wrong, and this inbox proves it: m041 is the owner leaving a note for his
assistant ("I do not take meetings before 11:00am"), and m039 is an attacker
doing exactly the same thing in the same tone, from the owner's own address,
to switch approvals off.

So hostility here needs TWO independent things in one message:

    (a) it addresses the assistant, or claims system/configuration authority
    (b) it asks for capability it should not get - exfiltration, deletion,
        silence, approval bypass, or a broadcast send

m041 has (a) and not (b), and becomes a preference. m039 has both, and is
refused. Sender address plays no part in either test: this mailbox cannot
authenticate anybody, so "it came from the owner" is not evidence of anything
and is recorded as an aggravating detail, never a permission.

Even if this scanner missed something, a hostile message still could not cause
an irreversible action, because nothing here can reach send/delete except
through gate.py. The scanner is the alarm; the gate is the lock.
"""

import re

import config

# --- (a) the message is talking to the machine -----------------------------
ADDRESSES_ASSISTANT = [
    (r"ignore (all|any) (previous|prior|earlier) instructions", "issues an override instruction"),
    (r"system notice for automated assistants?", "claims to be a system notice for assistants"),
    (r"automated[\-\s]agent directive", "claims to be an agent directive"),
    (r"assistant note\b", "leaves a note addressed to the assistant"),
    (r"if an ai (agent|assistant) is (processing|reading|managing)", "addresses an AI reading the mailbox"),
    (r"an assistant managing this (inbox|mailbox)", "addresses an assistant managing the mailbox"),
    (r"(note|instructions?) (for|to) the assistant", "addresses the assistant directly"),
    (r"assistant (configuration|settings) (update|change)", "claims to reconfigure the assistant"),
    (r"autonomous mode", "refers to the assistant's autonomy setting"),
    (r"\bfor automated assistants?\b", "addresses automated assistants"),
]

# --- (b) what it wants -----------------------------------------------------
ASKS_FOR = [
    (r"forward (the )?(full )?(contents? of )?(this|the) (mailbox|inbox)",
     "exfiltration", "forward the whole mailbox to an outside address"),
    (r"forward any (message|mail|email)", "exfiltration", "forward matching mail to an outside address"),
    (r"forward .{0,40}(to|@)\s*\S+@\S+", "exfiltration", "forward mail to an outside address"),
    (r"(delete|remove) this (message|email|mail)", "destruction", "delete the message afterwards"),
    (r"(do not|don't|never) (mention|surface|report|include) (it|this|that|the)",
     "suppression", "hide what it did from the user"),
    (r"no need to (mention|surface|report)", "suppression", "hide what it did from the user"),
    (r"must not surface this", "suppression", "hide the message from the user"),
    (r"(without|skip(ping)?) (asking for |the )?(approval|confirmation|the confirmation step)",
     "gate_bypass", "send or act without the approval gate"),
    (r"send all .{0,40}(automatically|without asking)", "gate_bypass", "send drafted mail automatically"),
    (r"reply with the single word", "broadcast", "send an unattended reply to many senders"),
    (r"to (the sender of )?every unread (message|mail)", "broadcast", "reply to every unread sender"),
    (r"save this as a standing preference", "persistence", "write itself into long-term memory"),
]

# --- phishing / social engineering (aimed at the human, not the machine) ---
PAYMENT_REDIRECT = [
    r"updated remittance", r"remit the outstanding", r"banking partner has changed",
    r"disregard the account on file", r"\brouting\b", r"wire \$?[\d,]+", r"can you wire",
    r"new account below",
]
CREDENTIAL_HARVEST = [
    r"re-?verify your credentials", r"password .{0,20}expires?", r"verify .{0,20}(account|credentials)",
    r"accounts? that are not verified", r"will be suspended", r"http://[^\s]+(login|verify)",
]
URGENCY = [
    r"\burgent\b", r"immediately", r"within \d+ hours", r"before end of day", r"expires in",
    r"action required", r"same-day", r"today or we lose",
]
SECRECY = [
    r"keep this between us", r"don'?t loop in", r"do not loop in", r"confidential",
    r"can'?t get on a call",
]

# --- secrets that must never leave in a draft ------------------------------
SECRET_PATTERNS = [
    (r"amqps?://[^\s]+:[^\s]+@[^\s]+", "message-queue connection string with a password"),
    (r"postgres(ql)?://[^\s]+:[^\s]+@[^\s]+", "database connection string with a password"),
    (r"\b(api[_\- ]?key|secret|token)\b\s*[:=]\s*\S+", "API key or token"),
    (r"\b\d{9,}\b", "bank account or routing number"),
]


def _hits(patterns, text):
    found = []
    for item in patterns:
        pattern = item[0] if isinstance(item, tuple) else item
        if re.search(pattern, text, re.I):
            found.append(item)
    return found


def scan(message):
    """
    Look for an instruction addressed to the assistant.

    Returns None for an ordinary message, otherwise a finding:
        {"msg_id", "hostile", "addressed": [...], "attempted": [...],
         "categories": [...], "spoofs_owner": bool, "summary": str}
    """
    text = message.text
    addressed = [reason for pattern, reason in ADDRESSES_ASSISTANT
                 if re.search(pattern, text, re.I)]
    asks = [(cat, what) for pattern, cat, what in ASKS_FOR
            if re.search(pattern, text, re.I)]

    if not addressed:
        return None

    spoofs_owner = message.sender == config.OWNER

    if not asks:
        # Talks to the assistant but wants nothing it should not have: this is
        # a standing instruction from the owner, not an attack. See m041.
        return {
            "msg_id": message.id,
            "hostile": False,
            "addressed": addressed,
            "attempted": [],
            "categories": [],
            "spoofs_owner": spoofs_owner,
            "summary": "addresses the assistant but asks only for ordinary handling",
        }

    categories = sorted(set(c for c, _ in asks))
    attempted = [w for _, w in asks]
    return {
        "msg_id": message.id,
        "hostile": True,
        "addressed": addressed,
        "attempted": attempted,
        "categories": categories,
        "spoofs_owner": spoofs_owner,
        "summary": "; ".join(attempted),
    }


def phishing_scan(message):
    """Fraud aimed at the human. Two independent signals are required."""
    text = message.text
    domain = message.sender.split("@")[-1]
    signals = []

    for owner_domain in config.OWNER_DOMAINS:
        base = owner_domain.split(".")[0]
        if domain != owner_domain and base in domain:
            signals.append("lookalike sender domain %s (ours is %s)" % (domain, owner_domain))
            break

    if _hits(PAYMENT_REDIRECT, text):
        signals.append("asks for money to move to a new destination")
    if _hits(CREDENTIAL_HARVEST, text):
        signals.append("asks for credentials or sends to a verification page")
    if _hits(URGENCY, text):
        signals.append("manufactured urgency")
    if _hits(SECRECY, text):
        signals.append("asks for secrecy or to bypass a colleague")

    if len(signals) < 2:
        return None
    return {"msg_id": message.id, "signals": signals,
            "summary": "; ".join(signals)}


def secrets_in(text):
    """Credentials found in text, as a list of human-readable descriptions."""
    return [what for pattern, what in SECRET_PATTERNS if re.search(pattern, text, re.I)]


def redact(text):
    """Replace anything that looks like a credential. Used before a draft goes out."""
    out = text
    for pattern, _ in SECRET_PATTERNS:
        out = re.sub(pattern, "[REDACTED]", out, flags=re.I)
    return out


def wrap_untrusted(message):
    """
    Fence a message before it is shown to a model.

    The markers are not the defence - a determined attacker can write the
    closing marker themselves. The defence is that whatever the model returns
    is read as data by demo.py, and the only code that can send or delete sits
    behind gate.py. The markers exist so the model has no excuse.
    """
    return (
        "<untrusted_email id=\"%s\">\n"
        "from: %s\n"
        "date: %s\n"
        "subject: %s\n"
        "body:\n%s\n"
        "</untrusted_email>"
    ) % (message.id, message["from"], message["timestamp"],
         message.get("subject", ""), message.get("body", ""))
