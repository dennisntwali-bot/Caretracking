"""Synthetic hospital: random patients, and a clock-driven simulation that
keeps producing observations, medication rounds, labs, admissions and
discharges. Everything is seeded so a run can be reproduced exactly.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .clinical import news2
from .models import Alert, DoseEvent, LabResult, MedicationOrder, Patient, VitalSigns

FIRST_NAMES = ["Amara", "Ben", "Chen", "Divya", "Elif", "Femi", "Grace", "Hugo", "Ines",
               "Jonas", "Kiri", "Leila", "Mateo", "Nadia", "Omar", "Priya", "Quinn",
               "Rosa", "Samir", "Tess", "Uma", "Victor", "Wren", "Yusuf", "Zoe"]
LAST_NAMES = ["Abara", "Brooks", "Costa", "Dubois", "Eze", "Fischer", "Garcia", "Haddad",
              "Ito", "Jensen", "Kowalski", "Lindqvist", "Mensah", "Nguyen", "Okafor",
              "Patel", "Rossi", "Silva", "Tanaka", "Uwimana", "Varga", "Walsh"]

WARDS = {"Medical": 12, "Surgical": 10, "Cardiology": 8, "Respiratory": 8}

DIAGNOSES = {
    "Medical": ["Community-acquired pneumonia", "Urinary tract infection", "Cellulitis",
                "Acute kidney injury", "Diabetic ketoacidosis (resolving)", "Fall, query cause"],
    "Surgical": ["Post-op laparoscopic cholecystectomy", "Post-op hip fracture repair",
                 "Small bowel obstruction", "Post-op appendicectomy", "Acute pancreatitis"],
    "Cardiology": ["NSTEMI", "Decompensated heart failure", "Atrial fibrillation with RVR",
                   "Chest pain, query ACS"],
    "Respiratory": ["COPD exacerbation", "Asthma exacerbation", "Pulmonary embolism",
                    "Pleural effusion"],
}
COMORBIDITIES = ["Hypertension", "Type 2 diabetes", "CKD stage 3", "COPD", "Atrial fibrillation",
                 "Heart failure", "Obesity", "Dementia", "Asthma"]
ALLERGIES = ["Penicillin", "Sulfonamides", "NSAIDs", "Latex", "Codeine"]

MEDS = [  # drug, dose, route, every_hours, time_critical
    ("Paracetamol", "1 g", "PO", 6, False),
    ("Co-amoxiclav", "1.2 g", "IV", 8, True),
    ("Piperacillin-tazobactam", "4.5 g", "IV", 8, True),
    ("Enoxaparin", "40 mg", "SC", 24, True),
    ("Insulin (variable rate)", "per scale", "IV", 4, True),
    ("Furosemide", "40 mg", "IV", 12, False),
    ("Bisoprolol", "2.5 mg", "PO", 24, False),
    ("Salbutamol nebuliser", "5 mg", "NEB", 4, False),
    ("Prednisolone", "40 mg", "PO", 24, False),
    ("Morphine", "2.5 mg", "IV", 4, False),
]

# Labs: name, unit, ref_low, ref_high, healthy mean, shift at full severity
LABS = [
    ("Lactate", "mmol/L", 0.5, 2.0, 1.1, 2.8),
    ("WBC", "x10^9/L", 4.0, 11.0, 7.5, 12.0),
    ("CRP", "mg/L", 0.0, 10.0, 6.0, 180.0),
    ("Creatinine", "umol/L", 60.0, 110.0, 85.0, 120.0),
    ("Potassium", "mmol/L", 3.5, 5.3, 4.3, 0.9),
    ("Haemoglobin", "g/L", 120.0, 170.0, 135.0, -25.0),
]


@dataclass
class _Physiology:
    """Hidden per-patient state that drives the vitals generator."""
    severity: float  # <= 0 = well, 1 = critically unwell (negative = illness not yet started)
    rate: float  # severity change per hour
    base_hr: int
    base_rr: int
    base_sbp: int
    base_temp: float
    base_spo2: int
    on_oxygen: bool
    obs_compliance: float  # probability nurses take obs on time
    next_obs_at: datetime | None = None  # when the (possibly late) next obs will happen


@dataclass
class Hospital:
    seed: int = 7
    now: datetime = field(default_factory=lambda: datetime(2026, 1, 15, 8, 0))
    patients: dict[str, Patient] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.rng = random.Random(self.seed)
        self._physio: dict[str, _Physiology] = {}
        self._ids = itertools.count(1001)
        self._order_ids = itertools.count(1)
        self._alert_ids = itertools.count(1)

    # ------------------------------------------------------------------ setup

    @classmethod
    def generate(cls, seed: int = 7, occupancy: float = 0.85, history_hours: int = 24,
                 start: datetime | None = None) -> "Hospital":
        h = cls(seed=seed, now=start or datetime(2026, 1, 15, 8, 0))
        sim_start = h.now - timedelta(hours=history_hours)
        h.now = sim_start
        for ward, beds in WARDS.items():
            for b in range(1, beds + 1):
                if h.rng.random() < occupancy:
                    h._admit(ward, f"{ward[0]}{b:02d}",
                             admitted_at=sim_start - timedelta(hours=h.rng.randint(2, 96)))
        h.events.clear()
        # Run the clock forward so every patient has an observation history.
        h.advance(minutes=history_hours * 60, step_minutes=15)
        h.events.clear()
        return h

    def _admit(self, ward: str, bed: str, admitted_at: datetime | None = None) -> Patient:
        r = self.rng
        pid = f"P{next(self._ids)}"
        diagnosis = r.choice(DIAGNOSES[ward])
        comorbid = r.sample(COMORBIDITIES, k=r.randint(0, 3))
        if diagnosis == "COPD exacerbation" and "COPD" not in comorbid:
            comorbid.append("COPD")
        hypercapnic = "COPD" in comorbid
        trajectory = r.choices(["stable", "improving", "deteriorating"], [0.6, 0.3, 0.1])[0]

        p = Patient(
            patient_id=pid,
            name=f"{r.choice(FIRST_NAMES)} {r.choice(LAST_NAMES)}",
            age=r.randint(19, 94),
            sex=r.choice(["F", "M"]),
            ward=ward,
            bed=bed,
            admitted_at=admitted_at or self.now,
            diagnosis=diagnosis,
            comorbidities=comorbid,
            allergies=r.sample(ALLERGIES, k=r.choices([0, 1, 2], [0.7, 0.25, 0.05])[0]),
            hypercapnic_risk=hypercapnic,
            trajectory=trajectory,
        )
        if trajectory == "deteriorating":
            # Negative start = onset sometime in the coming hours.
            severity, rate = r.uniform(-0.8, 0.2), r.uniform(0.03, 0.07)
        elif trajectory == "improving":
            severity, rate = r.uniform(0.25, 0.5), -r.uniform(0.01, 0.03)
        else:
            severity, rate = r.uniform(0.0, 0.15), 0.0
        self._physio[pid] = _Physiology(
            severity=severity, rate=rate,
            base_hr=r.randint(62, 88), base_rr=r.randint(13, 18), base_sbp=r.randint(112, 145),
            base_temp=round(r.uniform(36.3, 37.2), 1),
            base_spo2=r.randint(89, 92) if hypercapnic else r.randint(95, 99),
            on_oxygen=hypercapnic or r.random() < 0.15,
            obs_compliance=r.uniform(0.75, 0.98),
        )
        for drug, dose, route, every, critical in r.sample(MEDS, k=r.randint(2, 5)):
            p.orders.append(MedicationOrder(f"O{next(self._order_ids)}", drug, dose, route,
                                            every, critical))
        self._schedule_doses(p, self.now)
        self.patients[pid] = p
        self.events.append(f"{self.now:%H:%M} admitted {pid} to {ward} {bed}: {diagnosis}")
        self._take_obs(p)
        self._draw_labs(p)
        return p

    def _schedule_doses(self, p: Patient, start: datetime, hours: int = 48) -> None:
        for o in p.orders:
            t = start.replace(minute=0) + timedelta(hours=self.rng.randint(0, o.every_hours - 1))
            while t < start + timedelta(hours=hours):
                p.doses.append(DoseEvent(o.order_id, t))
                t += timedelta(hours=o.every_hours)
        p.doses.sort(key=lambda d: d.scheduled_at)

    # ------------------------------------------------------------- generators

    def _take_obs(self, p: Patient) -> None:
        ph, r = self._physio[p.patient_id], self.rng
        s = max(0.0, min(1.0, ph.severity))
        sepsis_like = p.diagnosis not in ("COPD exacerbation", "Asthma exacerbation")
        on_o2 = ph.on_oxygen or s > 0.55
        spo2 = ph.base_spo2 - 9 * s + r.gauss(0, 1)
        if on_o2 and not ph.on_oxygen:
            spo2 += 3  # supplemental O2 started, partially corrects saturation
        consciousness = "A"
        if s > 0.85:
            consciousness = r.choice(["C", "V"])
        elif s > 0.7 and r.random() < 0.3:
            consciousness = "C"
        p.vitals.append(VitalSigns(
            taken_at=self.now,
            heart_rate=round(ph.base_hr + 45 * s + r.gauss(0, 4)),
            resp_rate=max(8, round(ph.base_rr + 12 * s + r.gauss(0, 1.5))),
            systolic_bp=round(ph.base_sbp - (45 if sepsis_like else 15) * s + r.gauss(0, 6)),
            diastolic_bp=round(70 - 15 * s + r.gauss(0, 5)),
            temperature=round(ph.base_temp + (2.2 if sepsis_like else 0.6) * s + r.gauss(0, 0.2), 1),
            spo2=int(max(70, min(100, round(spo2)))),
            on_oxygen=on_o2,
            consciousness=consciousness,
        ))

    def _draw_labs(self, p: Patient) -> None:
        s = max(0.0, min(1.0, self._physio[p.patient_id].severity))
        for name, unit, lo, hi, mean, shift in LABS:
            noise = self.rng.gauss(0, abs(mean) * 0.06)
            p.labs.append(LabResult(self.now, name, round(mean + shift * s + noise, 1), unit, lo, hi))

    # ------------------------------------------------------------- simulation

    def advance(self, minutes: int = 60, step_minutes: int = 15) -> list[str]:
        """Move the clock forward, generating new data. Returns new events."""
        start_events = len(self.events)
        end = self.now + timedelta(minutes=minutes)
        while self.now < end:
            self.now += timedelta(minutes=step_minutes)
            hours = step_minutes / 60
            for p in list(self.patients.values()):
                if p.discharged:
                    continue
                self._tick_patient(p, hours)
            self._admissions()
        return self.events[start_events:]

    def _tick_patient(self, p: Patient, hours: float) -> None:
        ph, r = self._physio[p.patient_id], self.rng
        ph.severity = max(-1.0, min(1.0, ph.severity + ph.rate * hours + r.gauss(0, 0.01)))
        # Very sick patients eventually get treated and start to recover.
        if ph.severity > 0.8 and ph.rate > 0 and r.random() < 0.08:
            ph.rate = -r.uniform(0.03, 0.06)
            self.events.append(f"{self.now:%H:%M} treatment escalated for {p.patient_id}")

        # Observations follow the NEWS2 interval; some sets are taken late.
        if ph.next_obs_at is None:
            last = p.latest_vitals
            interval = news2(last, p.hypercapnic_risk).obs_interval_hours if last else 0
            due = last.taken_at + timedelta(hours=interval) if last else self.now
            if self.now >= due:
                delay = r.randint(0, 30) if r.random() < ph.obs_compliance else r.randint(60, 240)
                ph.next_obs_at = due + timedelta(minutes=delay)
        if ph.next_obs_at is not None and self.now >= ph.next_obs_at:
            self._take_obs(p)
            ph.next_obs_at = None

        # Medication round: most doses given within 45 min, some missed.
        for d in p.doses:
            if d.given_at is None and d.scheduled_at <= self.now < d.scheduled_at + timedelta(hours=hours):
                if r.random() < 0.95:
                    d.given_at = d.scheduled_at + timedelta(minutes=r.randint(0, 45))
        if p.doses and p.doses[-1].scheduled_at < self.now + timedelta(hours=12):
            self._schedule_doses(p, p.doses[-1].scheduled_at + timedelta(hours=1), hours=24)

        # Bloods at 06:00 and 18:00, plus an extra set if clearly unwell.
        if (self.now.hour in (6, 18) and self.now.minute == 0) or (
                ph.severity > 0.6 and r.random() < 0.02):
            self._draw_labs(p)

        # Recovered patients go home.
        if ph.severity < 0.08 and ph.rate <= 0 and r.random() < 0.004:
            p.discharged = True
            self.events.append(f"{self.now:%H:%M} discharged {p.patient_id} from {p.ward} {p.bed}")

    def _admissions(self) -> None:
        for ward, beds in WARDS.items():
            occupied = {p.bed for p in self.active_patients() if p.ward == ward}
            free = [f"{ward[0]}{b:02d}" for b in range(1, beds + 1) if f"{ward[0]}{b:02d}" not in occupied]
            if free and self.rng.random() < 0.01:
                self._admit(ward, self.rng.choice(free))

    # ---------------------------------------------------------------- queries

    def active_patients(self) -> list[Patient]:
        return [p for p in self.patients.values() if not p.discharged]

    def get(self, patient_id: str) -> Patient:
        p = self.patients.get(patient_id)
        if p is None or p.discharged:
            raise KeyError(f"No active patient with id {patient_id!r}")
        return p

    def raise_alert(self, patient_id: str, category: str, severity: str, reason: str,
                    recommended_action: str, raised_by: str) -> tuple[Alert, bool]:
        """Create an alert unless an equal-or-higher open one already exists.

        Returns (alert, created).
        """
        rank = {"info": 0, "urgent": 1, "critical": 2}
        self.get(patient_id)
        for a in self.alerts:
            if (a.patient_id == patient_id and a.category == category and not a.acknowledged
                    and rank[a.severity] >= rank[severity]):
                return a, False
        alert = Alert(f"A{next(self._alert_ids):04d}", patient_id, category, severity, reason,
                      recommended_action, self.now, raised_by)
        self.alerts.append(alert)
        return alert, True
