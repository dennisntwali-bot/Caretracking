"""The care-tracking agents.

ClaudeCareAgent   - Claude drives the review through tool calls, decides
                    what to escalate and writes the handover summary.
RuleBasedCareAgent - deterministic fallback using the same tools, so the
                    system still runs (and can be compared) without an API key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .hospital import Hospital
from .tools import TOOL_SCHEMAS, execute_tool, get_care_gaps, get_ward_overview, raise_alert

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """\
You are a clinical surveillance agent working alongside the nursing and medical \
teams in a hospital. Every time you are invoked you review all inpatients and act \
on what has changed since the last review.

Your job:
1. Find patients who are deteriorating or at risk, using NEWS2 (with its trend), \
qSOFA, labs and the patient's context (age, diagnosis, comorbidities).
2. Find care gaps: observations overdue against the NEWS2 schedule and missed \
doses, especially time-critical drugs such as antibiotics, insulin and anticoagulants.
3. Raise alerts with raise_alert. Put the evidence in `reason` and a concrete \
bedside action in `recommended_action`. Check list_alerts first and do not \
re-raise an open alert unless the patient has got worse.
4. Finish with a short handover summary for the nurse in charge: highest-priority \
patients first, then care gaps, then anything else worth knowing.

Scores come from the tools. Do not recalculate them. You support clinical staff \
and do not replace their judgement: frame actions as recommendations to the \
bedside team. All patient data is synthetic."""


@dataclass
class AgentReport:
    summary: str
    alerts_created: list[dict] = field(default_factory=list)
    tool_calls: int = 0


class ClaudeCareAgent:
    def __init__(self, client=None, model: str = MODEL, effort: str = "medium",
                 max_turns: int = 30, verbose: bool = False):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort
        self.max_turns = max_turns
        self.verbose = verbose

    def review(self, h: Hospital, instruction: str | None = None) -> AgentReport:
        prompt = instruction or (
            f"It is {h.now:%Y-%m-%d %H:%M}. Run your scheduled review of the hospital.")
        messages: list[dict] = [{"role": "user", "content": prompt}]
        created: list[dict] = []
        calls = 0

        for _ in range(self.max_turns):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                return AgentReport("Review stopped: the model declined the request.", created, calls)

            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                text = "\n".join(b.text for b in response.content if b.type == "text")
                return AgentReport(text.strip(), created, calls)

            results = []
            for block in tool_uses:
                calls += 1
                args = dict(block.input)
                if block.name == "raise_alert":
                    args["raised_by"] = "claude"
                try:
                    out = execute_tool(h, block.name, args)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
                    if block.name == "raise_alert":
                        parsed = json.loads(out)
                        if parsed["status"] == "created":
                            created.append(parsed["alert"])
                except (KeyError, TypeError, ValueError) as e:
                    out = f"Error: {e}"
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": out, "is_error": True})
                if self.verbose:
                    print(f"  -> {block.name}({json.dumps(block.input)}) {out[:160]}")
            messages.append({"role": "user", "content": results})

        return AgentReport("Review stopped: turn limit reached.", created, calls)


class RuleBasedCareAgent:
    """Deterministic escalation rules mirroring the NEWS2 response guidance."""

    def review(self, h: Hospital, instruction: str | None = None) -> AgentReport:
        created: list[dict] = []
        calls = 2

        def alert(*args):
            nonlocal calls
            calls += 1
            out = raise_alert(h, *args, raised_by="rules")
            if out["status"] == "created":
                created.append(out["alert"])

        overview = get_ward_overview(h)["patients"]
        for r in overview:
            pid, score = r["patient_id"], r["news2"]
            evidence = f"NEWS2 {score} ({r['news2_change_6h']:+d} over 6h), qSOFA {r['qsofa']}"
            if score >= 7:
                alert(pid, "deterioration", "critical", evidence,
                      "Emergency assessment by critical-care outreach; continuous monitoring.")
            elif score >= 5 or r["single_param_3"]:
                alert(pid, "deterioration", "urgent", evidence,
                      "Urgent review by ward doctor within 1 hour; hourly observations.")
            elif r["news2_change_6h"] >= 3:
                alert(pid, "deterioration", "urgent", evidence + " - rising trend",
                      "Repeat full observations and inform the nurse in charge.")
            if r["qsofa"] >= 2:
                alert(pid, "sepsis_screen", "urgent", evidence,
                      "Complete sepsis screen: lactate, blood cultures, consider antibiotics within 1 hour.")
            if "Lactate" in r["abnormal_labs"]:
                alert(pid, "abnormal_labs", "urgent", "Lactate above reference range",
                      "Repeat lactate and escalate to the medical team.")

        gaps = get_care_gaps(h)
        for d in gaps["overdue_doses"]:
            if d["time_critical"]:
                alert(d["patient_id"], "missed_medication", "urgent",
                      f"{d['drug']} {d['dose']} {d['route']} {d['minutes_late']} min late",
                      "Give now or document the reason it was withheld; inform the prescriber.")
        for o in gaps["overdue_observations"]:
            if o["minutes_overdue"] >= 60 and o["news2_at_last_obs"] >= 5:
                alert(o["patient_id"], "overdue_observations", "urgent",
                      f"Obs {o['minutes_overdue']} min overdue with last NEWS2 {o['news2_at_last_obs']}",
                      "Take a full set of observations now.")

        return AgentReport(_render_summary(h, overview, gaps, created), created, calls)


def _render_summary(h: Hospital, overview: list[dict], gaps: dict, created: list[dict]) -> str:
    lines = [f"Handover {h.now:%Y-%m-%d %H:%M} - {len(overview)} inpatients"]
    watch = [r for r in overview if r["news2"] >= 5 or r["single_param_3"] or r["news2_change_6h"] >= 3]
    lines.append(f"\nPriority patients ({len(watch)}):")
    for r in watch:
        lines.append(f"  - {r['patient_id']} {r['name']} ({r['ward']} {r['bed']}, {r['diagnosis']}): "
                     f"NEWS2 {r['news2']} [{r['news2_risk']}], {r['news2_change_6h']:+d}/6h, "
                     f"qSOFA {r['qsofa']}")
    if not watch:
        lines.append("  none")
    crit = [d for d in gaps["overdue_doses"] if d["time_critical"]]
    lines.append(f"\nCare gaps: {len(gaps['overdue_observations'])} overdue obs, "
                 f"{len(gaps['overdue_doses'])} overdue doses ({len(crit)} time-critical)")
    lines.append(f"New alerts this review: {len(created)}")
    return "\n".join(lines)
