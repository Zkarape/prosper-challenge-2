import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const projectRoot = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the Prosper Agent Studio shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Prosper Agent Studio<\/title>/i);
  assert.match(html, /Scheduling agent/);
  assert.match(html, /Agent graph/);
  assert.match(html, /Engine logic/);
  assert.match(html, /Evaluations/);
  assert.match(html, /System logs/);
  assert.doesNotMatch(html, /Scale test/);
  assert.match(html, /Loading voice test/);
  assert.match(html, /Context usage/);
  assert.match(html, /Session total/);
  assert.doesNotMatch(html, /Text conversation|See what happened underneath|Pipeline inspector/i);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("ships product metadata and removes the disposable starter", async () => {
  const [layout, page, graphEditor, voicePanel, evaluationsPanel, enginePanel, themeControl, styles, packageJson] = await Promise.all([
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/agent-graph-editor.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/voice-call-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/evaluations-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/engine-logic-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/theme-control.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(layout, /Prosper Agent Studio/);
  assert.match(layout, /og\.png/);
  assert.match(graphEditor, /Add a step/i);
  assert.match(graphEditor, /Save workflow/i);
  assert.match(graphEditor, /conversation.*subagent.*tool.*decision.*handoff.*end/is);
  assert.match(graphEditor, /\/api\/agent/);
  assert.match(page, /NEXT_PUBLIC_VOICE_AGENT_URL/);
  assert.match(page, /NEXT_PUBLIC_SCHEDULING_API_URL/);
  assert.doesNotMatch(page, /window\.open/);
  assert.match(voicePanel, /aria-label="Start voice call"/);
  assert.match(voicePanel, /RTVIEvent\.UserTranscript/);
  assert.match(voicePanel, /patient-live/);
  assert.doesNotMatch(voicePanel, /role="dialog"|voice-call-backdrop/);
  assert.match(evaluationsPanel, /Prove the scheduling pipeline/);
  assert.match(evaluationsPanel, /Run selected case/);
  assert.match(evaluationsPanel, /Run all 40/);
  assert.match(evaluationsPanel, /\/api\/evaluations\/dataset/);
  assert.match(evaluationsPanel, /\/api\/evaluations\/runs/);
  assert.doesNotMatch(evaluationsPanel, /response_requirements|Safety assertions/);
  assert.match(enginePanel, /One request through the real system/);
  assert.match(enginePanel, /SchedulingEngine\.evaluate/);
  assert.match(enginePanel, /The LLM has no booking authority/);
  assert.match(enginePanel, /graph does not yet compile into those rules/i);
  assert.match(themeControl, /light.*dark.*system/is);
  assert.match(layout, /prosper-theme/);
  assert.match(page, /ThemeControl/);
  assert.doesNotMatch(page, /ScalabilityPanel|Scale test/);
  assert.match(styles, /--purple:\s*#6348ff/i);
  assert.match(styles, /--pink:\s*#e25dcc/i);
  assert.match(packageJson, /"name": "prosper-agent-studio"/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  await access(new URL("../public/og.png", import.meta.url));
  await assert.rejects(access(new URL("../app/_sites-preview", projectRoot)));
});
