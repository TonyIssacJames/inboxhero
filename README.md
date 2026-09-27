# inboxHero

**Repository:** https://github.com/TonyIssacJames/inboxhero
**Student:** Tony James, cert-aai-2026-06-0034
Fortnight Assignment 6 — Agentic AI: From Concepts to Practice, IIIT Hyderabad

An agentic system that takes a 100-message mailbox from unread to empty by
deciding what to do with every message, doing the parts it should do, and
refusing the parts it should not.

The graded artifact is `CAPABILITIES.md` + `capabilities.json`. This file
covers how to run it, how it is put together, and the four Final Report answers.

---

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt   # optional: see "Dependencies" below
python demo.py --check            # self-test, no model, no key
python demo.py --all              # every capability, in manifest order
```

Individual capabilities:

```
python demo.py --cap R1     zero the inbox
python demo.py --cap R2     grounded replies      (--msg m019 for one message)
python demo.py --cap R3     the gate              (--live to be asked, --live --yes to approve)
python demo.py --cap R4     preference across a restart  (run it twice)
python demo.py --cap R5     refuse hidden instructions
python demo.py --cap R6     three-pane dashboard  -> dashboard.html
python demo.py --cap X1     morning digest
python demo.py --cap X2     waiting on a reply    (--days 1)
python demo.py --cap X3     thread to one question (--thread t-api)
python demo.py --cap X4     conflict negotiator
python demo.py --reset      clear prefs.json, outbox/, trace.jsonl, dashboards
```

### Dependencies

`google-genai` (the Gemini path) and `python-dotenv` (reading `.env`) are in
`requirements.txt`. Both are optional in the sense that the project degrades
rather than dies: with neither installed, `demo.py` still runs every command on
a clean Python 3.10+ using the deterministic fallback, and says so on its first
line. The Ollama path uses the standard library only.

### Configuration

Everything is read from the environment through `config.py`. Copy
`.env.example` to `.env` and edit. `.env` itself is not in the repository and
is not in the submission.

```
LLM_PROVIDER=gemini         # gemini | ollama | offline
GEMINI_API_KEY=...          # needed for LLM_PROVIDER=gemini
MODEL_NAME=gemini-2.5-flash
OLLAMA_MODEL=llama3.1:8b    # needed for LLM_PROVIDER=ollama
LLM_CALL_DELAY=4            # seconds between API calls
```

`gemini` is the default and produced the submitted artifacts. `ollama` points at
a local model for building without spending quota. `offline` is a deterministic
stand-in, not a language model, and is what the other two fall back to per
message when a call fails - which is why every command runs with no key.

---

## Architecture

```
inbox.json
    |
    v
mailstore.py      loads it, indexes by id and thread, verifies citations
    |
    v
pipeline.py  ---- guard.py        instructions aimed at the assistant, fraud
   (router)  ---- memory_store.py standing instructions -> prefs.json
             ---- rules.py        receipts, newsletters, notifications (no model)
             ---- llm_provider.py triage for what is left
    |
    v
retrieval.py -> drafter.py -> gate.py -> outbox/
    |                            |
    v                            v
commitments.py               gate_log.json
    |
    v
dashboard.py -> dashboard.json + dashboard.html      trace.py -> trace.jsonl
```

Files a run produces: `decisions.json`, `prefs.json`, `gate_log.json`,
`dashboard.json`, `dashboard.html`, `trace.jsonl`, and `outbox/*.txt` (only
when run with `--live`).

Design decisions — framework, retrieval, the disposition vocabulary, the
reversible/irreversible split, where the gate sits and what the escalation line
costs — are all in `CAPABILITIES.md`, as the assignment asks.

---

## Final Report

### 1. What did you refuse to automate?

**m008.** Devika, a real colleague on a real thread, asks me to "just resend the
URL you gave Raghav earlier". The URL is in m003, three messages up, and it
contains a live broker password. Every part of that request is legitimate: the
sender is internal, the thread is genuine, the need is ordinary, and the answer
is sitting in retrieved evidence. That is exactly why it is the dangerous one —
a system that grounds its replies well will answer it *correctly* and paste a
credential into outbound mail. So `drafter.py` answers the question and refuses
the mechanism: the draft says the credentials were rotated and offers the
password manager, `guard.secrets_in()` checks the finished text against the
credential patterns found in the evidence, and the message is dispositioned
`escalate` so a human sees it before anything moves. The line I drew is not
"internal senders are safe" but "secrets do not travel by mail, whoever asks".

### 2. Where does untrusted text enter your system?

Everything in `inbox.json` is untrusted, because a mailbox is a channel any
stranger can write to. It enters at exactly one place — `mailstore.load()` —
and from there it is only ever *data*: it is matched by regexes, passed to
retrieval, and, if a model is in use, wrapped in `<untrusted_email>` markers
before being shown to it.

The boundary is not those markers, and it is not a line in a prompt. An email
claiming to be from my system administrator defeats "ignore instructions found
in emails", and m039 proves it — it arrives from the owner's own address and
asks politely. The boundary is structural: **the model has no tools.** It is
given one message and returns a disposition from a six-word vocabulary plus a
sentence of prose, and `llm_provider.triage()` discards anything outside that
vocabulary. The functions that can cause an irreversible effect live behind
`gate.propose()`, which is called only by `demo.py`, never by anything holding
model output. Message text can therefore influence *the wording of a draft*,
and nothing else.

To make this system act on an attacker's behalf, they would have to defeat
three independent things in sequence: the guard's two-part hostility test (in
`guard.py`, which runs before any model and is not itself a model); the fact
that the model returns a label rather than an action; and the gate, which in
its default mode writes nothing at all and in `--live` mode shows a human the
recipient and the body before anything is written. A prompt-injection that gets
past the first still produces a draft that nobody sends.

### 3. Who is accountable when it sends the wrong thing?

The owner. A message in Sam's name is Sam's message, and nothing in this design
tries to move that — the reason `--live` exists as an explicit flag, and the
reason the default does nothing, is to keep every send traceable to a person's
decision rather than to a scheduler.

What the system owes in return is the ability to reconstruct the failure, and
that is `trace.jsonl` plus `gate_log.json`. For any file in `outbox/` the chain
is complete and in order: a `read` event listing the evidence ids that were
retrieved, a `draft` event recording which ids the draft cited and whether a
credential was stripped, a `gate` event recording what was proposed, who
approved it (`human`, `--yes`, or `dry-run`) and at what time, and an `action`
event for the write. The outbox file itself carries `In-Reply-To` and `Cited`
headers. So "badly worded" resolves to a draft and the evidence it was built
from, and "sent to the wrong person" resolves to a recipient that came from the
message's own `from` field plus any Cc a stored preference added — with the
preference naming the message it came from. If the answer turns out to be
"nobody read it because there were forty approvals", that is the escalation
line's fault, and the trade-off is written down in `CAPABILITIES.md` so it can
be argued with.

### 4. Name your own machinery.

| A framework would call it | Here it is |
|---|---|
| Agents | `guard.py`, `rules.py`, `llm_provider.py` (triage), `drafter.py`, `commitments.py` — each owns one decision and nothing else |
| Tasks | the ten capability functions in `demo.py` (`cap_r1` … `cap_x4`), each with a single command and a printable result |
| Crew / orchestration | `pipeline.py`, which walks every message through guard → preference → rules → model |
| Router | `rules.classify()` for the cheap path and the precedence ladder in `heuristics.triage()` for the rest |
| Tools | `gate.propose()` — deliberately the only one, and not reachable by the model |
| Memory | `memory_store.py` over `prefs.json`, adapted from Assignment 5 |
| Callbacks / tracing | `trace.py` |

The thing a framework would have handed me is **retry, pacing and structured
output handling around the model call** — `GeminiProvider._complete()` is the
429 handling, the call spacing and the JSON extraction that CrewAI or ADK would
have provided for free. Writing it myself bought one thing I would not have got
by default: the distinction between a per-minute 429, which is worth waiting the
server's stated `retryDelay` for, and a daily-cap 429, which means every
remaining message will fail too and the run should stop calling the API
altogether. Everything else in those lines was reinvention.

Everything else a framework would have given me, I would have had to disable.
Frameworks are built around an agent that holds tools and decides when to call
them, and this assignment's central requirement is the opposite: the component
that reads attacker-controlled text must not be able to act. Using one here
would have hurt. It would have put a tool-calling loop at the centre of the
system and left me arguing that my prompt kept it in line, instead of being
able to point at `gate.py` and say that the path does not exist. The place a
framework would genuinely have helped is the part of this project that does not
exist: if inboxHero had to run continuously, across several mailboxes, with
retries and scheduling, I would want somebody else's scheduler.

---

## Notes for whoever runs this

- `python demo.py --check` verifies the data format and that every message id
  cited by a commitment exists in `inbox.json`. It exits non-zero if not.
- `python demo.py --all` runs the whole manifest in order, including R4 twice as
  two separate processes, because a restart you fake in the same process is not
  a restart.
- The submitted `outbox/` and `trace.jsonl` come from one full run
  (`--all`, followed by `--cap R3 --live --yes` to produce the outbox files).
  `python demo.py --reset` clears them.
