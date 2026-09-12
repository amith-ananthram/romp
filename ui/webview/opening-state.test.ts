// A just-opened session said "Working" over an epoch-sized clock while its transcript didn't exist yet
// (the user 2026-08-05, who wanted "opening" with animated dots until it's ready). The fix is layered:
// the KERNEL reports state "opening" for a spawned session whose transcript isn't on disk (the first
// record is the deciding event — discover() then takes over), and the CLIENT shows the same line for a
// tab whose session payload hasn't arrived at all (which previously left the PREVIOUS tab's statusline
// standing). Same in-progress line treatment as compacting, dots in the accent per the loader rule.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");

test("the kernel reports OPENING while the transcript doesn't exist and the spawn is in flight — never a working chip on a broken clock", () => {
  assert.ok(KERNEL.includes('if chip in ("working", "ready") and not path_override and not os.path.exists(sess["path"]) \\'));
  assert.ok(KERNEL.includes("and spawn_inflight:"));
  assert.ok(KERNEL.includes('chip = "opening"'));
});

// The opening window is per-backend (the user 2026-08-08, who read minutes of dots as creation still
// running): a fresh session of EITHER backend writes NO transcript until its first turn, so keying the
// chip on the file alone held a fully-up idle session on the opening dots until the user typed.
// SDK: the backend's live `spawning` report (session thread up, client not yet; the handshake closes
// it). The kernel's terminal leg (the statusline hook's first @claude-state) stays pinned until the backend's
// removal lands (T331 is the UI stage). The SDK leg
// must key on `spawning`, NOT on `connected` being falsy — a DORMANT created session (kernel restarts
// kill idle CLIs; boot reconcile leaves them lazy) also reports no `connected`, and reading that as
// "still opening" kept the dots up for hours on a session one message from answering (the user
// 2026-08-13). A dormant row carries no spawning key, so it reads ready.
test("OPENING covers exactly each backend's spawn window — a dormant created session reads ready", () => {
  const SDK = fs.readFileSync(path.join(ROOT, "kernel", "sdk_backend.py"), "utf8");
  assert.ok(SDK.includes('"connected": bool(self.client)'), "the snapshot carries the handshake event");
  assert.ok(SDK.includes('"spawning": not self.client'), "the snapshot carries the in-flight window");
  assert.ok(KERNEL.includes('"connected": bool(st.get("connected"))'), "the live merge threads connected through");
  assert.ok(KERNEL.includes('"spawning": bool(st.get("spawning"))'), "the live merge threads spawning through");
  assert.ok(KERNEL.includes('spawn_inflight = bool(tm.get("spawning"))'), "the live spawn window is the backend's own report");
});

// A per-session chip event must not ride the periodic full push cycle, which runs SECONDS on a busy
// fleet (measured live 2026-08-10: the tab appeared 5-6s after the create landed, the opening→ready
// flip 12s after, while the kernel had known the session since 0.4s). The create paths and the SDK
// connect handshake each fire the kernel's targeted one-session push, so the one tab the user is
// guaranteed to be staring at paints first, not last.
test("create + connect push the ONE session directly instead of waiting out a full push cycle", () => {
  const SDK = fs.readFileSync(path.join(ROOT, "kernel", "sdk_backend.py"), "utf8");
  assert.ok(KERNEL.includes("def _push_session_now(sid):"), "the targeted push exists");
  assert.match(KERNEL, /_mark_views_dirty\(\)\s*\n\s*_push_session_now\(sid\)/,
    "an SDK create pushes its tab at once");
  assert.ok(KERNEL.includes("push_session=_push_session_now,"), "the backend is wired to it");
  assert.match(SDK, /self\.client = client\s*\n(\s*#[^\n]*\n)*\s*self\.backend\._push_session\(self\.sid\)/,
    "the handshake — the flip the opening chip stands down on — pushes immediately");
});

test("the statusline shows Opening + dots for BOTH the pre-payload tab and the kernel's opening state", () => {
  assert.match(RENDER, /function openingLine\(text = "Opening session"\): HTMLElement/);   // the text is a parameter since 2026-09-08: a skeleton tab (a running session whose transcript is on its way) says "Loading session"
  // pre-payload: a placeholder tab used to leave the PREVIOUS tab's statusline standing
  assert.match(RENDER, /if \(activeId && !s\) \{[\s\S]{0,1000}?sl\.replaceChildren\(openingLine\(isProvisionalId\(activeId\) \? "Opening session" : "Loading session"\)\);\s*\n\s*return;/);   // Opening for a provisional (a create); Loading for every other pre-payload id (2026-09-11: a new split column's session on its way is not being created)
  // kernel-reported opening rides the same line
  assert.match(RENDER, /s\.status\.state === "opening"/);
  assert.match(RENDER, /"opening"/);
  assert.ok(fs.readFileSync(path.join(ROOT, "ui", "webview", "status-chip.ts"), "utf8").includes('opening: "Opening…",'), "the chip vocabulary knows the state (CHIP_LABEL lives in status-chip.ts since T322b)");
  // three staggered accent dots — the loader idiom's smallest form, no new fonts
  assert.match(CSS, /\.opening-line-dots span \{ width: 4px; height: 4px; border-radius: 50%; background: var\(--accent\);/);
  assert.match(CSS, /@keyframes opening-line-pulse/);
  assert.match(CSS, /\.opening-line \{ color: var\(--accent\); \}/);
});

test("the MCP panel names a stale kernel instead of a raw parse error (the user 2026-08-05)", () => {
  assert.match(RENDER, /this romp kernel predates the MCP panel — restart romp to update it/);
  assert.match(RENDER, /\(\(e && e\.message\) \|\| e\)/);
});

test("the pusher builds a transcript-less session at ACTIVE priority — its creator can't declare it yet", () => {
  // A new session's payload took ~22s to reach the client that created it (the user 2026-08-08,
  // round two: the dots outlived a fully-ready session): the active-first build hint can never name
  // a JUST-CREATED sid, because a client cannot post activeTab for a tab whose first payload hasn't
  // arrived. A transcript-less session's build is near-free, so it rides the top priority tier.
  assert.ok(KERNEL.includes('build_order = sorted(chat_list, key=lambda s: 0 if s["sid"] in active'));
  assert.ok(KERNEL.includes('or not os.path.exists(s["path"]) else 1)'));
});
