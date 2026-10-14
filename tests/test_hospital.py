import json

from caretracking.hospital import Hospital
from caretracking.tools import TOOL_SCHEMAS, execute_tool


def test_generation_is_reproducible():
    a, b = Hospital.generate(seed=3), Hospital.generate(seed=3)
    assert [p.name for p in a.patients.values()] == [p.name for p in b.patients.values()]
    assert a.active_patients()[0].vitals[-1] == b.active_patients()[0].vitals[-1]


def test_every_patient_has_history():
    h = Hospital.generate(seed=1)
    assert len(h.active_patients()) > 20
    for p in h.active_patients():
        assert p.vitals and p.labs and p.orders


def test_advance_moves_clock_and_adds_obs():
    h = Hospital.generate(seed=1)
    start = h.now
    before = sum(len(p.vitals) for p in h.patients.values())
    h.advance(minutes=240)
    assert (h.now - start).total_seconds() == 240 * 60
    assert sum(len(p.vitals) for p in h.patients.values()) > before


def test_every_schema_tool_runs():
    h = Hospital.generate(seed=2)
    pid = h.active_patients()[0].patient_id
    args = {"get_patient_details": {"patient_id": pid},
            "get_vitals_history": {"patient_id": pid, "hours": 6},
            "raise_alert": {"patient_id": pid, "category": "other", "severity": "info",
                            "reason": "test", "recommended_action": "none"}}
    for schema in TOOL_SCHEMAS:
        json.loads(execute_tool(h, schema["name"], args.get(schema["name"], {})))


def test_duplicate_alerts_suppressed_but_escalation_allowed():
    h = Hospital.generate(seed=2)
    pid = h.active_patients()[0].patient_id
    _, created = h.raise_alert(pid, "deterioration", "urgent", "r", "a", "t")
    assert created
    _, created = h.raise_alert(pid, "deterioration", "urgent", "r", "a", "t")
    assert not created
    _, created = h.raise_alert(pid, "deterioration", "critical", "r", "a", "t")
    assert created
