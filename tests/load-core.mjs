// Load the agent core straight out of index.html, so tests exercise the
// exact code the browser runs.
import { readFileSync } from "node:fs";

export function loadCore() {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const m = /<script id="agent-core">([\s\S]*?)<\/script>/.exec(html);
  if (!m) throw new Error("agent-core script not found in index.html");
  new Function(m[1])();
  return globalThis.CareAgent;
}
