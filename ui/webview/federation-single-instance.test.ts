// federation.ts BOOTS a FederationManager when loaded in a browser (its module tail) — that is the
// design: it ships as its own script (esbuild.js entry), loaded once per chat/feed/fleet page, after
// the shim. Which makes IMPORTING it from any other webview module a bug, not a convenience: esbuild
// inlines a second copy — bootstrap included — into that pane's bundle, and the twin manager, hearing
// only the remote sockets it opens itself (the shim hands local frames to whichever manager claimed
// __rompFed last), emits REMOTE-ONLY merged feeds in alternation with the real manager's complete
// ones. Every local card blinked out and right back, remote cards persisting (the user 2026-07-31,
// screen recording; introduced by preview.ts importing hostOf/bareId from it). Those helpers live in
// host-prefix.ts, the side-effect-free module, precisely so nothing ever needs this import.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");

test("no webview module imports federation.ts — importing it boots a second FederationManager", () => {
  const offenders: string[] = [];
  for (const f of fs.readdirSync(UI)) {
    if (!f.endsWith(".ts") || f.endsWith(".test.ts") || f === "federation.ts") continue;
    const src = fs.readFileSync(path.join(UI, f), "utf8");
    if (/from "\.\/federation"/.test(src)) offenders.push(f);
  }
  assert.deepEqual(offenders, [], "these modules would bundle a second manager: " + offenders.join(", "));
});

test("hostOf/bareId live in host-prefix.ts (side-effect-free) and federation re-exports them", () => {
  const hp = fs.readFileSync(path.join(UI, "host-prefix.ts"), "utf8");
  assert.match(hp, /export function hostOf\(id: string\): string \{/);
  assert.match(hp, /export function bareId\(id: string\): string \{/);
  assert.doesNotMatch(hp, /import /, "host-prefix.ts must stay import-free — it is the safe home");
  const fed = fs.readFileSync(path.join(UI, "federation.ts"), "utf8");
  // hostDialLive (2026-09-10) is the third pure helper federation takes from the safe home: the host-down
  // notice's "is romp dialing right now" rule, over the row and socket state federation publishes
  assert.match(fed, /import \{ hostOf, bareId, hostDialLive \} from "\.\/host-prefix";/);
  assert.match(fed, /export \{ hostOf, bareId \};/);
  const pv = fs.readFileSync(path.join(UI, "preview.ts"), "utf8");
  assert.match(pv, /import \{ hostOf, bareId \} from "\.\/host-prefix";/);
});

test("the bootstrap this guards is still there — federation.ts self-starts on a browser page", () => {
  const fed = fs.readFileSync(path.join(UI, "federation.ts"), "utf8");
  assert.match(fed, /new FederationManager\(\)\.start\(\);/);
});
