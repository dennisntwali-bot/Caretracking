import test from "node:test";
import assert from "node:assert/strict";
import { loadCore } from "./load-core.mjs";
import { groundTruth } from "./ground-truth.mjs";

const C = loadCore();
const TODAY = C.DEFAULT_TODAY;
const clone = x => JSON.parse(JSON.stringify(x));
const state = data => ({ data: clone(data), tickets: [], audit: [] });

// Mock "models" ---------------------------------------------------------
const wrongModel = { name: "mock", model: "always-routine",
  triage: async () => ({ bucket: "routine", confidence: 0.99, signal_phrases: [], rationale: "Looks routine.", suggested_next_step: "Reply later." }),
  brief: async () => ({ headline: "h", workload: "w", top_risks: [], next_actions: [] }) };
const garbageModel = { name: "mock", model: "garbage", triage: async () => ({ bucket: "maybe", confidence: 7 }), brief: async () => "nope" };
const throwingModel = { name: "mock", model: "throws", triage: async () => { throw new Error("boom"); }, brief: async () => { throw new Error("boom"); } };

function stubFetch(status, body) {
  const calls = [];
  const f = async (url, init) => { calls.push({ url, init, body: JSON.parse(init.body) });
    return { ok: status >= 200 && status < 300, status, json: async () => (typeof body === "function" ? body(calls.at(-1)) : body) }; };
  f.calls = calls;
  return f;
}

// 1. Detector precision and recall = 1.0 ---------------------------------
test("detector matches the independent ground truth on 10 random clinics (precision = recall = 1.0)", () => {
  const kinds = new Set();
  for (let seed = 1; seed <= 10; seed++) {
    const { data } = C.generateClinic(seed, TODAY);
    const norm = C.normalizeDataset(data).data;
    const got = new Set(C.detectDeadEnds(norm, TODAY).map(f => f.finding_id));
    const want = groundTruth(norm, TODAY);
    const tp = [...got].filter(x => want.has(x)).length;
    const precision = got.size ? tp / got.size : 1, recall = want.size ? tp / want.size : 1;
    assert.equal(precision, 1, `seed ${seed}: false positives ${[...got].filter(x => !want.has(x))}`);
    assert.equal(recall, 1, `seed ${seed}: missed ${[...want].filter(x => !got.has(x))}`);
    for (const id of got) kinds.add(id.split(":")[0]);
  }
  assert.deepEqual([...kinds].sort(), ["overdue-followup", "stalled-lab", "unanswered-message", "unfilled-rx"]);
});

test("demo clinic findings match ground truth and hand-checked list", () => {
  const d = C.demoClinic();
  const got = C.detectDeadEnds(d, TODAY).map(f => f.finding_id).sort();
  assert.deepEqual(new Set(got), groundTruth(d, TODAY));
  assert.deepEqual(got, ["overdue-followup:A001", "overdue-followup:A003", "stalled-lab:L001", "stalled-lab:L002", "stalled-lab:L005",
    "unanswered-message:M002", "unanswered-message:M004", "unanswered-message:M007", "unfilled-rx:R001", "unfilled-rx:R002"]);
});

test("boundaries: exactly 7 days lab / 3 days rx / 1 day message are not flagged; one more day is", () => {
  const data = C.normalizeDataset({
    patients: [{ id: "P1", name: "A", age: 30, condition: "x", provider: "y" }],
    lab_orders: [{ id: "L7", patient_id: "P1", test: "t", ordered_date: "2026-09-24", result_date: "", status: "pending" },
                 { id: "L8", patient_id: "P1", test: "t", ordered_date: "2026-09-23", result_date: "", status: "pending" }],
    prescriptions: [{ id: "R3", patient_id: "P1", drug: "d", ordered_date: "2026-09-28", filled_date: "", status: "ordered" },
                    { id: "R4", patient_id: "P1", drug: "d", ordered_date: "2026-09-27", filled_date: "", status: "ordered" },
                    { id: "R8", patient_id: "P1", drug: "d", ordered_date: "2026-09-23", filled_date: "", status: "ordered" }],
    appointments: [], messages: [{ id: "M1", patient_id: "P1", received_date: "2026-09-30", read: false, subject: "", body: "" },
                                 { id: "M2", patient_id: "P1", received_date: "2026-09-29", read: "false", subject: "", body: "" }],
  }).data;
  const f = Object.fromEntries(C.detectDeadEnds(data, TODAY).map(x => [x.finding_id, x.severity]));
  assert.deepEqual(f, { "stalled-lab:L8": "high", "unfilled-rx:R4": "medium", "unfilled-rx:R8": "high", "unanswered-message:M2": "medium" });
});

// 2. Urgent recall = 1.0 even when the model is wrong ---------------------
test("every urgent message is escalated even when the model always answers 'routine'", async () => {
  let urgentSeen = 0;
  for (let seed = 1; seed <= 10; seed++) {
    const { data, truth } = C.generateClinic(seed, TODAY);
    const s = state(data);
    const unreadUrgent = s.data.messages.filter(m => !m.read && truth.message_bucket[m.id] === "urgent").map(m => m.id);
    await C.runCycle(s, { today: TODAY, model: wrongModel });
    const byId = Object.fromEntries(s.lastTriage.map(t => [t.message_id, t]));
    for (const id of unreadUrgent) {
      urgentSeen++;
      assert.equal(byId[id].bucket, "urgent", `seed ${seed} ${id}`);
      assert.equal(byId[id].ai_bucket, "routine");
      assert.equal(byId[id].override, true);
      const ticket = s.tickets.find(t => t.record_id === id);
      assert.ok(ticket.escalated && ticket.team === "clinical_team", `seed ${seed} ${id} ticket`);
    }
    assert.ok(s.audit.some(e => e.event === "safety_override") || !unreadUrgent.length);
  }
  assert.ok(urgentSeen >= 10, `only ${urgentSeen} urgent messages exercised`);
});

test("every urgent template is caught by the deterministic scan; no non-urgent template is", () => {
  for (let seed = 1; seed <= 30; seed++) {
    const { data, truth } = C.generateClinic(seed, TODAY);
    for (const m of data.messages) {
      const hit = C.urgentSignals(`${m.subject}\n${m.body}`).length > 0;
      assert.equal(hit, truth.message_bucket[m.id] === "urgent", `${m.subject}: ${m.body}`);
    }
  }
});

test("the model may escalate but never de-escalate", () => {
  const msg = { id: "M1", subject: "Refill", body: "Can I get a refill?" };
  const up = C.applyGuardrail(msg, { bucket: "urgent", confidence: 0.6, signal_phrases: [], rationale: "r", suggested_next_step: "s" }, "claude");
  assert.equal(up.bucket, "urgent"); assert.equal(up.override, false); assert.equal(up.escalated, true);
  const bleed = { id: "M2", subject: "hi", body: "There is heavy bleeding and I feel faint" };
  for (const b of ["administrative", "routine", "clinician_review"]) {
    const r = C.applyGuardrail(bleed, { bucket: b, confidence: 1, signal_phrases: [], rationale: "r", suggested_next_step: "s" }, "claude");
    assert.equal(r.bucket, "urgent"); assert.equal(r.override, true); assert.equal(r.team, "clinical_team");
  }
});

test("routing: admin/routine -> admin_team, clinician_review/urgent -> clinical_team", async () => {
  const s = state(C.demoClinic());
  await C.runCycle(s, { today: TODAY });
  for (const t of s.lastTriage) assert.equal(t.team, ["administrative", "routine"].includes(t.bucket) ? "admin_team" : "clinical_team");
  assert.ok(s.data.messages.every(m => m.read), "triaged messages marked read");
});

// 3. Idempotency ---------------------------------------------------------
test("a second cycle opens 0 new tickets (rules and Claude mock)", async () => {
  for (const model of [null, wrongModel]) {
    for (let seed = 1; seed <= 5; seed++) {
      const s = state(C.generateClinic(seed, TODAY).data);
      const r1 = await C.runCycle(s, { today: TODAY, model });
      const r2 = await C.runCycle(s, { today: TODAY, model });
      assert.ok(r1.tickets.opened > 0);
      assert.equal(r2.tickets.opened, 0);
      const ids = s.tickets.map(t => t.finding_id);
      assert.equal(new Set(ids).size, ids.length, "no duplicate finding ids");
    }
  }
});

test("tickets carried over from a previous session are reused, not duplicated", async () => {
  const s1 = state(C.demoClinic());
  await C.runCycle(s1, { today: TODAY });
  const s2 = { data: C.demoClinic(), tickets: clone(s1.tickets), audit: [] };
  const r = await C.runCycle(s2, { today: TODAY });
  assert.equal(r.tickets.opened, 0);
  assert.equal(r.tickets.reused, r.counts.dead_ends + r.triage_summary.total - r.counts.by_kind.unanswered_message);
});

test("a resolved ticket whose problem persists is reopened as a new ticket", async () => {
  const s = state(C.demoClinic());
  await C.runCycle(s, { today: TODAY });
  const t = s.tickets.find(x => x.finding_id === "stalled-lab:L001");
  t.status = "resolved";
  const r = await C.runCycle(s, { today: TODAY });
  assert.equal(r.tickets.opened, 1);
  assert.equal(s.tickets.filter(x => x.finding_id === "stalled-lab:L001" && x.status === "open").length, 1);
});

// 4. JSON vs CSV ---------------------------------------------------------
function asFiles(data, fmt) {
  return Object.keys(C.TABLES).map(t => ({ name: `${t}.${fmt}`, text: fmt === "csv" ? C.toCSV(data[t], C.TABLES[t]) : JSON.stringify(data[t]) }));
}
const strip = s => ({ tickets: s.tickets.map(({ opened_at, ...t }) => t), triage: s.lastTriage, findings: s.lastFindings,
  report: (({ generated_at, ...r }) => r)(s.lastReport) });

test("identical results for the same data as JSON vs CSV", async () => {
  for (let seed = 1; seed <= 10; seed++) {
    const { data } = C.generateClinic(seed, TODAY);
    data.messages.push({ id: "MX", patient_id: "P001", received_date: "2026-09-20", read: false, subject: 'Commas, "quotes"', body: "Line one,\nline two with heavy bleeding" });
    const j = C.loadFiles(asFiles(data, "json")), c = C.loadFiles(asFiles(data, "csv"));
    assert.deepEqual(c.data, j.data, `seed ${seed} tables`);
    const sj = { data: j.data, tickets: [], audit: [] }, sc = { data: c.data, tickets: [], audit: [] };
    await C.runCycle(sj, { today: TODAY, model: wrongModel });
    await C.runCycle(sc, { today: TODAY, model: wrongModel });
    assert.deepEqual(strip(sc), strip(sj), `seed ${seed} results`);
  }
});

test("JSON wins when both formats exist; a bundled clinic.json also loads", () => {
  const d = C.demoClinic();
  const res = C.loadFiles([{ name: "patients.csv", text: "id,name\nZ9,Zed\n" }, { name: "patients.json", text: JSON.stringify(d.patients) }]);
  assert.equal(res.data.patients.length, 10);
  const bundle = C.loadFiles([{ name: "clinic.json", text: JSON.stringify(d) }]);
  assert.equal(bundle.data.messages.length, d.messages.length);
});

// 5. Empty and healthy datasets -----------------------------------------
test("empty dataset: zero findings, no crash", async () => {
  for (const files of [[], Object.keys(C.TABLES).map(t => ({ name: `${t}.csv`, text: C.TABLES[t].join(",") + "\n" }))]) {
    const s = { data: C.loadFiles(files).data, tickets: [], audit: [] };
    const r = await C.runCycle(s, { today: TODAY, model: wrongModel });
    assert.equal(r.counts.dead_ends, 0); assert.equal(r.triage_summary.total, 0); assert.equal(r.tickets.opened, 0);
  }
});

test("all-healthy dataset: zero findings", async () => {
  const d = C.generateClinic(3, TODAY).data;
  d.lab_orders.forEach(l => { l.status = "resulted"; l.result_date = l.ordered_date; });
  d.prescriptions.forEach(r => { r.status = "filled"; r.filled_date = r.ordered_date; });
  d.appointments.forEach(a => { a.note = "Routine visit."; });
  d.messages.forEach(m => { m.read = true; });
  const s = state(d);
  const r = await C.runCycle(s, { today: TODAY });
  assert.equal(r.counts.dead_ends, 0); assert.equal(r.tickets.opened, 0);
  assert.equal(groundTruth(s.data, TODAY).size, 0);
});

test("messy real-world data does not crash the cycle", async () => {
  const res = C.loadFiles([
    { name: "patients.csv", text: "﻿ID,Name,Age,Condition,Provider,extra\r\nP1,  Ann  ,forty,PCOS,Dr X,zzz\r\nP1,Dup,30,x,y,\r\n,NoId,1,x,y,\r\n" },
    { name: "lab_orders.json", text: JSON.stringify([{ id: "L1", patient_id: "P404", test: "t", ordered_date: "09/01/2026", status: "Pending" }, { id: 5, patient_id: "P1", ordered_date: "2026-02-31", status: "pending", result_date: null }]) },
    { name: "messages.json", text: "{not json" },
    { name: "appointments.csv", text: "id,patient_id,date,status,note\nA1,P1,2026-08-01,COMPLETED,\"Follow up in 2 weeks, then 6 months\"\n" },
  ]);
  assert.ok(res.warnings.length >= 5);
  const s = { data: res.data, tickets: [], audit: [] };
  const r = await C.runCycle(s, { today: TODAY, warnings: res.warnings });
  assert.deepEqual(s.lastFindings.map(f => f.finding_id), ["overdue-followup:A1"]);
  assert.equal(r.warnings.length, res.warnings.length);
});

// 6. Missing / bad API key ----------------------------------------------
test("no key: rules-only cycle completes", async () => {
  const s = state(C.demoClinic());
  const r = await C.runCycle(s, { today: TODAY, model: null });
  assert.equal(r.reasoning.mode, "rules"); assert.equal(r.triage_summary.by_rules, 7); assert.equal(r.brief.source, "rules");
  assert.equal(r.triage_summary.urgent, 2);
});

test("bad key (401): clean fallback to rules, Claude disabled for the cycle, cycle completes", async () => {
  const f = stubFetch(401, { type: "error", error: { type: "authentication_error", message: "invalid x-api-key" } });
  const s = state(C.demoClinic());
  const r = await C.runCycle(s, { today: TODAY, model: C.claudeClient({ apiKey: "sk-bad", fetchImpl: f }) });
  assert.equal(r.triage_summary.by_rules, 7); assert.equal(r.triage_summary.urgent, 2);
  assert.equal(r.reasoning.ai_available_at_end, false); assert.match(r.reasoning.fallback_reason, /401/);
  assert.ok(f.calls.length <= 4, `stopped calling after auth failure (${f.calls.length} calls)`);
  assert.ok(s.audit.some(e => e.event === "ai_disabled"));
});

test("network errors, invalid JSON, schema violations, refusals all fall back per message", async () => {
  const cases = [
    async () => { throw new TypeError("Failed to fetch"); },
    stubFetch(200, { stop_reason: "end_turn", content: [{ type: "text", text: "not json" }] }),
    stubFetch(200, { stop_reason: "end_turn", content: [{ type: "text", text: '{"bucket":"spam","confidence":2}' }] }),
    stubFetch(200, { stop_reason: "refusal", content: [] }),
    stubFetch(529, { error: { message: "overloaded" } }),
  ];
  for (const fetchImpl of cases) {
    const s = state(C.demoClinic());
    const r = await C.runCycle(s, { today: TODAY, model: C.claudeClient({ apiKey: "k", fetchImpl }) });
    assert.equal(r.triage_summary.total, 7); assert.equal(r.triage_summary.by_rules, 7); assert.equal(r.triage_summary.urgent, 2);
  }
  for (const model of [garbageModel, throwingModel]) {
    const r = await C.runCycle(state(C.demoClinic()), { today: TODAY, model });
    assert.equal(r.triage_summary.by_rules, 7); assert.equal(r.brief.source, "rules");
  }
});

test("Claude request shape: model, structured output schema, headers, fallbacks", async () => {
  const f = stubFetch(200, ({ body }) => ({ stop_reason: "end_turn", model: body.model, content: [
    { type: "thinking", thinking: "" },
    { type: "text", text: JSON.stringify(body.system.includes("cycle brief")
      ? { headline: "H", workload: "W", top_risks: ["r"], next_actions: ["a"] }
      : { bucket: "clinician_review", confidence: 0.8, signal_phrases: ["x"], rationale: "r", suggested_next_step: "s" }) }] }));
  const s = state(C.demoClinic());
  const r = await C.runCycle(s, { today: TODAY, model: C.claudeClient({ apiKey: "sk-test", fetchImpl: f }) });
  const call = f.calls[0];
  assert.equal(call.url, "https://api.anthropic.com/v1/messages");
  assert.equal(call.init.headers["x-api-key"], "sk-test");
  assert.equal(call.init.headers["anthropic-version"], "2023-06-01");
  assert.equal(call.init.headers["anthropic-dangerous-direct-browser-access"], "true");
  assert.equal(call.body.model, "claude-opus-5-5");
  assert.equal(call.body.output_config.format.type, "json_schema");
  assert.deepEqual(call.body.output_config.format.schema.properties.bucket.enum, C.BUCKETS);
  assert.equal(call.body.fallbacks, "default");
  assert.equal(r.triage_summary.by_claude, 7);
  assert.equal(r.triage_summary.urgent, 2, "guardrail still raises the two urgent messages");
  assert.equal(r.triage_summary.overrides, 2);
  assert.equal(r.brief.source, "claude"); assert.equal(r.brief.headline, "H");
});

test("400 about fallbacks: retries once without the fallbacks parameter", async () => {
  let n = 0;
  const f = async (url, init) => { n++; const b = JSON.parse(init.body);
    if (b.fallbacks) return { ok: false, status: 400, json: async () => ({ error: { message: "fallbacks: not enabled for this organization" } }) };
    return { ok: true, status: 200, json: async () => ({ stop_reason: "end_turn", content: [{ type: "text", text: '{"bucket":"routine","confidence":0.7,"signal_phrases":[],"rationale":"r","suggested_next_step":"s"}' }] }) }; };
  const client = C.claudeClient({ apiKey: "k", fetchImpl: f });
  assert.equal((await client.triage({ subject: "a", body: "b" }, null)).bucket, "routine");
  assert.equal((await client.triage({ subject: "a", body: "b" }, null)).bucket, "routine");
  assert.equal(n, 3);
});

// Outputs ---------------------------------------------------------------
test("report and audit log have the required content", async () => {
  const s = state(C.demoClinic());
  const r = await C.runCycle(s, { today: TODAY });
  for (const k of ["counts", "severity_breakdown", "triage_summary", "tickets", "brief", "staff_minutes_saved_estimate"]) assert.ok(k in r, k);
  assert.deepEqual(new Set(s.audit.map(e => e.phase)), new Set(["perceive", "detect", "reason", "act", "brief", "log"]));
  for (const e of s.audit) { assert.ok(!isNaN(Date.parse(e.ts))); assert.equal(typeof e.message, "string"); }
  assert.ok(s.tickets.every(t => /^(stalled-lab|unfilled-rx|overdue-followup|unanswered-message|new-message):/.test(t.finding_id)));
});

test("works with a postMessage-style fetch relay that cannot clone AbortSignal", async () => {
  const relay = async (url, init) => {
    if (init.signal !== undefined) throw new DOMException("AbortSignal object could not be cloned.", "DataCloneError");
    return { ok: true, status: 200, json: async () => ({ stop_reason: "end_turn", content: [{ type: "text", text: '{"bucket":"routine","confidence":0.8,"signal_phrases":[],"rationale":"r","suggested_next_step":"s"}' }] }) };
  };
  const r = await C.claudeClient({ apiKey: "k", fetchImpl: relay }).triage({ subject: "Refill", body: "refill please" }, null);
  assert.equal(r.bucket, "routine");
});

test("a hung request times out cleanly and the cycle falls back", async () => {
  const hang = () => new Promise(() => {});
  const s = state(C.demoClinic());
  const r = await C.runCycle(s, { today: TODAY, model: C.claudeClient({ apiKey: "k", fetchImpl: hang, timeoutMs: 30 }) });
  assert.equal(r.triage_summary.by_rules, 7);
  assert.ok(s.audit.some(e => /timed out/.test(e.message)));
});
