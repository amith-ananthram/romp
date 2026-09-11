// A permission-mode selector sits to the LEFT of the model name in the statusline, a badge+dropdown
// like the model/effort pickers (the user 2026-06-16). There's no /mode slash command, so the host
// sets it by shift+tab cycling; the webview just posts setMode like setModel/setEffort. Source-level pin.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("MetaKind includes mode; the status carries it; there's a MODE_CHOICES menu", () => {
  assert.match(RENDER, /type MetaKind = "mode" \| "model" \| "effort"/);
  assert.match(RENDER, /mode\?: string;/);                       // Status.mode
  assert.match(RENDER, /const MODE_CHOICES/);
});

test("the mode button renders FIRST (left of model) and the picker posts setMode", () => {
  assert.match(RENDER, /if \(st\.mode\) meta\.appendChild\(metaButton\("mode", prettyMode\(st\.mode\), forSid\)\);\s*\n\s*if \(st\.model\)/);   // sid-scoped for the popover statusline (2026-08-25)
  assert.match(RENDER, /"setMode"/);
  assert.match(RENDER, /const META_CHOICES: Record<MetaKind/);   // model/effort + mode share the menu path
});

test("Bypass is offered, and offered ONLY on an SDK session", () => {
  // The SDK sets the mode outright (set_permission_mode), so bypassPermissions is reachable there; a
  // Codex session has its own vocabulary and cannot express it. Listing it there would be a menu entry
  // that silently does nothing.
  assert.match(RENDER, /value: "bypassPermissions", sdkOnly: true/);
  assert.match(RENDER, /\.filter\(\(c\) => !c\.sdkOnly \|\| s\.status\.backend === "sdk"\)/);
});

test("Bypass carries a sub-line saying what it costs", () => {
  // Not decoration: it is the one mode that removes the gate instead of moving it, and it also takes
  // romp's approve/deny cards with it (they render from can_use_tool, which bypass never fires).
  assert.match(RENDER, /sub: "every tool runs unasked, and romp stops showing approvals"/);
  assert.match(RENDER, /interface MetaChoice \{[^}]*sub\?: string; sdkOnly\?: boolean/);
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(CSS, /\.meta-item-sub \{ font-size: 0\.82em; opacity: 0\.6; \}/,
    "one sub-line size across every romp menu — same as .ctx-item-sub");
});

test("every mode wears a tagline, and 'Accept edits' reads 'Accept' everywhere (T140)", () => {
  // the user 2026-08-28: where taglines exist under some entries, add analogous ones — each line
  // states what the mode ENFORCES (worded from the plumbing, per the task), in the one menu
  // vocabulary (.meta-item-sub, pinned above). The mode ids stay the wire's.
  assert.match(RENDER, /\{ label: "Normal", value: "default", sub: "asks before edits and commands" \}/);
  assert.match(RENDER, /\{ label: "Accept", value: "acceptEdits", sub: "file edits apply without asking; commands still ask" \}/);
  assert.match(RENDER, /\{ label: "Auto", value: "auto", sub: "safe actions run unasked; risky ones still ask" \}/);
  assert.match(RENDER, /\{ label: "Plan", value: "plan", sub: "reads and proposes only — changes nothing" \}/);
  // the rename holds everywhere the mode name renders: the chip/badge…
  assert.match(RENDER, /case "acceptedits": return "Accept";/);
  assert.ok(!RENDER.includes('"Accept edits"'), "no surface still says the two-word label");
  // …and the kernel's cycle refusal (its terminal leg, until the backend's removal lands) names the same four modes with the same word
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  assert.match(KERNEL, /shift\+tab cycle — Normal, Accept, Auto, Plan\./);
});

test("no width blowout: every new tagline is no longer than the accepted bypass line (T117 fit rule)", () => {
  // the menu sizes to its longest line; the bypass sub shipped 2026-08-15 and set the accepted
  // width — the new taglines must all fit inside it, so the menu gets no wider than it already was
  const subs = [...RENDER.matchAll(/sub: "([^"]+)"/g)].map((m) => m[1]);
  const bypass = "every tool runs unasked, and romp stops showing approvals";
  assert.ok(subs.includes(bypass));
  for (const sub of subs) assert.ok(sub.length <= bypass.length, `tagline wider than the accepted menu: ${sub}`);
});
