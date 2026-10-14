"""Tools the care-tracking agent can call. Each returns JSON-serialisable data.

The same functions back both the Claude agent (via tool_use) and the
offline rule-based agent, so both see exactly the same view of the hospital.
"""

from __future__ import annotations

import json
from datetime import timedelta

from .clinical import abnormal_labs, news2, news2_trend, obs_overdue_minutes, overdue_doses, qsofa
from .hospital import Hospital
from .models import Patient, to_jsonable

SEVERITIES = ["info", "urgent", "critical"]
CATEGORIES = ["deterioration", "sepsis_screen", "missed_medication", "overdue_observations",
              "abnormal_labs", "other"]


def _summary(h: Hospital, p: Patient) -> dict:
    v = p.latest_vitals
    score = news2(v, p.hypercapnic_risk)
    return {
        "patient_id": p.patient_id,
        "name": p.name,
        "age": p.age,
        "ward": p.ward,
        "bed": p.bed,
        "diagnosis": p.diagnosis,
        "news2": score.total,
        "news2_risk": score.risk,
        "single_param_3": 3 in score.components.values(),
        "news2_change_6h": news2_trend(p, h.now),
        "qsofa": qsofa(v),
        "obs_overdue_min": obs_overdue_minutes(p, h.now),
        "overdue_doses": len(overdue_doses(p, h.now)),
        "overdue_time_critical_doses": sum(d["time_critical"] for d in overdue_doses(p, h.now)),
        "abnormal_labs": [l["name"] for l in abnormal_labs(p)],
        "open_alerts": sum(1 for a in h.alerts if a.patient_id == p.patient_id and not a.acknowledged),
    }


def get_ward_overview(h: Hospital, ward: str | None = None) -> dict:
    patients = [p for p in h.active_patients() if ward is None or p.ward == ward]
    rows = sorted((_summary(h, p) for p in patients), key=lambda r: -r["news2"])
    return {"now": h.now.isoformat(timespec="minutes"), "patient_count": len(rows), "patients": rows}


def get_patient_details(h: Hospital, patient_id: str) -> dict:
    p = h.get(patient_id)
    v = p.latest_vitals
    score = news2(v, p.hypercapnic_risk)
    return {
        **_summary(h, p),
        "sex": p.sex,
        "admitted_at": p.admitted_at.isoformat(timespec="minutes"),
        "comorbidities": p.comorbidities,
        "allergies": p.allergies,
        "spo2_scale": 2 if p.hypercapnic_risk else 1,
        "latest_vitals": to_jsonable(v),
        "news2_components": score.components,
        "recommended_obs_interval_hours": score.obs_interval_hours,
        "abnormal_labs_detail": abnormal_labs(p),
        "overdue_doses_detail": overdue_doses(p, h.now),
        "active_orders": [to_jsonable(o) for o in p.orders],
        "alerts": [to_jsonable(a) for a in h.alerts if a.patient_id == patient_id],
    }


def get_vitals_history(h: Hospital, patient_id: str, hours: int = 12) -> dict:
    p = h.get(patient_id)
    since = h.now - timedelta(hours=hours)
    rows = []
    for v in p.vitals:
        if v.taken_at >= since:
            rows.append({**to_jsonable(v), "news2": news2(v, p.hypercapnic_risk).total})
    return {"patient_id": patient_id, "hours": hours, "observations": rows}


def get_care_gaps(h: Hospital) -> dict:
    """Process failures across the hospital: overdue obs and missed doses."""
    obs, meds = [], []
    for p in h.active_patients():
        late = obs_overdue_minutes(p, h.now)
        if late > 0:
            obs.append({"patient_id": p.patient_id, "ward": p.ward, "bed": p.bed,
                        "minutes_overdue": late,
                        "news2_at_last_obs": news2(p.latest_vitals, p.hypercapnic_risk).total})
        for d in overdue_doses(p, h.now):
            meds.append({"patient_id": p.patient_id, "ward": p.ward, "bed": p.bed, **d})
    obs.sort(key=lambda r: -r["minutes_overdue"])
    meds.sort(key=lambda r: (not r["time_critical"], -r["minutes_late"]))
    return {"overdue_observations": obs, "overdue_doses": meds}


def list_alerts(h: Hospital, include_acknowledged: bool = False) -> dict:
    return {"alerts": [to_jsonable(a) for a in h.alerts if include_acknowledged or not a.acknowledged]}


def raise_alert(h: Hospital, patient_id: str, category: str, severity: str, reason: str,
                recommended_action: str, raised_by: str = "agent") -> dict:
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}")
    alert, created = h.raise_alert(patient_id, category, severity, reason, recommended_action, raised_by)
    status = "created" if created else "duplicate_suppressed (equal or higher open alert exists)"
    return {"status": status, "alert": to_jsonable(alert)}


TOOL_FUNCTIONS = {
    "get_ward_overview": get_ward_overview,
    "get_patient_details": get_patient_details,
    "get_vitals_history": get_vitals_history,
    "get_care_gaps": get_care_gaps,
    "list_alerts": list_alerts,
    "raise_alert": raise_alert,
}

_PATIENT_ID = {"type": "string", "description": "Patient id, e.g. P1004"}

TOOL_SCHEMAS = [
    {
        "name": "get_ward_overview",
        "description": "List every active patient (optionally one ward) sorted by NEWS2, with "
                       "risk band, 6-hour NEWS2 change, qSOFA, overdue obs/doses, abnormal labs "
                       "and open alert count. Start every review here.",
        "input_schema": {
            "type": "object",
            "properties": {"ward": {"type": "string", "enum": ["Medical", "Surgical", "Cardiology",
                                                               "Respiratory"]}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_patient_details",
        "description": "Full record for one patient: demographics, comorbidities, allergies, "
                       "latest vitals, NEWS2 component breakdown, abnormal labs, overdue doses, "
                       "medication orders and alert history.",
        "input_schema": {"type": "object", "properties": {"patient_id": _PATIENT_ID},
                         "required": ["patient_id"], "additionalProperties": False},
    },
    {
        "name": "get_vitals_history",
        "description": "Time series of observations with per-row NEWS2 for one patient, to judge "
                       "trajectory.",
        "input_schema": {"type": "object",
                         "properties": {"patient_id": _PATIENT_ID,
                                        "hours": {"type": "integer", "minimum": 1, "maximum": 72}},
                         "required": ["patient_id"], "additionalProperties": False},
    },
    {
        "name": "get_care_gaps",
        "description": "Hospital-wide process failures: observations overdue against the NEWS2 "
                       "schedule and medication doses not given (time-critical first).",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_alerts",
        "description": "Open alerts already raised, so you do not duplicate them.",
        "input_schema": {"type": "object",
                         "properties": {"include_acknowledged": {"type": "boolean"}},
                         "additionalProperties": False},
    },
    {
        "name": "raise_alert",
        "description": "Raise an alert to the clinical team. Duplicates of an equal-or-higher open "
                       "alert for the same patient and category are suppressed automatically. "
                       "Use 'critical' for NEWS2 >= 7 or rapid deterioration needing immediate "
                       "review, 'urgent' for NEWS2 5-6, a single parameter scoring 3, a positive "
                       "sepsis screen or a missed time-critical dose, 'info' for everything else.",
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": _PATIENT_ID,
                "category": {"type": "string", "enum": CATEGORIES},
                "severity": {"type": "string", "enum": SEVERITIES},
                "reason": {"type": "string", "description": "Evidence: the numbers that triggered this."},
                "recommended_action": {"type": "string",
                                       "description": "Concrete next step for the bedside team."},
            },
            "required": ["patient_id", "category", "severity", "reason", "recommended_action"],
            "additionalProperties": False,
        },
    },
]


def execute_tool(h: Hospital, name: str, args: dict) -> str:
    """Run a tool by name and return its JSON result string."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        raise KeyError(f"Unknown tool {name!r}")
    return json.dumps(fn(h, **args), default=str)
