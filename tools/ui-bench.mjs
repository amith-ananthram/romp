#!/usr/bin/env node
// tools/ui-bench.mjs: a reproducible headless-Chrome performance bench for romp's dashboard panes.
//
// The kernel pushes JSON frames over a WebSocket to each pane page: the feed, the Outline pane (app id
// fleet), the chat and the timeline. How long the page's main thread takes to absorb a frame is what the
// user feels as a laggy dashboard, and until now it was measured by eye. This tool makes it a number, in
// three steps:
//
//   1. --record <app> --seconds N --out /tmp/…/frames.jsonl
//      Connect to the LIVE kernel's WebSocket exactly as the browser page does (the shim's own query:
//      app, delta=1, iid; the token as the romp_token cookie with a same-origin Origin header; the
//      {type:"ready"} handshake) and save every frame the kernel sends, with a receive timestamp, as
//      JSONL. The client is read-only: it sends the ready handshake and nothing else (the ws library
//      answers protocol pings, which the kernel's liveness check needs). Recorded frames are REAL
//      session data, so the output path must be under /tmp and outside any git checkout; the tool
//      refuses anything else.
//
//   2. --replay <app> --frames FILE [--cpu-throttle K] [--iters N] [--fast | --gap MS] [--json OUT] [--cpu-profile OUT]
//      Serve the pane page and its bundles from the built dist and replay the recorded frames into a
//      headless Chromium at their recorded pacing (back-to-back with --fast, or a fixed gap apart with
//      --gap), measuring per frame the bytes, the WebSocket handler's synchronous time (the shim's own
//      work on the wire frame: parse, delta application, the held-state stamp), the bundle's time for
//      the delivery that carried the frame, and the time until the main thread is free again (message
//      receipt to the second requestAnimationFrame after that delivery); plus long-animation-frame
//      entries with script attribution (the task's entry point: the message handler, the shim's flush
//      task, a rAF, a timer, a script's evaluation; not the bundle function), JS heap after a forced
//      GC, DOM size, and page console errors. The shim hands frames to the bundle in its own task
//      (kernel.py _shim: a FIFO flushed by a MessageChannel message, where a newer whole-state frame
//      replaces an older one still queued and the flush is time-sliced), so a wire frame and the
//      bundle's render of it are two measurements: the report keeps them apart, says how many frames
//      reached the bundle as their own delivery and how many were coalesced into a newer frame's, and
//      for a shim from before that task (the bundle rendered inside the handler) says so and reads the
//      handler column as shim plus bundle. --cpu-profile OUT.cpuprofile samples the page's JavaScript
//      with the V8 profiler across the replay, writes a file Chrome DevTools loads, and prints the
//      functions with the most self and total time (with their source positions through the dist's
//      .map files, and for the hottest functions the lines that hold the time), overall and inside the
//      delivery of the first content frame and of the largest frame of each type: the attribution the
//      long-animation-frame entries cannot give.
//
//      Serving design: the REAL kernel HTTP Handler runs in a python3 subprocess under an isolated
//      environment, the pattern of tests/test_color_route.py with the floors tests/conftest.py applies:
//      a private XDG_STATE_HOME; the manager variables removed (the manager port set to a
//      dead one, as conftest does) and every key-source name conftest pops removed too (the API keys,
//      the key reference and command, the token credentials, the auth declaration and 1Password's
//      names; STRIPPED_KEY_ENV below); the manager's key FILE and the boot model-catalog fetch pointed away (the
//      kernel would otherwise read ~/.config/romp/service.env and carry its key to the Models API); the
//      Claude binary floored to /bin/false; the CLI scope off (ROMP_CLI_SCOPE=0, so nothing probes
//      systemd-run); the postal peer bus off; ROMP_KERNEL_NO_OPEN=1; a serve token minted for the run.
//      So the page HTML and the WebSocket shim are the kernel's own bytes. The subprocess holds a pipe
//      from the parent and exits when it closes, so it cannot outlive the bench however the bench ends.
//      Its directory, and the browser's profile and artifacts, live under one per-user parent whose
//      dead-owner entries the next run sweeps: a process-group SIGKILL leaves them until then, nothing
//      else does. The shim connects its
//      socket to location.host, so a small Node front server sits in front: it answers /ws itself as
//      the replay server and proxies every other request (the page, /dist/*, /media/*, the small
//      JSON routes the bundles fetch) to the kernel Handler. Nothing is rewritten. The live kernel is
//      never started, restarted, or imported with its manager variables set.
//
//   3. --synthesize <app> --cards N --out FILE
//      A plausible frame stream with invented content (the notes-api demo domain, placeholder
//      uuids) in the kernel's wire shapes, for tests and for a bench that must not depend on a live
//      board. Frames mirror kernel.py: build_feed's {type:"feed"} as the keyed full frame with the _keys
//      list _send_slot_delta appends, then its {type:"delta", slot:"feed"} frames, for the feed and
//      Outline pages; build_timeline's skeleton ({type:"data"}), the {type:"bars"} slot with its _keys
//      list and {type:"delta", slot:"bars"} for the timeline; keepalives throughout. The chat's
//      {type:"session"} frame is not synthesized:
//      build_session's shape is too rich to fake faithfully, so record it from the live kernel.
//
//   --compare A.json B.json prints the deltas between two replay reports.
//
// Run from the repo root; playwright and ws come from vscode-extension/node_modules. Examples:
//   node tools/ui-bench.mjs --synthesize feed --cards 200 --out /tmp/romp-perf/synth-feed.jsonl
//   node tools/ui-bench.mjs --replay feed --frames /tmp/romp-perf/synth-feed.jsonl --fast --json /tmp/romp-perf/a.json
//   node tools/ui-bench.mjs --record feed --seconds 90 --out /tmp/romp-perf/frames-feed.jsonl
//   node tools/ui-bench.mjs --replay feed --frames /tmp/romp-perf/frames-feed.jsonl --json /tmp/romp-perf/b.json
//   node tools/ui-bench.mjs --compare /tmp/romp-perf/a.json /tmp/romp-perf/b.json
//
// tests/ui-bench.test.mjs covers the classifier, the compare arithmetic, the synthesizer's shapes,
// the /tmp path guard, the recording client against a local WebSocket server, the Handler subprocess's
// environment and its exit with the parent, the CPU-profile aggregation, and a real replay of
// synthetic streams on the feed and timeline pages.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import crypto from "node:crypto";
import { spawn } from "node:child_process";
import { createRequire, SourceMap } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO = path.resolve(HERE, "..");
const EXT_DIR = path.join(REPO, "vscode-extension");
const requireExt = createRequire(path.join(EXT_DIR, "package.json"));

// The pane pages the kernel serves, by the app id their shim connects with (kernel.py: the /feed,
// /fleet, /chat and /timeline routes; `fleet` is the Outline pane).
export const APPS = ["feed", "fleet", "chat", "timeline"];
export const DELTA_SEP = "\u001f";   // kernel.py _DELTA_SEP: joins the keys of a keyed collection

// ── frame classification ─────────────────────────────────────────────────────────────────────────

const TYPE_RE = /"type"\s*:\s*"([^"\\]*)"/;
const SLOT_RE = /"slot"\s*:\s*"([^"\\]*)"/;

/** The report row a wire frame files under: its `type`, or `delta:<slot>` for a view delta; "other"
 *  when the text carries no type. A regex over the head of the string, so classifying a multi-megabyte
 *  frame costs nothing like a parse; the kernel writes `type` (and a delta's `slot`) first or right
 *  after `now`, in both json.dumps spacing and the hand-concatenated compact form. */
export function classifyFrame(text) {
  if (typeof text !== "string") return "other";
  const head = text.length > 4096 ? text.slice(0, 4096) : text;
  let type = null, slot = null;
  const m = TYPE_RE.exec(head);
  if (m) {
    type = m[1];
    if (type === "delta") { const s = SLOT_RE.exec(head); slot = s ? s[1] : null; }
  } else {
    try {
      const o = JSON.parse(text);
      if (o && typeof o.type === "string") { type = o.type; if (type === "delta") slot = typeof o.slot === "string" ? o.slot : null; }
    } catch { return "other"; }
  }
  if (!type) return "other";
  return type === "delta" ? "delta:" + (slot || "?") : type;
}

// ── statistics ───────────────────────────────────────────────────────────────────────────────────

/** Nearest-rank percentile of a numeric array (p in 0..100); null for an empty array. */
export function percentile(values, p) {
  const xs = values.filter((v) => typeof v === "number" && Number.isFinite(v)).sort((a, b) => a - b);
  if (!xs.length) return null;
  const rank = Math.min(xs.length - 1, Math.max(0, Math.ceil((p / 100) * xs.length) - 1));
  return xs[rank];
}

export function summarize(values) {
  const xs = values.filter((v) => typeof v === "number" && Number.isFinite(v));
  if (!xs.length) return { n: 0, p50: null, p90: null, max: null, mean: null };
  const sum = xs.reduce((a, b) => a + b, 0);
  return { n: xs.length, p50: percentile(xs, 50), p90: percentile(xs, 90), max: Math.max(...xs), mean: sum / xs.length };
}

// ── paths ────────────────────────────────────────────────────────────────────────────────────────

/** A recording holds real session data, so it may live only under /tmp and never inside a git
 *  checkout (a stray `git add` there would publish it). Both sides of the comparison are resolved
 *  through symlinks: on macOS /tmp is a link to /private/tmp and os.tmpdir() lives under /var/folders,
 *  so a root compared by name never matched a candidate compared by realpath. A directory that does
 *  not exist yet is resolved through its deepest existing ancestor. A symlink at the leaf is refused:
 *  a write through it lands wherever the link points. Returns the absolute path or throws. `roots` is
 *  a test seam; the default is /tmp and os.tmpdir(). */
export function assertTmpPath(p, { roots = ["/tmp", os.tmpdir()] } = {}) {
  const abs = path.resolve(p);
  const resolvedRoots = new Set();
  for (const r of roots) {
    if (!r) continue;
    const named = path.resolve(r);
    resolvedRoots.add(named);
    const real = safeReal(named);
    if (real) resolvedRoots.add(real);
  }
  const dir = resolveExistingPrefix(path.dirname(abs));
  const inTmp = [...resolvedRoots].some((r) => dir === r || dir.startsWith(r + path.sep));
  if (!inTmp) throw new Error(`refusing to write a recording outside /tmp: ${abs}`);
  let leaf = null;
  try { leaf = fs.lstatSync(abs); } catch (e) { if (e.code !== "ENOENT" && e.code !== "ENOTDIR") throw e; }
  if (leaf && leaf.isSymbolicLink()) throw new Error(`refusing to write a recording through a symlink: ${abs}`);
  for (let d = dir; ; d = path.dirname(d)) {
    if (fs.existsSync(path.join(d, ".git"))) throw new Error(`refusing to write a recording inside a git checkout: ${abs} (a .git lives at ${d})`);
    if (path.dirname(d) === d) break;
  }
  return abs;
}

function safeReal(p) {
  try { return fs.realpathSync(p); } catch { return null; }
}

/** The realpath of the deepest existing ancestor of `p`, with the missing tail appended unchanged. */
function resolveExistingPrefix(p) {
  const missing = [];
  for (let d = p; ; d = path.dirname(d)) {
    const real = safeReal(d);
    if (real) return missing.length ? path.join(real, ...missing) : real;
    if (path.dirname(d) === d) return p;
    missing.unshift(path.basename(d));
  }
}

// ── JSONL frame files ────────────────────────────────────────────────────────────────────────────

/** Read a frames file: rows {t, data} in file order (meta and event rows skipped). */
export function loadFrames(file) {
  const rows = [];
  let meta = null;
  for (const line of fs.readFileSync(file, "utf8").split("\n")) {
    if (!line.trim()) continue;
    const row = JSON.parse(line);
    if (row.meta) { meta = row.meta; continue; }
    if (typeof row.data !== "string") continue;
    rows.push({ t: Number(row.t) || 0, data: row.data });
  }
  return { meta, frames: rows };
}

/** Write a frames file private to the user: directory 0700, file 0600 (re-applied when the file
 *  already existed), and never through a symlink at the leaf (O_NOFOLLOW). A recording holds every
 *  frame the kernel pushed, and /tmp is readable by every local account. */
export function writeFrames(file, meta, frames) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const out = [JSON.stringify({ meta })];
  for (const f of frames) out.push(JSON.stringify({ t: f.t, bytes: Buffer.byteLength(f.data, "utf8"), data: f.data }));
  const buf = Buffer.from(out.join("\n") + "\n", "utf8");
  const flags = fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_TRUNC | (fs.constants.O_NOFOLLOW || 0);
  const fd = fs.openSync(file, flags, 0o600);
  try {
    fs.fchmodSync(fd, 0o600);
    for (let off = 0; off < buf.length;) off += fs.writeSync(fd, buf, off, buf.length - off);
  } finally {
    fs.closeSync(fd);
  }
}

// ── --record: a read-only client of the live kernel ──────────────────────────────────────────────

function stateDir() {
  if (process.env.ROMP_STATE_DIR) return process.env.ROMP_STATE_DIR;
  const xdg = process.env.XDG_STATE_HOME || path.join(os.homedir(), ".local", "state");
  return path.join(xdg, "romp");
}

export function readServeToken() {
  const f = path.join(stateDir(), "serve-token");
  const t = fs.readFileSync(f, "utf8").trim();
  if (!t) throw new Error(`empty serve token at ${f}`);
  return t;
}

export async function recordFrames({ app, seconds, out, port, log = console.error }) {
  if (!APPS.includes(app)) throw new Error(`unknown app ${app}; one of ${APPS.join(", ")}`);
  const file = assertTmpPath(out);
  const token = readServeToken();
  const WebSocket = requireExt("ws");
  const origin = `http://127.0.0.1:${port}`;
  const iid = crypto.randomUUID();
  const url = `ws://127.0.0.1:${port}/ws?app=${app}&delta=1&iid=${encodeURIComponent(iid)}`;   // the shim's connect query (kernel.py _shim)
  // The browser's credential form: the romp_token cookie plus a same-origin Origin header (the kernel's
  // _authorize accepts the cookie only with an acceptable Origin). No ?token=, no X-Romp-Token.
  const ws = new WebSocket(url, { origin, headers: { Cookie: `romp_token=${token}` }, maxPayload: 512 * 1024 * 1024 });
  const frames = [];
  const events = [];
  const started = Date.now();
  const meta = { tool: "ui-bench", mode: "record", app, port, seconds, startedAt: new Date(started).toISOString(), iid };
  await new Promise((resolve, reject) => {
    let done = false;
    const onInterrupt = () => { log("ui-bench: interrupted; writing what was recorded"); finish(); };
    const finish = (err) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      process.removeListener("SIGINT", onInterrupt);   // one listener per recording, gone when it ends
      try { ws.close(); } catch {}
      err ? reject(err) : resolve();
    };
    const timer = setTimeout(() => finish(), seconds * 1000);
    process.once("SIGINT", onInterrupt);
    ws.on("open", () => {
      events.push({ t: Date.now(), event: "open" });
      // The bundle's handshake (kernel _ws: ready → _push_one). There is no ready gate: the pusher serves a
      // fresh socket from its next cycle as well, and whichever runs first sends the keyed full frame while the
      // other is deduped against it, so a recording opens with one full frame per slot and then the tab order
      // the ready handler pushes, as a browser's stream does.
      ws.send(JSON.stringify({ type: "ready" }));
    });
    ws.on("message", (data, isBinary) => {
      const text = isBinary ? data.toString("utf8") : data.toString();
      frames.push({ t: Date.now(), data: text });
    });
    ws.on("unexpected-response", (_req, res) => {
      let body = "";
      res.on("data", (c) => { body += c; });
      res.on("end", () => finish(new Error(`the kernel refused the WebSocket: HTTP ${res.statusCode} ${body.trim()}`)));
    });
    ws.on("error", (e) => finish(new Error(`WebSocket error: ${e.message}`)));
    ws.on("close", (code, reason) => {
      events.push({ t: Date.now(), event: "close", code, reason: String(reason || "") });
      if (!done) finish(new Error(`the kernel closed the socket early (code ${code}) after ${frames.length} frames`));
    });
  }).finally(() => {
    meta.endedAt = new Date().toISOString();
    meta.frames = frames.length;
    meta.events = events;
    writeFrames(file, meta, frames);
  });
  const summary = streamSummary(frames);
  log(`ui-bench: recorded ${frames.length} frames (${fmtBytes(summary.bytes)}) from app=${app} over ${((Date.now() - started) / 1000).toFixed(1)}s → ${file}`);
  for (const [type, s] of Object.entries(summary.byType)) log(`  ${type.padEnd(12)} ${String(s.count).padStart(6)}  ${fmtBytes(s.bytes).padStart(10)}  max ${fmtBytes(s.max)}`);
  return { file, frames: frames.length, summary };
}

export function streamSummary(frames) {
  const byType = {};
  let bytes = 0;
  for (const f of frames) {
    const type = classifyFrame(f.data);
    const n = Buffer.byteLength(f.data, "utf8");
    bytes += n;
    const s = (byType[type] ||= { count: 0, bytes: 0, max: 0 });
    s.count++; s.bytes += n; if (n > s.max) s.max = n;
  }
  return { frames: frames.length, bytes, byType };
}

// ── --synthesize: invented frames in the kernel's shapes ─────────────────────────────────────────

const SIDS = ["11111111-2222-3333-4444-555555555501", "11111111-2222-3333-4444-555555555502", "11111111-2222-3333-4444-555555555503"];
const NAMES = ["web", "api", "tests"];
const COLORS = [{ bg: "#1EA1EB", fg: "white" }, { bg: "#54B204", fg: "black" }, { bg: "#E0B020", fg: "black" }];
const VERBS = ["Add", "Fix", "Refactor", "Document", "Test", "Wire", "Profile", "Remove"];
const OBJECTS = ["pagination on the notes list", "the notes-api auth middleware", "the search index rebuild", "the markdown export",
  "the tag filter query", "the rate limiter", "the notes sync endpoint", "the attachment upload path", "the CLI's note picker",
  "the flaky integration test", "the OpenAPI schema", "the migration runner"];
const SYNTH_NOW = 1_760_000_000;   // a fixed clock so a synthesized stream is byte-stable run to run
// A card's recency tint (kernel: "trgb": list(cm.age_rgb(age)), bright when recent, dark when old): a fixed
// four-step palette by age in place of the kernel's colormap. feed.ts destructures it on every card.
const TINTS = [[253, 231, 37], [94, 201, 98], [33, 145, 140], [68, 1, 84]];
const synthTint = (age) => TINTS[age < 600 ? 0 : age < 3600 ? 1 : age < 6 * 3600 ? 2 : 3];

function rng(seed) {
  let a = seed >>> 0;
  return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
const pick = (r, xs) => xs[Math.floor(r() * xs.length)];

function synthCard(i, r, now) {
  const k = i % SIDS.length;
  const column = i % 5 === 3 ? "needs_input" : i % 5 === 4 ? "completed" : "working";
  const text = `${VERBS[i % VERBS.length]} ${OBJECTS[i % OBJECTS.length]} (#${i + 1})`;
  const t = now - 300 * i - 17;
  const nodes = [];
  const kids = 1 + (i % 3);
  for (let j = 0; j < kids; j++) {
    nodes.push({
      id: `n${i}-${j}`, kind: "ask", text: `${text}: step ${j + 1}`, who: NAMES[k], whoSid: SIDS[k], whoColor: COLORS[k],
      status: column === "completed" ? "done" : (column === "needs_input" && j === kids - 1) ? "question" : (j === 0 ? "done" : "open"),
      t: t + 30 * j, last: t + 60 * j + 5, anchorUuid: null, promptAnchorUuid: null, children: [],
    });
  }
  const card = {
    itemId: `card-${i}`, sid: SIDS[k], name: NAMES[k], color: COLORS[k], text, t, trgb: synthTint(now - t), live: true, turnId: `turn-${i}`,
    column, tree: nodes, notify: null, summary: column === "completed" ? `Done: ${text.toLowerCase()} landed with a test.` : null,
    blockSummary: null, background: null, distillState: column === "completed" ? "completed" : null,
  };
  if (column === "working") card.working = { since: now - 40 - (i % 7) * 10, toolUses: 1 + (i % 9) };
  if (i % 11 === 6) card.awaiting = { why: "waiting on the test suite", kind: "task", since: now - 90, tasks: ["npm test"] };
  return card;
}

function synthLedger(k, cards, now) {
  const own = cards.filter((c) => c.sid === SIDS[k]);
  const tree = [];
  for (const c of own) {
    tree.push({ id: c.itemId, text: c.text, depth: 0, done: c.column === "completed", blocked: c.column === "needs_input", t: c.t, mt: c.t + 120,
      current: c.column === "working", children: c.tree.map((n) => n.id), summary: c.summary, blockSummary: null });
    for (const n of c.tree) tree.push({ id: n.id, text: n.text, depth: 1, done: n.status === "done", blocked: n.status === "question", t: n.t, mt: n.last, current: false, children: [] });
  }
  return { sid: SIDS[k], name: NAMES[k], color: COLORS[k], status: { state: k === 2 ? "ready" : "working" },
    ledger: { summary: `${NAMES[k]} is working through ${own.length} goals on notes-api.`, tree, current: { t: now - 60 } } };
}

/** build_feed's wire keys in its order, the pusher's buildId after them and, for the Outline page, the
 *  ledgers it attaches; `views` is the shape a kernel with no views file ships. */
function synthFeedFull(cards, now, buildId, withLedgers) {
  const frame = {
    type: "feed", asks: cards.map((c) => ({ ...c })), now,
    views: { active: "all", actives: { chat: { all: true }, timeline: { all: true }, outline: { all: true } }, tags: [] },
    judgeLimit: null, working: [NAMES[0], NAMES[1]], awaiting: [], stateUnknown: [], bgServices: {},
    dismissedCount: 0, showDismissed: false, order: SIDS.slice(),
    sessions: SIDS.map((sid, k) => ({ sid, name: NAMES[k], color: COLORS[k] })),
    clearNotices: [], sdkNotices: [], syncNotices: [], selfHost: "TESTHOST", canUndoClear: false, buildId,
  };
  if (withLedgers) frame.ledgers = SIDS.map((_s, k) => synthLedger(k, cards, now));
  return frame;
}

/** The kernel's _keys list for a {type:"feed"} full frame: the asks by itemId in payload order (kernel.py
 *  _DELTA_SLOTS feed: asks "byid:itemId"; _delta_key). The shim files the asks under these keys and applies
 *  every later delta against them; a full frame without the list makes it answer each delta with needSlot. */
export function feedKeys(feed) {
  return { asks: (feed.asks || []).map((a) => String(a.itemId)) };
}

/** The non-keyed remainder of a feed frame as _delta_parts splits it: every key but the keyed collection.
 *  `type`, the clock fields and the ledgers are part of it, so when a delta carries the WHOLE remainder
 *  (restAll) the shim keeps exactly the keys it names and drops the rest. */
function feedRest(frame) {
  const rest = {};
  for (const [k, v] of Object.entries(frame)) if (k !== "asks") rest[k] = v;
  return rest;
}

/** A feed-protocol stream (feed and Outline pages): the keyed full frame, then {type:"delta", slot:"feed"}
 *  frames and keepalives over about a minute of invented activity, in the shapes _send_slot_delta writes.
 *  Revisions run contiguously from the full's 0. `coll.asks.set` carries changed and appended cards (the
 *  shim appends a new key on its own, so no `order` rides for one), `coll.asks.del` a retired card, and
 *  `coll.asks.order` crosses only when a card moves. `rest` is the clock fields alone (_DEDUP_VOLATILE: now,
 *  buildId) until the remainder changes, when the WHOLE remainder rides with `restAll` (the Outline's
 *  ledgers are remainder, so a changed ledger goes this way). */
function synthFeedStream(app, cards, r, now) {
  const t0 = now * 1000;
  const withLedgers = app === "fleet";
  const items = [];
  for (let i = 0; i < cards; i++) items.push(synthCard(i, r, now));
  let buildId = 1;
  const full = synthFeedFull(items, now, buildId, withLedgers);
  const frames = [{ t: t0, data: JSON.stringify({ ...full, _keys: feedKeys(full) }) }];
  let rev = 0;
  let nextNew = cards;
  const ka = (t) => frames.push({ t, data: JSON.stringify({ type: "ka", dv: now }) });
  for (let step = 1; step <= 24; step++) {
    const t = t0 + step * 2500;
    if (step % 4 === 0) ka(t - 400);
    buildId++;
    full.now = now + Math.round((t - t0) / 1000); full.buildId = buildId;
    const set = {};
    const asks = { set };
    const nUp = 1 + Math.floor(r() * 3);
    for (let u = 0; u < nUp && items.length; u++) {
      const c = items[Math.floor(r() * items.length)];
      c.text = c.text.replace(/( · rev \d+)?$/, ` · rev ${step}`);
      if (c.working) c.working = { ...c.working, toolUses: (c.working.toolUses || 0) + 1 };
      set[c.itemId] = { ...c };
    }
    if (step % 5 === 0 && items.length > 1) {
      const gone = items.splice(Math.floor(r() * items.length), 1)[0];
      asks.del = [gone.itemId];
      const fresh = synthCard(nextNew++, r, full.now);
      items.push(fresh);
      set[fresh.itemId] = { ...fresh };
    }
    if (step % 9 === 0 && items.length > 2) {
      const moved = items.splice(items.length - 1, 1)[0];   // the newest card surfaces at the top
      items.unshift(moved);
      asks.order = items.map((c) => c.itemId);
    }
    let restChanged = false;
    if (step % 7 === 0) { full.working = step % 2 ? [NAMES[0]] : [NAMES[0], NAMES[1], NAMES[2]]; restChanged = true; }
    if (withLedgers && step % 3 === 0) { full.ledgers[step % SIDS.length] = synthLedger(step % SIDS.length, items, full.now); restChanged = true; }
    const d = { type: "delta", slot: "feed", base: rev, rev: rev + 1, coll: { asks } };
    if (restChanged) { d.rest = feedRest(full); d.restAll = 1; }
    else d.rest = { now: full.now, buildId };
    frames.push({ t, data: JSON.stringify(d) });
    rev++;
  }
  ka(t0 + 63_000);
  return frames;
}

function synthLane(k, now, live) {
  return {
    id: SIDS[k], name: NAMES[k], live, state: live ? (k === 2 ? "idle" : "working") : "idle",
    awaitingBg: null, awaitingKind: null, awaitingCount: null, awaitingPeers: null, awaitingTasks: [],
    since: now - 120 * (k + 1), color: COLORS[k].bg, model: "opus", effort: "high", modelPending: false,
    modelColor: null, effortColor: null, fast: "", modelTone: null, effortTone: null, ctxTone: null,
    context: 20 + 15 * k, ctxColor: null, subagents: [], awaiting: [], compacting: [], compactions: [], clears: [],
    faded: false, branch: null, comments: [], hideFromFeed: false, postalServiceOff: false, notify: true,
  };
}

function synthBar(k, j, now, open) {
  const start = now - 3600 + 240 * j;
  const text = `${VERBS[(j + k) % VERBS.length]} ${OBJECTS[(j * 3 + k) % OBJECTS.length]}`;
  return {
    id: `seg-${k}-${j}`, promptId: `aaaaaaaa-0000-4000-8000-${String(k).padStart(4, "0")}${String(j).padStart(8, "0")}`,
    workId: `bbbbbbbb-0000-4000-8000-${String(k).padStart(4, "0")}${String(j).padStart(8, "0")}`,
    start, end: open ? now : start + 150, open, cont: false, prompt: text, summary: `Working on ${text.toLowerCase()}`,
    msgCaption: text, src: "typed", mids: [], pending: false, tid: SIDS[k],
    uuid: `aaaaaaaa-0000-4000-8000-${String(k).padStart(4, "0")}${String(j).padStart(8, "0")}`,
    nudgeAuto: false, romp: false,
    workUuid: `bbbbbbbb-0000-4000-8000-${String(k).padStart(4, "0")}${String(j).padStart(8, "0")}`,
    replyUuid: `cccccccc-0000-4000-8000-${String(k).padStart(4, "0")}${String(j).padStart(8, "0")}`,
  };
}

/** The kernel's _keys list for a {type:"bars"} full frame (kernel.py _delta_split / _delta_key). */
export function barsKeys(bars) {
  const turns = [];
  for (const [sid, lane] of Object.entries(bars.turns || {})) {
    if (!Array.isArray(lane) || !lane.length) { turns.push(sid + DELTA_SEP); continue; }
    for (const b of lane) turns.push(sid + DELTA_SEP + String(b.id));
  }
  const judging = (bars.judging || []).map((row) => ["sid", "t", "judge", "t1"].map((f) => String(row[f])).join(DELTA_SEP));
  const messages = (bars.messages || []).map((m) => String(m.id));
  return { turns, judging, messages };
}

/** A timeline stream: the lanes skeleton, the keyed bars slot, then bar-level deltas, a skeleton
 *  re-push and keepalives. */
function synthTimelineStream(cards, r, now) {
  const t0 = now * 1000;
  const skeleton = () => ({
    type: "data",
    data: { type: "timeline", now, sessions: SIDS.map((_s, k) => synthLane(k, now, true)), turns: {}, messages: [], judging: [],
      palette: COLORS.map((c) => c.bg), cmapGrad: null, activeChat: null, focus: null, hover: null, usage: null },
  });
  const frames = [{ t: t0, data: JSON.stringify(skeleton()) }];
  const perLane = Math.max(1, Math.ceil(cards / SIDS.length));
  const turns = {};
  SIDS.forEach((sid, k) => {
    turns[sid] = [];
    for (let j = 0; j < perLane; j++) turns[sid].push(synthBar(k, j, now, k < 2 && j === perLane - 1));
  });
  const judging = [];
  for (let j = 0; j < Math.min(perLane, 12); j++) {
    const k = j % SIDS.length;
    const t = now - 3400 + 240 * j;
    judging.push({ judge: pick(r, ["planner", "closer", "distiller", "captioner"]), sid: SIDS[k], t, t1: t + 8, kind: "run", text: "",
      ms: 7200, in: 4000 + j * 10, out: 300, sent: t, recv: t + 8 });
  }
  const bars = { type: "bars", turns, judging, messages: [], now, warming: false };
  frames.push({ t: t0 + 300, data: JSON.stringify({ ...bars, _keys: barsKeys(bars) }) });
  let rev = 0;
  const ka = (t) => frames.push({ t, data: JSON.stringify({ type: "ka", dv: now }) });
  for (let step = 1; step <= 24; step++) {
    const t = t0 + step * 2500;
    const nowS = now + Math.round((t - t0) / 1000);
    if (step % 4 === 0) ka(t - 400);
    const k = step % 2;   // the two working lanes take turns
    const lane = turns[SIDS[k]];
    const set = {};
    if (step % 6 === 0) {
      // the open bar closes and a new one opens: two entries cross
      const last = lane[lane.length - 1];
      last.open = false; last.end = nowS - 5;
      set[SIDS[k] + DELTA_SEP + last.id] = { ...last };
      const fresh = synthBar(k, lane.length, nowS, true);
      lane.push(fresh);
      set[SIDS[k] + DELTA_SEP + fresh.id] = fresh;
    } else {
      const last = lane[lane.length - 1];
      last.end = nowS;
      set[SIDS[k] + DELTA_SEP + last.id] = { ...last };
    }
    frames.push({ t, data: JSON.stringify({ type: "delta", slot: "bars", base: rev, rev: rev + 1, coll: { turns: { set } }, rest: { now: nowS } }) });
    rev++;
    if (step % 8 === 0) {
      const sk = skeleton();
      sk.data.now = nowS;
      sk.data.sessions[2].state = step % 16 ? "working" : "idle";
      frames.push({ t: t + 200, data: JSON.stringify(sk) });
    }
  }
  ka(t0 + 63_000);
  return frames;
}

export function synthesizeFrames(app, cards, { seed = 7, now = SYNTH_NOW } = {}) {
  if (!APPS.includes(app)) throw new Error(`unknown app ${app}; one of ${APPS.join(", ")}`);
  if (app === "chat") throw new Error("chat frames are not synthesized (the session frame is built by build_session and is too rich to fake); record them from the live kernel with --record");
  const r = rng(seed);
  if (app === "timeline") return synthTimelineStream(cards, r, now);
  return synthFeedStream(app, cards, r, now);
}

// ── --replay: the kernel page route in a subprocess, a Node front server, headless Chromium ─────

// The kernel's HTTP Handler, alone, in a python3 subprocess. The import pattern is tests/test_color_route.py's:
// the two sibling modules first (they resolve their state root at import), then the kernel by its bin/ name.
// The Handler is the kernel's whole route surface (the pages, /ws, the POST routes that spawn and revive
// sessions), gated by the serve token, so it must not outlive the bench: a daemon thread blocks on stdin,
// which the parent holds open as a pipe, and when the parent exits, however it exits, the read returns
// EOF, the thread removes the run directory and the process ends. When the whole process group is
// killed the thread never runs and the directory stays; the next run's dead-owner sweep reclaims it.
const PAGE_SERVER_PY = `
import importlib.util, os, shutil, sys, threading
from http.server import ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
root, tmp = sys.argv[1], sys.argv[2]
def _parent_gone():
    try:
        sys.stdin.buffer.read()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)
    os._exit(0)
threading.Thread(target=_parent_gone, daemon=True).start()
b = os.path.join(root, "bin")
def _load(name, p):
    # the bin/ names carry no .py suffix, hence the explicit SourceFileLoader; registered before exec so
    # the module resolves its own name the way an import would
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, p))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m
_load("romp_event_model", os.path.join(b, "romp-event-model"))
_load("romp_judge", os.path.join(b, "romp-judge"))
km = _load("romp_kernel_ui_bench", os.path.join(b, "romp-kernel"))
srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
sys.stdout.write("PORT %d\\n" % srv.server_address[1]); sys.stdout.flush()
srv.serve_forever()
`;

/** The variables the Handler subprocess must not inherit: the manager's (so the kernel module never
 *  believes it is the supervised live kernel), the live kernel's ports and state root, and the API keys
 *  and Claude binary a spawned session would run with (nothing the bench needs reads them). */
export const STRIPPED_ENV = ["ROMP_MANAGER_PORT", "ROMP_MANAGER_PID", "ROMP_SUPERVISED", "ROMP_STATE_DIR", "ROMP_SERVE_PORT", "ROMP_KERNEL_PORT", "ROMP_PERF", "TMUX"];

/** The key-source names tests/conftest.py pops before any test runs (its KEY_SOURCE_ENV_NAMES and
 *  KEY_SOURCE_ENV_PREFIXES: credentials.FLOOR_ENV_NAMES, which is the retired provider names, the login
 *  tokens, the auth declaration and 1Password's names, plus sdk_backend.AUTH_ENV_NAMES), stripped here
 *  for the same reason: every shell under a romp-managed session inherits the manager's credentials, a
 *  retired provider name in the kernel's environment is a boot failure (credentials.check_boot_environment)
 *  and the login tokens would be claimed for a launch, so a replay run from inside a session would
 *  otherwise start a kernel Handler holding the operator's OAuth token and op token, or refuse to start.
 *  The first form of this list stripped ANTHROPIC_* and the key reference only (review find, 2026-09-08). */
export const STRIPPED_KEY_ENV = ["ANTHROPIC_API_KEY", "ROMP_API_KEY_REF", "ROMP_API_KEY_CMD", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
  "ROMP_EXPECTED_AUTH", "OP_SERVICE_ACCOUNT_TOKEN", "OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "OP_ACCOUNT"];
export const STRIPPED_KEY_ENV_PREFIXES = ["ANTHROPIC_", "OP_SESSION_"];

/** The parent every run of this tool on this machine keeps its state under: <tmp>/romp-ui-bench-<uid>,
 *  private to the user and refused when something else holds the name. Each run gets a subdirectory
 *  holding owner.pid, the Handler's XDG_STATE_HOME and, through TMPDIR at launch, the
 *  browser's profile and artifacts. This small parent is the only directory the tool ever lists. */
export function benchRoot(base = os.tmpdir()) {
  const uid = typeof os.userInfo === "function" ? os.userInfo().uid : -1;
  const root = path.join(base, `romp-ui-bench-${uid}`);
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  const st = fs.lstatSync(root);
  if (st.isSymbolicLink() || !st.isDirectory()) throw new Error(`${root} is not a directory; refusing to use it`);
  if (uid >= 0 && st.uid !== uid) throw new Error(`${root} belongs to uid ${st.uid}; refusing to use it`);
  if ((st.mode & 0o077) !== 0) fs.chmodSync(root, 0o700);
  return root;
}

/** Remove the run directories under `root` whose recorded owner process is gone: what a process-group
 *  SIGKILL leaves behind (the Handler dies with the group before its rmtree runs; Playwright removes its
 *  directories from exit hooks a SIGKILL skips). An entry without owner.pid is left alone. Returns the
 *  names removed. */
export function sweepDeadRuns(root) {
  const swept = [];
  let names = [];
  try { names = fs.readdirSync(root); } catch { return swept; }
  for (const name of names) {
    const dir = path.join(root, name);
    let pid = NaN;
    try { pid = Number(fs.readFileSync(path.join(dir, "owner.pid"), "utf8").trim()); } catch { continue; }
    if (!Number.isInteger(pid) || pid <= 0) continue;
    let alive = true;
    try { process.kill(pid, 0); } catch (e) { alive = e.code === "EPERM"; }   // EPERM: it exists and is not ours
    if (alive) continue;
    try { fs.rmSync(dir, { recursive: true, force: true }); swept.push(name); } catch {}
  }
  return swept;
}

/** Start the kernel Handler subprocess under the isolated environment. Resolves to {port, token, pid,
 *  tmp, root, stderr, stop, exited}; `exited` settles once the child is gone (reaped, so a kill(pid, 0)
 *  after it reports ESRCH), which is what a caller waits on after stop() rather than a timer. On a start
 *  failure (an interpreter that cannot be spawned, exits before its port, or never announces one) the
 *  child is gone and its directory removed before the rejection.
 *  The serve token is minted per run: the Handler is the kernel's whole route surface on a loopback
 *  port any local process can reach, and a token in the source would open it to all of them. */
export async function startPageServer({ dist, python = "python3", log = () => {} } = {}) {
  const distDir = dist || path.join(EXT_DIR, "dist");
  if (!fs.existsSync(path.join(distDir, "feed.js"))) throw new Error(`no built bundles at ${distDir} (run: cd vscode-extension && npm run build)`);
  const root = benchRoot();
  const swept = sweepDeadRuns(root);
  if (swept.length) log(`ui-bench: removed ${swept.length} run director${swept.length === 1 ? "y" : "ies"} left behind by dead runs`);
  const tmp = fs.mkdtempSync(path.join(root, "run-"));
  fs.writeFileSync(path.join(tmp, "owner.pid"), `${process.pid}\n`);
  const token = crypto.randomBytes(18).toString("base64url");
  const env = { ...process.env };
  for (const k of Object.keys(env)) if (STRIPPED_KEY_ENV_PREFIXES.some((p) => k.startsWith(p)) || STRIPPED_ENV.includes(k) || STRIPPED_KEY_ENV.includes(k)) delete env[k];
  // conftest's form of the manager-port floor, not a bare removal: one kernel consumer maps an ABSENT port
  // to the default, live, one, so a dead value is the state that is safe against every consumer.
  env.ROMP_MANAGER_PORT = "1";
  env.XDG_STATE_HOME = path.join(tmp, "state");
  fs.mkdirSync(env.XDG_STATE_HOME, { recursive: true });
  env.ROMP_KERNEL_NO_OPEN = "1";
  env.ROMP_POSTAL_PEERS = "0";   // the feed page polls /tunnels, which otherwise asks the LIVE postal bus for its peers
  // The floors tests/conftest.py applies, for the same reasons. The kernel's boot check reads the manager's
  // env FILE for retired provider lines (kernel/credentials.py falls back to ~/.config/romp/service.env
  // when these two are unset), the boot model-catalog fetch would run the operator's apiKeyHelper and
  // carry its key to the Models API from the first /sessions request a pane makes, and a missing
  // ROMP_CLAUDE_BIN resolves to the real CLI, so it is set to a binary that runs nothing rather than removed.
  env.ROMP_SERVICE_ENV_FILE = env.ROMP_SERVICE_ENV = path.join(tmp, "no-service.env");   // never created
  env.ROMP_MODEL_CATALOG = "off";
  env.ROMP_CLAUDE_BIN = "/bin/false";
  // One more of conftest's floors: a route that constructs the SDK backend decides whether to wrap CLIs in
  // systemd-run scopes (on by default under a supervised kernel, probing the user manager). The retired
  // key reference and command names the boot check refuses went with STRIPPED_KEY_ENV above.
  env.ROMP_CLI_SCOPE = "0";
  env.ROMP_SERVE_TOKEN = token;
  env.ROMP_DIST_DIR = distDir;
  const child = spawn(python, ["-c", PAGE_SERVER_PY, REPO, tmp], { env, stdio: ["pipe", "pipe", "pipe"] });
  child.stdin.on("error", () => {});   // EPIPE once the child is gone
  let stderr = "";
  child.stderr.on("data", (c) => { stderr += c; if (stderr.length > 64_000) stderr = stderr.slice(-32_000); log(String(c)); });
  const exited = new Promise((resolve) => child.on("exit", resolve));   // before the port wait, so a start-failure exit settles it too
  let stopped = false;
  const stop = (signal = "SIGTERM") => {
    if (!stopped) { stopped = true; try { child.stdin.end(); } catch {} }
    try { child.kill(signal); } catch {}
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {}
  };
  const port = await new Promise((resolve, reject) => {
    let buf = "";
    const timer = setTimeout(() => { stop("SIGKILL"); reject(new Error(`the kernel page server did not start within 60s\n${stderr}`)); }, 60_000);
    // An interpreter that cannot be spawned (ENOENT) emits 'error' and never 'exit'; unhandled, that is an
    // uncaught exception thrown from a tick outside the promise chain, and the directory stays behind.
    child.on("error", (e) => { clearTimeout(timer); try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {} reject(new Error(`could not start ${python}: ${e.message}`)); });
    child.stdout.on("data", (c) => {
      buf += c;
      const m = /PORT (\d+)/.exec(buf);
      if (m) { clearTimeout(timer); resolve(Number(m[1])); }
    });
    child.on("exit", (code) => { clearTimeout(timer); try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {} reject(new Error(`the kernel page server exited with ${code}\n${stderr}`)); });
  });
  return { port, token, pid: child.pid, tmp, root, stderr: () => stderr, stop: () => stop(), exited };
}

/** The front server: /ws is ours (the replay socket), everything else proxies to the kernel Handler. */
export async function startFront({ pagePort }) {
  const { WebSocketServer } = requireExt("ws");
  let onSocket = null;
  const server = http.createServer((req, res) => {
    // The browser's Host header passes through untouched: the kernel's _origin_ok compares a request's Origin
    // against its Host, and Chrome sends Origin on CORS-mode subresource loads (the woff2 fonts), so a
    // rewritten Host turned every font load into a 403.
    const upstream = http.request({ host: "127.0.0.1", port: pagePort, method: req.method, path: req.url, headers: req.headers }, (up) => {
      res.writeHead(up.statusCode, up.headers);
      up.pipe(res);
    });
    upstream.on("error", (e) => { res.writeHead(502, { "Content-Type": "text/plain" }); res.end("front proxy: " + e.message); });
    req.pipe(upstream);
  });
  const wss = new WebSocketServer({ noServer: true, maxPayload: 512 * 1024 * 1024 });
  server.on("upgrade", (req, socket, head) => {
    const u = new URL(req.url, "http://127.0.0.1");
    const selfOrigin = `http://127.0.0.1:${server.address().port}`;
    // The page's own origin, REQUIRED: a browser page always sends Origin on a WebSocket upgrade, so a client
    // without one is some other local process on this loopback port, and the frames it would be handed may be a
    // recording of real session data. The first form let an Origin-less upgrade through (review find, 2026-09-08).
    if (u.pathname !== "/ws" || req.headers.origin !== selfOrigin) {
      socket.write("HTTP/1.1 403 Forbidden\r\n\r\n"); socket.destroy(); return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => { if (onSocket) onSocket(ws, u); else ws.close(); });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return {
    port: server.address().port,
    setSocketHandler(fn) { onSocket = fn; },
    stop() { for (const c of wss.clients) { try { c.terminate(); } catch {} } server.close(); },
  };
}

// In-page instrumentation, installed before any page script runs. Three seams of the kernel's pane shim
// (kernel.py _shim) are wrapped, each recorded in the instrument's file under a named function so a CPU
// profile can tell its samples apart:
//   1. WebSocket.prototype's onmessage setter, so the shim's handler is timed per wire frame: t0 at entry,
//      `handler` when it returns. That is the shim's synchronous work (parse, the keepalive and restart
//      answers, delta application, the held-state stamp, the enqueue); a resync ask (needSlot) sent from
//      inside it marks the frame as one the shim refused. On a shim from before the flush task the bundle's
//      render ran in here too, and the delivery wrapper below records it inside the handler.
//   2. MessagePort.prototype's onmessage setter, so the shim's flush task (ch.port1.onmessage = flush) is
//      timed per task, and MessagePort.prototype.postMessage, so the posts that arm it from inside a
//      handler (a frame queued) or a flush (the slice budget spent, the rest re-armed) are counted: the
//      queue is drained once every counted post has fired.
//   3. The handoff to the bundle: the shim's deliver() calls window.__rompFed.inbound("", m) once
//      federation.js has published its object, else dispatches a MessageEvent on window. The window slot
//      is trapped so the published inbound is wrapped, and window.dispatchEvent is wrapped for the
//      fallback; a dispatch made inside an inbound (federation re-emitting the merged frame to the pane)
//      is part of that delivery, never a second one. Each delivery records the delivered object's type,
//      its time, the flush or handler it ran in, and `settle` at the second requestAnimationFrame after
//      it (the main thread has rendered and is free). Handlers stamp a settle of their own for the
//      frames that never reach the bundle.
// Long animation frames are observed with script attribution; longtask is the fallback.
// Exported for the test that drives it on a blank page with a MessageChannel and a stub federation object.
export const INIT_SCRIPT = `
(() => {
  const R = window.__rompBench = { recs: [], flushes: [], deliveries: [], loaf: [], loafKind: null, n: 0, addListenerMessages: 0, portPosts: 0, portFires: 0 };
  let curRec = -1, curFlush = -1, depth = 0;
  const settleAfter = (row) => requestAnimationFrame(() => requestAnimationFrame(() => { row.settle = performance.now() - row.t0; }));
  const wsProto = WebSocket.prototype;
  const wsDesc = Object.getOwnPropertyDescriptor(wsProto, "onmessage");
  function wrapOnMessage(fn) {
    return function rompBenchOnMessage(ev) {
      const i = R.n++;
      const t0 = performance.now();
      const rec = { i, t0, len: typeof ev.data === "string" ? ev.data.length : -1, handler: -1, settle: -1, needSlot: false };
      R.recs.push(rec);
      const prev = curRec;
      curRec = i;
      try { return fn.call(this, ev); }
      finally {
        curRec = prev;
        rec.handler = performance.now() - t0;
        settleAfter(rec);
      }
    };
  }
  Object.defineProperty(wsProto, "onmessage", {
    configurable: true, enumerable: wsDesc.enumerable,
    get() { return wsDesc.get.call(this); },
    set(fn) { wsDesc.set.call(this, typeof fn === "function" ? wrapOnMessage(fn) : fn); },
  });
  const origAdd = wsProto.addEventListener;
  wsProto.addEventListener = function (type, fn, opts) { if (type === "message") R.addListenerMessages++; return origAdd.call(this, type, fn, opts); };
  const origSend = wsProto.send;
  wsProto.send = function (data) {
    if (curRec >= 0 && typeof data === "string" && data.indexOf('"needSlot"') >= 0) R.recs[curRec].needSlot = true;
    return origSend.call(this, data);
  };
  const portProto = MessagePort.prototype;
  const portDesc = Object.getOwnPropertyDescriptor(portProto, "onmessage");
  function wrapFlush(fn) {
    return function rompBenchFlush(ev) {
      const i = R.flushes.length;
      const t0 = performance.now();
      const row = { i, t0, ms: -1, deliveries: 0, err: null };
      R.flushes.push(row);
      R.portFires++;
      const prev = curFlush;
      curFlush = i;
      try { return fn.call(this, ev); }
      catch (e) { row.err = String((e && e.message) || e); throw e; }
      finally { curFlush = prev; row.ms = performance.now() - t0; }
    };
  }
  Object.defineProperty(portProto, "onmessage", {
    configurable: true, enumerable: portDesc.enumerable,
    get() { return portDesc.get.call(this); },
    set(fn) { portDesc.set.call(this, typeof fn === "function" ? wrapFlush(fn) : fn); },
  });
  const origPost = portProto.postMessage;
  portProto.postMessage = function () { if (curRec >= 0 || curFlush >= 0) R.portPosts++; return origPost.apply(this, arguments); };
  function rompBenchDeliver(type, via, run) {
    if (depth > 0) return run();
    const i = R.deliveries.length;
    const t0 = performance.now();
    const row = { i, type, via, t0, ms: -1, settle: -1, flush: curFlush, rec: curRec, err: null };
    R.deliveries.push(row);
    if (curFlush >= 0) R.flushes[curFlush].deliveries++;
    depth++;
    try { return run(); }
    catch (e) { row.err = String((e && e.message) || e); throw e; }
    finally { depth--; row.ms = performance.now() - t0; settleAfter(row); }
  }
  const typeOf = (m) => ((m && typeof m.type === "string") ? m.type : "?");
  let fed;
  Object.defineProperty(window, "__rompFed", {
    configurable: true, enumerable: true,
    get() { return fed; },
    set(v) {
      fed = v;
      if (v && typeof v.inbound === "function") { const inbound = v.inbound; v.inbound = function rompBenchInbound(h, m) { return rompBenchDeliver(typeOf(m), "inbound", () => inbound.call(v, h, m)); }; }
    },
  });
  const origDispatch = window.dispatchEvent;
  window.dispatchEvent = function rompBenchDispatch(ev) {
    if (ev && ev.type === "message" && typeof MessageEvent === "function" && ev instanceof MessageEvent) return rompBenchDeliver(typeOf(ev.data), "dispatch", () => origDispatch.call(this, ev));
    return origDispatch.call(this, ev);
  };
  const types = (window.PerformanceObserver && PerformanceObserver.supportedEntryTypes) || [];
  if (types.includes("long-animation-frame")) {
    R.loafKind = "long-animation-frame";
    R.obs = new PerformanceObserver((list) => { for (const e of list.getEntries()) R.loaf.push(loafRow(e)); });
    R.obs.observe({ type: "long-animation-frame", buffered: true });
  } else if (types.includes("longtask")) {
    R.loafKind = "longtask";
    R.obs = new PerformanceObserver((list) => { for (const e of list.getEntries()) R.loaf.push({ start: e.startTime, duration: e.duration, blocking: Math.max(0, e.duration - 50), scripts: [] }); });
    R.obs.observe({ type: "longtask", buffered: true });
  }
  function loafRow(e) {
    return { start: e.startTime, duration: e.duration, blocking: e.blockingDuration, renderStart: e.renderStart, styleAndLayoutStart: e.styleAndLayoutStart,
      scripts: Array.from(e.scripts || [], (s) => ({ url: s.sourceURL || "", fn: s.sourceFunctionName || "", invoker: s.invoker || "", invokerType: s.invokerType || "", duration: s.duration, line: s.sourceCharPosition })) };
  }
  R.collect = () => {
    if (R.obs) for (const e of R.obs.takeRecords()) R.loaf.push(R.loafKind === "long-animation-frame" ? loafRow(e) : { start: e.startTime, duration: e.duration, blocking: Math.max(0, e.duration - 50), scripts: [] });
    const mem = performance.memory ? { used: performance.memory.usedJSHeapSize, total: performance.memory.totalJSHeapSize, limit: performance.memory.jsHeapSizeLimit } : null;
    const bar = document.getElementById("romp-stale-self");   // the shim's banner: "build" when a keepalive's dv outran the served dist, "conn" for a dead socket
    return { recs: R.recs, flushes: R.flushes, deliveries: R.deliveries, portPosts: R.portPosts, portFires: R.portFires,
      loaf: R.loaf, loafKind: R.loafKind, domElements: document.getElementsByTagName("*").length, heap: mem, addListenerMessages: R.addListenerMessages,
      banner: bar ? (bar.dataset.kind || "?") : null };
  };
})();
//# sourceURL=ui-bench-instrument.js
`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, timeoutMs, what) {
  const t0 = Date.now();
  for (;;) {
    if (await fn()) return;
    if (Date.now() - t0 > timeoutMs) throw new Error(`timed out after ${timeoutMs} ms waiting for ${what}`);
    await sleep(25);
  }
}

/** Which browser --replay can launch: {ok, how, why}. Playwright's own Chromium when installed, else a
 *  system Google Chrome / Chromium through playwright's channel option. */
export function browserAvailability() {
  let chromium;
  try { ({ chromium } = requireExt("playwright")); } catch (e) { return { ok: false, why: `playwright is not installed under vscode-extension/ (npm ci there): ${e.message}` }; }
  try { const exe = chromium.executablePath(); if (exe && fs.existsSync(exe)) return { ok: true, how: "playwright chromium", exe }; } catch {}
  for (const name of ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]) {
    for (const dir of (process.env.PATH || "").split(path.delimiter)) {
      const p = path.join(dir, name);
      if (dir && fs.existsSync(p)) return { ok: true, how: `system ${name}`, exe: p, channel: name.startsWith("google") ? "chrome" : "chromium" };
    }
  }
  return { ok: false, why: "no Chromium: run `cd vscode-extension && npx playwright install chromium`, or install Google Chrome" };
}

/** Launch headless Chromium. Playwright creates the browser's profile and its artifacts directory with
 *  mkdtemp under os.tmpdir(), which follows TMPDIR, and removes them only from exit hooks a SIGKILL
 *  skips; with TMPDIR pointed at the run directory for the launch, both land inside it, where stop() and
 *  the next run's dead-owner sweep reach them. The browser process inherits the same TMPDIR. */
export async function launchBrowser({ tmpRoot } = {}) {
  const avail = browserAvailability();
  if (!avail.ok) throw new Error(avail.why);
  const { chromium } = requireExt("playwright");
  const saved = process.env.TMPDIR;
  if (tmpRoot) process.env.TMPDIR = tmpRoot;
  try {
    if (avail.how === "playwright chromium") return await chromium.launch({ headless: true });
    try { return await chromium.launch({ headless: true, channel: avail.channel }); }
    catch { return await chromium.launch({ headless: true, executablePath: avail.exe }); }
  } finally {
    if (tmpRoot) { if (saved === undefined) delete process.env.TMPDIR; else process.env.TMPDIR = saved; }
  }
}

async function replayOnce({ browser, app, frames, fast, gapMs = null, cpuThrottle, front, token, cpuProfile = false, log }) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const consoleErrors = [], pageErrors = [];
  let warnings = 0;
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); else if (m.type() === "warning") warnings++; });
  page.on("pageerror", (e) => pageErrors.push(String((e && e.message) || e)));
  const failedResources = [];
  page.on("response", (resp) => { if (resp.status() >= 400) failedResources.push(`${resp.status()} ${new URL(resp.url()).pathname}`); });
  await page.addInitScript(INIT_SCRIPT);
  const cdp = await context.newCDPSession(page);
  await cdp.send("Performance.enable");
  if (cpuThrottle && cpuThrottle !== 1) await cdp.send("Emulation.setCPUThrottlingRate", { rate: cpuThrottle });
  if (cpuProfile) {
    await cdp.send("Profiler.enable");
    await cdp.send("Profiler.setSamplingInterval", { interval: PROFILE_INTERVAL_US });
  }

  const session = { current: null, clientMessages: {}, clientDiag: {}, reconnects: 0, readyAt: 0 };
  let readyResolve;
  const ready = new Promise((r) => { readyResolve = r; });
  front.setSocketHandler((ws) => {
    ws.on("message", (data) => {
      let m = null;
      try { m = JSON.parse(data.toString()); } catch {}
      const type = (m && typeof m.type === "string") ? m.type : "?";
      session.clientMessages[type] = (session.clientMessages[type] || 0) + 1;
      if (type === "clientDiag" && m) { const w = `${m.surface || "?"}:${m.what || "?"}`; session.clientDiag[w] = (session.clientDiag[w] || 0) + 1; }
      if (type === "ready") {
        if (session.current && session.current !== ws) session.reconnects++;
        session.current = ws;
        if (!session.readyAt) session.readyAt = Date.now();
        readyResolve();
      }
    });
  });

  const navT0 = Date.now();
  await page.goto(`http://127.0.0.1:${front.port}/${app}?token=${encodeURIComponent(token)}`, { waitUntil: "load", timeout: 60_000 });
  let handshakeTimer;
  try {
    await Promise.race([ready, new Promise((_, rej) => { handshakeTimer = setTimeout(() => rej(new Error("the pane never sent its {type:\"ready\"} handshake within 30s")), 30_000); })]);
  } finally {
    clearTimeout(handshakeTimer);   // a losing timer left armed kept every replay process alive for the full 30 s
  }
  const readyMs = session.readyAt - navT0;
  // The profile's clock is V8's; the page's records are performance.now(). Bracketing Profiler.start
  // with two reads of performance.now() puts the profile's startTime between them, so a page time maps to
  // the profile's to within half the gap (alignMs, about a millisecond).
  let profiling = null;
  if (cpuProfile) {
    const before = await page.evaluate(() => performance.now());
    await cdp.send("Profiler.start");
    const after = await page.evaluate(() => performance.now());
    profiling = { p0: (before + after) / 2, alignMs: (after - before) / 2, profile: null };
  }

  const sent = [];
  const t0 = Date.now();
  let prev = frames.length ? frames[0].t : 0;
  for (const f of frames) {
    const gap = fast ? 0 : gapMs != null ? gapMs : Math.max(0, f.t - prev);
    prev = f.t;
    if (gap > 0) await sleep(gap);
    const ws = session.current;
    if (!ws || ws.readyState !== 1) throw new Error(`the pane's socket is not open at frame ${sent.length} (state ${ws ? ws.readyState : "none"})`);
    ws.send(f.data);
    sent.push({ type: classifyFrame(f.data), bytes: Buffer.byteLength(f.data, "utf8"), at: Date.now() - t0 });
  }
  const replayMs = Date.now() - t0;
  // Every sent frame dispatched in the page, then the shim's queue drained (every flush the frames armed has
  // run; a sliced flush re-arms itself and is counted again), then every settle stamped, then one more
  // rendered frame.
  const budget = 30_000 + sent.reduce((a, s) => a + s.bytes, 0) / 1024;   // a second per MB on top of the floor
  await waitFor(async () => (await page.evaluate(() => window.__rompBench.n)) >= sent.length, budget, `the page to dispatch all ${sent.length} frames`);
  await waitFor(async () => page.evaluate(() => window.__rompBench.portFires >= window.__rompBench.portPosts), budget, "the shim's queue to drain").catch((e) => log(`ui-bench: ${e.message}`));
  await waitFor(async () => page.evaluate(() => window.__rompBench.recs.every((r) => r.settle >= 0) && window.__rompBench.deliveries.every((d) => d.settle >= 0)), 10_000, "every frame's and delivery's settle stamp").catch((e) => log(`ui-bench: ${e.message}`));
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 0)))));
  if (profiling) {
    profiling.profile = (await cdp.send("Profiler.stop")).profile;
    await cdp.send("Profiler.disable");
  }
  // Collect the heap after a forced GC: without it the figure is live objects plus whatever garbage
  // happens to be pending, and two replays of the same page differed by a third. The figure before the
  // GC is kept beside it, so the report can show that the collection ran (afterGc is true only once the
  // CDP call has returned).
  const heapBeforeGc = Object.fromEntries((await cdp.send("Performance.getMetrics")).metrics.map((m) => [m.name, m.value])).JSHeapUsedSize;
  await cdp.send("HeapProfiler.collectGarbage");
  const afterGc = true;
  const data = await page.evaluate(() => window.__rompBench.collect());
  const { metrics } = await cdp.send("Performance.getMetrics");
  const met = Object.fromEntries(metrics.map((m) => [m.name, m.value]));
  await context.close();
  // which seam each delivery came through: federation's published inbound, or the MessageEvent fallback
  // the shim uses before federation.js has run (a page without federation would take it throughout)
  const deliverySeams = {};
  for (const d of data.deliveries) deliverySeams[d.via || "?"] = (deliverySeams[d.via || "?"] || 0) + 1;

  const handoffs = attributeDeliveries({ types: sent.map((s) => s.type), recs: data.recs, flushes: data.flushes, deliveries: data.deliveries });
  const perFrame = sent.map((s, i) => {
    const r = data.recs[i], h = handoffs.frames[i];
    return { i, type: s.type, bytes: s.bytes, at: s.at, handlerMs: r ? r.handler : null, settleMs: h.settleMs, t0: r ? r.t0 : null, lenMatch: r ? r.len === frames[i].data.length : false,
      handoff: h.handoff, bundleMs: h.bundleMs, delivery: h.delivery };
  });
  const misaligned = perFrame.filter((f) => f.handlerMs != null && !f.lenMatch).length;
  return {
    readyMs, replayMs, sent, perFrame, misaligned, reconnects: session.reconnects, clientMessages: session.clientMessages, clientDiag: session.clientDiag,
    handoff: handoffs.handoff, flushes: data.flushes.length, deliveries: data.deliveries.length, deliveryRows: data.deliveries.map((d) => ({ t0: d.t0, ms: d.ms, type: d.type })),
    flushRows: data.flushes.map((f) => ({ t0: f.t0, ms: f.ms })), unattributed: handoffs.unattributed.length, unattributedMs: handoffs.unattributed.reduce((a, d) => a + Math.max(0, d.ms), 0),
    deliveryErrors: data.deliveries.filter((d) => d.err).map((d) => `${d.type}: ${d.err}`), queueDrained: data.portFires >= data.portPosts, deliverySeams,
    loaf: data.loaf, loafKind: data.loafKind, domElements: data.domElements, heap: data.heap, addListenerMessages: data.addListenerMessages, banner: data.banner,
    profiling, afterGc,
    cdp: { nodes: met.Nodes, documents: met.Documents, jsEventListeners: met.JSEventListeners, layoutCount: met.LayoutCount, recalcStyleCount: met.RecalcStyleCount,
      layoutMs: (met.LayoutDuration || 0) * 1000, recalcStyleMs: (met.RecalcStyleDuration || 0) * 1000, scriptMs: (met.ScriptDuration || 0) * 1000,
      taskMs: (met.TaskDuration || 0) * 1000, heapUsed: met.JSHeapUsedSize, heapTotal: met.JSHeapTotalSize, heapBeforeGc },
    consoleErrors, pageErrors, warnings, failedResources,
  };
}

// ── the handoff: which wire frame each delivery to the bundle carried ─────────────────────────────

/** kernel.py _shim WHOLE: the kinds whose newer frame replaces an older one still queued (a whole state,
 *  not a chained update), so several wire frames of one kind can reach the bundle as one delivery. */
export const WHOLE_STATE_KINDS = new Set(["feed", "bars", "data", "tabOrder", "working", "globalRetryPaused"]);
/** Frames the shim answers itself and never hands on: keepalives and the kernel's restart notice. */
const SHIM_ANSWERS = new Set(["ka", "restarting"]);
/** The kind a wire frame reaches the bundle as: a view delta is reassembled into the whole slot it patches. */
export const deliveredKind = (wireType) => (typeof wireType === "string" && wireType.startsWith("delta:") ? wireType.slice("delta:".length) : wireType);

/** Pair the page's deliveries with the wire frames they carried. `types` are the sent frames' wire types
 *  in order, `recs` the page's per-frame handler records (t0, handler, needSlot, settle), `flushes` its
 *  flush tasks (i, t0, ms) and `deliveries` its handoffs to the bundle (i, type, t0, ms, settle, flush,
 *  rec). The shim's rules decide the pairing: a delivery made inside a handler belongs to that handler's
 *  frame (a shim from before the flush task); a delivery made in a flush carries frames whose handler had
 *  finished before the flush began and whose delivered kind is the delivery's type, all of them for a
 *  whole-state kind (the newest is the one delivered, the others were coalesced into it) and the oldest
 *  one for a chained kind; keepalives, restart notices and deltas the shim refused (needSlot) are the
 *  shim's alone. Frames left over were still queued when the run ended. Each frame's settle becomes
 *  end-to-end: its own receipt to the main thread free after the delivery that carried it.
 *  Returns {frames: [{handoff, bundleMs, delivery, settleMs}], handoff: "flush"|"handler"|"none",
 *  unattributed: deliveries no frame explains (the page re-emitting state on its own)}. */
export function attributeDeliveries({ types, recs, flushes, deliveries }) {
  const frames = types.map((type, i) => {
    const r = recs[i];
    if (!r || r.handler == null || r.handler < 0) return { handoff: null, bundleMs: null, delivery: -1, settleMs: r ? r.settle : null };
    return { handoff: (SHIM_ANSWERS.has(type) || r.needSlot) ? "shim" : null, bundleMs: null, delivery: -1, settleMs: r.settle };
  });
  const attach = (i, d, own) => {
    const f = frames[i];
    f.handoff = own ? "delivered" : "coalesced";
    f.delivery = d.i;
    if (own) f.bundleMs = d.ms;
    f.settleMs = d.settle != null && d.settle >= 0 ? d.t0 + d.settle - recs[i].t0 : -1;
  };
  const unattributed = [];
  const inFlush = new Map();
  for (const d of deliveries) {
    if (d.rec != null && d.rec >= 0 && frames[d.rec] && frames[d.rec].handoff === null) { attach(d.rec, d, true); continue; }
    if (d.flush != null && d.flush >= 0) { if (!inFlush.has(d.flush)) inFlush.set(d.flush, []); inFlush.get(d.flush).push(d); continue; }
    unattributed.push(d);
  }
  for (const fl of [...flushes].sort((a, b) => a.t0 - b.t0)) {
    for (const d of inFlush.get(fl.i) || []) {
      const cands = [];
      for (let i = 0; i < frames.length; i++) {
        const r = recs[i];
        if (frames[i].handoff === null && r && r.t0 + r.handler <= fl.t0 && deliveredKind(types[i]) === d.type) cands.push(i);
      }
      if (!cands.length) { unattributed.push(d); continue; }
      if (WHOLE_STATE_KINDS.has(d.type)) { const last = cands[cands.length - 1]; for (const i of cands) attach(i, d, i === last); }
      else attach(cands[0], d, true);
    }
  }
  for (const f of frames) if (f.handoff === null && f.settleMs != null) f.handoff = "queued";
  const handoff = flushes.length ? "flush" : deliveries.some((d) => d.rec != null && d.rec >= 0) ? "handler" : "none";
  return { frames, handoff, unattributed };
}

function attributeLoaf(loaf, handoff = "none") {
  const by = new Map();
  for (const e of loaf) {
    for (const s of e.scripts || []) {
      const url = s.url ? path.basename(s.url.split("?")[0]) : "(inline)";
      const inv = s.invoker || s.invokerType || "?";
      const invoker = /^https?:\/\//.test(inv) ? "script " + (path.basename(inv.split("?")[0]) || "/") : inv;   // a script's own evaluation is invoked by its URL; keep the basename, never the query
      // A long-animation-frame script entry names the task's ENTRY POINT. For every pushed frame that is
      // the bench's own onmessage wrapper, and for every flush of the shim's queue its flush wrapper, so
      // those rows are labelled for what runs inside them rather than for the instrument's file: the shim's
      // handler (plus the bundle's render, on a shim that hands frames over inside the handler), and the
      // flush task with the bundle's renders it carries. The instrument's OTHER entry points are its own
      // bookkeeping: the settle stamp (the requestAnimationFrame callback that reads the clock), the
      // long-animation-frame observer's callback (which records the entries) and the collector the bench
      // evaluates at the end. No pane work runs inside any of them, and one appears here only when a
      // descheduled main thread stretched it past the entry threshold (the settle stamp once on a run pinned
      // to one CPU, the observer's callback once on a loaded box; review find, 2026-09-08), so its row says
      // what it is instead of naming the file as if the instrument had done work.
      let key;
      if (url !== "ui-bench-instrument.js") key = `${url}:${s.fn || "(anonymous)"} <${invoker}>`;
      else if (s.fn === "rompBenchFlush" || /MessagePort/.test(inv)) key = `bundle handoff (shim flush + bundle) <${invoker}>`;
      else if (s.fn === "rompBenchOnMessage" || /WebSocket/.test(inv)) key = `message handler (${handoff === "flush" ? "shim" : "shim + bundle"}) <${invoker}>`;
      else {
        const what = /FrameRequestCallback/.test(inv) ? "the settle stamp's requestAnimationFrame"
          : /PerformanceObserver/.test(inv) ? "the long-animation-frame observer's callback" : s.fn || "(anonymous)";
        key = `instrument bookkeeping (${what}; no pane work) <${invoker}>`;
      }
      const row = by.get(key) || { key, count: 0, durationMs: 0 };
      row.count++; row.durationMs += s.duration || 0;
      by.set(key, row);
    }
  }
  return Array.from(by.values()).sort((a, b) => b.durationMs - a.durationMs).slice(0, 10)
    .map((r) => ({ ...r, durationMs: round1(r.durationMs) }));
}

const round1 = (x) => (x == null ? null : Math.round(x * 10) / 10);
const roundStats = (s) => ({ n: s.n, p50: round1(s.p50), p90: round1(s.p90), max: round1(s.max), mean: round1(s.mean) });

/** Fold one or more replay runs into the report shape --json writes and --compare reads. */
export function buildReport({ app, framesFile, cpuThrottle, fast, gapMs = null, iters, browser, runs, cpuProfileFiles = [], sourceMapDir = null }) {
  const frames = runs.flatMap((r) => r.perFrame);
  const byType = {};
  for (const f of frames) {
    const s = (byType[f.type] ||= { count: 0, measured: 0, delivered: 0, coalesced: 0, shim: 0, queued: 0, settleMissing: 0, bytes: 0, bytesMax: 0, handler: [], bundle: [], settle: [] });
    s.count++; s.bytes += f.bytes; if (f.bytes > s.bytesMax) s.bytesMax = f.bytes;
    if (f.handlerMs != null && f.handlerMs >= 0) { s.measured++; s.handler.push(f.handlerMs); }
    if (f.handoff === "delivered") { s.delivered++; if (f.bundleMs != null && f.bundleMs >= 0) s.bundle.push(f.bundleMs); }
    else if (f.handoff === "coalesced") s.coalesced++;
    else if (f.handoff === "shim") s.shim++;
    else if (f.handoff === "queued") s.queued++;
    if (f.settleMs != null && f.settleMs >= 0) s.settle.push(f.settleMs);
    else if (f.settleMs != null) s.settleMissing++;   // dispatched and timed, but the two-rAF settle stamp never landed
  }
  const types = {};
  const perRun = (n) => round1(n / runs.length);   // pooled iterations: a per-run count, fractional when the runs differ
  for (const [type, s] of Object.entries(byType).sort((a, b) => b[1].bytes - a[1].bytes)) {
    types[type] = { count: perRun(s.count), measured: perRun(s.measured), delivered: perRun(s.delivered), coalesced: perRun(s.coalesced),
      shim: perRun(s.shim), queued: perRun(s.queued), settleMissing: s.settleMissing, bytes: s.bytes / runs.length, bytesMax: s.bytesMax,
      handlerMs: roundStats(summarize(s.handler)), bundleMs: roundStats(summarize(s.bundle)), settleMs: roundStats(summarize(s.settle)) };
  }
  const settleMissing = Object.values(byType).reduce((a, s) => a + s.settleMissing, 0);
  const firstIdx = runs[0].perFrame.findIndex((f) => f.type !== "ka");
  const first = firstIdx >= 0 ? runs.map((r) => r.perFrame[firstIdx]).filter(Boolean) : [];
  const loafAll = runs.flatMap((r) => r.loaf);
  const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  // How the shim handed frames to the bundle in these runs: in its flush task, inside the WebSocket handler
  // (a shim from before the task), or not seen at all (no delivery reached either wrapper).
  const modes = new Set(runs.map((r) => r.handoff || "none"));
  const handoff = modes.size === 1 ? [...modes][0] : "mixed";
  const firstBundle = first.filter((f) => f.handoff === "delivered" && f.bundleMs != null);
  return {
    tool: "ui-bench", version: 2, app, framesFile, cpuThrottle: cpuThrottle || 1, fast: !!fast, gapMs: fast ? null : gapMs, iters: runs.length, browser,
    generatedAt: new Date().toISOString(),
    frames: { total: runs[0].perFrame.length, bytes: runs[0].perFrame.reduce((a, f) => a + f.bytes, 0), replayMs: round1(mean(runs.map((r) => r.replayMs))),
      readyMs: round1(mean(runs.map((r) => r.readyMs))), reconnects: runs.reduce((a, r) => a + r.reconnects, 0), misaligned: runs.reduce((a, r) => a + r.misaligned, 0),
      settleMissing, addListenerMessages: runs.reduce((a, r) => a + (r.addListenerMessages || 0), 0),
      buildBannerRaised: runs.filter((r) => r.banner === "build").length, connBannerRaised: runs.filter((r) => r.banner === "conn").length },
    handoff: { mode: handoff, flushes: round1(mean(runs.map((r) => r.flushes || 0))), deliveries: round1(mean(runs.map((r) => r.deliveries || 0))),
      delivered: perRun(Object.values(byType).reduce((a, s) => a + s.delivered, 0)), coalesced: perRun(Object.values(byType).reduce((a, s) => a + s.coalesced, 0)),
      shim: perRun(Object.values(byType).reduce((a, s) => a + s.shim, 0)), queued: perRun(Object.values(byType).reduce((a, s) => a + s.queued, 0)),
      unattributed: runs.reduce((a, r) => a + (r.unattributed || 0), 0), unattributedMs: round1(runs.reduce((a, r) => a + (r.unattributedMs || 0), 0)),
      queueDrained: runs.every((r) => r.queueDrained !== false), deliveryErrors: runs.flatMap((r) => r.deliveryErrors || []),
      deliverySeams: runs.reduce((acc, r) => { for (const [k, v] of Object.entries(r.deliverySeams || {})) acc[k] = (acc[k] || 0) + v; return acc; }, {}) },
    // The first content frame over the iterations; its bundle time is the mean over the runs that delivered it
    // on its own (`deliveredRuns`), since a run that queued a newer frame behind it before the flush ran
    // coalesced it and has no render of it to time.
    first: first.length ? { index: firstIdx, type: first[0].type, bytes: first[0].bytes, handlerMs: round1(mean(first.map((f) => f.handlerMs))),
      bundleMs: firstBundle.length ? round1(mean(firstBundle.map((f) => f.bundleMs))) : null, deliveredRuns: firstBundle.length,
      handoff: firstBundle.length === first.length ? (first[0].handoff || null) : firstBundle.length ? "mixed" : (first[0].handoff || null),
      settleMs: round1(mean(first.map((f) => f.settleMs))) } : null,
    types,
    loaf: { kind: runs[0].loafKind, count: round1(loafAll.length / runs.length), durationMs: round1(mean(runs.map((r) => r.loaf.reduce((a, e) => a + e.duration, 0)))),
      blockingMs: round1(mean(runs.map((r) => r.loaf.reduce((a, e) => a + (e.blocking || 0), 0)))), maxMs: round1(Math.max(0, ...loafAll.map((e) => e.duration))),
      topScripts: attributeLoaf(loafAll, handoff) },
    // afterGc is what the runs report: true only when every run's forced collection returned before the read
    end: { afterGc: runs.every((r) => r.afterGc === true), heapUsed: Math.round(mean(runs.map((r) => r.cdp.heapUsed ?? (r.heap ? r.heap.used : 0)))),
      heapBeforeGc: runs.every((r) => r.cdp.heapBeforeGc != null) ? Math.round(mean(runs.map((r) => r.cdp.heapBeforeGc))) : null,
      heapTotal: Math.round(mean(runs.map((r) => r.cdp.heapTotal ?? (r.heap ? r.heap.total : 0)))),
      domElements: Math.round(mean(runs.map((r) => r.domElements))), cdpNodes: Math.round(mean(runs.map((r) => r.cdp.nodes))), jsEventListeners: Math.round(mean(runs.map((r) => r.cdp.jsEventListeners))),
      layoutCount: Math.round(mean(runs.map((r) => r.cdp.layoutCount))), recalcStyleCount: Math.round(mean(runs.map((r) => r.cdp.recalcStyleCount))),
      layoutMs: round1(mean(runs.map((r) => r.cdp.layoutMs))), recalcStyleMs: round1(mean(runs.map((r) => r.cdp.recalcStyleMs))),
      scriptMs: round1(mean(runs.map((r) => r.cdp.scriptMs))), taskMs: round1(mean(runs.map((r) => r.cdp.taskMs))) },
    console: { errors: runs.flatMap((r) => r.consoleErrors), pageErrors: runs.flatMap((r) => r.pageErrors), warnings: runs.reduce((a, r) => a + r.warnings, 0),
      failedResources: runs.flatMap((r) => r.failedResources) },
    clientMessages: runs.reduce((acc, r) => { for (const [k, v] of Object.entries(r.clientMessages)) acc[k] = (acc[k] || 0) + v; return acc; }, {}),
    clientDiag: runs.reduce((acc, r) => { for (const [k, v] of Object.entries(r.clientDiag)) acc[k] = (acc[k] || 0) + v; return acc; }, {}),
    perFrame: runs.length === 1 ? runs[0].perFrame.map((f) => ({ i: f.i, type: f.type, bytes: f.bytes, at: f.at, handlerMs: round1(f.handlerMs), handoff: f.handoff ?? null, bundleMs: round1(f.bundleMs), settleMs: round1(f.settleMs) })) : undefined,
    cpuProfile: runs.some((r) => r.profiling && r.profiling.profile) ? profileReport(runs, firstIdx, cpuProfileFiles, sourceMapDir) : undefined,
  };
}

export async function replay({ app, framesFile, cpuThrottle = 1, iters = 1, fast = false, gapMs = null, dist, jsonOut, cpuProfile, log = console.error }) {
  if (!APPS.includes(app)) throw new Error(`unknown app ${app}; one of ${APPS.join(", ")}`);
  if (gapMs != null && !(Number.isFinite(gapMs) && gapMs >= 0)) throw new Error(`--gap needs a number of milliseconds, not ${gapMs}`);
  const { frames } = loadFrames(framesFile);
  if (!frames.length) throw new Error(`no frames in ${framesFile}`);
  const pageServer = await startPageServer({ dist, log: (s) => log("kernel page server: " + s.trimEnd()) });
  let front, browser;
  // A signal aimed at this process stops both servers before it exits; the finally below covers every
  // other way out. The Handler would also end on its own when its stdin pipe closes, but not the front.
  const onSignal = (sig) => {
    if (browser) browser.close().catch(() => {});
    if (front) front.stop();
    pageServer.stop();
    process.exit(sig === "SIGINT" ? 130 : 143);
  };
  process.once("SIGINT", onSignal);
  process.once("SIGTERM", onSignal);
  try {
    front = await startFront({ pagePort: pageServer.port });
    browser = await launchBrowser({ tmpRoot: pageServer.tmp });
    const runs = [];
    for (let i = 0; i < iters; i++) {
      log(`ui-bench: replaying ${frames.length} frames into app=${app} (${pacingLabel(fast, gapMs)}, cpu x${cpuThrottle})${iters > 1 ? ` iteration ${i + 1}/${iters}` : ""}`);
      runs.push(await replayOnce({ browser, app, frames, fast, gapMs, cpuThrottle, front, token: pageServer.token, cpuProfile: !!cpuProfile, log }));
    }
    const cpuProfileFiles = cpuProfile ? writeProfiles(cpuProfile, runs) : [];
    const report = buildReport({ app, framesFile, cpuThrottle, fast, gapMs, iters, browser: browser.version(), runs, cpuProfileFiles, sourceMapDir: dist || path.join(EXT_DIR, "dist") });
    if (jsonOut) { fs.mkdirSync(path.dirname(path.resolve(jsonOut)), { recursive: true }); fs.writeFileSync(jsonOut, JSON.stringify(report, null, 1) + "\n"); }
    return report;
  } finally {
    process.removeListener("SIGINT", onSignal);
    process.removeListener("SIGTERM", onSignal);
    if (browser) await browser.close().catch(() => {});
    if (front) front.stop();
    pageServer.stop();
  }
}

// ── --cpu-profile: V8 samples across the replay, folded by function ─────────────────────────────

export const PROFILE_INTERVAL_US = 500;
const PROFILE_META = new Set(["(root)", "(program)", "(idle)", "(garbage collector)"]);
const TOP_OVERALL = 25, TOP_WINDOW = 20;

/** The label a call frame files under: url-basename:function:line, the line 1-based as DevTools shows
 *  it. V8's bookkeeping nodes ((program), (idle), (garbage collector), (root)) keep their bare names; a
 *  builtin (no url, no line: getBoundingClientRect, querySelectorAll) is "(native):name"; code without a
 *  url but with a line (an eval) is "(inline)". A page's inline script carries the page URL, so the shim
 *  files under the page's basename with the query dropped. */
export function frameKey(cf) {
  if (!cf) return "(unknown)";
  const fn = cf.functionName || "";
  const line = cf.lineNumber ?? -1;
  if (!cf.url) {
    if (PROFILE_META.has(fn)) return fn;
    if (line < 0) return `(native):${fn || "(anonymous)"}`;
    return `(inline):${fn || "(anonymous)"}:${line + 1}`;
  }
  return `${path.basename(cf.url.split("?")[0]) || cf.url}:${fn || "(anonymous)"}:${line + 1}`;
}

/** Bundle positions to source positions through the .map files esbuild writes beside the bundles in
 *  `distDir`. Returns a function of a call frame giving "ui/webview/feed.ts:4477" (relative to the repo
 *  when the source lies inside it) or null when there is no map or no mapping. */
export function sourceLocator(distDir) {
  const maps = new Map();
  const loaded = new Set(), missing = new Set();
  const load = (base) => {
    if (maps.has(base)) return maps.get(base);
    let m = null;
    const file = path.join(distDir, base + ".map");
    try { m = { sm: new SourceMap(JSON.parse(fs.readFileSync(file, "utf8"))), dir: path.dirname(file) }; } catch { m = null; }
    maps.set(base, m);
    (m ? loaded : missing).add(base);
    return m;
  };
  const locate = (cf) => {
    if (!cf || !cf.url || cf.lineNumber == null || cf.lineNumber < 0) return null;
    const m = load(path.basename(cf.url.split("?")[0]));
    if (!m) return null;
    const e = m.sm.findEntry(cf.lineNumber, Math.max(0, cf.columnNumber || 0));
    // findEntry returns the nearest mapping at or before the position, on any line; a line the map does not
    // cover would borrow the previous line's source, so the mapping must sit on the asked-for line.
    if (!e || e.originalSource == null || e.originalLine == null || e.generatedLine !== cf.lineNumber) return null;
    const abs = path.resolve(m.dir, e.originalSource);
    const rel = path.relative(REPO, abs);
    const shown = rel && !rel.startsWith("..") && !path.isAbsolute(rel) ? rel : e.originalSource.replace(/^(\.\.\/)+/, "");
    return `${shown}:${e.originalLine + 1}`;
  };
  /** The source of a 0-based bundle LINE's first mapping, for V8's per-line ticks. A line's first mapping
   *  can start at its indentation column (bundled node_modules code does this), where a column-0 probe
   *  lands on the previous line and the same-line guard refuses it; so the columns are stepped until a
   *  mapping on the line answers. */
  locate.line = (cf, line) => {
    if (!cf || !cf.url || line == null || line < 0 || !load(path.basename(cf.url.split("?")[0]))) return null;
    for (let c = 0; c < 256; c++) { const r = locate({ ...cf, lineNumber: line, columnNumber: c }); if (r) return r; }
    return null;
  };
  /** Whether a bundle's map is beside it (loads it). */
  locate.probe = (base) => !!load(base);
  locate.loaded = loaded;
  locate.missing = missing;
  return locate;
}

/** The instrument's wrappers whose samples are wrapper time: the shim's handler, its flush task and a
 *  delivery to the bundle. The settle stamps and the passthrough of other events are in the same file but
 *  run outside every window, so a URL match alone would count them against the alignment. */
const WRAPPER_FUNCTIONS = new Set(["rompBenchOnMessage", "rompBenchFlush", "rompBenchDeliver"]);

/** Sort [t0, t1] windows and merge the ones that touch or overlap (a delivery inside a flush, a flush
 *  starting as a handler ends), so a binary search on the starts answers "inside any window". */
export function mergeWindows(windows) {
  const out = [];
  for (const w of windows.filter((w) => w[1] > w[0]).sort((a, b) => a[0] - b[0])) {
    const last = out[out.length - 1];
    if (last && w[0] <= last[1]) last[1] = Math.max(last[1], w[1]);
    else out.push([w[0], w[1]]);
  }
  return out;
}

/** Pin the page-to-profile clock offset with the profile's own evidence. Every sample whose stack holds
 *  one of the bench's wrappers was taken inside some frame's handler window, some flush of the shim's
 *  queue or some delivery, so the offsets that put the most of them inside the windows hold the right
 *  one; the bracketing reads of performance.now() only bound it. A grid search over ±bound ms at a
 *  quarter of the sampling interval; the offsets that tie for the maximum form a plateau, and the answer
 *  is its midpoint with half its width (at least one grid step) as the uncertainty. When even the best
 *  offset places under half the wrapper's samples inside, the evidence does not fit the windows and the
 *  bracketing estimate is returned unchanged. `windows` are [t0, t1] in page ms, overlapping allowed.
 *  Returns {p0, alignMs, inside, refined}. */
export function refineAlignment(profile, p0, bound, windows) {
  const nodes = new Map(), parent = new Map(), wrapped = new Map();
  for (const n of profile.nodes || []) { nodes.set(n.id, n); for (const c of n.children || []) parent.set(c, n.id); }
  const underWrapper = (id) => {
    if (wrapped.has(id)) return wrapped.get(id);
    let r = false;
    for (let cur = id; cur != null && nodes.has(cur); cur = parent.get(cur)) {
      const cf = nodes.get(cur).callFrame;
      if (cf && cf.url && WRAPPER_FUNCTIONS.has(cf.functionName) && path.basename(cf.url.split("?")[0]) === "ui-bench-instrument.js") { r = true; break; }
    }
    wrapped.set(id, r);
    return r;
  };
  const xs = [];
  const samples = profile.samples || [], deltas = profile.timeDeltas || [];
  let t = profile.startTime || 0;
  for (let i = 0; i < samples.length; i++) { t += deltas[i] || 0; if (underWrapper(samples[i])) xs.push((t - (profile.startTime || 0)) / 1000 + p0); }
  const sorted = mergeWindows(windows);
  if (!xs.length || !sorted.length) return { p0, alignMs: bound, inside: null, refined: false };
  const inside = (x) => {
    let lo = 0, hi = sorted.length - 1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (sorted[mid][0] <= x) lo = mid + 1; else hi = mid - 1; }
    return hi >= 0 && x < sorted[hi][1];
  };
  const step = PROFILE_INTERVAL_US / 1000 / 4;
  const span = Math.max(bound, 1);
  let bestN = -1;
  const plateau = [];
  for (let k = 0, d = -span; d <= span + 1e-9; k++, d = -span + k * step) {
    let n = 0;
    for (const x of xs) if (inside(x + d)) n++;
    if (n > bestN) { bestN = n; plateau.length = 0; }
    if (n === bestN) plateau.push(d);
  }
  const share = bestN / xs.length;
  if (share < 0.5) return { p0, alignMs: bound, inside: share, refined: false };
  const lo = plateau[0], hi = plateau[plateau.length - 1];
  return { p0: p0 + (lo + hi) / 2, alignMs: Math.max(step, (hi - lo) / 2), inside: share, refined: true };
}

/** Fold a V8 .cpuprofile ({nodes, samples, timeDeltas, startTime, endTime}, times in microseconds)
 *  into per-function time. Sample i owns the interval to sample i+1 (the last owns the interval to
 *  endTime): its node's function takes it as self time, and every distinct function on its stack takes
 *  it once as total time, so recursion is not double counted. The bookkeeping nodes are reported in
 *  `meta`, never ranked. `window` = [fromUs, toUs] on the profile's clock restricts the fold. */
export function aggregateProfile(profile, window = null) {
  const nodes = new Map(), parent = new Map(), keyOf = new Map(), stacks = new Map(), cfOf = new Map();
  for (const n of profile.nodes || []) { nodes.set(n.id, n); for (const c of n.children || []) parent.set(c, n.id); }
  const key = (id) => {
    let k = keyOf.get(id);
    if (k === undefined) { const n = nodes.get(id); k = frameKey(n && n.callFrame); keyOf.set(id, k); if (n && n.callFrame && !cfOf.has(k)) cfOf.set(k, n.callFrame); }
    return k;
  };
  const stackKeys = (id) => {
    let s = stacks.get(id);
    if (s) return s;
    const seen = new Set();
    for (let cur = id; cur != null && nodes.has(cur); cur = parent.get(cur)) { const k = key(cur); if (!PROFILE_META.has(k)) seen.add(k); }
    s = [...seen]; stacks.set(id, s); return s;
  };
  const self = new Map(), total = new Map(), count = new Map(), nodeSelf = new Map(), meta = {};
  const samples = profile.samples || [], deltas = profile.timeDeltas || [];
  let t = profile.startTime || 0, sampledUs = 0, inWindow = 0;
  for (let i = 0; i < samples.length; i++) {
    t += deltas[i] || 0;
    const dur = i + 1 < samples.length ? (deltas[i + 1] || 0) : Math.max(0, (profile.endTime || t) - t);
    if (window && (t < window[0] || t >= window[1])) continue;
    sampledUs += dur; inWindow++;
    const k = key(samples[i]);
    if (PROFILE_META.has(k)) { meta[k] = (meta[k] || 0) + dur; continue; }
    self.set(k, (self.get(k) || 0) + dur);
    count.set(k, (count.get(k) || 0) + 1);
    nodeSelf.set(samples[i], (nodeSelf.get(samples[i]) || 0) + dur);
    for (const sk of stackKeys(samples[i])) total.set(sk, (total.get(sk) || 0) + dur);
  }
  // V8's per-line ticks (positionTicks) split a node's self time over the lines of its function, 1-based
  // lines of the bundle. They cover the whole profile, so a window gets none; a node's self time is spread
  // over its lines in proportion to their ticks.
  const lines = new Map();
  if (!window) for (const n of profile.nodes || []) {
    if (!n.positionTicks || !n.positionTicks.length || !n.hitCount || !nodeSelf.has(n.id)) continue;
    const k = key(n.id);
    if (PROFILE_META.has(k)) continue;
    const perTick = nodeSelf.get(n.id) / n.hitCount;
    const m = lines.get(k) || new Map();
    for (const pt of n.positionTicks) m.set(pt.line, (m.get(pt.line) || 0) + pt.ticks * perTick);
    lines.set(k, m);
  }
  const functions = [...total.keys()].map((k) => ({ key: k, selfMs: (self.get(k) || 0) / 1000, totalMs: total.get(k) / 1000, samples: count.get(k) || 0, cf: cfOf.get(k),
    ...(lines.has(k) ? { lines: [...lines.get(k)].map(([line, us]) => ({ line, ms: us / 1000 })).sort((a, b) => b.ms - a.ms) } : {}) }));
  return { durationMs: (window ? window[1] - window[0] : (profile.endTime || 0) - (profile.startTime || 0)) / 1000, sampledMs: sampledUs / 1000, samples: inWindow,
    meta: Object.fromEntries(Object.entries(meta).map(([k, v]) => [k, v / 1000])), functions };
}

const bySelf = (fns) => [...fns].sort((a, b) => b.selfMs - a.selfMs || b.totalMs - a.totalMs || a.key.localeCompare(b.key));
const byTotal = (fns) => [...fns].sort((a, b) => b.totalMs - a.totalMs || b.selfMs - a.selfMs || a.key.localeCompare(b.key));
const HOT_LINES = 4, HOT_LINE_SHARE = 0.05;
const roundFn = (locate, withLines = false) => (f) => {
  const src = locate ? locate(f.cf) : null;
  const row = { key: f.key, selfMs: round1(f.selfMs), totalMs: round1(f.totalMs), samples: f.samples, ...(src ? { src } : {}) };
  if (withLines && f.lines && f.selfMs > 0 && f.cf && f.cf.url) {
    // The lines of the function that hold its self time (the 1-based bundle line, its share, its source). A
    // builtin's ticks name its call sites' lines with no file to read them in, so those stay unlisted.
    row.lines = f.lines.filter((l) => l.ms / f.selfMs >= HOT_LINE_SHARE).slice(0, HOT_LINES).map((l) => {
      const at = locate && f.cf ? locate.line(f.cf, l.line - 1) : null;
      return { line: l.line, ms: round1(l.ms), share: Math.round((l.ms / f.selfMs) * 100) / 100, ...(at ? { src: at } : {}) };
    });
  }
  return row;
};

/** Sum aggregates from several runs (iterations) by function key. */
export function mergeAggregates(aggs) {
  const fns = new Map(), meta = {};
  let durationMs = 0, sampledMs = 0, samples = 0;
  for (const a of aggs) {
    durationMs += a.durationMs; sampledMs += a.sampledMs; samples += a.samples;
    for (const [k, v] of Object.entries(a.meta)) meta[k] = (meta[k] || 0) + v;
    for (const f of a.functions) {
      const m = fns.get(f.key) || { key: f.key, selfMs: 0, totalMs: 0, samples: 0, cf: f.cf };
      m.selfMs += f.selfMs; m.totalMs += f.totalMs; m.samples += f.samples;
      if (f.lines) { const ln = new Map((m.lines || []).map((l) => [l.line, l.ms])); for (const l of f.lines) ln.set(l.line, (ln.get(l.line) || 0) + l.ms); m.lines = [...ln].map(([line, ms]) => ({ line, ms })).sort((a, b) => b.ms - a.ms); }
      fns.set(f.key, m);
    }
  }
  return { durationMs, sampledMs, samples, meta, functions: [...fns.values()] };
}

/** Rank an aggregate: the top functions by self time and by total time, the bookkeeping totals beside;
 *  `locate` (sourceLocator) adds each function's source position when the dist carries maps. */
export function rankProfile(agg, top, locate = null) {
  const r = roundFn(locate), rl = roundFn(locate, true);
  const self = bySelf(agg.functions);
  const hot = Math.min(top, HOT_FUNCTIONS);
  return { durationMs: round1(agg.durationMs), sampledMs: round1(agg.sampledMs), samples: agg.samples, functions: agg.functions.length,
    meta: Object.fromEntries(Object.entries(agg.meta).map(([k, v]) => [k, round1(v)])),
    topSelf: [...self.slice(0, hot).map(rl), ...self.slice(hot, top).map(r)], topTotal: byTotal(agg.functions).slice(0, top).map(r) };
}
const HOT_FUNCTIONS = 5;   // the top self-time functions whose lines are shown

/** The frames worth their own window: the first content frame and the largest frame of every type
 *  except keepalives. A frame's window is the delivery that carried it (t0 to t0 + the bundle's time;
 *  for a coalesced frame, the newer frame's delivery it rode in), the part of a frame's cost the
 *  JavaScript sampler can see; style, layout and paint after it are not JavaScript. A frame that never
 *  reached the bundle falls back to its handler window. Frames that share a delivery share a window. */
function profileWindows(perFrame, firstIdx) {
  const picks = [];
  if (firstIdx >= 0 && perFrame[firstIdx]) picks.push({ label: "first content frame", f: perFrame[firstIdx] });
  const largest = new Map();
  for (const f of perFrame) { if (f.type === "ka") continue; const cur = largest.get(f.type); if (!cur || f.bytes > cur.bytes) largest.set(f.type, f); }
  for (const [type, f] of largest) if (!picks.some((p) => p.f.i === f.i)) picks.push({ label: `largest ${type}`, f });
  const byWindow = new Map();
  for (const p of picks) {
    const key = p.f.delivery != null && p.f.delivery >= 0 ? `d${p.f.delivery}` : `h${p.f.i}`;
    const cur = byWindow.get(key);
    if (cur) cur.label += ` + ${p.label}`; else byWindow.set(key, { ...p });
  }
  return [...byWindow.values()];
}

/** The page-time window of frame `f` in run `r`: its delivery's [t0, t0 + ms] when one carried it, else
 *  its handler's; null when the run holds no record of it. */
function frameWindow(r, f) {
  const pf = r.perFrame[f.i];
  if (!pf || pf.t0 == null) return null;
  const d = pf.delivery != null && pf.delivery >= 0 && r.deliveryRows ? r.deliveryRows[pf.delivery] : null;
  if (d && d.ms >= 0) return { kind: "delivery", t0: d.t0, ms: d.ms };
  if (pf.handlerMs != null && pf.handlerMs >= 0) return { kind: "handler", t0: pf.t0, ms: pf.handlerMs };
  return null;
}

function profileReport(runs, firstIdx, files, sourceMapDir) {
  const locate = sourceMapDir ? sourceLocator(sourceMapDir) : null;
  const profiled = runs.filter((r) => r.profiling && r.profiling.profile);
  // Every interval a wrapper was on the stack: the handlers, the flushes of the shim's queue and the
  // deliveries outside both (a shim from before the flush task delivers inside the handler).
  const wrapperWindows = (r) => [
    ...r.perFrame.filter((f) => f.t0 != null && f.handlerMs != null && f.handlerMs >= 0).map((f) => [f.t0, f.t0 + f.handlerMs]),
    ...(r.flushRows || []).filter((x) => x.ms >= 0).map((x) => [x.t0, x.t0 + x.ms]),
    ...(r.deliveryRows || []).filter((x) => x.ms >= 0).map((x) => [x.t0, x.t0 + x.ms]),
  ];
  const aligned = profiled.map((r) => ({ r, al: refineAlignment(r.profiling.profile, r.profiling.p0, r.profiling.alignMs, wrapperWindows(r)) }));
  const overall = rankProfile(mergeAggregates(profiled.map((r) => aggregateProfile(r.profiling.profile))), TOP_OVERALL, locate);
  const windows = [];
  for (const { label, f } of profileWindows(runs[0].perFrame, firstIdx)) {
    const aggs = [];
    let kind = null, ms = [];
    for (const { r, al } of aligned) {
      const w = frameWindow(r, f);
      if (!w) continue;
      const { profile } = r.profiling;
      const toUs = (x) => profile.startTime + (x - al.p0) * 1000;
      aggs.push(aggregateProfile(profile, [toUs(w.t0), toUs(w.t0 + w.ms)]));
      kind = kind || w.kind; ms.push(w.ms);
    }
    if (!aggs.length) continue;
    windows.push({ label, index: f.i, type: f.type, bytes: f.bytes, window: kind, windowMs: round1(ms.reduce((a, b) => a + b, 0) / ms.length), handlerMs: round1(f.handlerMs),
      bundleMs: f.handoff === "delivered" ? round1(f.bundleMs) : null, coalesced: f.handoff === "coalesced", ...rankProfile(mergeAggregates(aggs), TOP_WINDOW, locate) });
  }
  const insides = aligned.map(({ al }) => al.inside).filter((x) => x != null);
  // The bundles the profile names (served from /dist/): the source-position claim rests on their maps
  // having loaded, not on a directory having been configured. A --production dist is minified and has none.
  const bundles = new Set();
  for (const r of profiled) for (const n of r.profiling.profile.nodes || []) {
    const url = n.callFrame && n.callFrame.url;
    if (!url) continue;
    try { const u = new URL(url); if (u.pathname.startsWith("/dist/") && u.pathname.endsWith(".js")) bundles.add(path.basename(u.pathname)); } catch {}
  }
  const sourceMapsLoaded = [], sourceMapsMissing = [];
  for (const b of [...bundles].sort()) (locate && locate.probe(b) ? sourceMapsLoaded : sourceMapsMissing).push(b);
  return { files, samplingIntervalUs: PROFILE_INTERVAL_US,
    alignMs: round1(Math.max(...aligned.map(({ al }) => al.alignMs))), alignBoundMs: round1(Math.max(...profiled.map((r) => r.profiling.alignMs))),
    alignRefined: aligned.length > 0 && aligned.every(({ al }) => al.refined),
    wrapperSamplesInWindows: insides.length ? round1(Math.min(...insides) * 100) / 100 : null,
    sourceMaps: sourceMapsLoaded.length > 0, sourceMapsLoaded, sourceMapsMissing, ...overall, windows };
}

/** Write each profiled iteration's .cpuprofile (Chrome DevTools loads it); with several iterations the
 *  index goes before the extension. Returns the paths written. */
function writeProfiles(out, runs) {
  const abs = path.resolve(out);
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  const profiled = runs.filter((r) => r.profiling && r.profiling.profile);
  const files = [];
  profiled.forEach((r, i) => {
    const ext = path.extname(abs);
    const file = profiled.length === 1 ? abs : path.join(path.dirname(abs), `${path.basename(abs, ext)}-${i + 1}${ext}`);
    fs.writeFileSync(file, JSON.stringify(stripProfileQueries(r.profiling.profile)));
    files.push(file);
  });
  return files;
}

/** The profile with every call frame's URL query dropped: V8 records the document URL for the page's inline
 *  shim, and the page was navigated to /feed?token=<the run's serve token>; the file is meant to be opened
 *  and shared, so the token never reaches it (frameKey and the locator already drop the query). */
export function stripProfileQueries(profile) {
  const nodes = (profile.nodes || []).map((n) => (n.callFrame && typeof n.callFrame.url === "string" && n.callFrame.url.includes("?"))
    ? { ...n, callFrame: { ...n.callFrame, url: n.callFrame.url.split("?")[0] } } : n);
  return { ...profile, nodes };
}

const fmtFn = (f) => `${fmtMs(f.selfMs).padStart(9)} ${fmtMs(f.totalMs).padStart(9)} ${String(f.samples).padStart(7)}  ${f.key}${f.src ? `  ${f.src}` : ""}`;
const fmtMeta = (meta) => Object.entries(meta).map(([k, v]) => `${k} ${fmtMs(v)} ms`).join(", ") || "none";

export function renderProfile(cp) {
  const out = [];
  out.push(`cpu profile: ${cp.samples} samples over ${fmtMs(cp.durationMs)} ms at ${cp.samplingIntervalUs} us, ${cp.functions} functions; bookkeeping: ${fmtMeta(cp.meta)}`);
  const pct = cp.wrapperSamplesInWindows != null ? `${Math.round(cp.wrapperSamplesInWindows * 100)}% of the instrument's samples inside the handler, flush and delivery windows` : "no windows to check against";
  out.push(`  page-to-profile clock alignment ±${fmtMs(cp.alignMs)} ms ${cp.alignRefined ? `(refined from the ±${fmtMs(cp.alignBoundMs)} ms bracketing estimate; ${pct})` : `(the bracketing estimate; the refinement did not apply: ${pct})`}${cp.sourceMaps ? `; source positions from ${(cp.sourceMapsLoaded || []).map((b) => b + ".map").join(", ")}` : ""}`);
  if (cp.sourceMapsMissing && cp.sourceMapsMissing.length) out.push(`  warning: no ${cp.sourceMapsMissing.map((b) => b + ".map").join(", ")} beside the served bundle${cp.sourceMapsMissing.length === 1 ? "" : "s"}; names and lines are the bundle's own, and a --production build is minified (rebuild with node esbuild.js, no --production)`);
  for (const f of cp.files || []) out.push(`  written: ${f} (load it in Chrome DevTools, Performance panel)`);
  const head = `${"self ms".padStart(9)} ${"total ms".padStart(9)} ${"samples".padStart(7)}  url:function:line${cp.sourceMaps ? "  source:line" : ""}`;
  out.push(`  top ${cp.topSelf.length} by self time${cp.topSelf.some((f) => f.lines) ? " (under a function, the lines that hold its self time: share, ms, bundle line, source)" : ""}`, `  ${head}`);
  for (const f of cp.topSelf) {
    out.push(`  ${fmtFn(f)}`);
    for (const l of f.lines || []) out.push(`  ${" ".repeat(28)}${String(Math.round(l.share * 100)).padStart(3)}%  ${fmtMs(l.ms).padStart(7)} ms  line ${l.line}${l.src ? `  ${l.src}` : ""}`);
  }
  out.push(`  top ${cp.topTotal.length} by total time`, `  ${head}`);
  for (const f of cp.topTotal) out.push(`  ${fmtFn(f)}`);
  for (const w of cp.windows || []) {
    const span = w.window === "delivery"
      ? `delivery ${fmtMs(w.windowMs)} ms${w.coalesced ? " (a newer frame's delivery, this one coalesced into it)" : ""}, shim handler ${fmtMs(w.handlerMs)} ms`
      : `handler ${fmtMs(w.handlerMs)} ms`;
    out.push("", `  window: ${w.label} (${w.type}, ${fmtBytes(w.bytes)}, frame ${w.index}): ${span}, ${w.samples} samples, ${fmtMs(w.sampledMs)} ms sampled; bookkeeping: ${fmtMeta(w.meta)}`);
    out.push(`    top ${w.topSelf.length} by self time`, `    ${head}`);
    for (const f of w.topSelf) out.push(`    ${fmtFn(f)}`);
    out.push(`    top ${w.topTotal.length} by total time`, `    ${head}`);
    for (const f of w.topTotal) out.push(`    ${fmtFn(f)}`);
  }
  return out.join("\n");
}

// ── text rendering ───────────────────────────────────────────────────────────────────────────────

export function fmtBytes(n) {
  if (n == null) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}
const fmtMs = (x) => (x == null ? "-" : x >= 100 ? String(Math.round(x)) : x.toFixed(1));
const stats3 = (s) => (s && s.n ? `${fmtMs(s.p50)} / ${fmtMs(s.p90)} / ${fmtMs(s.max)}` : "-");
const fmtCount = (x) => (x == null ? "-" : Number.isInteger(x) ? String(x) : x.toFixed(1));
const plural = (n, one, many = one + "s") => (n === 1 ? one : many);
const pacingLabel = (fast, gapMs) => (fast ? "back-to-back" : gapMs != null ? `${gapMs} ms gaps` : "recorded pacing");

/** The lines under the table that say what its timing columns hold, by how the shim handed frames on. */
function handoffLines(r) {
  const h = r.handoff || { mode: "none" };
  const out = [];
  if (h.mode === "flush") {
    out.push(`handoff: the shim hands frames to the bundle in its own flush task (${fmtCount(h.flushes)} ${plural(h.flushes, "task")} carrying ${fmtCount(h.deliveries)} ${plural(h.deliveries, "delivery", "deliveries")} per run). handler = the shim's synchronous work per wire frame (parse, delta application, the held-state stamp); bundle = the pane's render per delivery; settle = a frame's receipt to the main thread free after the delivery that carried it.`);
    const parts = [];
    if (h.coalesced) parts.push(`${fmtCount(h.coalesced)} ${plural(h.coalesced, "frame")} coalesced into a newer frame of the same kind before delivery (no bundle time of their own)`);
    if (h.shim) parts.push(`${fmtCount(h.shim)} answered by the shim alone (keepalives, restart notices, resync asks)`);
    if (parts.length) out.push(`  ${parts.join("; ")}`);
  } else if (h.mode === "handler") {
    out.push("handoff: the shim hands frames to the bundle inside the WebSocket handler, so the handler column includes the bundle's render; bundle = that handoff's own time within it.");
    if (h.shim) out.push(`  ${fmtCount(h.shim)} ${plural(h.shim, "frame")} answered by the shim alone (keepalives, restart notices, resync asks)`);
  } else if (h.mode === "mixed") {
    out.push("warning: the runs disagree on how the shim handed frames to the bundle (some in a flush task, some inside the handler); the bundle and settle columns pool both");
  } else {
    out.push("handoff: no delivery to the bundle was observed; the handler column is the WebSocket handler's synchronous time and the bundle column is empty");
  }
  if (h.queued) out.push(`warning: ${fmtCount(h.queued)} ${plural(h.queued, "frame was", "frames were")} still queued in the shim when the run ended`);
  if (h.queueDrained === false) out.push("warning: the shim's queue had not drained when the run ended (a flush it armed never ran)");
  if (h.unattributed) out.push(`note: ${h.unattributed} ${plural(h.unattributed, "render")} (${fmtMs(h.unattributedMs)} ms) not caused by a wire frame: the page re-emitted state on its own`);
  if (h.deliverySeams && h.deliverySeams.dispatch) out.push(`note: ${h.deliverySeams.dispatch} ${plural(h.deliverySeams.dispatch, "delivery", "deliveries")} reached the pane through the MessageEvent fallback (no federation object published when the frame arrived), so the bundle column omits federation's prefixing and merge for them`);
  for (const e of (h.deliveryErrors || []).slice(0, 5)) out.push(`  delivery threw: ${e.slice(0, 300)}`);
  return out;
}

export function renderReport(r) {
  const out = [];
  out.push(`ui-bench ${r.app}: ${r.frames.total} frames, ${fmtBytes(r.frames.bytes)}, replay ${fmtMs(r.frames.replayMs)} ms (${pacingLabel(r.fast, r.gapMs)}), cpu x${r.cpuThrottle}, ${r.iters} iteration${r.iters === 1 ? "" : "s"}, ${r.browser}`);
  out.push(`page ready (navigation to the bundle's handshake): ${fmtMs(r.frames.readyMs)} ms${r.frames.reconnects ? `; shim reconnects during replay: ${r.frames.reconnects}` : ""}${r.frames.misaligned ? `; frames whose page record did not match in length: ${r.frames.misaligned}` : ""}`);
  if (r.first) {
    const bundle = r.first.handoff === "coalesced" ? "bundle - (coalesced into a newer frame's delivery)"
      : r.first.handoff === "mixed" ? `bundle ${fmtMs(r.first.bundleMs)} ms (delivered on its own in ${r.first.deliveredRuns} of ${r.iters} runs, coalesced in the others)`
      : `bundle ${fmtMs(r.first.bundleMs)} ms`;
    out.push(`first content frame: ${r.first.type}, ${fmtBytes(r.first.bytes)}, handler ${fmtMs(r.first.handlerMs)} ms, ${bundle}, settled ${fmtMs(r.first.settleMs)} ms`);
  }
  out.push("");
  out.push(`${"type".padEnd(14)} ${"count".padStart(7)} ${"delivered".padStart(9)} ${"bytes".padStart(10)} ${"max".padStart(10)}   ${"handler p50/p90/max ms".padEnd(24)} ${"bundle p50/p90/max ms".padEnd(24)} ${"settle p50/p90/max ms".padEnd(24)}`);
  for (const [type, s] of Object.entries(r.types)) {
    const notes = [];
    if (s.measured < s.count) notes.push(`${fmtCount(s.count - s.measured)} unmeasured`);
    if (s.coalesced) notes.push(`${fmtCount(s.coalesced)} coalesced`);
    if (s.queued) notes.push(`${fmtCount(s.queued)} still queued`);
    if (s.settleMissing) notes.push(`${s.settleMissing} settle missing`);
    out.push(`${type.padEnd(14)} ${fmtCount(s.count).padStart(7)} ${(s.delivered == null ? "-" : fmtCount(s.delivered)).padStart(9)} ${fmtBytes(s.bytes).padStart(10)} ${fmtBytes(s.bytesMax).padStart(10)}   ${stats3(s.handlerMs).padEnd(24)} ${stats3(s.bundleMs).padEnd(24)} ${stats3(s.settleMs).padEnd(24)}${notes.length ? `  (${notes.join(", ")})` : ""}`);
  }
  out.push(...handoffLines(r));
  if (r.frames.settleMissing) out.push(`warning: ${r.frames.settleMissing} frame${r.frames.settleMissing === 1 ? "" : "s"} never received a settle stamp; the settle columns are computed over the frames that did`);
  if (r.frames.addListenerMessages) out.push(`warning: ${r.frames.addListenerMessages} message listener${r.frames.addListenerMessages === 1 ? " was" : "s were"} added with addEventListener; the handler column does not time work done there`);
  if (r.frames.buildBannerRaised) out.push(`warning: the page raised its "newer build" banner in ${r.frames.buildBannerRaised} run${r.frames.buildBannerRaised === 1 ? "" : "s"} (a keepalive's dv is newer than the dist under test): a few extra elements and a layout the frames did not cause`);
  if (r.frames.connBannerRaised) out.push(`warning: the page raised its "connection stale" banner in ${r.frames.connBannerRaised} run${r.frames.connBannerRaised === 1 ? "" : "s"}`);
  out.push("");
  out.push(`long animation frames (${r.loaf.kind || "unsupported"}): ${r.loaf.count} entries, ${fmtMs(r.loaf.durationMs)} ms total, ${fmtMs(r.loaf.blockingMs)} ms blocking, longest ${fmtMs(r.loaf.maxMs)} ms`);
  out.push("  script attribution names each task's entry point (the message handler, the shim's flush task, a rAF, a timer, a script's evaluation), not the bundle function; --cpu-profile gives functions");
  for (const s of r.loaf.topScripts) out.push(`  ${fmtMs(s.durationMs).padStart(8)} ms  x${String(s.count).padEnd(4)} ${s.key}`);
  const gc = r.end.afterGc ? `after a forced GC${r.end.heapBeforeGc != null ? ` (${fmtBytes(r.end.heapBeforeGc)} before it)` : ""}` : "with NO forced GC (the collection did not run; the figure includes pending garbage)";
  out.push(`end state: JS heap ${fmtBytes(r.end.heapUsed)} used of ${fmtBytes(r.end.heapTotal)} ${gc}; DOM ${r.end.domElements} elements (${r.end.cdpNodes} nodes, ${r.end.jsEventListeners} listeners)`);
  out.push(`  cumulative since navigation (page load and idle timers included; the timeline redraws every animation frame while it follows now): ${r.end.layoutCount} layouts ${fmtMs(r.end.layoutMs)} ms; ${r.end.recalcStyleCount} style recalcs ${fmtMs(r.end.recalcStyleMs)} ms; script ${fmtMs(r.end.scriptMs)} ms; tasks ${fmtMs(r.end.taskMs)} ms`);
  out.push(`console: ${r.console.errors.length} errors, ${r.console.pageErrors.length} uncaught exceptions, ${r.console.warnings} warnings`);
  for (const e of r.console.errors.slice(0, 10)) out.push(`  error: ${e.slice(0, 300)}`);
  for (const e of r.console.pageErrors.slice(0, 10)) out.push(`  uncaught: ${e.slice(0, 300)}`);
  for (const e of r.console.failedResources.slice(0, 10)) out.push(`  failed resource: ${e}`);
  out.push(`messages the pane sent: ${Object.entries(r.clientMessages).map(([k, v]) => `${k} ${v}`).join(", ") || "none"}${Object.keys(r.clientDiag || {}).length ? ` (clientDiag: ${Object.entries(r.clientDiag).map(([k, v]) => `${k} ${v}`).join(", ")})` : ""}`);
  if (r.fast) out.push("note: back-to-back replay; frames queued together coalesce (a newer whole-state frame replaces its older queued twin), so the bundle column covers the frames that were delivered, and a frame's settle time includes the frames dispatched after it before the next rendered frame, so settle percentiles overlap while handler times do not. Use --gap or the recorded pacing for the bundle's cost per frame.");
  if (r.cpuProfile) out.push("", renderProfile(r.cpuProfile));
  return out.join("\n");
}

// ── --compare ────────────────────────────────────────────────────────────────────────────────────

function delta(a, b) {
  if (a == null || b == null) return { a, b, diff: null, pct: null };
  const diff = b - a;
  return { a, b, diff: round1(diff), pct: a ? round1((diff / a) * 100) : null };
}

/** The differences between two replay reports: per-type timing percentiles, long-animation-frame
 *  totals, and end state. Pure arithmetic over the JSON shape, no browser. */
export function compareReports(a, b) {
  const types = {};
  for (const type of new Set([...Object.keys(a.types || {}), ...Object.keys(b.types || {})])) {
    const x = a.types[type] || {}, y = b.types[type] || {};
    const sx = x.settleMs || {}, sy = y.settleMs || {}, hx = x.handlerMs || {}, hy = y.handlerMs || {}, bx = x.bundleMs || {}, by = y.bundleMs || {};
    types[type] = { count: delta(x.count ?? null, y.count ?? null), delivered: delta(x.delivered ?? null, y.delivered ?? null), bytes: delta(x.bytes ?? null, y.bytes ?? null),
      settleP50: delta(sx.p50, sy.p50), settleP90: delta(sx.p90, sy.p90), settleMax: delta(sx.max, sy.max),
      handlerP50: delta(hx.p50, hy.p50), handlerP90: delta(hx.p90, hy.p90), handlerMax: delta(hx.max, hy.max),
      bundleP50: delta(bx.p50 ?? null, by.p50 ?? null), bundleP90: delta(bx.p90 ?? null, by.p90 ?? null), bundleMax: delta(bx.max ?? null, by.max ?? null) };
  }
  // LayoutCount, ScriptDuration and TaskDuration are cumulative since navigation, so they scale with how
  // long the page sat there: a percentage between runs of different pacing or length says nothing.
  const replayMs = [a.frames?.replayMs ?? null, b.frames?.replayMs ?? null];
  const sameLength = replayMs[0] > 0 && replayMs[1] > 0 && Math.max(replayMs[0] / replayMs[1], replayMs[1] / replayMs[0]) <= 1.25;
  const endComparable = !!a.fast === !!b.fast && (a.gapMs ?? null) === (b.gapMs ?? null) && sameLength;
  const handoff = [a.handoff?.mode ?? null, b.handoff?.mode ?? null];
  return {
    apps: [a.app, b.app], cpuThrottle: [a.cpuThrottle, b.cpuThrottle], fast: [a.fast, b.fast], gapMs: [a.gapMs ?? null, b.gapMs ?? null], replayMs, endComparable, handoff,
    first: { bytes: delta(a.first?.bytes, b.first?.bytes), handlerMs: delta(a.first?.handlerMs, b.first?.handlerMs), bundleMs: delta(a.first?.bundleMs ?? null, b.first?.bundleMs ?? null), settleMs: delta(a.first?.settleMs, b.first?.settleMs) },
    types,
    loaf: { count: delta(a.loaf?.count, b.loaf?.count), durationMs: delta(a.loaf?.durationMs, b.loaf?.durationMs), blockingMs: delta(a.loaf?.blockingMs, b.loaf?.blockingMs), maxMs: delta(a.loaf?.maxMs, b.loaf?.maxMs) },
    end: { heapUsed: delta(a.end?.heapUsed, b.end?.heapUsed), domElements: delta(a.end?.domElements, b.end?.domElements), layoutCount: delta(a.end?.layoutCount, b.end?.layoutCount),
      scriptMs: delta(a.end?.scriptMs, b.end?.scriptMs), taskMs: delta(a.end?.taskMs, b.end?.taskMs) },
    console: { errors: [a.console?.errors?.length ?? 0, b.console?.errors?.length ?? 0] },
  };
}

const fmtNum = (x) => (x == null ? "-" : Number.isInteger(x) ? String(x) : fmtMs(x));
const fmtDelta = (d, unit = "", { pct = true } = {}) => {
  if (d.diff == null) return `${fmtNum(d.a)} → ${fmtNum(d.b)}${unit}`;
  if (d.diff === 0) return `${fmtNum(d.a)} → ${fmtNum(d.b)}${unit} (unchanged)`;
  const sign = (x) => (x > 0 ? "+" : "-");
  return `${fmtNum(d.a)} → ${fmtNum(d.b)}${unit} (${sign(d.diff)}${fmtNum(Math.abs(d.diff))}${pct && d.pct != null ? `, ${sign(d.pct)}${fmtNum(Math.abs(d.pct))}%` : ""})`;
};

export function renderCompare(c) {
  const out = [];
  const pacing = (i) => (c.fast[i] ? "fast" : c.gapMs && c.gapMs[i] != null ? `${c.gapMs[i]} ms gaps` : "paced");
  out.push(`compare: ${c.apps[0]} (cpu x${c.cpuThrottle[0]}, ${pacing(0)}) → ${c.apps[1]} (cpu x${c.cpuThrottle[1]}, ${pacing(1)})`);
  if (c.handoff && c.handoff[0] && c.handoff[1] && c.handoff[0] !== c.handoff[1]) out.push(`  the shims differ in how they hand frames to the bundle (${c.handoff[0]} → ${c.handoff[1]}): on a shim that delivers inside the handler the handler column includes the bundle's render, so compare bundle and settle, not handler`);
  out.push(`first content frame: bytes ${fmtDelta(c.first.bytes)}; handler ${fmtDelta(c.first.handlerMs, " ms")}; bundle ${fmtDelta(c.first.bundleMs || { a: null, b: null, diff: null, pct: null }, " ms")}; settled ${fmtDelta(c.first.settleMs, " ms")}`);
  for (const [type, t] of Object.entries(c.types)) {
    const delivered = t.delivered && (t.delivered.a != null || t.delivered.b != null) ? ` (delivered ${fmtDelta(t.delivered)})` : "";
    const bundle = t.bundleP50 && (t.bundleP50.a != null || t.bundleP50.b != null) ? `; bundle p50 ${fmtDelta(t.bundleP50, " ms")}` : "";
    out.push(`${type.padEnd(14)} count ${fmtDelta(t.count)}${delivered}; settle p50 ${fmtDelta(t.settleP50, " ms")}, p90 ${fmtDelta(t.settleP90, " ms")}, max ${fmtDelta(t.settleMax, " ms")}; handler p50 ${fmtDelta(t.handlerP50, " ms")}${bundle}`);
  }
  out.push(`long animation frames: count ${fmtDelta(c.loaf.count)}; total ${fmtDelta(c.loaf.durationMs, " ms")}; blocking ${fmtDelta(c.loaf.blockingMs, " ms")}; longest ${fmtDelta(c.loaf.maxMs, " ms")}`);
  const pct = { pct: c.endComparable !== false };
  out.push(`end state: heap ${fmtDelta(c.end.heapUsed, " B")}; DOM elements ${fmtDelta(c.end.domElements)}; layouts ${fmtDelta(c.end.layoutCount, "", pct)}; script ${fmtDelta(c.end.scriptMs, " ms", pct)}; tasks ${fmtDelta(c.end.taskMs, " ms", pct)}`);
  if (c.endComparable === false) out.push(`  layouts, script and tasks are cumulative since navigation and the runs differ in pacing or length (replay ${fmtNum(c.replayMs?.[0])} → ${fmtNum(c.replayMs?.[1])} ms), so they carry no percentage`);
  out.push(`console errors: ${c.console.errors[0]} → ${c.console.errors[1]}`);
  return out.join("\n");
}

// ── CLI ──────────────────────────────────────────────────────────────────────────────────────────

const USAGE = `usage:
  node tools/ui-bench.mjs --record <app> --seconds N --out /tmp/…/frames.jsonl [--port P]
  node tools/ui-bench.mjs --replay <app> --frames FILE [--cpu-throttle K] [--iters N] [--fast | --gap MS] [--json OUT] [--dist DIR] [--cpu-profile OUT.cpuprofile]
  node tools/ui-bench.mjs --synthesize <app> --cards N --out FILE [--seed S]
  node tools/ui-bench.mjs --compare A.json B.json
apps: ${APPS.join(", ")} (synthesize: feed, fleet, timeline)`;

export function parseArgs(argv) {
  const o = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) { o._.push(a); continue; }
    const key = a.slice(2);
    const flags = new Set(["fast", "help"]);
    if (flags.has(key)) { o[key] = true; continue; }
    const v = argv[i + 1];
    if (v === undefined || v.startsWith("--")) throw new Error(`--${key} needs a value`);
    o[key] = v; i++;
  }
  return o;
}

async function main(argv) {
  const o = parseArgs(argv);
  if (o.help || !argv.length) { console.log(USAGE); return 0; }
  const num = (k, d) => (o[k] === undefined ? d : Number(o[k]));
  if (o.record) {
    const port = num("port", Number(process.env.ROMP_KERNEL_PORT) || 29855);
    if (!o.out) throw new Error("--record needs --out");
    await recordFrames({ app: o.record, seconds: num("seconds", 60), out: o.out, port });
    return 0;
  }
  if (o.synthesize) {
    if (!o.out) throw new Error("--synthesize needs --out");
    const frames = synthesizeFrames(o.synthesize, num("cards", 50), { seed: num("seed", 7) });
    writeFrames(o.out, { tool: "ui-bench", mode: "synthesize", app: o.synthesize, cards: num("cards", 50), seed: num("seed", 7), synthetic: true }, frames);
    const s = streamSummary(frames);
    console.error(`ui-bench: wrote ${frames.length} synthetic frames (${fmtBytes(s.bytes)}) for app=${o.synthesize} → ${o.out}`);
    for (const [type, st] of Object.entries(s.byType)) console.error(`  ${type.padEnd(12)} ${String(st.count).padStart(6)}  ${fmtBytes(st.bytes).padStart(10)}`);
    return 0;
  }
  if (o.replay) {
    if (!o.frames) throw new Error("--replay needs --frames FILE");
    if (o.fast && o.gap !== undefined) throw new Error("--fast and --gap are two pacings; give one");
    const report = await replay({ app: o.replay, framesFile: o.frames, cpuThrottle: num("cpu-throttle", 1), iters: num("iters", 1), fast: !!o.fast, gapMs: o.gap === undefined ? null : Number(o.gap), dist: o.dist, jsonOut: o.json, cpuProfile: o["cpu-profile"] });
    console.log(renderReport(report));
    return 0;
  }
  if (o.compare) {
    const b = o._[0];
    if (!b) throw new Error("--compare needs two report files: --compare A.json B.json");
    const A = JSON.parse(fs.readFileSync(o.compare, "utf8")), B = JSON.parse(fs.readFileSync(b, "utf8"));
    console.log(renderCompare(compareReports(A, B)));
    return 0;
  }
  console.log(USAGE);
  return 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).then((code) => process.exit(code), (e) => { console.error(`ui-bench: ${e.message}`); process.exit(1); });
}
