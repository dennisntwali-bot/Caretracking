# Care Tracker Agent

A care-operations AI agent for a women's health clinic ("Women's Wellness Throughout
the Reproductive Continuum"). It finds patients whose care has stalled, triages
patient messages with Claude, opens one ticket per problem, and keeps a full audit trail.

All of it is one file, **`index.html`**. Open it in a browser (or deploy it to any
static host, such as Vercel) and the agent runs in that tab. There's no backend, and no
data leaves the tab except the Claude API calls you turn on.

## Use it

1. Open `index.html`. The demo clinic is already loaded.
2. Press **Run agent cycle**. Without an API key it uses rule-based reasoning.
3. To use Claude, open **Data**, paste an Anthropic API key and choose **Use this key**.
   The key is held in memory only, never stored, and **Clear key** wipes it.
   Default model: `claude-opus-5-5` (you can change it in the Model field).
   To skip pasting each time on your own computer, copy `config.example.js` to
   `config.local.js` next to `index.html` and put your key in it. The page loads it
   automatically. `config.local.js` is git-ignored: never commit it or upload it to a
   public host such as Vercel.
4. On the **Data** page you can also generate a random clinic (seeded), load an empty
   clinic, or upload your own tables (files, a folder, or drag and drop).
5. Download `task_tickets.json`, `agent_audit_log.jsonl`, `agent_report.json` and the
   updated tables. Upload `task_tickets.json` again with your data next time, and open
   tickets won't be duplicated.

Sample data: `sample-data/demo-csv/` (the demo as CSV) and `sample-data/random-clinic-seed7.json`
(a 40-patient random clinic as a single bundle).

## Data format

Five tables, each as JSON (a list of objects) or CSV (a header row). If both exist,
JSON is used. A single `clinic.json` holding all five tables also works. Extra columns
are ignored, and dates are ISO `YYYY-MM-DD`.

| table | columns |
|---|---|
| patients | id, name, age, condition, provider |
| lab_orders | id, patient_id, test, ordered_date, result_date, status (`pending`/`resulted`) |
| prescriptions | id, patient_id, drug, ordered_date, filled_date, status (`ordered`/`filled`) |
| appointments | id, patient_id, date, type, note, status (`completed`/`scheduled`/`cancelled`) |
| messages | id, patient_id, received_date, read (`true`/`false`), subject, body |

"Today" defaults to 2026-10-01 and can be changed on the Data page. Messy input
doesn't crash the agent: a BOM, CRLF line endings, mixed-case headers or statuses, duplicate
or missing ids, bad dates and unknown patient ids all become warnings in the audit log.

## What one cycle does

**Perceive.** The agent loads and validates the five tables.

**Detect** dead-ends with exact rules:

| finding id | rule | severity |
|---|---|---|
| `stalled-lab:L001` | pending, no result, ordered more than 7 days ago | high |
| `unfilled-rx:R001` | ordered, not filled, more than 3 days old | medium (high after 7 days) |
| `overdue-followup:A001` | a completed note says "follow up in N weeks" (digits or one to twelve), that date has passed, and the patient has no scheduled appointment today or later | medium |
| `unanswered-message:M001` | unread and received more than 1 day ago | taken from triage |

**Reason (where Claude comes in).**
- Every unread message goes to Claude with structured JSON output
  (`output_config.format`). Claude returns a `bucket` (administrative, routine,
  clinician_review or urgent), a `confidence`, the `signal_phrases`, a one-line
  `rationale` and a `suggested_next_step`.
- **Safety rule.** After Claude answers, a fixed scan looks for urgent phrases: heavy
  bleeding, severe pain, chest pain, trouble breathing, fainting, unconscious, 911,
  going to urgent care or the ER, reduced fetal movement, waters breaking, severe headache
  or blurred vision, self-harm, seizure and high fever. Any match makes the message
  urgent. Claude can escalate a message, but it can never lower one.
- If there's no key, or the API errors, refuses, times out or returns invalid JSON, the
  message falls back to rule-based triage with the same four buckets. A 401 or 403
  turns Claude off for the rest of the cycle. The cycle never crashes.
- Claude also writes the **cycle brief** (headline, workload, top risks, next actions).
  If it can't, a built-in template writes the brief instead.

**Act.**
- The agent opens one ticket per finding, keyed by a stable finding id, and checks open
  tickets first so it never creates duplicates.
- Fresh unread messages get a `new-message:` ticket.
- Administrative and routine messages go to `admin_team`; clinician_review and urgent go
  to `clinical_team`. Urgent messages are also escalated to the on-call clinician.
- Triaged messages are marked read.

**Log.** Every step is written to `agent_audit_log.jsonl` with a timestamp and a
plain-language message. `agent_report.json` holds the counts, severity breakdown,
triage summary, tickets opened and reused, the brief, and an estimate of staff-minutes
saved (3 min per triage, 6 per chart check, 10 per brief).

The Claude call is a plain `fetch` to `https://api.anthropic.com/v1/messages` with the
`anthropic-dangerous-direct-browser-access` header, which allows a browser to call the
API directly. It also sends `fallbacks: "default"`, so if Claude declines a request, the
server retries it on another model. If an account doesn't support that option, the
agent retries once without it.

## Tests

```bash
node --test tests/acceptance.test.mjs             # agent logic, loaded straight from index.html
NODE_PATH=$(npm root -g) node tests/ui.smoke.mjs   # headless Chromium run of the page (needs playwright)
```

The acceptance suite covers every criterion in the brief:

1. **Detector precision and recall are 1.0** against an independent ground-truth
   implementation (`tests/ground-truth.mjs`) on 10 random clinics, plus boundary cases.
2. **Urgent recall is 1.0** with a mock model that always answers "routine". Every
   urgent message is still escalated.
3. **The second cycle opens 0 new tickets**, including when tickets are carried over
   from a previous session.
4. **JSON and CSV inputs give identical results**, including quoted commas and newlines.
5. **Empty and all-healthy clinics** produce zero findings and don't crash.
6. **A missing or bad key** (401, network error, invalid JSON, refusal, 529) falls back
   cleanly to rules, and the cycle completes.

The UI test checks a rules run, a Claude run with the API mocked at the network layer, a
bad key, CSV upload, downloads, ticket resolving, and that the page fits a 390px phone screen.

This is decision support on synthetic data, not a medical device.
