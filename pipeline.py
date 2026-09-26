# Roll No: cert-aai-2026-06-0034
"""
pipeline.py - one pass over the mailbox (Part 2).

This is the router the Final Report calls "the thing a framework would have
given me". Every message goes through the same four stations, in this order,
and the order is the design:

    guard  ->  preference  ->  rules  ->  model

  guard       quarantines anything carrying an instruction for the assistant,
              or any attempt at fraud, before a model sees it and before a
              rule can archive it.
  preference  a standing instruction from the owner is stored, not answered.
  rules       receipts, newsletters and notifications get a disposition for
              free. These never reach a model.
  model       whatever is left. Under LLM_PROVIDER=offline that is
              heuristics.triage; under gemini it is one call per message,
              paced and 429-tolerant.

Every message comes out with exactly one disposition and a reason, and the
count of messages with no disposition is printed at the end of every run,
because Part 2 says that is the first thing checked.
"""

import json

import commitments
import config
import guard
import mailstore
import memory_store
import retrieval
import rules
import trace


def run(provider, cap="R1", store_preferences=False, verbose=False):
    """
    Triage the whole mailbox. Returns a result dict.

    store_preferences is off by default on purpose. Writing to long-term
    memory is itself an action with consequences - m039 is an attempt to do
    exactly that - so it happens in the one capability that owns it (R4, and
    X4 which depends on it) rather than as a side effect of every run. Every
    other run READS prefs.json and is changed by it, which is the point of
    Part 5.
    """
    messages = mailstore.all_messages()
    decisions = []
    hostile = []
    phishing = []
    preferences = []
    rule_handled = 0
    model_handled = 0

    # Conflicts are needed during triage (a proposed time that collides is not
    # a reply, it is an escalation), so they are computed once, up front.
    dated = commitments.extract()
    clash_ids = set()
    for clash in commitments.conflicts(dated):
        clash_ids.update(clash["cited"])

    for message in messages:
        context = {}

        finding = guard.scan(message)
        if finding and finding["hostile"]:
            context["hostile"] = finding["summary"]
            hostile.append(finding)
            trace.event(cap, "refusal", msg_id=message.id,
                        attempted=finding["attempted"],
                        categories=finding["categories"],
                        spoofs_owner=finding["spoofs_owner"],
                        action_taken="flagged, left in place, nothing sent")
        else:
            fraud = guard.phishing_scan(message)
            if fraud:
                context["phishing"] = fraud["summary"]
                phishing.append(fraud)
                trace.event(cap, "refusal", msg_id=message.id,
                            attempted=fraud["signals"],
                            action_taken="flagged as social engineering, no action taken")
            else:
                # Not hostile, not fraud. It may still be a standing
                # instruction - either one addressed to the assistant (m041)
                # or one a colleague simply states (m015, which never
                # mentions assistants at all).
                pref = memory_store.extract(message)
                if pref and (finding or pref["kind"] in ("scheduling", "cc")):
                    context["preference"] = pref
                    preferences.append((message.id, pref))
                    if store_preferences:
                        stored = memory_store.remember(
                            pref["key"], pref["value"], message.id,
                            message["body"], pref["kind"], pref.get("scope"))
                        trace.event(cap, "preference", msg_id=message.id,
                                    key=stored["key"], value=stored["value"])

        if message.id in clash_ids:
            context["conflicts"] = True

        source = "guard"
        if context.get("hostile") or context.get("phishing"):
            # Deliberately not a model decision. Whether to obey a stranger is
            # not a judgement call this system delegates.
            disposition, reason = _guard_disposition(context)
        elif context.get("preference"):
            disposition, reason = ("archive",
                                   "standing instruction recorded in prefs.json; no reply needed")
            source = "preference"
        else:
            ruled = rules.classify(message)
            if ruled:
                disposition, reason, rule_name = ruled
                source = "rule:" + rule_name
                rule_handled += 1
                trace.event(cap, "rule_hit", msg_id=message.id, rule=rule_name,
                            disposition=disposition)
            else:
                # Retrieval is free (no model call), so we can tell triage
                # whether this message can be grounded at all before it
                # decides to promise a reply.
                context["no_grounding"] = retrieval.refers_outside_inbox(message)
                disposition, reason = provider.triage(message, context)
                source = "model:" + provider.name
                model_handled += 1

        decisions.append({
            "id": message.id,
            "from": message.sender,
            "subject": message.get("subject", ""),
            "disposition": disposition,
            "reason": reason,
            "decided_by": source,
        })
        trace.event(cap, "decision", msg_id=message.id, disposition=disposition,
                    reason=reason, decided_by=source)
        if verbose:
            print("  %-5s %-9s %-14s %s" % (message.id, disposition,
                                            source.split(":")[0], reason[:60]))

    undecided = [d for d in decisions if d["disposition"] not in config.DISPOSITIONS]
    result = {
        "messages": len(messages),
        "decisions": decisions,
        "undecided": len(undecided),
        "rule_handled": rule_handled,
        "model_handled": model_handled,
        "guard_handled": len(hostile) + len(phishing) + len(preferences),
        "hostile": hostile,
        "phishing": phishing,
        "preferences": preferences,
        "commitments": dated,
        "provider": provider.name,
    }
    with open(config.DECISIONS_FILE, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in result.items()
                   if k in ("messages", "rule_handled", "model_handled", "undecided", "decisions")},
                  f, indent=2, ensure_ascii=False)
    return result


def _guard_disposition(context):
    if context.get("hostile"):
        return "flag", "carries an instruction aimed at the assistant: %s" % context["hostile"]
    return "flag", "social engineering: %s" % context["phishing"]


def counts_by_disposition(result):
    counts = {}
    for d in result["decisions"]:
        counts[d["disposition"]] = counts.get(d["disposition"], 0) + 1
    return counts
