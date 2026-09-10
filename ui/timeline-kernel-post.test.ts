// The Obsidian timeline panel writes THROUGH the kernel (2026-09-08). The panel runs inside Obsidian's
// Electron with Node's fs and the state dir, and no socket to the kernel — so it used to write
// session-flags.json, timeline-views.json and session-order.json itself, a second writer beside the
// kernel that bypassed the views judge and its stale-writer guard, the flags setter's proved read and
// the order merge. Now every write is a POST to the kernel's /flag, /views and /order (_kernelPost);
// when no kernel takes the write the gesture is REFUSED where it was made, never written to a file the
// kernel cannot check — except the lens, this viewer's own filter, which stays applied LOCALLY and says
// it is not saved. EXECUTED against real loopback servers standing in for the kernel (the transport,
// the token, the port record, the answers no romp kernel would give) and against a stubbed _kernelPost
// (the three writers' contracts, the Filter menu). Synthetic ids only; the state dir is a temp dir, the
// CLI-default port is pinned to one nothing listens on, and no real file or kernel is ever touched.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as http from "node:http";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const VIEW_PATH = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const SRC = fs.readFileSync(VIEW_PATH, "utf8");
const { TimelinePanel, timelineLens, viewTagUnion } = requireCjs(VIEW_PATH);

const SID = "11111111-2222-3333-4444-555555555555";
const SID2 = "22222222-3333-4444-5555-666666666666";
const TOKEN = "test-token-DO-NOT-USE";
const KERNEL_DOWN = "the kernel is not running; start romp and try again";
const KERNEL_DOWN_NO_RECORD = "the kernel is not running, or the one running predates this panel and left no port record; start or restart romp and try again";

function panel(): any {
  const p: any = Object.create(TimelinePanel.prototype);
  p.draw = () => {};
  p.data = { sessions: [{ id: SID, name: "web", hideFromFeed: false }] };
  p._pendingFlags = {}; p._laneRefusal = null; p._laneMenu = null; p._laneMenuBuild = null; p._metaMenu = null;
  p._viewsWrites = []; p._pendingViews = null; p._pendingTagEdits = {}; p._tagEditErr = null; p._localLens = null;
  p._views = null; p._rejectedViews = null; p._announcedViewsSeq = null; p._viewsWriteSeq = 0; p._legacyViewsAge = 0;
  p._viewsDialog = null; p._viewsDialogBuild = null; p._viewsMenu = null; p._caps = new Set();
  return p;
}

const tick = () => new Promise<void>((r) => setImmediate(r));

// a loopback port nothing listens on (bound, then released)
async function closedPort(): Promise<number> {
  const srv = http.createServer();
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", () => r()));
  const port = (srv.address() as any).port;
  await new Promise<void>((r) => srv.close(() => r()));
  return port;
}

// an Obsidian-shaped process for the duration of fn: Electron on, the state dir a temp dir holding
// whichever of the kernel's two records the test plants ("dir" plants an unreadable one), and the
// CLI-default port the no-record fallback resolves (ROMP_KERNEL_PORT) pinned to `envPort` — a closed
// port unless the test says — with ROMP_SERVE_PORT cleared, so no fallback here can reach a kernel
// that happens to be running on this machine
async function asObsidian(records: { port?: number | string; token?: string; envPort?: number }, fn: (root: string) => Promise<void>) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "romp-kernel-post-"));
  if (records.port === "dir") fs.mkdirSync(path.join(root, "serve-port"));
  else if (records.port !== undefined) fs.writeFileSync(path.join(root, "serve-port"), String(records.port) + "\n");
  if (records.token === "dir") fs.mkdirSync(path.join(root, "serve-token"));
  else if (records.token !== undefined) fs.writeFileSync(path.join(root, "serve-token"), records.token + "\n", { mode: 0o600 });
  const saved = { root: process.env.ROMP_STATE_DIR, kport: process.env.ROMP_KERNEL_PORT, sport: process.env.ROMP_SERVE_PORT };
  const versions: any = process.versions;
  const hadElectron = versions.electron;
  versions.electron = "1.0.0-test";
  process.env.ROMP_STATE_DIR = root;
  process.env.ROMP_KERNEL_PORT = String(records.envPort !== undefined ? records.envPort : await closedPort());
  delete process.env.ROMP_SERVE_PORT;
  try { await fn(root); } finally {
    if (hadElectron === undefined) delete versions.electron; else versions.electron = hadElectron;
    for (const [k, v] of [["ROMP_STATE_DIR", saved.root], ["ROMP_KERNEL_PORT", saved.kport], ["ROMP_SERVE_PORT", saved.sport]] as const)
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    fs.rmSync(root, { recursive: true, force: true });
  }
}

// a kernel stand-in on loopback: keeps every request (with the client port it came in on), answers what
// the test says: a JSON body, or `raw` text, or (`drop`) headers and a partial body followed by a
// destroyed socket. GET /healthz, the identity the panel asks for before it sends a token, is answered
// as a romp kernel answers it (200 "ok" with X-Romp-Boot) unless the test hands the stand-in another
// answer (`healthz`: what a squatter on the port would say)
type Seen = { method: string; url: string; headers: http.IncomingHttpHeaders; body: any; remotePort: number };
type Answer = { status: number; body?: any; raw?: string; drop?: boolean; headers?: Record<string, string> };
const HEALTHZ_KERNEL: Answer = { status: 200, raw: "ok", headers: { "X-Romp-Boot": "4242.1781000000" } };
const posts = (k: { seen: Seen[] }) => k.seen.filter((q) => q.method === "POST");
function kernel(answer: (req: Seen) => Answer, opts: { healthz?: Answer } = {}) {
  return new Promise<{ port: number; seen: Seen[]; close: () => Promise<void> }>((resolve) => {
    const seen: Seen[] = [];
    const srv = http.createServer((req, res) => {
      let text = "";
      req.setEncoding("utf8");
      req.on("data", (c) => { text += c; });
      req.on("end", () => {
        let body: any = null;
        try { body = text ? JSON.parse(text) : null; } catch { body = text; }
        const rec: Seen = { method: req.method || "", url: req.url || "", headers: req.headers, body, remotePort: req.socket.remotePort || 0 };
        seen.push(rec);
        const a = (rec.method === "GET" && rec.url === "/healthz") ? (opts.healthz || HEALTHZ_KERNEL) : answer(rec);
        if (a.drop) { res.writeHead(a.status, { "Content-Type": "application/json", "Content-Length": "40" }); res.write('{"ok":'); setTimeout(() => res.destroy(), 5); return; }
        res.writeHead(a.status, Object.assign({ "Content-Type": a.raw !== undefined ? "text/plain" : "application/json" }, a.headers || {}));
        res.end(a.raw !== undefined ? a.raw : JSON.stringify(a.body));
      });
    });
    srv.listen(0, "127.0.0.1", () => resolve({
      port: (srv.address() as any).port, seen,
      close: () => new Promise<void>((r) => srv.close(() => r())),
    }));
  });
}

const gearClick = (p: any, s: any, next: boolean) => {   // the lane gear's toggle, as it runs: optimistic, sticky, then the writer (whose settle promise comes back)
  s.hideFromFeed = next; (p._pendingFlags[s.id] = p._pendingFlags[s.id] || {}).hideFromFeed = next; return p._setSessionFlag(s, "hideFromFeed", next);
};

test("executed: the token rides the header from the kernel's 0600 file, the port comes from its record, and the URL carries neither", async () => {
  const k = await kernel(() => ({ status: 200, body: { ok: true, id: SID, flag: "hideFromFeed", value: true } }));
  try {
    await asObsidian({ port: k.port, token: TOKEN }, async () => {
      const r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
      assert.equal(r.ok, true);
      assert.equal(r.body.value, true, "the kernel's answer rides back whole");
      assert.equal(k.seen.length, 2, "the proof, then the write");
      assert.deepEqual([k.seen[0].method, k.seen[0].url, k.seen[0].headers["x-romp-token"]], ["GET", "/healthz", undefined], "the port proves itself first, with no token in that request");
      assert.equal(k.seen[0].remotePort, k.seen[1].remotePort, "the write rides the connection that proved itself");
      const q = k.seen[1];
      assert.equal(q.method, "POST");
      assert.equal(q.url, "/flag", "the route and nothing else — no token in the URL");
      assert.equal(q.headers["x-romp-token"], TOKEN, "the token from the file, in the header every kernel client uses");
      assert.equal(q.headers["content-type"], "application/json");
      assert.deepEqual(q.body, { id: SID, flag: "hideFromFeed", value: true });
    });
  } finally { await k.close(); }
  const kp = SRC.slice(SRC.indexOf("  _kernelPost(route, body) {"), SRC.indexOf("  _kernelPost(route, body) {") + 5000);
  assert.doesNotMatch(kp, /token=/, "the token is never a query parameter");
  assert.match(kp, /'X-Romp-Token': tok/);
  assert.match(kp, /readFileSync\(at\('serve-port'\)/, "the port is the kernel's own record first…");
  assert.match(kp, /e\.code !== 'ENOENT'\) return unreadable\('port record'/, "…an unreadable record is said, never read as 'not running'…");
  assert.match(kp, /envVar = pick\('ROMP_KERNEL_PORT'\) \? 'ROMP_KERNEL_PORT' : \(pick\('ROMP_SERVE_PORT'\) \? 'ROMP_SERVE_PORT' : ''\);\s*\n\s*envRaw = envVar \? pick\(envVar\) : '29855';/,
    "…and only a MISSING record falls to the CLI's resolution: the environment in _kernel_urls' order, else bin/romp's one default");
  assert.match(kp, /bin\/romp's default, \$\{ROMP_KERNEL_PORT:-29855\}/, "the comment names the rule the panel actually mirrors");
  assert.match(kp, /require\('http'\)\.request\(/, "Node's http, not fetch: no CORS preflight from Obsidian's app:// origin can read as a kernel that is down");
});

test("executed: a refusal comes back in the kernel's words — its own ok:false, a typed 400, a refused token — and the writer frames it", async () => {
  const answers: Answer[] = [];
  const k = await kernel(() => answers.shift() || { status: 500, body: {} });
  try {
    await asObsidian({ port: k.port, token: TOKEN }, async () => {
      const p = panel();
      const text = "couldn't save that setting — session-flags.json could not be read (read failed: [Errno 5] injected EIO); try again";
      answers.push({ status: 200, body: { ok: false, error: text, value: true } });
      let r = await p._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: false });
      assert.deepEqual([r.ok, r.refusal, r.error, r.body.value], [false, true, text, true], "the kernel's own refusal: its full sentence, and the value it still paints");
      answers.push({ status: 400, body: { ok: false, error: "'value' must be true or false, got \"false\"" } });
      r = await p._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: "false" });
      assert.deepEqual([r.ok, r.status, r.error, r.refusal, r.unreachable], [false, 400, "'value' must be true or false, got \"false\"", false, undefined], "a typed refusal, verbatim; not the kernel's own sentence, not 'not running'");
      answers.push({ status: 403, body: {} });
      r = await p._kernelPost("/order", { order: [SID] });
      assert.equal(r.error, "the kernel on 127.0.0.1:" + k.port + " refused this panel's token (HTTP 403)");
      assert.deepEqual([r.ok, r.unreachable], [false, undefined], "a kernel that answered is not 'not running'");
      // the writer frames the bare reason
      answers.push({ status: 403, body: {} });
      const s = p.data.sessions[0];
      await gearClick(p, s, true);
      assert.equal(p._laneRefusal.text, "couldn't save that setting — the kernel on 127.0.0.1:" + k.port + " refused this panel's token (HTTP 403)");
      assert.equal(s.hideFromFeed, false, "…and the toggle goes back where the click found it");
    });
  } finally { await k.close(); }
});

test("executed: a 2xx that is not a romp kernel's {ok:true} is NOT accepted — some other service owns that port — and the writer reverts (review find, 2026-09-08)", async () => {
  const answers: Answer[] = [];
  const k = await kernel(() => answers.shift() || { status: 500, body: {} });
  try {
    await asObsidian({ port: k.port, token: TOKEN }, async () => {
      const p = panel();
      const foreign = "127.0.0.1:" + k.port + " answered, but not as a romp kernel";
      for (const a of [{ status: 200, body: {} }, { status: 200, body: { status: "ok" } }, { status: 200, body: { ok: "true" } },
                       { status: 200, body: [1] }, { status: 200, raw: "OK" }, { status: 204, raw: "" }] as Answer[]) {
        answers.push(a);
        const r = await p._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
        assert.deepEqual([r.ok, r.unreachable, r.foreign, r.error], [false, true, true, foreign], JSON.stringify(a));
      }
      // …so the optimistic toggle is never left re-applying itself on every push until a value that never comes
      const s = p.data.sessions[0];
      answers.push({ status: 200, body: {} });
      await gearClick(p, s, true);
      assert.deepEqual([s.hideFromFeed, p._pendingFlags, p._laneRefusal.text],
        [false, {}, "couldn't save that setting — " + foreign]);
    });
  } finally { await k.close(); }
});

test("executed: a socket that drops after the headers settles the promise (never a hung optimistic state, never an uncaught 'error')", async () => {
  const k = await kernel(() => ({ status: 200, drop: true }));
  try {
    await asObsidian({ port: k.port, token: TOKEN }, async () => {
      const t0 = Date.now();
      const r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
      assert.deepEqual([r.ok, r.unreachable, r.error], [false, true, KERNEL_DOWN]);
      assert.ok(Date.now() - t0 < 5000, "settled by the drop itself, not the 10 s timeout");
    });
  } finally { await k.close(); }
  const kp = SRC.slice(SRC.indexOf("  _kernelPost(route, body) {"), SRC.indexOf("  _kernelPost(route, body) {") + 5000);
  assert.match(kp, /res\.on\('error', dropped\); res\.on\('aborted', dropped\); res\.on\('close', dropped\);/, "every way a response can die after its headers is handled, behind the done guard");
});

test("executed: no serve-port record → the CLI's port (env, else 29855) is tried; a kernel there takes the write, an old kernel there is named, nothing there is the combined down text (review find, 2026-09-08)", async () => {
  // a running kernel that predates the record: the write lands through the CLI's port
  const k = await kernel(() => ({ status: 200, body: { ok: true, id: SID, flag: "hideFromFeed", value: true } }));
  try {
    await asObsidian({ token: TOKEN, envPort: k.port }, async () => {
      const r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
      assert.equal(r.ok, true, "ROMP_KERNEL_PORT, as bin/romp resolves it");
      assert.equal(posts(k).length, 1);
      assert.equal(posts(k)[0].headers["x-romp-token"], TOKEN);
      assert.deepEqual([k.seen[0].method, k.seen[0].url], ["GET", "/healthz"], "the fallback port proves itself first, like the recorded one");
      delete process.env.ROMP_KERNEL_PORT; process.env.ROMP_SERVE_PORT = String(k.port);
      const r2 = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
      assert.equal(r2.ok, true, "ROMP_SERVE_PORT too, as the CLI resolves it");
      assert.equal(posts(k).length, 2);
    });
  } finally { await k.close(); }
  // a kernel there from before these routes: 404, named as such — not "not running"
  const old = await kernel(() => ({ status: 404, raw: "not found" }));
  try {
    await asObsidian({ token: TOKEN, envPort: old.port }, async () => {
      const r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
      assert.deepEqual([r.ok, r.unreachable, r.status], [false, true, 404]);
      assert.equal(r.error, "the kernel on 127.0.0.1:" + old.port + " predates this panel and has no /flag route; restart romp (`romp refresh`) and try again");
    });
  } finally { await old.close(); }
  // an unusable override is refused as _kernel_urls refuses it — naming the variable actually read — never
  // silently replaced by the default
  await asObsidian({ token: TOKEN, envPort: k.port }, async () => {
    process.env.ROMP_KERNEL_PORT = "29855x";
    let r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
    assert.deepEqual([r.ok, r.unreachable, r.error], [false, true, 'ROMP_KERNEL_PORT="29855x" is not a port']);
    delete process.env.ROMP_KERNEL_PORT; process.env.ROMP_SERVE_PORT = " 7x ";
    r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
    assert.equal(r.error, 'ROMP_SERVE_PORT="7x" is not a port');
    process.env.ROMP_KERNEL_PORT = ""; process.env.ROMP_SERVE_PORT = "70000";
    r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
    assert.equal(r.error, 'ROMP_SERVE_PORT="70000" is not a port', "an empty variable is unset, as the CLI reads it; the next one is the one read");
  });
  // nothing there: the down text that covers both readings
  await asObsidian({ token: TOKEN }, async () => {
    const r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
    assert.deepEqual([r.ok, r.unreachable, r.error], [false, true, KERNEL_DOWN_NO_RECORD]);
  });
  // WITH a record and nothing there: plainly not running (the record is the kernel's own word on its port)
  await asObsidian({ token: TOKEN, port: await closedPort() }, async () => {
    const r = await panel()._kernelPost("/views", { views: { active: "all", tags: [] } });
    assert.deepEqual([r.ok, r.unreachable, r.error], [false, true, KERNEL_DOWN]);
  });
  assert.match(SRC, /const KERNEL_DOWN = 'the kernel is not running; start romp and try again';/);
  assert.match(SRC, /const KERNEL_DOWN_NO_RECORD = 'the kernel is not running, or the one running predates this panel and left no port record; start or restart romp and try again';/);
});

test("executed: a record or token file this panel cannot read is said as such — never 'not running' — and no token means no kernel ever ran here", async () => {
  await asObsidian({ token: TOKEN, port: "dir" }, async (root) => {
    const r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
    assert.deepEqual([r.ok, r.unreachable], [false, true]);
    assert.equal(r.error, "this panel could not read the kernel's port record " + path.join(root, "serve-port") + " (EISDIR)");
  });
  await asObsidian({ token: TOKEN, port: "not-a-port" }, async (root) => {
    const r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
    assert.equal(r.error, "this panel could not read the kernel's port record " + path.join(root, "serve-port") + " (not a port number)");
  });
  await asObsidian({ token: "dir", port: 1 }, async (root) => {
    const r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
    assert.equal(r.error, "this panel could not read the kernel's token file " + path.join(root, "serve-token") + " (EISDIR)");
  });
  // no token at all: the kernel mints it at its first boot, so none means no kernel has run against this state dir
  const k = await kernel(() => ({ status: 200, body: { ok: true } }));
  try {
    await asObsidian({ port: k.port, envPort: k.port }, async () => {
      const r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
      assert.deepEqual([r.ok, r.unreachable, r.error], [false, true, KERNEL_DOWN]);
      assert.equal(k.seen.length, 0, "nothing is posted without a token to present");
    });
  } finally { await k.close(); }
  // plain node (the runner): no Electron, so no host to post from — and never a file
  const r = await panel()._kernelPost("/order", { order: [SID] });
  assert.deepEqual([r.ok, r.unreachable], [false, true]);
  assert.doesNotMatch(SRC, /writeFileSync|renameSync/, "the panel writes none of the kernel's state files itself, kernel up or down");
});

test("executed: the three writers post first, and a refusal reverts the optimistic state and says why where the gesture was made", async () => {
  await asObsidian({}, async () => {
    const p = panel();
    const posts: any[] = [];
    const answers: any[] = [];
    p._kernelPost = (route: string, body: any) => { posts.push([route, body]); return Promise.resolve(answers.shift()); };
    const s = p.data.sessions[0];

    // ── a lane flag, as the gear toggles it: optimistic + sticky, then the kernel ──
    answers.push({ ok: true, body: { ok: true } });
    gearClick(p, s, true); await tick();
    assert.deepEqual(posts[0], ["/flag", { id: SID, flag: "hideFromFeed", value: true }]);
    assert.deepEqual([s.hideFromFeed, p._pendingFlags[SID].hideFromFeed, p._laneRefusal], [true, true, null], "accepted: the copy holds sticky until the next poll confirms it");
    const text = "couldn't save that setting — session-flags.json could not be read (read failed: [Errno 5] injected EIO); try again";
    answers.push({ ok: false, refusal: true, error: text, body: { ok: false, error: text, value: false } });
    gearClick(p, s, true); await tick();
    assert.deepEqual(p._pendingFlags, {}, "the kernel's refusal ends the optimistic state…");
    assert.equal(s.hideFromFeed, false, "…repaints the toggle to what the kernel still paints…");
    assert.deepEqual(p._laneRefusal, { sid: SID, flag: "hideFromFeed", text }, "…and shows the kernel's words in the lane gear");
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    gearClick(p, s, true); await tick();
    assert.equal(s.hideFromFeed, false, "no kernel: the toggle goes back where the click found it");
    assert.equal(p._laneRefusal.text, "couldn't save that setting — " + KERNEL_DOWN);

    // ── the views blob, as the tags dialog posts it (a tag edit: never kept locally) ──
    p._views = { active: "all", actives: { timeline: { all: true } }, tags: [], seq: 3 };
    const v = { active: "all", actives: { timeline: { all: true } }, tags: [{ id: "gA", name: "web", members: [SID] }] };
    answers.push({ ok: true, body: { type: "viewsAck", ok: true, views: Object.assign({}, v, { seq: 4 }), seq: 4, refused: [] } });
    p._setViews(v, ["gA"]);
    assert.equal(p._pendingViews, v, "optimistic while the kernel judges");
    assert.equal(posts[3][0], "/views");
    assert.deepEqual([posts[3][1].views, posts[3][1].edited], [v, ["gA"]], "the blob and the tag ids this write changed, as the socket op posts them");
    assert.match(posts[3][1].writeId, /^w/, "…and a writeId the ack echoes");
    await tick();
    assert.equal(p._pendingViews, null, "the ack settles the copy — an event, never a frame count");
    assert.equal(p._views.seq, 4, "the kernel's post-write blob is the new base");
    assert.equal(p._viewsWrites.length, 0);
    const store = { active: "all", actives: { timeline: { all: true } }, tags: [{ id: "gA", name: "web", members: [SID, SID2] }], seq: 5 };
    const err = '"web": it was edited after your copy was taken, so your copy predates the store\'s and was not applied';
    answers.push({ ok: false, refusal: true, error: err, body: { type: "viewsAck", ok: false, views: store, seq: 5, refused: [{ tid: "gA", name: "web", reason: "predates" }], error: err } });
    p._setViews(Object.assign({}, v, { tags: [{ id: "gA", name: "web", members: [] }] }), ["gA"]);
    await tick();
    assert.equal(p._pendingViews, null, "refused: the copy reverts at once");
    assert.equal(p._views, store, "…to the blob the kernel serves");
    assert.deepEqual([p._tagEditErr.error, p._tagEditErr.name], [err, "web"], "…with the kernel's words in the dialog");
    p._tagEditErr = null;
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    p._setViews(v, ["gA"]);
    await tick();
    assert.equal(p._pendingViews, null, "a tag edit no kernel took reverts…");
    assert.equal(p._tagEditErr.error, "nothing was changed — " + KERNEL_DOWN, "…and says so");
    assert.equal(p._localLens, null, "a tag edit is never kept locally");
    p._tagEditErr = null;

    // ── the lens, as the Filter menu posts it: no kernel → kept LOCAL and said; a kernel's answer clears it ──
    const lens = { actives: { timeline: { tags: ["web"] } } };
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    p._setLens(lens);
    assert.deepEqual(p._pendingViews.actives.timeline, { tags: ["web"] }, "optimistic while it posts");
    await tick();
    assert.deepEqual(p._localLens, { fields: { actives: { timeline: { tags: ["web"] } } }, reason: KERNEL_DOWN }, "no kernel: the filter stays, marked unsaved, with the reason — only the surface the gesture changed");
    assert.deepEqual([p._pendingViews, p._viewsWrites.length, p._tagEditErr], [null, 0, null], "nothing is in flight and nothing is called an error — the filter is the viewer's own");
    assert.deepEqual(timelineLens(p._curViews()).tags, ["web"], "…and it applies to what the panel shows");
    assert.equal(p._views.actives.timeline.all, true, "…while the store's blob is untouched");
    answers.push({ ok: false, refusal: true, error: '"web": refused', body: { type: "viewsAck", ok: false, views: store, seq: 5, refused: [{ tid: "gA", name: "web", reason: "refused" }], error: '"web": refused' } });
    p._setLens({ actives: { timeline: { all: true } } });
    await tick();
    assert.equal(p._localLens, null, "a kernel that RULED on a lens write (even refusing it) clears the local filter: its blob is what stands");
    assert.equal(p._tagEditErr.error, '"web": refused');
    p._tagEditErr = null;
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    p._setLens(lens); await tick();
    assert.ok(p._localLens, "unsaved again");
    const saved = Object.assign({}, store, { actives: { timeline: { tags: ["web"] } }, seq: 6 });
    answers.push({ ok: true, body: { type: "viewsAck", ok: true, views: saved, seq: 6, refused: [] } });
    p._setLens(lens); await tick();
    assert.deepEqual([p._localLens, p._pendingViews, p._views.seq], [null, null, 6], "a later write the kernel takes clears the note; the kernel's blob carries the filter now");

    // ── the lane order, as a drag persists it: prev + the moved lane ride along for the revert ──
    p.data.sessions = [{ id: "a" }, { id: "b" }, { id: "c" }];
    p._applyOrderToData(["c", "b", "a"]);
    answers.push({ ok: true, body: { ok: true, order: ["c", "b", "a"] } });
    p._persistOrder(["c", "b", "a"], ["a", "b", "c"], "c");
    await tick();
    assert.deepEqual(posts[posts.length - 1], ["/order", { order: ["c", "b", "a"] }]);
    assert.deepEqual(p.data.sessions.map((x: any) => x.id), ["c", "b", "a"], "accepted: the lanes stay");
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    p._persistOrder(["c", "b", "a"], ["a", "b", "c"], "c");
    await tick();
    assert.deepEqual(p.data.sessions.map((x: any) => x.id), ["a", "b", "c"], "refused: the lanes go back at once");
    assert.deepEqual(p._laneRefusal, { sid: "c", flag: "", text: "couldn't save the new order — " + KERNEL_DOWN },
      "…and the dragged lane's gear says why (no shell bell here)");
    assert.equal(p._tagEditErr, null, "a lane drag's refusal is not the dialog's to show");
    // a row dragged INSIDE the tags dialog: the dialog's own row carries the reason, and only the dialog's
    p._laneRefusal = null;
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    p._persistOrder(["c", "b", "a"], ["a", "b", "c"], "c", "dialog");
    await tick();
    assert.deepEqual(p._tagEditErr, { host: "", name: "", error: "couldn't save the new order — " + KERNEL_DOWN, kind: "order" });
    assert.equal(p._laneRefusal, null, "not the lane gear's slot too (it rendered both: the same sentence twice)");
    p._viewsDialog = { remove() {} };
    p._closeViewsDialog();
    assert.equal(p._tagEditErr, null, "the dialog's close clears what the dialog showed");
  });
  // the two drag sites hand the writer the pre-drag order and the moved lane; the dialog's names itself
  assert.match(SRC, /const prev = \(\(this\.data && this\.data\.sessions\) \|\| \[\]\)\.map\(\(s\) => s\.id\);[\s\S]{0,400}this\._persistOrder\(full, prev, d\.sid\);/, "the lane drag");
  assert.match(SRC, /const prev = \(\(this\.data && this\.data\.sessions\) \|\| \[\]\)\.map\(\(s\) => s\.id\);[\s\S]{0,400}this\._persistOrder\(full, prev, vis\[toIdx\], 'dialog'\);/, "the dialog's row drag");
});

// ── the Filter menu and the tags dialog, executed over a fake DOM (the timeline-views-ack shape) ──────
function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, _text: "", parentNode: null, value: "", _listeners: {} as any,
    get textContent() { return n._text + n.children.map((c: any) => c.textContent).join(""); },
    set textContent(v: any) { n._text = v == null ? "" : String(v); for (const c of n.children) c.parentNode = null; n.children.length = 0; },
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); }, remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { n._attrs[k] = v; }, getAttribute(k: string) { return n._attrs[k]; },
    setAttributeNS(_ns: any, k: string, v: any) { n._attrs[k] = v; }, removeAttribute(k: string) { delete n._attrs[k]; },
    appendChild(c: any) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = n; n.children.push(c); return c; },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = n.children.indexOf(ref); i < 0 ? n.children.push(c) : n.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = n.children.indexOf(c); if (i >= 0) n.children.splice(i, 1); c.parentNode = null; return c; },
    get firstChild() { return n.children[0] || null; },
    remove() { if (n.parentNode) n.parentNode.removeChild(n); },
    addEventListener(t: string, fn: any) { n._listeners[t] = fn; }, removeEventListener(t: string) { delete n._listeners[t]; },
    setPointerCapture() {}, releasePointerCapture() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, closest() { return null; },
    getBoundingClientRect() { return { width: 40, height: 20, left: 10, top: 700, right: 50, bottom: 720 }; },
    focus() { const g: any = globalThis; if (g.document) g.document.activeElement = n; }, select() {}, setSelectionRange() {},
    selectionStart: 0, selectionEnd: 0,
    createEl(t: string, o: any) { const e = makeNode(t); if (o && o.cls) e.classList.add(o.cls); if (o && o.text) e.textContent = o.text; n.appendChild(e); return e; },
    createDiv(o: any) { return n.createEl("div", o); }, createSpan(o: any) { return n.createEl("span", o); },
  };
  return n;
}
async function withDom(fn: () => Promise<void>) {
  const g: any = globalThis;
  const saved = { setTimeout: g.setTimeout };
  g.document = {
    body: makeNode("body"), documentElement: makeNode("html"), head: makeNode("head"), activeElement: null,
    createElement(t: string) { return t === "canvas" ? { getContext() { return { font: "", measureText(s: string) { return { width: (s ? s.length : 0) * 6 }; } }; } } : makeNode(t); },
    createElementNS(_ns: any, t: string) { return makeNode(t); },
    createTextNode(text: string) { const t = makeNode("#text"); t._text = text; return t; },
    getElementById() { return null; }, addEventListener() {}, removeEventListener() {},
  };
  g.window = g; g.innerWidth = 1400; g.innerHeight = 800;
  g.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
  g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)", fontFamily: "sans-serif" });
  g.requestAnimationFrame = () => 0;
  g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  g.addEventListener = () => {}; g.removeEventListener = () => {};
  // a real timer, its callback guarded: a dialog's deferred focus may fire after this DOM is gone
  g.setTimeout = (fn: any, ms?: number) => saved.setTimeout(() => { try { fn(); } catch { /* a fake node after teardown */ } }, ms);
  try { await fn(); } finally {
    g.setTimeout = saved.setTimeout;
    for (const k of ["document", "window", "innerWidth", "innerHeight", "localStorage", "getComputedStyle", "requestAnimationFrame", "matchMedia", "addEventListener", "removeEventListener"]) delete g[k];
  }
}
const walk = (n: any): any[] => [n, ...(n && n.children ? n.children.flatMap(walk) : [])];
const sess = (id: string, name: string, color: string) => ({
  id, name, color, state: "working", live: true, model: "Opus", effort: "high",
  context: 40, since: 1_781_000_000 - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false,
});
// the harness's textContent already walks a node's children, so a tag row reads as its CHIP's text (since
// T283b the text lives on the chip span, the shared menu's shape); a tag row's selected state is the chip's
// aria-pressed, a plain row's the trailing ✓
const rows = (menu: any) => menu.children.map((c: any) => c.textContent as string);
const rowNamed = (menu: any, label: string) => menu.children.find((c: any) => c.textContent === label || c.textContent === label + "✓");
const selected = (row: any) => !!row && ((row.children || []).some((c: any) => c._attrs && c._attrs["aria-pressed"] === "true") || String(row.textContent).endsWith("✓"));

test("executed: the Filter menu shows a refused lens write's reason, keeps an unsaved filter and says so, and clears the note once a kernel takes one (review find, 2026-09-08)", async () => {
  await withDom(() => asObsidian({}, async () => {
    const p = panel();
    const posts: any[] = [];
    const answers: any[] = [];
    p._kernelPost = (route: string, body: any) => { posts.push([route, body]); return Promise.resolve(answers.shift()); };
    const S = { active: "all", actives: { timeline: { all: true } }, seq: 3, tags: [{ id: "gA", name: "web", color: "#3b82f6", members: [SID] }] };
    p._views = S;
    const anchor = makeNode("button");
    p._openViewsMenu(anchor);
    const menu = p._viewsMenu;
    assert.ok(menu && typeof menu._build === "function", "the menu takes a rebuild handle, like the lane gear");
    assert.ok(rowNamed(menu, "web"), "the tag row is there: " + JSON.stringify(rows(menu)));
    assert.ok(!rows(menu).some((t: string) => t.startsWith("⚠")), "nothing to say yet");

    // a kernel REFUSES the lens write: the copy reverts and the reason shows in THIS menu, repainted in place
    const err = '"web": it was edited after your copy was taken, so your copy predates the store\'s and was not applied';
    answers.push({ ok: false, refusal: true, error: err, body: { type: "viewsAck", ok: false, views: S, seq: 3, refused: [{ tid: "gA", name: "web", reason: "predates" }], error: err } });
    rowNamed(menu, "web")._listeners.click();
    assert.equal(posts[0][0], "/views");
    assert.deepEqual(posts[0][1].views.actives.timeline, { tags: ["web"] }, "the lens write, from the menu's toggle");
    await tick();
    assert.equal(p._viewsMenu, menu, "the menu stayed open (a settings panel, not a command)");
    assert.ok(rows(menu).some((t: string) => t === "⚠ " + err + "✕"), "the refusal's reason, with ✕, in the menu: " + JSON.stringify(rows(menu)));
    assert.deepEqual([p._pendingViews, p._localLens], [null, null], "refused: nothing kept");
    assert.ok(rowNamed(menu, "web") && !selected(rowNamed(menu, "web")), "the row reads unselected again");
    p._tagEditErr = null; menu._build();

    // NO kernel takes it: the filter stays applied, and the menu says it is not saved and why
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    rowNamed(menu, "web")._listeners.click();
    await tick();
    assert.ok(selected(rowNamed(menu, "web")), "the filter is on: " + JSON.stringify(rows(menu)));
    assert.deepEqual(timelineLens(p._curViews()).tags, ["web"], "…and applies to the lanes");
    assert.ok(rows(menu).includes("⚠ this filter is not saved — " + KERNEL_DOWN), "the not-saved note, with the reason: " + JSON.stringify(rows(menu)));
    assert.equal(p._tagEditErr, null, "not an error: the viewer's own filter, just unsaved");
    assert.deepEqual(p._pendingViews, null);

    // a later lens write a kernel takes: the note clears, the kernel's blob carries the filter
    const saved = Object.assign({}, S, { actives: { timeline: { all: true } }, seq: 4 });
    answers.push({ ok: true, body: { type: "viewsAck", ok: true, views: saved, seq: 4, refused: [] } });
    rowNamed(menu, "All")._listeners.click();          // All closes the menu
    assert.equal(p._viewsMenu, null);
    await tick();
    assert.deepEqual([p._localLens, p._pendingViews, p._views.seq], [null, null, 4]);
    p._openViewsMenu(anchor);
    assert.ok(!rows(p._viewsMenu).some((t: string) => t.startsWith("⚠")), "nothing left to say: " + JSON.stringify(rows(p._viewsMenu)));
    assert.ok(rowNamed(p._viewsMenu, "All").textContent.endsWith("✓"));
    p._closeViewsMenu();
  }));
  assert.match(SRC, /menu\._build = build;   \/\/ viewsAck \/ setCaps \/ _kernelViewsAnswer repaint the open menu/);
  assert.match(SRC, /if \(this\._viewsMenu && typeof this\._viewsMenu\._build === 'function'\) this\._viewsMenu\._build\(\);/, "_repaintTagSurfaces reaches the Filter menu");
});

test("executed: the local lens is only the viewer's own surfaces and fields, overlaid on the store, never persisted by another gesture (review finds, 2026-09-08)", async () => {
  await withDom(() => asObsidian({}, async () => {
    const p = panel();
    const posts: any[] = [];
    const answers: any[] = [];
    p._kernelPost = (route: string, body: any) => { posts.push([route, body]); return Promise.resolve(answers.shift()); };
    const web = { id: "gA", name: "web", color: "#3b82f6", members: [SID] }, api = { id: "gB", name: "api", color: "#DD42FF", members: [SID2] };
    const S = { active: "all", actives: { chat: { tags: ["api"] }, timeline: { all: true }, outline: { all: true } }, seq: 3, tags: [web, api] };
    p._views = S;
    p.data.sessions.push({ id: SID2, name: "api" });
    const anchor = makeNode("button");
    p._openViewsMenu(anchor);
    const menu = p._viewsMenu;

    // F0: kernel down, a timeline filter from the Filter menu — the whole map was posted (the store's chat
    // and outline plus the gesture's timeline), but only TIMELINE is held
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    rowNamed(menu, "web")._listeners.click();
    assert.deepEqual(posts[0][1].views.actives, { chat: { tags: ["api"] }, timeline: { tags: ["web"] }, outline: { all: true } }, "posted over the store's map");
    await tick();
    assert.deepEqual(p._localLens.fields, { actives: { timeline: { tags: ["web"] } } }, "held: the gesture's own surface, nothing else");
    // the store moves under the panel (a dashboard changes the chat lens): the panel shows the STORE's chat…
    const S2 = Object.assign({}, S, { actives: { chat: { tags: ["web"] }, timeline: { all: true }, outline: { none: true } }, seq: 4 });
    p._takeViews(S2);
    assert.deepEqual(p._curViews().actives, { chat: { tags: ["web"] }, timeline: { tags: ["web"] }, outline: { none: true } },
      "overlaid PER KEY: the store's new chat and outline, the viewer's own timeline (the dialog's pane-filter rows read this map)");
    // …and the next Filter-menu click posts the store's new chat with the gesture's timeline, never the snapshot's
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    rowNamed(menu, "api")._listeners.click();
    assert.deepEqual(posts[1][1].views.actives, { chat: { tags: ["web"] }, timeline: { tags: ["web", "api"] }, outline: { none: true } },
      "the store's chat as it is NOW, the gesture's timeline — the local snapshot's chat never flips the dashboard's filter back");
    await tick();
    assert.deepEqual(p._localLens.fields, { actives: { timeline: { tags: ["web", "api"] } } });

    // F2: a second unreachable gesture of another kind MERGES (a pill drag's tagOrder beside the filter)
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    p._setLens({ tagOrder: ["api", "web"] });
    await tick();
    assert.deepEqual(p._localLens.fields, { actives: { timeline: { tags: ["web", "api"] } }, tagOrder: ["api", "web"] }, "both held");
    assert.deepEqual(viewTagUnion(p._curViews()).map((g: any) => g.name), ["api", "web"], "the pills show the dragged order");
    assert.ok(rows(menu).includes("⚠ this filter is not saved — " + KERNEL_DOWN), "one note: " + JSON.stringify(rows(menu)));

    // F1: the kernel is back and a TAG edit lands (the Obsidian whole-blob path): its blob carries the
    // STORE's timeline lens and tagOrder, not the local ones; the filter stays applied, the note stays
    const g = viewTagUnion(p._curViews()).find((x: any) => x.name === "web");
    const stored = Object.assign({}, S2, { tags: [web, Object.assign({}, api)], seq: 5 });
    stored.tags[0] = Object.assign({}, web, { members: [SID, SID2] });
    answers.push({ ok: true, body: { type: "viewsAck", ok: true, views: stored, seq: 5, refused: [] } });
    p._editTagUnion(g, { add: [SID2] });
    const tagPost = posts[posts.length - 1][1];
    assert.deepEqual(tagPost.edited, ["gA"], "a tag edit, on the legacy whole-blob path");
    assert.deepEqual(tagPost.views.actives.timeline, { all: true }, "the STORE's timeline lens rides the tag edit, never the unsaved one");
    assert.equal(tagPost.views.tagOrder, undefined, "…and the store's (absent) tagOrder, never the unsaved drag");
    assert.deepEqual(tagPost.views.tags[0].members, [SID, SID2], "the edit itself is there");
    await tick();
    assert.equal(p._views, stored, "the kernel's blob is the base");
    assert.deepEqual(p._localLens.fields, { actives: { timeline: { tags: ["web", "api"] } }, tagOrder: ["api", "web"] }, "the local lens is untouched by a write that did not carry it");
    assert.deepEqual(timelineLens(p._curViews()).tags, ["web", "api"], "…and still applies");
    assert.ok(rows(menu).includes("⚠ this filter is not saved — " + KERNEL_DOWN), "…and the note still shows");

    // F2: a ruled write of ONE kind releases only that kind — the kernel takes the pill order, the filter stays local
    const ordered = Object.assign({}, stored, { tagOrder: ["api", "web"], seq: 6 });
    answers.push({ ok: true, body: { type: "viewsAck", ok: true, views: ordered, seq: 6, refused: [] } });
    p._setLens({ tagOrder: ["api", "web"] });
    await tick();
    assert.deepEqual(p._localLens.fields, { actives: { timeline: { tags: ["web", "api"] } } }, "tagOrder released, the filter still held");
    assert.ok(rows(menu).includes("⚠ this filter is not saved — " + KERNEL_DOWN));
    // …and a ruled write of the filter's own surface releases it: the note goes, the store carries the lens
    const saved = Object.assign({}, ordered, { actives: Object.assign({}, ordered.actives, { timeline: { tags: ["web", "api", "x"] } }), seq: 7 });
    answers.push({ ok: true, body: { type: "viewsAck", ok: true, views: saved, seq: 7, refused: [] } });
    rowNamed(menu, "(no tags)")._listeners.click();
    assert.deepEqual(posts[posts.length - 1][1].views.actives.chat, { tags: ["web"] }, "over the store's map");
    await tick();
    assert.deepEqual([p._localLens, p._pendingViews], [null, null], "nothing local is left");
    assert.ok(!rows(menu).some((t: string) => t.startsWith("⚠")), "nothing to say: " + JSON.stringify(rows(menu)));

    // a filter toggled back to what the store has, with no kernel, is the store's — not held as unsaved
    p._views = S; p._localLens = null; menu._build();
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    rowNamed(menu, "web")._listeners.click(); await tick();
    assert.ok(p._localLens, "on");
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    rowNamed(menu, "web")._listeners.click(); await tick();
    assert.equal(p._localLens, null, "off again: equal to the store, so nothing is unsaved and no note shows");
    assert.ok(!rows(menu).some((t: string) => t.startsWith("⚠")));

    // F3: a dialog drag's refusal (kind 'order') is not the Filter menu's to show
    p._tagEditErr = { host: "", name: "", error: "couldn't save the new order — " + KERNEL_DOWN, kind: "order" }; menu._build();
    assert.ok(!rows(menu).some((t: string) => t.startsWith("⚠")), "an order refusal is the dialog's: " + JSON.stringify(rows(menu)));
    p._tagEditErr = { host: "", name: "web", error: '"web": refused' }; menu._build();
    assert.ok(rows(menu).some((t: string) => t.startsWith('⚠ "web": refused')), "a tag or lens refusal shows");
    p._closeViewsMenu();
  }));
});

test("executed: a lens write in flight never lends the local lens to another write — a tag edit and a chat pane-filter row post the STORE's timeline while a pill drag is pending (review finds, 2026-09-08)", async () => {
  await withDom(() => asObsidian({}, async () => {
    // a REAL panel (constructed, fed a frame), so the tags dialog builds as it does in Obsidian
    const panel: any = new TimelinePanel(makeNode("div"));
    const now = 1_781_000_000;
    const web = { id: "gA", name: "web", color: "#3b82f6", members: [SID], mtime: 100 }, api = { id: "gB", name: "api", color: "#DD42FF", members: [SID2], mtime: 100 };
    const S = { active: "all", actives: { chat: { all: true }, timeline: { all: true }, outline: { all: true } }, at: 100, seq: 1000, tags: [web, api] };
    panel.update({ now, sessions: [sess(SID, "web", "#f7768e"), sess(SID2, "api", "#7aa2f7")], turns: {}, messages: [], judging: [], views: JSON.parse(JSON.stringify(S)), palette: ["#1EA1EB", "#54B204"] });
    assert.deepEqual(panel._views.actives.timeline, { all: true });
    const posts: any[] = [];
    const answers: any[] = [];
    // an answer may be a function of the posted body (a kernel echoing the write, stamped) or a promise the test settles
    panel._kernelPost = (route: string, body: any) => { posts.push([route, body]); const a = answers.shift(); return Promise.resolve(typeof a === "function" ? a(body) : a); };
    const echo = (seq: number) => (body: any) => ({ ok: true, body: { type: "viewsAck", ok: true, views: Object.assign({}, body.views, { seq }), seq, refused: [] } });

    // kernel down: the viewer filters the timeline; the filter is held
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    panel._setLens({ actives: Object.assign(panel._lensBaseActives(), { timeline: { tags: ["web"] } }) }, { surfaces: ["timeline"] });
    await tick();
    assert.deepEqual(panel._localLens.fields, { actives: { timeline: { tags: ["web"] } } });

    // the kernel is back; a pill drag goes out and stays IN FLIGHT
    let settleDrag: (a: any) => void = () => {};
    answers.push(new Promise((r) => { settleDrag = r; }));
    panel._setLens({ tagOrder: ["api", "web"] }, { tagOrder: true });
    assert.deepEqual(posts[1][1].views.tagOrder, ["api", "web"]);
    assert.deepEqual(panel._pendingViews.actives.timeline, { all: true }, "the pending copy is the WRITE base plus the drag — the store's timeline, never the held one");
    assert.deepEqual(panel._writeBase().actives.timeline, { all: true }, "…and that is what every write built during the flight starts from");
    assert.deepEqual(timelineLens(panel._curViews()).tags, ["web"], "while the held filter still shows");
    assert.deepEqual(viewTagUnion(panel._curViews()).map((g: any) => g.name), ["api", "web"], "…with the drag's order (the pending copy)");

    // during the flight, a TAG EDIT (the Obsidian whole-blob path) posts the store's timeline lens
    answers.push(echo(1002));
    const g = viewTagUnion(panel._curViews()).find((x: any) => x.name === "web");
    panel._editTagUnion(g, { add: [SID2] });
    const tagPost = posts[2][1];
    assert.deepEqual(tagPost.edited, ["gA"]);
    assert.deepEqual(tagPost.views.actives.timeline, { all: true }, "the tag edit carries the STORE's timeline, not the unsaved filter");
    assert.deepEqual(tagPost.views.tags.find((t: any) => t.id === "gA").members, [SID, SID2], "…and the edit itself");
    await tick();
    assert.deepEqual(panel._localLens.fields, { actives: { timeline: { tags: ["web"] } } }, "the held filter is untouched");

    // …and a CHAT pane-filter row in the tags dialog posts chat's new value with the store's timeline (F2, trace c)
    answers.push(echo(1003));
    panel._openViewsDialog(null);
    // the pane filters open FOLDED to one summary line per pane since the many-tags change (2026-09-09); the
    // chips this test clicks exist only in the open matrix, so open it through the caption's caret first
    const filtersCap = walk(panel._viewsDialog).find((n) => n.tag === "span" && String(n.textContent).startsWith("pane filters"));
    assert.ok(filtersCap && filtersCap._listeners.click, "the pane-filters caption folds and opens the matrix");
    if (filtersCap._attrs["aria-expanded"] !== "true") filtersCap._listeners.click();
    const chatLabel = walk(panel._viewsDialog).find((n) => n.tag === "span" && n.textContent === "Chat");
    assert.ok(chatLabel, "the Chat pane-filter row");
    // the chips sit in their own wrapping cell beside the label since the many-tags change, so search the row
    const chatWeb = walk(chatLabel.parentNode).find((n: any) => n.textContent === "web" && n._listeners && n._listeners.click);
    assert.ok(chatWeb, "its web pill");
    chatWeb._listeners.click();
    const chatPost = posts[3][1];
    assert.deepEqual(chatPost.views.actives, { chat: { tags: ["web"] }, timeline: { all: true }, outline: { all: true } },
      "chat's new value, the STORE's timeline and outline — never the held timeline");
    await tick();
    assert.deepEqual(panel._views.actives.chat, { tags: ["web"] }, "the kernel took chat");
    assert.deepEqual(panel._localLens.fields, { actives: { timeline: { tags: ["web"] } } }, "the kernel's ok released only chat; the timeline filter survives");
    assert.deepEqual(timelineLens(panel._curViews()).tags, ["web"], "…and still shows");
    panel._closeViewsDialog();
    panel._openViewsMenu(makeNode("button"));
    assert.ok(rows(panel._viewsMenu).includes("⚠ this filter is not saved — " + KERNEL_DOWN), "…with its note: " + JSON.stringify(rows(panel._viewsMenu)));
    panel._closeViewsMenu();

    // the drag lands at last: only tagOrder was its own, so the filter and its note stay
    settleDrag(echo(1004)(posts[1][1]));
    await tick(); await tick();
    assert.deepEqual(panel._views.tagOrder, ["api", "web"]);
    assert.deepEqual(panel._localLens.fields, { actives: { timeline: { tags: ["web"] } } });
    assert.equal(panel._pendingViews, null);
  }));
});

test("every Node require the panel makes sits inside a try, so the browser bundle still builds (PR #1078's first CI run)", () => {
  // esbuild bundles this file for the BROWSER too (ui/webview/timeline-main.ts inlines it; platform browser)
  // and resolves every literal require at bundle time: an unresolvable one fails the build UNLESS a
  // try/catch wraps it, which esbuild reads as run-time handled and leaves as-is. The Electron guard keeps
  // the page from evaluating any of them. This pin fails on a bare require before `npm run build` would.
  const fnOf = (at: number) => { const s = SRC.lastIndexOf("\n  _", at); return SRC.slice(s, SRC.indexOf("\n  }\n", at) + 4); };
  let seen = 0;
  for (const m of SRC.matchAll(/require\('(fs|os|path|http|child_process)'\)/g)) {
    seen++;
    const fn = fnOf(m.index!);
    const inFn = m.index! - SRC.lastIndexOf("\n  _", m.index!);
    assert.ok(fn.lastIndexOf("try {", inFn) >= 0 || fn.lastIndexOf("try { ", inFn) >= 0, "require('" + m[1] + "') at " + m.index + " sits inside a try: " + fn.slice(0, 60));
  }
  assert.ok(seen >= 6, "the requires were found: " + seen);
  const host = SRC.slice(SRC.indexOf("  _kernelHost() {"), SRC.indexOf("  _kernelPost(route, body) {"));
  assert.match(host, /return null;\s*\n(?:\s*\/\/[^\n]*\n)*\s*try \{\s*\n\s*const fs = require\('fs'\), os = require\('os'\), path = require\('path'\);/,
    "_kernelHost: the Electron guard, then the requires inside a try");
});

test("plain node never posts: the writers do nothing without a host hook or Electron (the 2026-07-02 runner rule)", async () => {
  const p = panel();
  const posts: any[] = [];
  p._kernelPost = (route: string, body: any) => { posts.push([route, body]); return Promise.resolve({ ok: true, body: { ok: true } }); };
  assert.equal((process.versions as any).electron, undefined, "this runner is plain node");
  p._setSessionFlag(p.data.sessions[0], "hideFromFeed", true);
  p._setViews({ active: "all", tags: [] }, []);
  p._persistOrder([SID], [SID], SID);
  await tick();
  assert.deepEqual(posts, [], "nothing to post from, nothing posted — and nothing written (no fs writer exists)");
  assert.equal(p._viewsWrites.length, 0, "no write record was minted for a write that never left");
  assert.match(SRC, /_kernelHost\(\) \{[\s\S]{0,500}if \(typeof process === 'undefined' \|\| !process\.versions \|\| !process\.versions\.electron\) return null;/,
    "the one Electron-or-nothing guard every writer takes");
});

test("executed: before the token leaves this panel the port must prove itself over GET /healthz with no token; a port another service answers on receives nothing else and is refused by name (review find, 2026-09-08, on #1078)", async () => {
  // a stale serve-port record that some other local service now listens on: every shape a squatter can
  // answer the proof with. The stand-in would ACCEPT the POST if it ever arrived, so a token leaking
  // through would read as ok:true here
  const squatters: [string, Answer][] = [
    ["a plain 200 OK", { status: 200, raw: "OK" }],
    ["a JSON service", { status: 200, body: { ok: true } }],
    ["a 404", { status: 404, raw: "not found" }],
    ["the body without the kernel identity header", { status: 200, raw: "ok" }],
    ["the header on the wrong body", { status: 200, raw: "ready", headers: { "X-Romp-Boot": "1.2" } }],
  ];
  for (const [label, hz] of squatters) {
    const sq = await kernel(() => ({ status: 200, body: { ok: true, id: SID, flag: "hideFromFeed", value: true } }), { healthz: hz });
    try {
      await asObsidian({ port: sq.port, token: TOKEN }, async () => {
        const p = panel();
        const r = await p._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
        assert.deepEqual([r.ok, r.unreachable, r.foreign], [false, true, true], label);
        assert.ok(r.error.startsWith("127.0.0.1:" + sq.port + " answered, but not as a romp kernel (GET /healthz answered HTTP " + hz.status), label + ": " + r.error);
        assert.ok(r.error.endsWith("); this panel's token was not sent to it"), label + ": " + r.error);
        assert.equal(sq.seen.length, 1, label + ": the proof and nothing after it");
        assert.deepEqual([sq.seen[0].method, sq.seen[0].url], ["GET", "/healthz"], label);
        // the writer, as the gear runs it: the toggle goes back where the click found it and the gear says why
        const s = p.data.sessions[0];
        await gearClick(p, s, true);
        assert.deepEqual([s.hideFromFeed, p._pendingFlags], [false, {}], label);
        assert.ok(p._laneRefusal.text.startsWith("couldn't save that setting — 127.0.0.1:" + sq.port + " answered, but not as a romp kernel"), label + ": " + p._laneRefusal.text);
        for (const q of sq.seen) {
          assert.equal(q.headers["x-romp-token"], undefined, label + ": no token ever reached the port");
          assert.equal(q.headers["authorization"], undefined, label);
          assert.equal(q.method + " " + q.url, "GET /healthz", label + ": nothing but token-less proofs");
        }
      });
    } finally { await sq.close(); }
  }
  // the CLI-default fallback (no record) goes through the very same proof: a squatter there sees no token either
  const sq2 = await kernel(() => ({ status: 200, body: { ok: true } }), { healthz: { status: 200, raw: "OK" } });
  try {
    await asObsidian({ token: TOKEN, envPort: sq2.port }, async () => {
      const r = await panel()._kernelPost("/order", { order: [SID] });
      assert.deepEqual([r.ok, r.unreachable, r.foreign], [false, true, true]);
      assert.equal(sq2.seen.length, 1);
      assert.ok(sq2.seen.every((q) => q.method === "GET" && q.url === "/healthz" && q.headers["x-romp-token"] === undefined), "the fallback port is asked to prove itself and the token stays home");
    });
  } finally { await sq2.close(); }
  // a kernel that proves itself gets the write: the proof first, the token only on the POST, both on one connection
  const k = await kernel(() => ({ status: 200, body: { ok: true, order: [SID] } }));
  try {
    await asObsidian({ port: k.port, token: TOKEN }, async () => {
      const r = await panel()._kernelPost("/order", { order: [SID] });
      assert.equal(r.ok, true);
      assert.deepEqual(k.seen.map((q) => [q.method, q.url, q.headers["x-romp-token"]]), [["GET", "/healthz", undefined], ["POST", "/order", TOKEN]]);
      assert.equal(k.seen[0].remotePort, k.seen[1].remotePort, "the token rides the connection that answered as a romp kernel");
    });
  } finally { await k.close(); }
  // nothing answering on the proof is still plainly 'not running' (the record's word), never 'not a romp kernel'
  await asObsidian({ token: TOKEN, port: await closedPort() }, async () => {
    const r = await panel()._kernelPost("/flag", { id: SID, flag: "hideFromFeed", value: true });
    assert.deepEqual([r.ok, r.unreachable, r.foreign, r.error], [false, true, undefined, KERNEL_DOWN]);
  });
  // the shape: the proof gates the POST, on loopback, the liveness route, no token in that request
  const kp = SRC.slice(SRC.indexOf("  _kernelPost(route, body) {"), SRC.indexOf("  _kernelProve(port, agent, down) {"));
  assert.ok(kp.length > 0 && kp.length < 9000, "the writer's slice");
  assert.match(kp, /return this\._kernelProve\(port, agent, down\)\.then\(\(proof\) => \{\s*\n\s*if \(!proof\.ok\) \{ teardown\(\); return proof; \}/, "the proof gates the POST; a failed proof is the answer, and no request follows it");
  assert.ok(kp.indexOf("this._kernelProve(") < kp.indexOf("'X-Romp-Token': tok"), "the proof is asked before the token is put in any header");
  const pv = SRC.slice(SRC.indexOf("  _kernelProve(port, agent, down) {"), SRC.indexOf("  _kernelProve(port, agent, down) {") + 3500);
  assert.match(pv, /host: '127\.0\.0\.1', port, path: '\/healthz', method: 'GET', agent: agent \|\| undefined \}/, "loopback only, the liveness route, no headers at all");
  assert.doesNotMatch(pv, /X-Romp-Token|\btok\b/, "no token in the proof");
  assert.ok(pv.includes("if (st === 200 && text.trim() === 'ok' && /^\\d+\\.\\d+$/.test(boot)) return finish({ ok: true, boot });"),
    "the frozen liveness body and the kernel identity header (X-Romp-Boot: <pid>.<epoch>), both required");
  // the fallback is STATED, in the panel, the kernel's record docstring and the reference, as what it is
  assert.match(SRC, /the port the CLI resolves: ROMP_KERNEL_PORT, then ROMP_SERVE_PORT, else 29855\. Record or fallback,\s*\n\s*\/\/ the port must first PROVE itself a romp kernel/, "the panel's comment");
  assert.doesNotMatch(SRC, /the port from the kernel's own\s*\n\s*\/\/ record \(never a guess\)/, "the sentence that was untrue is gone");
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  assert.match(KERNEL, /the panel tries the port the CLI resolves -- ROMP_KERNEL_PORT,\s*\n\s*then ROMP_SERVE_PORT, else 29855/, "the serve-port record's docstring");
  assert.match(KERNEL, /the panel first asks the port to prove itself over GET \/healthz/, "and it names the proof");
  assert.doesNotMatch(KERNEL, /never guessing a port or falling back to a write the kernel cannot check/, "the record's untrue sentence is gone");
  const DOCS = fs.readFileSync(path.resolve(process.cwd(), "..", "docs", "reference.md"), "utf8");
  assert.match(DOCS, /it tries the port the command line resolves, `ROMP_KERNEL_PORT`, then\s*\n`ROMP_SERVE_PORT`, else `29855`/, "the reference");
  assert.match(DOCS, /`GET \/healthz` with no token, on `127\.0\.0\.1` only, must answer `200 ok` with\s*\nthe kernel's `X-Romp-Boot` identity before the token is sent/);
});

test("executed: the not-saved note is reconciled on the store read: a poll that shows the store carrying the held filter releases it and repaints the Filter menu; a different store value or a stale frame leaves it (review find, 2026-09-08, on #1078)", async () => {
  await withDom(() => asObsidian({}, async () => {
    const p = panel();
    const answers: any[] = [];
    p._kernelPost = () => Promise.resolve(answers.shift());
    const S = { active: "all", actives: { timeline: { all: true } }, seq: 3, tags: [{ id: "gA", name: "web", color: "#3b82f6", members: [SID] }] };
    p._views = S;
    p._openViewsMenu(makeNode("button"));
    const menu = p._viewsMenu;
    // no kernel takes the lens write: held locally, said in the menu
    answers.push({ ok: false, unreachable: true, error: KERNEL_DOWN });
    rowNamed(menu, "web")._listeners.click();
    await tick();
    assert.ok(rows(menu).includes("⚠ this filter is not saved — " + KERNEL_DOWN), "held, and said: " + JSON.stringify(rows(menu)));
    assert.deepEqual(timelineLens(p._curViews()).tags, ["web"]);
    // a poll whose store carries a DIFFERENT timeline filter (a dashboard picked another tag): the viewer's own
    // unsaved choice still stands, note and all
    const tags = S.tags.concat([{ id: "gB", name: "other", color: "#ef4444", members: [SID2] }]);
    const other = Object.assign({}, S, { actives: { timeline: { tags: ["other"] }, chat: { all: true } }, seq: 4, tags });
    assert.equal(p._takeViews(other), true, "adopted");
    assert.deepEqual(timelineLens(p._curViews()).tags, ["web"], "the held filter still shows over the store's");
    assert.ok(rows(menu).includes("⚠ this filter is not saved — " + KERNEL_DOWN), "still unsaved, still said");
    // a stale frame (an older seq) adopts nothing and settles nothing
    assert.equal(p._takeViews(Object.assign({}, other, { actives: { timeline: { tags: ["web"] } }, seq: 2 })), false);
    assert.ok(p._localLens, "a frame the gate turned away is not the store");
    assert.ok(rows(menu).includes("⚠ this filter is not saved — " + KERNEL_DOWN));
    // the poll that shows the store carrying the very filter (a dashboard saved it, or this panel's own write landed
    // after an answer that read as unreachable): released on that read, the note gone, the menu repainted in place
    const saved = Object.assign({}, other, { actives: { timeline: { tags: ["web"] }, chat: { all: true } }, seq: 5 });
    assert.equal(p._takeViews(saved), true);
    assert.equal(p._localLens, null, "nothing held: the store has it");
    assert.ok(!rows(menu).some((t: string) => t.startsWith("⚠")), "the note went with it: " + JSON.stringify(rows(menu)));
    assert.ok(selected(rowNamed(menu, "web")), "the filter shows, as the store's now");
    assert.deepEqual(timelineLens(p._curViews()).tags, ["web"]);
    p._closeViewsMenu();
  }));
  // per key: only the keys the store now carries are released; the rest stay held with the reason, and a
  // second read with nothing new releases nothing (no repaint for nothing)
  const p = panel();
  p._views = { active: "all", actives: { timeline: { tags: ["web"] }, chat: { all: true } }, tagOrder: ["web", "api"], seq: 9,
               tags: [{ id: "gA", name: "web", color: "#3b82f6", members: [] }, { id: "gB", name: "api", color: "#ef4444", members: [] }] };
  p._localLens = { fields: { actives: { timeline: { tags: ["web"] }, chat: { tags: ["api"] } }, tagOrder: ["web", "api"] }, reason: KERNEL_DOWN };
  assert.equal(p._reconcileLocalLens(), true);
  assert.deepEqual(p._localLens, { fields: { actives: { chat: { tags: ["api"] } } }, reason: KERNEL_DOWN }, "the timeline filter and the pill order the store carries are released; the chat filter it does not is held");
  assert.equal(p._reconcileLocalLens(), false, "nothing more to release");
  p._localLens = null;
  assert.equal(p._reconcileLocalLens(), false);
  // the event: the store read, in _takeViews (the poll's update() and the ack's viewsAck both land there) and the caps adoption
  assert.match(SRC, /if \(data\.views\) this\._takeViews\(data\.views\);/, "the poll adopts the store through _takeViews");
  assert.match(SRC, /this\._views = v; this\._rejectedViews = null;\s*\n\s*if \(this\._reconcileLocalLens\(\)\) this\._repaintTagSurfaces\(\);/, "…which reconciles the held lens on adoption and repaints only when something was released");
  assert.match(SRC, /if \(adopted\) \{ this\._views = this\._rejectedViews; this\._reconcileLocalLens\(\); \}/, "the caps frame's adoption too");
});
