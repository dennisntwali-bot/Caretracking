"""Core data structures for the hospital care-tracking domain."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class VitalSigns:
    taken_at: datetime
    heart_rate: int
    resp_rate: int
    systolic_bp: int
    diastolic_bp: int
    temperature: float
    spo2: int
    on_oxygen: bool
    consciousness: str  # ACVPU: "A", "C", "V", "P", "U"


@dataclass
class LabResult:
    taken_at: datetime
    name: str
    value: float
    unit: str
    ref_low: float
    ref_high: float

    @property
    def abnormal(self) -> bool:
        return not (self.ref_low <= self.value <= self.ref_high)


@dataclass
class MedicationOrder:
    order_id: str
    drug: str
    dose: str
    route: str
    every_hours: int
    critical: bool  # time-critical drugs (antibiotics, insulin, anticoagulants...)


@dataclass
class DoseEvent:
    order_id: str
    scheduled_at: datetime
    given_at: datetime | None = None


@dataclass
class Patient:
    patient_id: str
    name: str
    age: int
    sex: str
    ward: str
    bed: str
    admitted_at: datetime
    diagnosis: str
    comorbidities: list[str]
    allergies: list[str]
    hypercapnic_risk: bool  # use NEWS2 SpO2 scale 2 (e.g. COPD)
    trajectory: str  # hidden ground truth: "stable", "improving", "deteriorating"
    vitals: list[VitalSigns] = field(default_factory=list)
    labs: list[LabResult] = field(default_factory=list)
    orders: list[MedicationOrder] = field(default_factory=list)
    doses: list[DoseEvent] = field(default_factory=list)
    discharged: bool = False

    @property
    def latest_vitals(self) -> VitalSigns | None:
        return self.vitals[-1] if self.vitals else None


@dataclass
class Alert:
    alert_id: str
    patient_id: str
    category: str
    severity: str  # "info", "urgent", "critical"
    reason: str
    recommended_action: str
    raised_at: datetime
    raised_by: str
    acknowledged: bool = False


def to_jsonable(obj):
    """Convert dataclasses/datetimes into plain JSON-safe structures."""
    if hasattr(obj, "__dataclass_fields__"):
        obj = asdict(obj)
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat(timespec="minutes")
    return obj
