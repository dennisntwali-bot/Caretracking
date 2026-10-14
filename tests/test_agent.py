from types import SimpleNamespace as NS

from caretracking.agent import ClaudeCareAgent, RuleBasedCareAgent
from caretracking.hospital import Hospital


class FakeClient:
    """Plays back scripted model turns and records the requests it receives."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.turns.pop(0)


def tool_use(id_, name, **inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


def test_claude_agent_loop_executes_tools_and_returns_summary():
    h = Hospital.generate(seed=4)
    pid = h.active_patients()[0].patient_id
    client = FakeClient([
        NS(stop_reason="tool_use", content=[
            tool_use("t1", "get_ward_overview"), tool_use("t2", "list_alerts")]),
        NS(stop_reason="tool_use", content=[
            tool_use("t3", "raise_alert", patient_id=pid, category="deterioration",
                     severity="urgent", reason="NEWS2 6", recommended_action="Review in 1h"),
            tool_use("t4", "get_patient_details", patient_id="P0000")]),
        NS(stop_reason="end_turn", content=[NS(type="text", text="Handover: one urgent patient.")]),
    ])
    report = ClaudeCareAgent(client=client).review(h)

    assert report.summary == "Handover: one urgent patient."
    assert report.tool_calls == 4
    assert [a["patient_id"] for a in report.alerts_created] == [pid]
    assert h.alerts[0].raised_by == "claude"
    # Both parallel tool results go back in a single user message.
    results = client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    # Unknown patient comes back as a tool error, not an exception.
    err = client.requests[2]["messages"][-1]["content"][1]
    assert err["is_error"] and "P0000" in err["content"]
    assert client.requests[0]["model"] == "claude-opus-5-5"


def test_claude_agent_handles_refusal():
    h = Hospital.generate(seed=4)
    client = FakeClient([NS(stop_reason="refusal", content=[])])
    assert "declined" in ClaudeCareAgent(client=client).review(h).summary


def test_rule_agent_flags_high_news2_and_does_not_repeat():
    h = Hospital.generate(seed=7)
    agent = RuleBasedCareAgent()
    first = agent.review(h)
    assert any(a["severity"] == "critical" for a in first.alerts_created)
    assert agent.review(h).alerts_created == []
