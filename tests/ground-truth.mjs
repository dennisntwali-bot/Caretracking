// Independent reference implementation of the four dead-end rules, written
// from DATA_FORMAT.md without reusing any agent code. Used only by tests.

const DAY = 24 * 60 * 60 * 1000;
const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];

function day(s) {
  if (typeof s !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(s.trim())) return NaN;
  const d = new Date(s.trim() + "T00:00:00Z");
  return d.toISOString().slice(0, 10) === s.trim() ? d.getTime() / DAY : NaN;
}

function weeksFromNote(note) {
  const tokens = String(note || "").toLowerCase().replace(/follow-up|followup|follow up/g, "FOLLOWUP").split(/[^a-z0-9A-Z]+/);
  for (let i = 0; i + 3 < tokens.length; i++) {
    if (tokens[i] === "FOLLOWUP" && tokens[i + 1] === "in" && (tokens[i + 3] === "week" || tokens[i + 3] === "weeks")) {
      const t = tokens[i + 2];
      const n = /^[0-9]+$/.test(t) ? parseInt(t, 10) : WORDS.indexOf(t);
      if (n > 0) return n;
    }
  }
  return 0;
}

export function groundTruth(data, todayIso) {
  const today = day(todayIso);
  const ids = new Set();
  for (const l of data.lab_orders) {
    if (l.status === "pending" && !l.result_date && today - day(l.ordered_date) > 7) ids.add("stalled-lab:" + l.id);
  }
  for (const r of data.prescriptions) {
    if (r.status === "ordered" && !r.filled_date && today - day(r.ordered_date) > 3) ids.add("unfilled-rx:" + r.id);
  }
  for (const a of data.appointments) {
    if (a.status !== "completed") continue;
    const w = weeksFromNote(a.note);
    if (!w) continue;
    const due = day(a.date) + 7 * w;
    if (!(due < today)) continue;
    const future = data.appointments.filter(x => x.patient_id === a.patient_id && x.status === "scheduled" && day(x.date) >= today);
    if (future.length === 0) ids.add("overdue-followup:" + a.id);
  }
  for (const m of data.messages) {
    const unread = m.read === false || String(m.read).toLowerCase() === "false";
    if (unread && today - day(m.received_date) > 1) ids.add("unanswered-message:" + m.id);
  }
  return ids;
}
