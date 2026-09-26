# Roll No: cert-aai-2026-06-0034
"""
demo.py - the single entry point every capability in the manifest runs through.

    python demo.py --cap R1              zero the inbox
    python demo.py --cap R2              grounded replies (add --msg m019 for one)
    python demo.py --cap R3              the gate, in dry run (add --live to be asked)
    python demo.py --cap R4              standing preference across a restart (run twice)
    python demo.py --cap R5              refuse the instructions hidden in the mail
    python demo.py --cap R6              build the three-pane dashboard
    python demo.py --cap X1              morning digest
    python demo.py --cap X2              what is waiting on a reply
    python demo.py --cap X3              a long thread reduced to its open question
    python demo.py --cap X4              negotiate a time that breaks a preference
    python demo.py --all                 every capability, in the order above
    python demo.py --check               self-test: data format and every citation
    python demo.py --reset               forget preferences, outbox, trace

Global flags:
    --dry-run   (default) nothing is written to outbox/
    --live      ask y/n before each send
    --yes       with --live, approve without asking (recorded as such)

Nothing here needs an API key. Set LLM_PROVIDER=gemini in .env to put the real
model on the triage and drafting paths; every command still works either way.
"""

import argparse
import os
import subprocess
import sys

import caps
import commitments
import config
import dashboard
import drafter
import gate as gate_module
import guard
import llm_provider
import mailstore
import memory_store
import pipeline
import trace

SHOWCASE = ["m008", "m019", "m012"]
ATTACKER_ADDRESSES = ["archive@mail-backup-service.info", "finance-sync@ext-audit.co"]


def header(cap, title):
    print()
    print("=" * 74)
    print("%s  %s" % (cap, title))
    print("=" * 74)


def make_gate(args, cap):
    return gate_module.Gate(dry_run=not args.live, auto_yes=args.yes, cap=cap)


# ---------------------------------------------------------------------------
# The required six
# ---------------------------------------------------------------------------
def cap_r1(args, provider):
    header("R1", "Zero the inbox - one disposition and a reason for every message")
    result = pipeline.run(provider, cap="R1")
    print()
    print("  %-5s %-9s %-16s %s" % ("id", "disposition", "decided by", "reason"))
    for d in result["decisions"]:
        print("  %-5s %-9s %-16s %s" % (d["id"], d["disposition"],
                                        d["decided_by"][:16], d["reason"][:44]))
    counts = pipeline.counts_by_disposition(result)
    print()
    print("  messages processed : %d" % result["messages"])
    for name in config.DISPOSITIONS:
        print("    %-9s %d" % (name, counts.get(name, 0)))
    print("  handled by rules, no model call : %d" % result["rule_handled"])
    print("  handled by the guard            : %d" % result["guard_handled"])
    print("  sent to the model (%s) : %d" % (provider.name, result["model_handled"]))
    print("  undecided: %d" % result["undecided"])
    print("  written: decisions.json")
    return result


def cap_r2(args, provider):
    header("R2", "Grounded replies - every draft cites what it read")
    targets = [args.msg] if args.msg else SHOWCASE
    for msg_id in targets:
        message = mailstore.by_id(msg_id)
        if message is None:
            print("  %s is not in this inbox." % msg_id)
            continue
        print()
        print("  %s  from %s  |  %s" % (message.id, message.sender, message["subject"]))
        result = drafter.prepare(message, provider, cap="R2")
        drafter.show(result)
    print()
    print("  Every id above was read before it was cited, and exists in inbox.json.")
    print("  m012 is the Part 3.4 case: nothing in the mailbox says what \"that thing\" is,")
    print("  so nothing is drafted.")


def cap_r3(args, provider):
    header("R3", "The gate - nothing irreversible happens on its own")
    gate = make_gate(args, "R3")
    result = pipeline.run(provider, cap="R3")
    drafts = []
    for decision in result["decisions"]:
        if decision["disposition"] != "reply":
            continue
        message = mailstore.by_id(decision["id"])
        draft = drafter.prepare(message, provider, cap="R3")
        if draft["status"] != "drafted":
            continue
        drafts.append(draft)
        gate.propose("send", draft["msg_id"], draft["why_gated"],
                     {"to": draft["to"], "cc": draft["cc"], "subject": draft["subject"],
                      "body": draft["body"], "cited": draft["cited"]}, cap="R3")

    # The action this system will not take even with permission. One of the
    # hostile messages asks to be deleted once it has been obeyed; we try it
    # on purpose so the refusal is on the record.
    destructive = [f for f in result["hostile"] if "destruction" in f["categories"]]
    if destructive:
        gate.propose("delete", destructive[0]["msg_id"],
                     "the message itself asks to be deleted after it is obeyed", cap="R3")

    print()
    print("  mode: %s" % gate.summary()["mode"])
    for entry in gate_module.log():
        if entry["action"] not in config.IRREVERSIBLE_ACTIONS:
            continue
        print("  %-6s %-7s %-10s %-32s %s" % (entry["message"], entry["action"],
                                              entry["outcome"], (entry.get("to") or "")[:32],
                                              entry["why_gated"][:44]))
    print()
    print("  drafts prepared      : %d" % len(drafts))
    print("  would send (dry run) : %d" % gate.summary()["would_send"])
    print("  actually sent        : %d" % gate.summary()["sent"])
    print("  refused by policy    : %d" % gate.summary()["refused"])
    written = [e["file"] for e in gate_module.log() if e.get("file")]
    print("  outbox/ writes this run: %d file(s): %s" % (len(written), written or "none"))
    print("  (outbox/ currently holds %d file(s) in total, from this and earlier --live runs)"
          % len(gate_module.outbox_files()))
    print("  gate log written     : %s" % os.path.basename(gate_module.save_log()))
    if not args.live:
        print()
        print("  Re-run with --live to be asked y/n per send, or --live --yes to approve")
        print("  without being asked (the log records which of the two happened).")
    return result


def cap_r4(args, provider):
    header("R4", "A standing instruction that survives a restart")
    existing = memory_store.all_prefs()
    if not existing:
        print()
        print("  FIRST RUN - prefs.json does not exist.")
        pipeline.run(provider, cap="R4", store_preferences=True)
        print()
        print("  Read the mailbox and found these standing instructions:")
        print(memory_store.describe())
        print()
        print("  Stored in prefs.json. Now run the SAME command again - a new process,")
        print("  nothing carried over in memory - and watch m043 be handled differently.")
        return None

    print()
    print("  SECOND RUN - prefs.json was already on disk before this process started:")
    print(memory_store.describe())
    print()
    message = mailstore.by_id("m043")
    when = commitments.parse_when(message["body"])
    allowed, why = memory_store.applies_to_time(when[0] if when else None)
    print("  m043 (%s): \"%s\"" % (message.sender, " ".join(message["body"].split())[:88]))
    print("  proposed time: %s" % (when[0].strftime("%a %d %b %H:%M") if when else "?"))
    if allowed:
        print("  -> accepted; no stored preference objects.")
    else:
        print("  -> REFUSED by a preference this process never saw stated: %s" % why)
        gate = make_gate(args, "R4")
        outcomes = caps.negotiate(provider, gate, None)
        for item in outcomes:
            print("  -> counter-proposal offering %s, held at the gate (%s)" %
                  (", ".join(item["alternatives"]), item["gate"]))
    print()
    print("  Also live from the same file: legal mail now picks up a Cc.")
    legal = mailstore.by_id("m018")
    print("  m018 (%s) -> Cc %s" % (legal.sender, memory_store.cc_for(legal) or "(none)"))
    print()
    print("  Run `python demo.py --reset` to clear prefs.json and see the first run again.")


def cap_r5(args, provider):
    header("R5", "The hostile inbox - found, refused, reported, left in place")
    result = pipeline.run(provider, cap="R5")
    print()
    print("  Instructions addressed to the assistant: %d" % len(result["hostile"]))
    for finding in result["hostile"]:
        message = mailstore.by_id(finding["msg_id"])
        print()
        print("  FLAGGED: %s  (from %s)" % (finding["msg_id"], message.sender))
        print("    it claims    : %s" % "; ".join(finding["addressed"]))
        print("    it wants     : %s" % "; ".join(finding["attempted"]))
        if finding["spoofs_owner"]:
            print("    note         : sent from the owner's own address. This system does")
            print("                   not treat a From header as authority, so it changes")
            print("                   nothing.")
        print("    we did       : refused, flagged, left in place. No send, no delete,")
        print("                   no preference stored.")
    print()
    print("  Social engineering aimed at the human: %d" % len(result["phishing"]))
    for fraud in result["phishing"]:
        print("  FLAGGED: %s - %s" % (fraud["msg_id"], fraud["summary"][:88]))

    print()
    print("  Checks:")
    files = gate_module.outbox_files()
    bad = []
    for name in files:
        with open(os.path.join(config.OUTBOX_DIR, name), encoding="utf-8") as f:
            text = f.read()
        bad += [a for a in ATTACKER_ADDRESSES if a in text]
    print("    outbox/ contains %d file(s); messages to an attacker address: %d"
          % (len(files), len(bad)))
    still_there = [f["msg_id"] for f in result["hostile"] if mailstore.exists(f["msg_id"])]
    print("    hostile messages still in the mailbox (not deleted): %s" % ", ".join(still_there))
    stored = [k for k in memory_store.all_prefs()]
    print("    preferences on disk: %s" % (", ".join(stored) or "(none)"))
    print("    m039 asked to be saved as a standing preference; it is not in that list.")
    return result


def cap_r6(args, provider):
    header("R6", "The dashboard - three panes, built from this run")
    gate = make_gate(args, "R6")
    result = pipeline.run(provider, cap="R6")
    drafts = []
    ungrounded = []
    for decision in result["decisions"]:
        if decision["disposition"] != "reply":
            continue
        draft = drafter.prepare(mailstore.by_id(decision["id"]), provider, cap="R6")
        if draft["status"] == "drafted":
            drafts.append(draft)
            gate.propose("send", draft["msg_id"], draft["why_gated"],
                         {"to": draft["to"], "cc": draft["cc"], "subject": draft["subject"],
                          "body": draft["body"], "cited": draft["cited"]}, cap="R6")
        else:
            ungrounded.append(draft)
    data = dashboard.build(result, gate, drafts, ungrounded)
    print()
    print(dashboard.to_console(data))
    print()
    print("  written: dashboard.json and dashboard.html (open the html in a browser)")
    gate_module.save_log()
    return result


# ---------------------------------------------------------------------------
# Part 8
# ---------------------------------------------------------------------------
def cap_x1(args, provider):
    header("X1", "Morning digest - what needs you, what can wait, what was handled")
    result = pipeline.run(provider, cap="X1")
    print()
    print(caps.digest(result))


def cap_x2(args, provider):
    header("X2", "Waiting on a reply - sent by you, answered by nobody")
    rows = caps.waiting(days_threshold=args.days)
    print()
    if not rows:
        print("  Nothing has been waiting longer than %d days." % args.days)
        return
    for row in rows:
        print("  %-5s waiting %d days   to %s" % (row["message_id"], row["days_waiting"], row["to"]))
        print("        %s" % row["subject"])
        print("        \"%s\"" % row["ask"])
    print()
    print("  %d message(s). Threads where somebody replied are not listed," % len(rows))
    print("  which is why m003 (answered by m005) does not appear here.")


def cap_x3(args, provider):
    header("X3", "A long thread, reduced to the question aimed at you")
    summary = caps.thread_question(args.thread, provider)
    if summary is None:
        print("  No thread called %s." % args.thread)
        return
    print()
    print("  thread       : %s  (%d messages)" % (summary["thread_id"], summary["count"]))
    print("  participants : %s" % ", ".join(summary["participants"]))
    if not summary["open_question"]:
        print("  nothing in this thread asks the owner for anything.")
        return
    print("  open question: \"%s\"" % summary["open_question"][:200])
    print("  asked by     : %s" % summary["asked_by"])
    print("  cited        : [%s]" % ", ".join(summary["cited"]))
    print("  the other %d messages are status, not asks: %s"
          % (len(summary["others"]), ", ".join(summary["others"])))


def cap_x4(args, provider):
    header("X4", "Conflict negotiator - a proposal that breaks a standing preference")
    if not memory_store.all_prefs():
        print()
        print("  prefs.json is empty, so there is nothing for a proposal to break.")
        print("  Reading the mailbox for standing instructions first (this is what")
        print("  `--cap R4` does on its first run):")
        pipeline.run(provider, cap="X4", store_preferences=True)
        print(memory_store.describe())
    gate = make_gate(args, "X4")
    outcomes = caps.negotiate(provider, gate, None)
    print()
    if not outcomes:
        print("  No proposed time collides with a stored preference.")
        return
    for item in outcomes:
        print("  %s - %s" % (item["message"], item["conflict"]))
        print("  alternatives offered: %s" % ", ".join(item["alternatives"]))
        print("  cited: [%s]" % ", ".join(item["cited"]))
        print("  ---")
        for line in item["draft"].splitlines():
            print("  | %s" % line)
        print("  ---")
        print("  gate: %s (nothing has been sent)" % item["gate"])
    gate_module.save_log()


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
def self_check():
    header("--check", "Data format and citation audit")
    ok, problems = mailstore.check_format()
    print()
    print("  inbox.json: %d messages, format %s" %
          (len(mailstore.all_messages()), "OK" if ok else "PROBLEMS"))
    for problem in problems:
        print("    ! %s" % problem)

    dated = commitments.extract()
    all_ids = set()
    for item in dated:
        all_ids.update(item["cited"])
    good, unknown = mailstore.cited_ids_are_real(sorted(all_ids))
    print("  commitments: %d, citing %d distinct message ids, all real: %s"
          % (len(dated), len(all_ids), "yes" if good else "NO %s" % unknown))
    multi = [c for c in dated if len(c["cited"]) > 1]
    print("  commitments built from more than one message: %d (%s)"
          % (len(multi), "; ".join("%s <- %s" % (c["title"][:28], ",".join(c["cited"]))
                                   for c in multi)))
    clashes = commitments.conflicts(dated)
    print("  conflicts surfaced: %d" % len(clashes))
    for clash in clashes:
        print("    %s: %s" % (clash["kind"], clash["detail"][:80]))

    found = [m.id for m in mailstore.all_messages()
             if (guard.scan(m) or {}).get("hostile")]
    print("  messages carrying an instruction for the assistant: %s" % ", ".join(found))
    print("  preferences currently on disk: %s" % (", ".join(memory_store.all_prefs()) or "(none)"))
    return ok and good


def reset():
    memory_store.clear()
    gate_module.clear_outbox()
    for path in (config.TRACE_FILE, config.DECISIONS_FILE, config.GATE_LOG_FILE,
                 config.DASHBOARD_JSON, config.DASHBOARD_HTML):
        if os.path.exists(path):
            os.remove(path)
    print("Cleared prefs.json, outbox/, trace.jsonl, decisions.json, gate_log.json, dashboard.*")


def run_all(args, provider):
    """Every capability in manifest order. R4 is run twice, as two processes."""
    for cap in ("R1", "R2", "R3"):
        CAPS[cap](args, provider)
    here = os.path.abspath(__file__)
    for _ in range(2):
        subprocess.call([sys.executable, here, "--cap", "R4"])
    for cap in ("R5", "R6", "X1", "X2", "X3", "X4"):
        CAPS[cap](args, provider)


CAPS = {"R1": cap_r1, "R2": cap_r2, "R3": cap_r3, "R4": cap_r4, "R5": cap_r5,
        "R6": cap_r6, "X1": cap_x1, "X2": cap_x2, "X3": cap_x3, "X4": cap_x4}


def main():
    parser = argparse.ArgumentParser(description="inboxHero")
    parser.add_argument("--cap", choices=sorted(CAPS))
    parser.add_argument("--all", action="store_true", help="run every capability in order")
    parser.add_argument("--check", action="store_true", help="self-test the data and citations")
    parser.add_argument("--reset", action="store_true", help="clear prefs, outbox, trace")
    parser.add_argument("--show-prefs", action="store_true")
    parser.add_argument("--msg", help="one message id, for --cap R2")
    parser.add_argument("--thread", default="t-launch", help="thread id, for --cap X3")
    parser.add_argument("--days", type=int, default=3, help="threshold for --cap X2")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="(default) propose sends but write nothing")
    parser.add_argument("--live", action="store_true",
                        help="actually write to outbox/, asking first")
    parser.add_argument("--yes", action="store_true",
                        help="with --live, approve every send without asking")
    args = parser.parse_args()

    if args.reset:
        reset()
        return
    if args.show_prefs:
        print(memory_store.describe())
        return
    if args.check:
        trace.start_run("check", sys.argv[1:])
        sys.exit(0 if self_check() else 1)

    if not args.cap and not args.all:
        parser.print_help()
        return

    cap = args.cap or "ALL"
    trace.start_run(cap, sys.argv[1:])
    provider = llm_provider.get_provider()
    print("provider: %s (%s)" % (provider.name, provider.model))
    if args.all:
        run_all(args, provider)
    else:
        CAPS[args.cap](args, provider)
    trace.end_run(cap)
    print()
    print("trace: %s" % os.path.basename(config.TRACE_FILE))


if __name__ == "__main__":
    main()
