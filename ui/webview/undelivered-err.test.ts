// The kernel's LOUD channel (the user 2026-07-29). An op naming a session this kernel doesn't have used to
// degrade into a no-op — a send with nothing behind it — so typed messages vanished with
// no bubble, no error and no record. The kernel now refuses and emits `err`; this pins the two panes that can
// fire such an op rendering it as a DIALOG rather than a fading toast, and handing the text back.
//
// `err` is deliberately a separate type from `warn`: warn is right for "that name has a bad character" and
// wrong for "the message you just typed was never sent." No jsdom for these renderers, so pin at source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("chat: an `err` takes the confirm MODAL, not the warn toast", () => {
  assert.match(RENDER, /else if \(m\.type === "err" && typeof m\.text === "string" && m\.text\) \{/);
  assert.match(RENDER, /const title = typeof m\.title === "string" && m\.title \? m\.title : "That action was not delivered";/);
  assert.match(RENDER, /showConfirm\(title, m\.text,/);
  // the fading toast stays for the soft cases it was written for (unless a create is in flight, in which
  // case the warn IS that create's verdict and takes a dialog of its own — 2026-07-30)
  assert.match(RENDER, /if \(provisionalId\) failProvisional\(m\.text\); else warnToast\(m\.text\);/);
});

test("chat: the refused text is offered back, because the composer already cleared it", () => {
  assert.match(RENDER, /const copy = typeof m\.copy === "string" \? m\.copy : "";/);
  assert.match(RENDER, /copy \? \[\{ label: "Copy my text", value: "copy" \}, \{ label: "Dismiss", value: "ok" \}\]/);
  assert.match(RENDER, /\(v\) => \{ if \(v === "copy"\) navigator\.clipboard\?\.writeText\(copy\); \}/);
  // no text to hand back (an interrupt, a compact) → just the dismiss
  assert.match(RENDER, /: \[\{ label: "Dismiss", value: "ok" \}\],/);
});

test("feed: the pane that fires card ops gets an error dialog of its own", () => {
  // it had NONE: kernel `warn` has no handler on this page at all, and feedToast fades
  assert.doesNotMatch(FEED, /m\.type === "warn"/);
  assert.match(FEED, /function showErrDialog\(title: string, text: string, copy: string\)/);
  assert.match(FEED, /\} else if \(m\.type === "err" && typeof m\.text === "string" && m\.text\) \{/);
  assert.match(FEED, /const title = typeof m\.title === "string" && m\.title \? m\.title : "That action was not delivered";/);
  assert.match(FEED, /showErrDialog\(title, m\.text, copy\);/);
});

test("feed: the dialog reuses the resume-picker chrome rather than inventing another look", () => {
  const dlg = FEED.split("function showErrDialog(")[1].split("\n}")[0];
  assert.match(dlg, /el\("div", "pickdlg-overlay"\)/);
  assert.match(dlg, /el\("div", "pickdlg-box"\)/);
  assert.match(dlg, /el\("div", "pickdlg-title"\)/);
  // one style is added, for prose the all-buttons picker dialog never needed
  assert.match(dlg, /el\("div", "pickdlg-detail"\)/);
  assert.match(CSS, /\.pickdlg-detail \{/);
  // dismissible by button OR backdrop — an error must never trap the pane
  assert.match(dlg, /ok\.onclick = \(\) => overlay\.remove\(\);/);
  assert.match(dlg, /overlay\.onclick = \(e\) => \{ if \(e\.target === overlay\) overlay\.remove\(\); \};/);
});

test("feed: copying acknowledges the click, per the always-acknowledge rule", () => {
  const dlg = FEED.split("function showErrDialog(")[1].split("\n}")[0];
  assert.match(dlg, /c\.onclick = \(\) => \{ navigator\.clipboard\?\.writeText\(copy\); c\.textContent = "Copied"; \};/);
});

// …and it is also FILED, not just flashed (the user 2026-07-29). romp's error center — the bell in the
// shell's bottom bar, opening a newest-first panel with per-kind filters — is where a problem is findable
// after the fact. A dismissed modal must not erase the fact that a message never landed, so `err` writes an
// entry there too, through the same {romp:'notify'} bridge the card-badge mirror uses.
test("both panes file the refusal in the error center, not only in the modal", () => {
  assert.match(RENDER, /notifyShell\("undelivered", copy \? title \+ ": " \+ copy : title, typeof m\.sid === "string" \? m\.sid : ""\);/);
  assert.match(FEED, /window\.parent\?\.postMessage\(\{ romp: "notify", kind: "undelivered",/);
  // the entry carries the text and the session, so the log answers "what did I lose, and where to?"
  assert.match(FEED, /text: copy \? title \+ ": " \+ copy : title,/);
  assert.match(FEED, /sid: typeof m\.sid === "string" \? m\.sid : "" \}, "\*"\);/);
});

test("the error center knows the new kind: chip label, description and filter toggle", () => {
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  // KINDS drives the filter row, KINDLBL the chip, DESC the tooltip — a kind missing from any of the three
  // renders as an unlabelled entry with no way to mute it
  assert.match(KERNEL, /var KINDS=\[[^\]]*'undelivered'\]/);
  assert.match(KERNEL, /undelivered:'not sent'/);
  assert.match(KERNEL, /undelivered:"something you sent never reached a session/);
  // and it wears the follow-up-failed red: both mean a message of yours didn't land
  assert.match(KERNEL, /\.rerr-chip\.k-nudge,\.rerr-chip\.k-undelivered\{color:#ff6a6a/);
});
