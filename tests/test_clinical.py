from datetime import datetime

from caretracking.clinical import news2, qsofa
from caretracking.models import VitalSigns

T = datetime(2026, 1, 1, 8, 0)


def vitals(**kw):
    base = dict(taken_at=T, heart_rate=75, resp_rate=16, systolic_bp=125, diastolic_bp=75,
                temperature=36.8, spo2=97, on_oxygen=False, consciousness="A")
    base.update(kw)
    return VitalSigns(**base)


def test_normal_obs_score_zero():
    r = news2(vitals())
    assert r.total == 0 and r.risk == "low" and r.obs_interval_hours == 12


def test_band_edges():
    assert news2(vitals(resp_rate=8)).components["resp_rate"] == 3
    assert news2(vitals(resp_rate=21)).components["resp_rate"] == 2
    assert news2(vitals(heart_rate=131)).components["heart_rate"] == 3
    assert news2(vitals(systolic_bp=220)).components["systolic_bp"] == 3
    assert news2(vitals(temperature=39.1)).components["temperature"] == 2
    assert news2(vitals(temperature=35.0)).components["temperature"] == 3


def test_single_parameter_three_is_low_medium():
    r = news2(vitals(consciousness="C"))
    assert r.total == 3 and r.risk == "low-medium" and r.obs_interval_hours == 1


def test_high_risk():
    r = news2(vitals(heart_rate=125, resp_rate=26, systolic_bp=95, temperature=39.3, spo2=92,
                     on_oxygen=True))
    assert r.total >= 7 and r.risk == "high"


def test_spo2_scale_2_for_hypercapnic_patients():
    assert news2(vitals(spo2=89), hypercapnic=True).components["spo2"] == 0
    assert news2(vitals(spo2=89), hypercapnic=False).components["spo2"] == 3
    assert news2(vitals(spo2=98, on_oxygen=True), hypercapnic=True).components["spo2"] == 3
    assert news2(vitals(spo2=98, on_oxygen=False), hypercapnic=True).components["spo2"] == 0


def test_qsofa():
    assert qsofa(vitals()) == 0
    assert qsofa(vitals(resp_rate=24, systolic_bp=95, consciousness="V")) == 3
