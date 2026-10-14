"""Deterministic clinical scoring: NEWS2, qSOFA, trends and care-gap checks.

These are the "ground truth" numbers the agent reasons over. The LLM never
computes scores itself; it reads them from tools so they are reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import Patient, VitalSigns


@dataclass
class News2Result:
    total: int
    components: dict[str, int]
    risk: str  # "low", "low-medium", "medium", "high"
    obs_interval_hours: float


def _band(value: float, bands: list[tuple[float, int]], default: int) -> int:
    """Return the score of the first band whose upper bound >= value."""
    for upper, score in bands:
        if value <= upper:
            return score
    return default


def _spo2_score(spo2: int, on_oxygen: bool, hypercapnic: bool) -> int:
    if not hypercapnic:
        return _band(spo2, [(91, 3), (93, 2), (95, 1)], 0)
    # Scale 2: target 88-92%. High SpO2 only scores when on supplemental O2.
    if spo2 <= 83:
        return 3
    if spo2 <= 85:
        return 2
    if spo2 <= 87:
        return 1
    if spo2 <= 92 or not on_oxygen:
        return 0
    return _band(spo2, [(94, 1), (96, 2)], 3)


def news2(v: VitalSigns, hypercapnic: bool = False) -> News2Result:
    """National Early Warning Score 2 (Royal College of Physicians, 2017)."""
    c = {
        "resp_rate": _band(v.resp_rate, [(8, 3), (11, 1), (20, 0), (24, 2)], 3),
        "spo2": _spo2_score(v.spo2, v.on_oxygen, hypercapnic),
        "air_or_oxygen": 2 if v.on_oxygen else 0,
        "systolic_bp": _band(v.systolic_bp, [(90, 3), (100, 2), (110, 1), (219, 0)], 3),
        "heart_rate": _band(v.heart_rate, [(40, 3), (50, 1), (90, 0), (110, 1), (130, 2)], 3),
        "consciousness": 0 if v.consciousness == "A" else 3,
        "temperature": _band(v.temperature, [(35.0, 3), (36.0, 1), (38.0, 0), (39.0, 1)], 2),
    }
    total = sum(c.values())
    if total >= 7:
        risk, interval = "high", 0.25  # continuous monitoring
    elif total >= 5:
        risk, interval = "medium", 1.0
    elif 3 in c.values():
        risk, interval = "low-medium", 1.0
    elif total >= 1:
        risk, interval = "low", 4.0
    else:
        risk, interval = "low", 12.0
    return News2Result(total, c, risk, interval)


def qsofa(v: VitalSigns) -> int:
    """Quick SOFA: >= 2 suggests sepsis-related organ dysfunction risk."""
    return int(v.resp_rate >= 22) + int(v.consciousness != "A") + int(v.systolic_bp <= 100)


def news2_trend(patient: Patient, now: datetime, window_hours: int = 6) -> int:
    """Change in NEWS2 between the oldest obs in the window and the latest."""
    recent = [v for v in patient.vitals if v.taken_at >= now - timedelta(hours=window_hours)]
    if len(recent) < 2:
        return 0
    first = news2(recent[0], patient.hypercapnic_risk).total
    last = news2(recent[-1], patient.hypercapnic_risk).total
    return last - first


def obs_overdue_minutes(patient: Patient, now: datetime) -> int:
    """Minutes past the NEWS2-mandated observation interval (0 if on time)."""
    v = patient.latest_vitals
    if v is None:
        return 0
    due = v.taken_at + timedelta(hours=news2(v, patient.hypercapnic_risk).obs_interval_hours)
    return max(0, int((now - due).total_seconds() // 60))


def overdue_doses(patient: Patient, now: datetime, grace_minutes: int = 60,
                  lookback_hours: int = 12) -> list[dict]:
    """Doses scheduled in the last `lookback_hours` not given within the grace window."""
    orders = {o.order_id: o for o in patient.orders}
    out = []
    for d in patient.doses:
        if (d.given_at is None and d.scheduled_at >= now - timedelta(hours=lookback_hours)
                and d.scheduled_at + timedelta(minutes=grace_minutes) <= now):
            o = orders[d.order_id]
            out.append({
                "drug": o.drug,
                "dose": o.dose,
                "route": o.route,
                "time_critical": o.critical,
                "scheduled_at": d.scheduled_at.isoformat(timespec="minutes"),
                "minutes_late": int((now - d.scheduled_at).total_seconds() // 60),
            })
    return out


def abnormal_labs(patient: Patient) -> list[dict]:
    """Latest value of each lab, where it is outside its reference range."""
    latest = {}
    for lab in patient.labs:
        latest[lab.name] = lab
    return [
        {"name": l.name, "value": l.value, "unit": l.unit,
         "ref_range": f"{l.ref_low}-{l.ref_high}",
         "taken_at": l.taken_at.isoformat(timespec="minutes")}
        for l in latest.values() if l.abnormal
    ]
