// Headless browser check of index.html: rules run, Claude run (API mocked at
// the network layer), uploads, downloads, layout at desktop and phone width.
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");
import { mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const out = process.argv[2] || path.join(tmpdir(), "care-ui");
mkdirSync(out, { recursive: true });
const url = "file://" + path.resolve("index.html");
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const errors = [];
const fail = m => { console.error("FAIL:", m); process.exitCode = 1; };
const ok = (cond, m) => cond ? console.log("ok -", m) : fail(m);

async function newPage(viewport) {
  const page = await browser.newPage({ viewport, acceptDownloads: true });
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.abort());
  return page;
}

// 1. Rules-only cycle on the demo clinic
let page = await newPage({ width: 1360, height: 900 });
await page.goto(url);
await page.click("#openRunner");
await page.click("#startRun");
await page.waitForFunction(() => /done/.test(document.getElementById("runMessage").textContent));
ok(await page.textContent("#mDead") === "10", "demo: 10 dead-ends");
ok(await page.textContent("#mTriage") === "7", "demo: 7 messages triaged");
ok(await page.textContent("#mUrgent") === "2", "demo: 2 urgent escalations");
await page.screenshot({ path: `${out}/run-modal.png` });
await page.click("[data-close=runner]");
await page.screenshot({ path: `${out}/command.png`, fullPage: true });
await page.click(".case-row");
ok(await page.isVisible("#ticketModal.open"), "ticket modal opens");
await page.click("#resolveTicket");
ok((await page.textContent("#sOpen")) === "13", "14 tickets opened; resolving one leaves 13 open");

await page.click("[data-page=inbox]");
await page.click(".message-card");
ok(/Final level/.test(await page.textContent("#messageDetail")), "inbox detail shows triage");
await page.screenshot({ path: `${out}/inbox.png`, fullPage: true });
await page.click("[data-page=audit]");
ok((await page.$$(".audit-item")).length > 10, "audit timeline rendered");
await page.screenshot({ path: `${out}/audit.png`, fullPage: true });

// second cycle opens nothing
await page.click("#openRunner"); await page.click("#startRun");
await page.waitForFunction(() => /Cycle 2 done/.test(document.getElementById("runMessage").textContent));
ok(/0 new/.test(await page.textContent("#runMessage")), "second cycle: 0 new tickets");
await page.click("[data-close=runner]");

// downloads
await page.click("[data-page=data]");
await page.screenshot({ path: `${out}/data.png`, fullPage: true });
const [dl] = await Promise.all([page.waitForEvent("download"), page.click("[data-dl='0']")]);
ok(dl.suggestedFilename() === "task_tickets.json", "download task_tickets.json");

// 2. Upload CSV folder files + random clinic
const dir = path.join(out, "upload"); mkdirSync(dir, { recursive: true });
writeFileSync(`${dir}/patients.csv`, "id,name,age,condition,provider\nP001,Test Patient,40,PCOS,Dr. L. Chen\n");
writeFileSync(`${dir}/lab_orders.csv`, "id,patient_id,test,ordered_date,result_date,status\nL001,P001,Hormone panel,2026-09-20,,pending\n");
for (const t of ["prescriptions", "appointments", "messages"]) writeFileSync(`${dir}/${t}.csv`, { prescriptions: "id,patient_id,drug,ordered_date,filled_date,status\n", appointments: "id,patient_id,date,type,note,status\n", messages: "id,patient_id,received_date,read,subject,body\n" }[t]);
await page.setInputFiles("#fileInput", ["patients", "lab_orders", "prescriptions", "appointments", "messages"].map(t => `${dir}/${t}.csv`));
await page.waitForFunction(() => /Loaded 5 file/.test(document.getElementById("dataStatus").textContent));
await page.click("#openRunner"); await page.click("#startRun");
await page.waitForFunction(() => /done/.test(document.getElementById("runMessage").textContent));
ok(await page.textContent("#mDead") === "1", "uploaded minimal CSV clinic: 1 stalled lab");
await page.click("[data-close=runner]");
await page.click("[data-page=data]");
await page.click("#genRandom");
await page.click("#openRunner"); await page.click("#startRun");
await page.waitForFunction(() => /done/.test(document.getElementById("runMessage").textContent));
ok(+(await page.textContent("#mDead")) > 0, "random clinic produces findings");
await page.close();

// 3. Claude mode with the API mocked; model always says "routine"
page = await newPage({ width: 1360, height: 900 });
let apiCalls = 0, sawHeaders = null;
await page.route("https://api.anthropic.com/v1/messages", async route => {
  apiCalls++; const req = route.request(); sawHeaders = req.headers(); const body = req.postDataJSON();
  const isBrief = body.system.includes("cycle brief");
  const payload = isBrief ? { headline: "Claude brief headline.", workload: "W.", top_risks: ["Risk"], next_actions: ["Act"] }
    : { bucket: "routine", confidence: 0.91, signal_phrases: ["question"], rationale: "Mock says routine.", suggested_next_step: "Reply." };
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ stop_reason: "end_turn", model: body.model, content: [{ type: "text", text: JSON.stringify(payload) }] }) });
});
await page.goto(url);
await page.click("[data-page=data]");
await page.fill("#apiKey", "sk-ant-test-key");
await page.click("#saveKey");
ok(await page.inputValue("#apiKey") === "", "key field cleared after saving (held in memory only)");
await page.click("#openRunner"); await page.click("#startRun");
await page.waitForFunction(() => /done/.test(document.getElementById("runMessage").textContent));
ok(apiCalls === 8, `Claude called for 7 messages + brief (${apiCalls})`);
ok(sawHeaders["x-api-key"] === "sk-ant-test-key" && sawHeaders["anthropic-dangerous-direct-browser-access"] === "true", "browser request headers");
ok(await page.textContent("#mUrgent") === "2", "guardrail still escalates 2 urgent messages despite Claude saying routine");
ok(/2 raised by the safety override/.test(await page.textContent("#mUrgentSub")), "overrides counted");
await page.click("[data-close=runner]");
ok(/written by Claude/.test(await page.textContent("#briefMeta")), "brief written by Claude");
await page.click("[data-page=inbox]");
await page.click("[data-filter=override]");
await page.click(".message-card");
await page.screenshot({ path: `${out}/inbox-claude.png`, fullPage: true });
ok(/Routine · confidence 0.91/.test(await page.textContent("#messageDetail")), "detail shows Claude's suggestion vs final level");
await page.click("[data-page=data]");
await page.click("#clearKey");
ok(/No key set/.test(await page.textContent("#keyStatus")), "clear key");
await page.close();

// 4. Bad key -> 401 -> fallback, cycle completes
page = await newPage({ width: 1360, height: 900 });
await page.route("https://api.anthropic.com/v1/messages", r => r.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ type: "error", error: { type: "authentication_error", message: "invalid x-api-key" } }) }));
await page.goto(url);
await page.click("[data-page=data]"); await page.fill("#apiKey", "sk-bad"); await page.click("#saveKey");
await page.click("#openRunner"); await page.click("#startRun");
await page.waitForFunction(() => /done/.test(document.getElementById("runMessage").textContent));
ok(/fallback/i.test(await page.textContent("#runMessage")) && await page.textContent("#mUrgent") === "2", "bad key falls back to rules");
await page.close();

// 5. Phone width
page = await newPage({ width: 390, height: 844 });
await page.goto(url);
await page.click("#openRunner"); await page.click("#startRun");
await page.waitForFunction(() => /done/.test(document.getElementById("runMessage").textContent));
await page.click("[data-close=runner]");
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
ok(overflow <= 0, `no horizontal scroll at 390px (${overflow})`);
await page.screenshot({ path: `${out}/mobile.png`, fullPage: true });
await page.click("[data-page=data]");
ok(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) <= 0, "data page fits at 390px");
await page.screenshot({ path: `${out}/mobile-data.png`, fullPage: true });

ok(errors.length === 0, `no page errors ${errors.join(" | ")}`);
await browser.close();
