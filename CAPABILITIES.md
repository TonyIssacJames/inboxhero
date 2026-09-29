# CAPABILITIES.md — inboxHero

**Student:** Tony James, cert-aai-2026-06-0034
**Repository:** https://github.com/TonyIssacJames/inboxhero

Run everything through one entry point:

```
python demo.py --cap R1        # one capability
python demo.py --all           # all of them, in the order below
python demo.py --check         # self-test: data format and every citation
python demo.py --reset         # clear prefs.json, outbox/, trace.jsonl
```

`LLM_PROVIDER` defaults to `gemini` (gemini-2.5-flash, key in `.env`), which is
what the submitted run was produced with. `ollama` runs a local model instead.
`offline` is a deterministic stand-in - not a language model - and it is what
both providers fall back to when a call fails, so every command below still runs
on a machine with no key. The first line of output names the provider in use.

---

## The system, in one paragraph

A single Python pipeline, no framework. Every message passes four stations in a
fixed order — **guard → preference → rules → model**. The guard quarantines
anything carrying an instruction aimed at the assistant, or aimed at defrauding
the owner, before a model sees it. A standing instruction from the owner is
stored rather than answered. Receipts, newsletters and notifications are settled
by string matching: 59 of the 100 messages never reach a model. The remaining 32
are triaged, and the ones worth answering go through retrieve → draft → verify
citations → gate. State that must outlive a run — preferences, the action log,
the trace — is kept in small JSON files beside the code.

## Design choices you were asked to state

- **Framework: none.** The work is a linear pipeline with one branch
  (rule path vs model path) and one strict ordering constraint (the guard runs
  first). A crew or a graph would have added a scheduler I do not need and
  obscured the one property this assignment is actually about: that the model
  cannot reach a tool. See Final Report Q4 in `README.md`.
- **Model: gemini-2.5-flash, with two fallbacks.** One call per model-path
  message - 32 of the 100, since rules and the guard settle the rest. A local
  `llama3.1:8b` over Ollama is the build-time alternative, so iterating costs no
  free-tier quota. `heuristics.py` is the last resort: a deterministic
  stand-in, and explicitly *not* a language model. It exists so a marker with no
  key can still run every command here, and so there is a control to compare the
  model against. When it is in use the first line of output says so.
- **Rate limits.** Pacing is `LLM_CALL_DELAY` seconds (default 4, inside the
  usual fifteen-per-minute free tier), measured from the end of the previous
  call. On a 429 the wait is the server's own `retryDelay` or `Retry-After`
  when it sends one, capped at 90s, and a doubling backoff from 5s when it does
  not - guessing 5s against a server asking for 47 just burns both retries. A
  429 that names a *daily* cap is treated differently from a per-minute one:
  waiting cannot help, so the provider says so once, stops calling the API for
  the rest of the run, and lets the offline path answer the remaining messages.
  `is_daily_cap()` matches only wording that names a day, because
  "quota exceeded" appears in the per-minute error too.
- **No batching, on purpose.** The brief suggests considering it, and it would
  cut 32 calls to about 3. Each call here carries exactly one untrusted email,
  so m024's "ignore all previous instructions" sits alone in its own context
  and cannot colour how nine other messages in the same batch are classified.
  The 59 messages the rules handle already bank a larger saving than batching
  would have.
- **Retrieval: thread-walk, then a date-fact lookup, then keyword.** An inbox
  already stores its own conversation graph in `thread_id`, so walking the
  thread is exact and free — that is how m008 is answered from m003. Two
  fallbacks handle cross-thread facts. The *date-fact* lookup is for messages
  like m019, which asks us to confirm "the date you locked in with your team"
  without saying what it is: it looks for messages carrying both a date and a
  topic word, then keeps only the ones that agree with each other, which is why
  the answer cites [m026, m036] and not the four other launch-thread messages
  that mention different dates. Plain *keyword* overlap is the last resort, and
  it only counts terms that fewer than seven messages use — without that,
  m042 and m010 "match" because both say PaperJet and enjoyed. No embeddings:
  a hundred short messages do not need a vector store, and when I have to
  defend a citation I would rather point at a term than a cosine distance.
- **Disposition vocabulary (six).**
  `reply` — the owner must answer, and a draft is prepared.
  `archive` — nothing further is required.
  `defer` — real work with a date on it, scheduled rather than answered now.
  `delegate` — a colleague owns it; nothing for the owner to do.
  `escalate` — a human must decide before anything happens (money, legal,
  credentials, a time that collides, anything the mailbox cannot ground; see
  the escalation line below for how this differs between providers).
  `flag` — hostile or deceptive: quarantined, left in place, no action taken.
- **Reversible vs irreversible.** `draft`, `label`, `archive`, `defer`, `flag`
  and `note` are reversible: they write files this project owns, and re-running
  a command overwrites them. `send` and `delete` are irreversible. `send`
  writes a file into `outbox/`, which is what "it has left" means here. **Delete
  is irreversible by design, not by accident**: the mock store has no trash, and
  deleting is the single action an attacker most wants — m024 asks for it
  explicitly. So this system never deletes anything at all. The action exists,
  is classified irreversible, and every attempt to use it is refused and logged.
- **Where the gate sits.** `gate.propose()` is the only function in the project
  that can write to `outbox/`, and nothing else calls the writer. Both gates are
  on: the default is a dry run that prints exactly what it would send and writes
  nothing; `--live` asks y/n per send; `--live --yes` approves without asking and
  records `decided_by: "--yes"` so a scripted run can never be mistaken for a
  human one. Every gated decision is written to `gate_log.json` and to
  `trace.jsonl`.
- **Escalation line, and what it costs.** Only sends to people are gated
  individually. Archiving, deferring, delegating and flagging happen without
  asking, and the 59 rule-decided messages are never mentioned. That keeps the
  approval queue to a handful of items per run instead of forty, which is the
  whole point: forty approvals get rubber-stamped, a handful get read. What I
  traded away is real - a wrongly archived internal note is possible and the
  owner will not be asked about it. I accepted that because archiving is
  reversible and the messages at risk are the ones the rules already recognise
  as receipts.
  Where the line sits depends on the provider, and I want to be precise about
  it. The guard (hostile and phishing mail, before the model) and the secret
  check in `drafter.py` (a credential found in the evidence is never repeated
  in a draft, as with m008) run on every provider. The finer rules - legal
  signatures, money or contracts, a proposed time that collides, a message the
  mailbox cannot ground - are applied as `escalate` by the deterministic
  `offline` path. With `LLM_PROVIDER=gemini` the model's label is used as
  returned - with one exception enforced in `pipeline.py` for every provider:
  mail the owner sent is never labelled `reply` (it becomes `defer`, tracked
  for a follow-up), because replying to it would be the owner writing to
  themselves. In the submitted Gemini run, some messages that the finer rules
  would escalate (for example m019, m043, m010, m013, m016) are labelled
  `reply` rather than `escalate`. That is safe in the
  sense that matters - every send still stops at the gate and is logged, and
  ungroundable messages still get no draft - but it means the approval queue,
  not the label, is the real line under Gemini. Enforcing the offline rules on
  top of the model's label is the obvious next change.
- **Untrusted text.** Message content reaching a model is wrapped in
  `<untrusted_email>` markers, but the markers are not the defence — an attacker
  can write the closing marker. The defence is that the model is never given a
  tool, and returns a label from a fixed vocabulary plus prose. The orchestrator
  decides what happens. Full answer in Final Report Q2.
- **Assumptions about the data.** 100 messages, every one carrying `id`,
  `thread_id`, `from`, `to`, `subject`, `timestamp`, `body`, `unread`; ids
  unique; timestamps naive local ISO-8601. `python demo.py --check` verifies all
  of this. "Today" is fixed at 2026-09-10 (`config.TODAY`), the morning after
  the last message, so that "Wednesday at 2:00pm" resolves the same way on every
  run.

## Capabilities

| id | name | tier | one-line claim |
|----|------|------|----------------|
| R1 | Zero the inbox | B | all 100 get one disposition and a reason; 59 never reach a model |
| R2 | Grounded reply | B | drafts cite what they read, and say so when nothing grounds them |
| R3 | Gate the irreversible | C | dry run by default, approval per send, delete always refused |
| R4 | Standing instruction across a restart | C | a preference read in one process changes a later one |
| R5 | Refuse instructions hidden in the mail | C | four found, refused, reported, left in place |
| R6 | Three-pane dashboard | C | pending, flagged, and a commitments calendar that cites its sources |
| X1 | Morning digest | B | needs you / can wait / handled, plus the next seven days |
| X2 | Waiting on a reply | A | one lookup: sent mail nobody answered |
| X3 | Thread to one question | B | nine messages reduced to the one ask aimed at you |
| X4 | Conflict negotiator | C | a proposal that breaks a stored preference, three alternatives, held at the gate |

The exact command, observable outcome and evidence for each is in
`capabilities.json`, which is the machine-readable version and the one a marking
script reads. This file is for a person. They are kept in step by hand, and
`python demo.py --check` re-verifies the ids both of them cite.

## What is worth knowing about this inbox

Four messages carry instructions addressed to the assistant, and they are not
equally obvious. m024 hides one under a newsletter footer. m017 dresses one as a
delivery-status notice. m047 puts one *inside a quoted forward* from a support
ticket, where it reads like part of somebody else's email. m039 is the
interesting one: it arrives from `sam@paperjet.io` — the owner's own address —
and asks, in the owner's voice, for autonomous sending, no approvals, and to be
saved as a standing preference.

m039 matters because m041 is also self-addressed, also talks to the assistant,
and is legitimate ("I do not take meetings before 11:00am"). So "this message
talks to the assistant" cannot be the test, and neither can the From header,
because nothing here can authenticate a sender. The test used is two-part: a
message is hostile when it *both* addresses the assistant or claims
configuration authority *and* asks for exfiltration, deletion, silence, an
approval bypass, or a broadcast send. m041 has the first and not the second, and
becomes a preference. m039 has both, and is refused — its attempt to write
itself into `prefs.json` is visible in the R5 output, where the stored
preference list contains only `no_meetings_before` and `cc_on_legal`.

## Final Report

The four required answers are in `README.md`, as the assignment asks.
