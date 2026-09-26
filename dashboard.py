# Roll No: cert-aai-2026-06-0034
"""
dashboard.py - three panes, built from a run (Part 7).

Panes, in the order the assignment asks for them:

  1. Pending actions - everything inboxHero wanted to do but is not allowed to
     do alone: gated sends, and messages it escalated.
  2. Flagged        - everything it refused: the instructions aimed at the
     assistant, the fraud attempts, and anything it could not ground.
  3. Commitments    - the calendar, every entry citing the message ids it came
     from, with collisions called out rather than listed quietly.

It writes dashboard.json first and renders dashboard.html from that, so the
page cannot contain anything the data does not. Nothing here is hand-written:
re-running the command rebuilds both files.
"""

import html
import json
from datetime import datetime

import config
import mailstore


def build(result, gate, drafts, no_grounding):
    """Assemble the three panes from one completed run."""
    pending = []
    for entry in gate.pending():
        pending.append({
            "message": entry["message"],
            "from": _sender(entry["message"]),
            "action": entry["action"],
            "to": entry.get("to", ""),
            "why": entry["why_gated"],
            "outcome": entry["outcome"],
            "decided_by": entry["decided_by"],
        })
    for decision in result["decisions"]:
        if decision["disposition"] == "escalate":
            pending.append({
                "message": decision["id"],
                "from": decision["from"],
                "action": "escalate",
                "to": "",
                "why": decision["reason"],
                "outcome": "waiting for the owner",
                "decided_by": decision["decided_by"],
            })

    flagged = []
    for finding in result["hostile"]:
        flagged.append({
            "message": finding["msg_id"],
            "from": _sender(finding["msg_id"]),
            "kind": "instruction aimed at the assistant",
            "attempted": "; ".join(finding["attempted"]),
            "response": "refused, flagged, left in place; nothing written to outbox/",
            "note": ("claims to come from the owner's own address"
                     if finding["spoofs_owner"] else ""),
            "quote": mailstore.quote_for(finding["msg_id"], 200),
        })
    for fraud in result["phishing"]:
        flagged.append({
            "message": fraud["msg_id"],
            "from": _sender(fraud["msg_id"]),
            "kind": "social engineering aimed at the owner",
            "attempted": "; ".join(fraud["signals"]),
            "response": "flagged for the owner; no reply, no payment, no click",
            "note": "",
            "quote": mailstore.quote_for(fraud["msg_id"], 200),
        })
    for item in no_grounding:
        flagged.append({
            "message": item["msg_id"],
            "from": _sender(item["msg_id"]),
            "kind": "could not be grounded",
            "attempted": "draft a reply",
            "response": "no draft written: %s" % item["reason"],
            "note": "",
            "quote": mailstore.quote_for(item["msg_id"], 200),
        })

    import commitments as commitments_module
    dated = result["commitments"]
    clashes = commitments_module.conflicts(dated)
    calendar = []
    for item in dated:
        ok, unknown = mailstore.cited_ids_are_real(item["cited"])
        calendar.append({
            "when": item["when"].strftime("%a %d %b %Y %H:%M") if item["has_time"]
                    else item["when"].strftime("%a %d %b %Y"),
            "iso": item["when"].isoformat(),
            "what": item["title"],
            "cited": item["cited"],
            "citations_verified": ok,
            "multi_source": len(item["cited"]) > 1,
            "derived": item.get("derived_from", []),
            "source_text": item["when_text"],
        })

    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provider": result["provider"],
        "today": config.TODAY,
        "counts": {
            "messages": result["messages"],
            "rule_handled": result["rule_handled"],
            "model_handled": result["model_handled"],
            "guard_handled": result["guard_handled"],
            "undecided": result["undecided"],
            "drafts": len([d for d in drafts if d["status"] == "drafted"]),
        },
        "gate": gate.summary(),
        "pending_actions": pending,
        "flagged": flagged,
        "commitments": calendar,
        "conflicts": [{
            "kind": c["kind"],
            "when": c["when"].strftime("%a %d %b %Y %H:%M"),
            "what": c["what"],
            "cited": c["cited"],
            "detail": c["detail"],
        } for c in clashes],
    }

    with open(config.DASHBOARD_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    with open(config.DASHBOARD_HTML, "w", encoding="utf-8") as f:
        f.write(render(data))
    return data


def _sender(msg_id):
    message = mailstore.by_id(msg_id)
    return message.sender if message else "?"


# ---------------------------------------------------------------------------
# Console view
# ---------------------------------------------------------------------------
def to_console(data):
    lines = []
    lines.append("PANE 1 - PENDING ACTIONS (%d)" % len(data["pending_actions"]))
    lines.append("  %-6s %-9s %-28s %s" % ("msg", "action", "to", "why it needs a human"))
    for row in data["pending_actions"]:
        lines.append("  %-6s %-9s %-28s %s" % (row["message"], row["action"],
                                               (row["to"] or row["from"])[:28], row["why"][:64]))
    lines.append("")
    lines.append("PANE 2 - FLAGGED (%d)" % len(data["flagged"]))
    for row in data["flagged"]:
        lines.append("  %-6s %s" % (row["message"], row["kind"]))
        lines.append("         attempted: %s" % row["attempted"][:90])
        lines.append("         we did   : %s" % row["response"][:90])
        if row["note"]:
            lines.append("         note     : %s" % row["note"])
    lines.append("")
    lines.append("PANE 3 - COMMITMENTS (%d)" % len(data["commitments"]))
    for row in data["commitments"]:
        mark = " *" if row["multi_source"] else "  "
        lines.append("  %s %-22s %-58s %s" % (mark, row["when"], row["what"][:58],
                                              "[" + ", ".join(row["cited"]) + "]"))
    lines.append("  (* built from more than one message)")
    if data["conflicts"]:
        lines.append("")
        for clash in data["conflicts"]:
            lines.append("  CONFLICT (%s): %s  [%s]" %
                         (clash["kind"], clash["detail"], ", ".join(clash["cited"])))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML view
# ---------------------------------------------------------------------------
def render(data):
    def esc(value):
        return html.escape(str(value))

    def cites(ids):
        return " ".join('<code>%s</code>' % esc(i) for i in ids)

    pending_rows = "".join(
        "<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" %
        (esc(r["message"]), esc(r["action"]), esc(r["to"] or r["from"]),
         esc(r["why"]), esc(r["outcome"]))
        for r in data["pending_actions"]) or "<tr><td colspan=5>nothing pending</td></tr>"

    flagged_rows = "".join(
        "<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td>"
        "<td class='quote'>%s</td></tr>" %
        (esc(r["message"]), esc(r["kind"]), esc(r["attempted"]),
         esc(r["response"] + ((" (" + r["note"] + ")") if r["note"] else "")),
         esc(r["quote"]))
        for r in data["flagged"]) or "<tr><td colspan=5>nothing flagged</td></tr>"

    commit_rows = "".join(
        "<tr class='%s'><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" %
        ("multi" if r["multi_source"] else "",
         esc(r["when"]), esc(r["what"]), cites(r["cited"]),
         "yes" if r["citations_verified"] else "NO")
        for r in data["commitments"]) or "<tr><td colspan=4>no commitments found</td></tr>"

    conflict_rows = "".join(
        "<li><b>%s</b> - %s %s</li>" % (esc(c["kind"]), esc(c["detail"]), cites(c["cited"]))
        for c in data["conflicts"]) or "<li>no conflicts</li>"

    grid = _calendar_grid(data, esc)

    counts = data["counts"]
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>inboxHero dashboard</title>
<style>
 body {font: 14px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; color:#1c1c1c;}
 h1 {font-size: 20px; margin-bottom: 2px;}
 .sub {color:#666; font-size:12px; margin-bottom:18px;}
 h2 {font-size: 15px; margin-top: 28px; border-bottom: 2px solid #222; padding-bottom:4px;}
 table {border-collapse: collapse; width: 100%%; margin-top: 8px;}
 th, td {text-align: left; padding: 6px 8px; border-bottom: 1px solid #e3e3e3; vertical-align: top;}
 th {background:#f5f5f5; font-size:12px; text-transform:uppercase; letter-spacing:.04em;}
 code {background:#f0f0f0; padding:1px 4px; border-radius:3px; font-size:12px;}
 tr.multi td {background:#fffbe6;}
 .quote {color:#555; font-style: italic; font-size: 12px;}
 .stats span {display:inline-block; margin-right:18px; font-size:12px; color:#444;}
 ul {margin-top:8px;}
 table.cal {table-layout: fixed;}
 table.cal td {height: 74px; font-size: 11px; border: 1px solid #e3e3e3;}
 table.cal td.empty {background: #fafafa;}
 table.cal td.clash {background: #fdecec; border-color: #e6a3a3;}
 .dn {font-weight: 600; color: #777; margin-bottom: 3px;}
 .ev {margin-bottom: 3px; line-height: 1.25;}
 .ids {color: #999;}
</style></head><body>
<h1>inboxHero &mdash; run dashboard</h1>
<div class="sub">generated %s &middot; provider: %s &middot; &ldquo;today&rdquo; is %s &middot;
 gate mode: %s</div>
<div class="stats">
 <span><b>%d</b> messages</span>
 <span><b>%d</b> handled by rules (no model call)</span>
 <span><b>%d</b> by the model</span>
 <span><b>%d</b> by the guard</span>
 <span><b>%d</b> undecided</span>
 <span><b>%d</b> drafts</span>
</div>

<h2>1. Pending actions &mdash; wants to, may not alone</h2>
<table><tr><th>message</th><th>action</th><th>to</th><th>why it needs a human</th><th>outcome</th></tr>
%s</table>

<h2>2. Flagged &mdash; refused, and why</h2>
<table><tr><th>message</th><th>kind</th><th>what it attempted</th><th>what we did instead</th>
<th>quote</th></tr>
%s</table>

<h2>3. Commitments &mdash; every entry cites its source</h2>
%s
<table><tr><th>when</th><th>what</th><th>from messages</th><th>ids verified</th></tr>
%s</table>
<p><b>Conflicts</b></p><ul>%s</ul>
</body></html>
""" % (esc(data["generated_at"]), esc(data["provider"]), esc(data["today"]),
       esc(data["gate"]["mode"]),
       counts["messages"], counts["rule_handled"], counts["model_handled"],
       counts["guard_handled"], counts["undecided"], counts["drafts"],
       pending_rows, flagged_rows, grid, commit_rows, conflict_rows)


def _calendar_grid(data, esc):
    """A Monday-first month grid covering every commitment, conflicts in red."""
    from datetime import date, timedelta

    if not data["commitments"]:
        return ""
    days = {}
    for row in data["commitments"]:
        day = row["iso"][:10]
        days.setdefault(day, []).append(row)
    clash_days = set()
    for clash in data["conflicts"]:
        for row in data["commitments"]:
            if set(row["cited"]) & set(clash["cited"]) and row["iso"][11:16] != "00:00":
                clash_days.add(row["iso"][:10])

    first = date.fromisoformat(min(days))
    last = date.fromisoformat(max(days))
    start = first - timedelta(days=first.weekday())
    cells = []
    current = start
    while current <= last or current.weekday() != 0:
        key = current.isoformat()
        items = "".join(
            "<div class='ev'>%s%s <span class='ids'>%s</span></div>" %
            ((row["iso"][11:16] + " ") if row["iso"][11:16] != "00:00" else "",
             esc(row["what"][:38]), esc(",".join(row["cited"])))
            for row in days.get(key, []))
        cls = "day" + (" clash" if key in clash_days else "") + ("" if items else " empty")
        cells.append("<td class='%s'><div class='dn'>%s</div>%s</td>" %
                     (cls, current.strftime("%d %b"), items))
        current += timedelta(days=1)
    rows = "".join("<tr>%s</tr>" % "".join(cells[i:i + 7]) for i in range(0, len(cells), 7))
    head = "".join("<th>%s</th>" % d for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
    return ("<table class='cal'><tr>%s</tr>%s</table>"
            "<p class='sub'>red = a day with a conflict. Full list with sources below.</p>"
            % (head, rows))
