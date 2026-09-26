# Roll No: cert-aai-2026-06-0034
"""
drafter.py - writing a reply that can be checked (Part 3).

The rule this file enforces is that a draft may only contain things the
mailbox can be shown to say. Practically that means four checks, in order,
and any one of them can stop a draft:

  1. grounding  - retrieval.gather() must return at least one earlier message.
                  Nothing retrieved, nothing drafted; we say so instead.
  2. citation   - every message id in the finished text must be an id we
                  actually read, and must exist in the store. A model that
                  invents "m031" (a plausible id that is not in this inbox)
                  gets its draft rejected here, not printed.
  3. secrets    - if the draft repeats a credential found in the evidence, it
                  is redacted and the reply is marked for approval. m008 asks
                  for the staging queue password that m003 contains; answering
                  that helpfully is exactly the failure worth designing out.
  4. gate       - the finished draft is still only a draft. Sending is
                  gate.py's decision, never this file's.
"""

import re

import guard
import mailstore
import memory_store
import retrieval
import trace

ID_IN_TEXT = re.compile(r"\bm\d{3}\b")


def prepare(message, provider, cap="R2"):
    """
    Build a reply for one message. Returns a dict; status is one of
    "drafted", "no_grounding".
    """
    evidence, method = retrieval.gather(message)
    trace.event(cap, "read", msg_id=message.id,
                evidence=[m.id for m in evidence], method=method)

    if not evidence:
        reason = ("the message points at something discussed outside the mailbox"
                  if retrieval.refers_outside_inbox(message)
                  else "no earlier message in the mailbox answers this")
        trace.event(cap, "no_grounding", msg_id=message.id, reason=reason)
        return {"status": "no_grounding", "msg_id": message.id, "reason": reason,
                "method": method, "cited": []}

    text = provider.draft(message, evidence, method)
    if not text or text.strip() == "INSUFFICIENT_EVIDENCE":
        reason = "the retrieved messages do not contain an answer"
        trace.event(cap, "no_grounding", msg_id=message.id, reason=reason)
        return {"status": "no_grounding", "msg_id": message.id, "reason": reason,
                "method": method, "cited": []}

    read_ids = [m.id for m in evidence]
    # Any id the draft mentions must be one we read and one that exists.
    mentioned = set(ID_IN_TEXT.findall(text))
    invented = [i for i in mentioned if i not in read_ids or not mailstore.exists(i)]
    if invented:
        trace.event(cap, "no_grounding", msg_id=message.id,
                    reason="draft cited ids it never read: %s" % invented)
        return {"status": "no_grounding", "msg_id": message.id,
                "reason": "draft cited message ids it never read (%s); discarded" % ", ".join(invented),
                "method": method, "cited": []}

    # What the evidence contained, and whether any of it survived into the
    # draft. The second list should always be empty; R2 prints it either way.
    secrets_available = sorted(set(sum((guard.secrets_in(m.text) for m in evidence), [])))
    leaked = guard.secrets_in(text)
    if leaked:
        text = guard.redact(text)

    cc = memory_store.cc_for(message)
    subject = message.get("subject", "")
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject

    why = "reply to %s leaves the system and cannot be unsent" % message.sender
    if leaked:
        why = "draft touched a credential from %s; redacted and held for a human" % read_ids[0]
    elif cc:
        why += "; a standing preference adds %s to Cc" % ", ".join(cc)

    result = {
        "status": "drafted",
        "msg_id": message.id,
        "to": message["from"],
        "cc": cc,
        "subject": subject,
        "body": text,
        "cited": read_ids,
        "method": method,
        "redacted": bool(leaked),
        "secrets_in_evidence": secrets_available,
        "why_gated": why,
    }
    trace.event(cap, "draft", msg_id=message.id, cited=read_ids, method=method,
                redacted=bool(leaked), chars=len(text))
    return result


def show(result):
    """Print a draft the way the demos print it."""
    if result["status"] != "drafted":
        print("  %s: NO DRAFT - %s" % (result["msg_id"], result["reason"]))
        print("  cited: []")
        return
    print("  to      : %s" % result["to"])
    if result["cc"]:
        print("  cc      : %s   (standing preference)" % ", ".join(result["cc"]))
    print("  subject : %s" % result["subject"])
    print("  cited   : [%s]   (retrieval: %s)" % (", ".join(result["cited"]), result["method"]))
    for secret in result.get("secrets_in_evidence", []):
        state = "REDACTED from the draft" if result["redacted"] else "not repeated in the draft"
        print("  secret  : evidence contains a %s - %s" % (secret, state))
    print("  ---")
    for line in result["body"].splitlines():
        print("  | %s" % line)
    print("  ---")
