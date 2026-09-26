# Roll No: cert-aai-2026-06-0034
"""
caps.py - the four capabilities that are mine rather than the brief's (Part 8).

  X1  Morning digest        tier B   what needs you, what can wait, what was handled
  X2  Waiting on a reply    tier A   one lookup: sent mail nobody answered
  X3  Thread to one question tier B  collapse a long thread to the ask aimed at you
  X4  Conflict negotiator   tier C   spot a proposal that breaks a stored preference,
                                     offer three alternatives, hold the reply at the gate

Each runs on its own through demo.py --cap <id> and prints something a person
can check against inbox.json without reading any code.
"""

import commitments
import config
import heuristics
import mailstore
import memory_store
import trace

CAP = {"X1": "Morning digest", "X2": "Waiting on a reply",
       "X3": "Thread to one question", "X4": "Conflict negotiator"}


# ---------------------------------------------------------------------------
# X1 - morning digest (tier B)
# ---------------------------------------------------------------------------
def digest(result):
    """
    Three buckets built from the dispositions of a completed run, so the
    digest cannot disagree with the run that produced it.
    """
    needs_you = [d for d in result["decisions"] if d["disposition"] in ("escalate", "reply")]
    can_wait = [d for d in result["decisions"] if d["disposition"] == "defer"]
    handled = [d for d in result["decisions"] if d["disposition"] in ("archive", "delegate")]
    flagged = [d for d in result["decisions"] if d["disposition"] == "flag"]

    due_soon = []
    horizon = commitments.today()
    for item in result["commitments"]:
        days = (item["when"] - horizon).days
        if 0 <= days <= 7:
            due_soon.append((days, item))
    due_soon.sort(key=lambda pair: pair[0])

    trace.event("X1", "action", produced="digest", needs_you=len(needs_you),
                can_wait=len(can_wait), handled=len(handled))

    lines = []
    lines.append("NEEDS YOU (%d)" % len(needs_you))
    for d in needs_you:
        lines.append("  %-5s %-28s %s" % (d["id"], d["from"][:28], d["reason"][:58]))
    lines.append("")
    lines.append("CAN WAIT (%d)" % len(can_wait))
    for d in can_wait:
        lines.append("  %-5s %-28s %s" % (d["id"], d["from"][:28], d["subject"][:58]))
    lines.append("")
    lines.append("DUE IN THE NEXT SEVEN DAYS (%d)" % len(due_soon))
    for days, item in due_soon:
        lines.append("  in %-2d days  %-58s [%s]" %
                     (days, item["title"][:58], ", ".join(item["cited"])))
    lines.append("")
    lines.append("HANDLED WITHOUT YOU: %d archived or delegated, %d flagged and left in place"
                 % (len(handled), len(flagged)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# X2 - waiting on a reply (tier A)
# ---------------------------------------------------------------------------
def waiting(days_threshold=3):
    """
    One lookup, one output: messages the owner sent that nobody answered.

    A message counts as answered if anybody else wrote in the same thread
    after it. Deliberately no drafting and no model call - this is the cheap
    end of the system, and it is useful precisely because it is cheap.
    """
    today = commitments.today()
    rows = []
    for sent in mailstore.sent_by_owner():
        later = [m for m in mailstore.thread(sent["thread_id"])
                 if m["timestamp"] > sent["timestamp"] and m.sender != config.OWNER]
        if later:
            continue
        waited = (today - sent.when).days
        if waited < days_threshold:
            continue
        rows.append({
            "message_id": sent.id,
            "to": sent["to"],
            "subject": sent["subject"],
            "sent": sent["timestamp"][:10],
            "days_waiting": waited,
            "ask": " ".join(sent["body"].split())[:100],
        })
    rows.sort(key=lambda r: -r["days_waiting"])
    trace.event("X2", "action", produced="follow-ups", count=len(rows),
                ids=[r["message_id"] for r in rows])
    return rows


# ---------------------------------------------------------------------------
# X3 - a long thread reduced to its open question (tier B)
# ---------------------------------------------------------------------------
def thread_question(thread_id, provider):
    messages = mailstore.thread(thread_id)
    if not messages:
        return None
    summary = provider.summarise_thread(messages)
    ok, unknown = mailstore.cited_ids_are_real(summary["cited"])
    if not ok:
        summary["open_question"] = None
        summary["cited"] = []
    trace.event("X3", "action", thread=thread_id, messages=len(messages),
                cited=summary["cited"])
    summary["thread_id"] = thread_id
    summary["count"] = len(messages)
    summary["participants"] = sorted(set(m.sender for m in messages))
    return summary


# ---------------------------------------------------------------------------
# X4 - conflict negotiator (tier C)
# ---------------------------------------------------------------------------
def negotiate(provider, gate, result=None):
    """
    Find a proposed time that breaks a stored preference, work out three
    alternatives that do not, draft the reply, and hand it to the gate.

    This is the one capability that uses everything at once: memory from a
    previous run, a commitment extracted from another message, a draft, and a
    human in the loop. It does not send anything.
    """
    dated = (result or {}).get("commitments") or commitments.extract()
    clashes = [c for c in commitments.conflicts(dated) if c["kind"] == "breaks-preference"]
    outcomes = []

    pref = memory_store.get("no_meetings_before")
    for clash in clashes:
        source_id = clash["cited"][0]
        message = mailstore.by_id(source_id)
        if message is None:
            continue
        options = heuristics.alternatives_for(pref["value"] if pref else "11:00")
        # Cite both halves of the reasoning: the message proposing the time,
        # and the message the preference came from.
        cited = sorted(set(clash["cited"] + ([pref["source_msg"]] if pref else [])))
        day = clash["when"].strftime("%A %d %B")
        body = (
            "Hi %s,\n\n"
            "%s does not work on my side - I keep the morning before %s clear. "
            "Any of these on %s would: %s.\n\n"
            "Happy to hold whichever suits your partner.\n\n%s"
        ) % (message.sender.split("@")[0].split(".")[0].capitalize(),
             clash["when"].strftime("%H:%M"),
             pref["value"] if pref else "11:00",
             day, ", ".join(options), config.OWNER_NAME)

        decision = gate.propose(
            "send", message.id,
            "a counter-proposal in the owner's name, driven by a stored preference (%s)"
            % (pref["source_msg"] if pref else "none"),
            {"to": message["from"], "subject": "Re: " + message["subject"],
             "body": body, "cited": cited},
            cap="X4")
        outcomes.append({
            "message": message.id,
            "conflict": clash["detail"],
            "alternatives": options,
            "draft": body,
            "gate": decision["outcome"],
            "cited": cited,
        })
        trace.event("X4", "action", msg_id=message.id, alternatives=options,
                    gate=decision["outcome"], cited=cited)
    return outcomes
