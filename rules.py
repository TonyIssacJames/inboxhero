# Roll No: cert-aai-2026-06-0034
"""
rules.py - the cheap path (Part 2.3).

Most of this mailbox is receipts, newsletters and "no action needed" notices.
Recognising one of those is a string match, not a reasoning problem, and paying
a model to read it costs money, adds latency and adds a chance of being wrong
about something that was never in doubt.

So every message meets these rules first. A rule that fires ends the decision:
the message gets a disposition and a reason and never reaches a model. Only
what survives goes to the model path, and demo.py reports the split.

Order matters. guard.py runs before this file, so a hostile message wearing a
newsletter costume (m024) is already flagged and never reaches these rules.
"""

import re

# Sender local-parts that only ever send machine mail.
_MACHINE_SENDERS = re.compile(
    r"^(no[-_.]?reply|noreply|do[-_.]?not[-_.]?reply|receipts?|invoice[+\w]*|billing|"
    r"notifications?|notify|alerts?|updates?|digest|newsletter|news|info|hello|orders?|"
    r"ship-confirm|no-reply-aws|status|insights|feedback|support|checkin|mailer|calendar-notification|security|appointments|success|help|hello)",
    re.I,
)

# Subjects that are self-evidently a record of something that already happened.
_RECEIPT_SUBJECT = re.compile(
    r"\b(receipt|invoice|statement|bill|your (order|trip|ride)|payment|payout|"
    r"renews?|renewal|usage|analytics|report|digest|summary|recommendations?|"
    r"verification code|screen time)\b",
    re.I,
)

# Body lines that say, in so many words, that nothing is required.
_NO_ACTION = re.compile(
    r"(no (further )?action (is )?needed|no action is required|for your records|"
    r"this is an automated receipt|if this was you, (ignore|no action)|"
    r"do not reply|nothing to do)",
    re.I,
)

# Notification traffic from tools, where the real work lives in the tool.
_TOOL_NOTIFICATION = re.compile(
    r"^(slack|notion|figma|linkedin|twitter|medium|producthunt|substack|coursera|"
    r"hackernewsletter|pragmaticengineer|grammarly|dropbox|todoist|zoom|apple|netflix|"
    r"spotify|github|google|intercom|mailchimp|postmark|cloudflare|datadog|sentry|"
    r"pagerduty|vercel|stripe|calendly|instacart|doordash|swiggy|uber|lyft|amazon|"
    r"bluebottlecoffee|namecheap|digitalocean|openai|1password|ramp|robinhood|chase|"
    r"united|paystream|zenboard|brightsmile-dental|email\.apple|members\.netflix|"
    r"accounts\.google|mail\.notion\.so|paperjet-monitoring)",
    re.I,
)


def _domain_root(address):
    domain = address.split("@")[-1].lower()
    parts = domain.split(".")
    if len(parts) > 2 and parts[-2] in ("co", "com"):   # e.g. foo.co.uk
        parts = parts[:-1]
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def classify(message):
    """
    Return (disposition, reason, rule_name) or None if a model is needed.
    """
    local = message.sender.split("@")[0]
    body = message.get("body", "")
    subject = message.get("subject", "")

    machine = bool(_MACHINE_SENDERS.match(local))
    tool_domain = bool(_TOOL_NOTIFICATION.match(_domain_root(message.sender)) or
                       _TOOL_NOTIFICATION.match(message.sender.split("@")[-1]))

    if machine and _NO_ACTION.search(body):
        return ("archive", "automated sender and the message says no action is needed",
                "machine_no_action")

    if machine and _RECEIPT_SUBJECT.search(subject):
        return ("archive", "receipt, invoice or usage report from an automated sender",
                "machine_receipt")

    if machine and tool_domain:
        return ("archive", "routine notification from a tool we already use",
                "tool_notification")

    if _NO_ACTION.search(body) and tool_domain:
        return ("archive", "vendor notice that states no action is needed",
                "vendor_no_action")

    if machine and re.search(r"\b(unsubscribe|read (more|in your browser)|open the app)\b", body, re.I):
        return ("archive", "bulk mail with an unsubscribe or read-online footer",
                "bulk_mail")

    return None


def would_handle(messages):
    """Convenience for reporting: how many messages never need a model."""
    return [m for m in messages if classify(m) is not None]
