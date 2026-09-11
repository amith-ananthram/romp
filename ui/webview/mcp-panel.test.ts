// `/mcp` in a romp session used to be a dead end (the user 2026-08-05): the CLI's own panel is an
// interactive TUI an SDK-driven session cannot render, so it replied "use a terminal". The SDK exposes
// the same facts and repairs as designed control requests — get_mcp_status, toggle_mcp_server,
// reconnect_mcp_server — so romp intercepts the command and renders its own panel from them. Fails
// LOUDLY: a disconnected-CLI session or a disconnected CLI is NAMED, never an empty list that reads as
// "no servers configured". No jsdom harness → source pins (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const BACKEND = fs.readFileSync(path.join(ROOT, "kernel", "sdk_backend.py"), "utf8");
const ABC = fs.readFileSync(path.join(ROOT, "kernel", "session_backend.py"), "utf8");

test("the composer intercepts /mcp before it can reach the CLI", () => {
  assert.match(RENDER, /if \(\/\^\\\/mcp\\s\*\$\/\.test\(text\)\) \{/);
  assert.match(RENDER, /openMcpPanel\(sid\);/);
  // the box clears like any consumed command, and the draft with it
  assert.match(RENDER, /clearBox\(\);\s*\n\s*drafts\.delete\(sid\); persistDrafts\(\);\s*\n\s*openMcpPanel\(sid\);/);   // clearBox: the composer's one clear path, so the menus see the box go empty
});

test("the panel reads the SDK's designed control requests through the kernel", () => {
  // backend: status + the two repairs, each bounded and loud on failure
  assert.ok(BACKEND.includes("def mcp_status(self):"));
  assert.ok(BACKEND.includes("self.client.get_mcp_status()"));
  assert.ok(BACKEND.includes("self.client.toggle_mcp_server(name, enabled)"));
  assert.ok(BACKEND.includes("self.client.reconnect_mcp_server(name)"));
  assert.ok(BACKEND.includes("asyncio.run_coroutine_threadsafe(coro_fn(), self.loop)"),
    "runs on the session's own loop, bounded wait on the kernel thread");
  // kernel: a GET for the list, a WS op for the actions
  assert.ok(KERNEL.includes('if p == "/mcp":'));
  assert.ok(KERNEL.includes('json.dumps({"servers": servers, "error": err})'));
  assert.ok(KERNEL.includes('elif t == "mcpAction" and msg.get("server"):'));
  assert.ok(KERNEL.includes('"mcpAction"'), "routes by session id like every session op (ID_OPS)");
  // disconnected-CLI says so explicitly rather than returning a misleading empty list
  assert.ok(ABC.includes("def mcp_status(self, sid: str):"));
  assert.ok(ABC.includes("no running backend owns this one"));
});

test("every action refetches — the panel never shows an optimistic row", () => {
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "mcpAction", id: sid, server: srv\.name, action, enabled \}\);/);
  assert.match(RENDER, /b\.disabled = true; b\.textContent = busy;/);   // acknowledged before the round-trip
  assert.match(RENDER, /if \(body && mcpPanelSid\) loadMcpPanel\(mcpPanelSid, body\);/);
  assert.match(RENDER, /if \(m\.error\) warnToast\("MCP " \+ \(m\.server \|\| "server"\) \+ ": " \+ m\.error\);/);
});

test("a refusal is named, and the panel borrows the confirm overlay's chrome", () => {
  assert.match(RENDER, /if \(d\?\.error\) \{/);
  assert.match(RENDER, /"No MCP servers configured for this session\."/);
  assert.match(RENDER, /el\("div", "picker-overlay confirm-overlay"\); overlay\.id = "mcp-panel";/);
  // layout + status tint only — the fonts come from .confirm-detail (the consistent-fonts rule)
  assert.match(CSS, /\.mcp-list \{ display: flex; flex-direction: column;/);
  assert.doesNotMatch(CSS, /\.mcp-(row|name|status|meta)[^{]*\{[^}]*font-size/);
});
