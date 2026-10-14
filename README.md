# CareTracking agent

An AI agent that watches a (synthetic) hospital: it reads vital signs, labs and
medication records, scores every patient, spots deterioration and missed care,
raises alerts, and writes a handover for the nurse in charge.

All patient data is randomly generated. No real patient information is used.

## Quick start

```bash
pip install -e ".[dev]"

python -m caretracking snapshot                   # see the generated hospital
python -m caretracking run                        # one review
python -m caretracking run --cycles 6 --interval 60 --verbose   # watch it over 6 simulated hours
python -m caretracking export hospital.json       # dump the dataset
pytest
```

With `ANTHROPIC_API_KEY` set, `run` uses Claude as the agent. Without a key, or with
`--offline`, it uses a deterministic rule engine that calls the same tools.

## How it fits together

```
 hospital.py          clinical.py            tools.py                agent.py
 ───────────          ───────────            ────────                ────────
 random patients  →   NEWS2, qSOFA,      →   get_ward_overview   ←→  ClaudeCareAgent
 vitals over time     6h trend, overdue      get_patient_details      (Claude decides,
 meds / labs          obs and doses,         get_vitals_history        explains, writes
 admissions,          abnormal labs          get_care_gaps             the handover)
 discharges                                  list_alerts
 (seeded clock)                              raise_alert         ←→  RuleBasedCareAgent
                                                                      (offline fallback)
```

### 1. Synthetic data (`hospital.py`)
- 38 beds over 4 wards (Medical, Surgical, Cardiology, Respiratory), about 85% occupied.
- Each patient gets demographics, a ward-appropriate diagnosis, comorbidities and
  allergies, and 2-5 medication orders (some of them time-critical, such as IV
  antibiotics, insulin and enoxaparin).
- A hidden **trajectory** (stable, improving or deteriorating) drives a severity
  value that moves the vitals: heart rate up, respiratory rate up, blood pressure
  down, fever, falling SpO2, and confusion when severe. Labs (lactate, WBC, CRP,
  creatinine, K+, Hb) follow the same severity value.
- The clock advances in 15-minute steps. Nurses take observations on the NEWS2
  schedule, but some sets come late. About 5% of doses are missed. Patients are
  admitted and discharged, and very sick patients eventually get treated and recover.
- Everything is seeded (`--seed`), so a run can be reproduced exactly.

### 2. Clinical scoring (`clinical.py`)
These are deterministic and unit-tested. The AI reads these numbers rather than
inventing its own:
- **NEWS2** (Royal College of Physicians, 2017), including SpO2 scale 2 for
  hypercapnic (COPD) patients. Gives the risk band and the required observation interval.
- **qSOFA** sepsis screen.
- The **6-hour NEWS2 trend**, observations overdue against the NEWS2 schedule,
  overdue doses in the last 12 hours, and abnormal latest labs.

### 3. Where the AI comes in (`agent.py`)
The scoring finds the numbers. The AI does the judgement work around them:
- **Triage across the whole hospital.** It decides who to look at closely,
  drilling into details and vitals history only where the overview suggests a problem.
- **Context.** It weighs a score against the patient: NEWS2 5 in a 90-year-old
  with a falling BP is different from NEWS2 5 in a COPD patient on their usual
  oxygen. A missed antibiotic in a patient with a positive sepsis screen matters
  more than a late paracetamol.
- **Escalation.** It raises alerts with the evidence and a concrete bedside
  action. Before each alert it checks the open alerts so it doesn't repeat them,
  and re-raises only when a patient gets worse. The tool layer also enforces this.
- **Handover.** It writes a prioritised plain-language summary for the nurse in charge.

The agent is a tool-use loop on the Claude API (`claude-opus-5-5`, adaptive
thinking, `effort` adjustable with `--effort`). It has server-side refusal
fallback turned on (`fallbacks: "default"`). Tool errors, such as an unknown
patient ID, go back to the model as `is_error` results, so the run doesn't crash.

`RuleBasedCareAgent` codifies the NEWS2 escalation thresholds. It's useful as an
offline fallback and as a baseline to compare the LLM's alerts against.

## Running it continuously

`run --cycles N --interval M` advances the simulated clock by M minutes between
reviews. To run it as a long-lived service, call `agent.review(hospital)` on a
schedule and feed `hospital.advance()` from a real data source instead of the simulator:

```python
from caretracking import ClaudeCareAgent, Hospital

h = Hospital.generate(seed=42)
agent = ClaudeCareAgent()
while True:
    h.advance(minutes=60)
    report = agent.review(h)
    print(report.summary)
```

## Disclaimer
This is a demo of decision support using synthetic data. It is not a medical
device, and it must not be used for real patient care.
