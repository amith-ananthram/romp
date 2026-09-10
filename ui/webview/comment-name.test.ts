// T289 (the user 2026-09-09): a comment on a REMOTE session was refused as a create-by-name. The comment
// dialog prefilled its name box from the viewer's DECORATED session name ("host:name", which federation
// prefixes on every session-bearing frame) sanitized to "host-name", plus the viewer's own thread count,
// and sent that prefill as if the user had chosen it — so the owning kernel stored a name wearing the
// viewer's host label, and the next dialog (same count, after a deleted thread or a lost ack) collided
// with it and was refused. The pure module owns the rule: the prefill is built from the BARE name
// (host-prefix.ts, the rename dialog's 2026-08-02 idiom), and an UNTOUCHED prefill is sent as "" so the
// kernel picks its own default (bare name, next free number). Executed tests on the module; source pins
// on the dialogs that must use it.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { bareSessionName, defaultCommentName, defaultBreakoutName, defaultForkName, nameToSend } from "./comment-name";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const RSID = "TESTHOST:aaaaaaaa-1111-2222-3333-444444444444";
const LSID = "aaaaaaaa-1111-2222-3333-444444444444";

test("a remote session's bare name drops the viewer's host label; a local name is untouched", () => {
  assert.equal(bareSessionName("TESTHOST:web", RSID), "web");
  assert.equal(bareSessionName("web", LSID), "web");
  // a name that merely CONTAINS a colon-like shape under a local sid is not a label (the sid is the marker)
  assert.equal(bareSessionName("TESTHOST:web", LSID), "TESTHOST:web");
});

test("the comment prefill is <bare>-comment-<known+1>, sanitized, never wearing the host label", () => {
  assert.equal(defaultCommentName("TESTHOST:web", RSID, 1), "web-comment-2");
  assert.equal(defaultCommentName("web", LSID, 0), "web-comment-1");
  assert.equal(defaultCommentName("my api", LSID, 2), "my-api-comment-3", "illegal characters become dashes");
  assert.equal(defaultCommentName("", LSID, 0), "session-comment-1", "no name yet: the generic stem");
  assert.doesNotMatch(defaultCommentName("TESTHOST:web", RSID, 4), /TESTHOST/);
});

test("the break-out and fork prefills use the bare name too", () => {
  assert.equal(defaultBreakoutName("TESTHOST:web", RSID), "web-thread");
  assert.equal(defaultBreakoutName("web", LSID), "web-thread");
  assert.equal(defaultForkName("TESTHOST:web", RSID), "web-fork");
  assert.equal(defaultForkName("web", LSID), "web-fork");
});

test("an untouched prefill is sent as an EMPTY name — the kernel's default is the kernel's to pick", () => {
  assert.equal(nameToSend("web-comment-2", "web-comment-2"), "");
  assert.equal(nameToSend("  web-comment-2 ", "web-comment-2"), "", "whitespace around the prefill is still untouched");
  assert.equal(nameToSend("", "web-comment-2"), "", "an emptied box asks for the default as well");
  assert.equal(nameToSend("why-jitter", "web-comment-2"), "why-jitter", "a typed name is the user's choice");
});

test("the comment dialog prefills through the module and sends nameToSend; the old inline stem is gone", () => {
  const pop = RENDER.slice(RENDER.indexOf("function renderCommentPopover("));
  const popBody = pop.slice(0, pop.indexOf("\nfunction ", 10));
  assert.match(popBody, /defaultCommentName\(sess0\?\.name, sid, \(commentThreads\.get\(sid\) \|\| \[\]\)\.length\)/);
  assert.match(popBody, /nameBox\.dataset\.prefill = prefill/, "the dialog remembers what it prefilled");
  const send = RENDER.slice(RENDER.indexOf("function commentSendFromPop("));
  const sendBody = send.slice(0, send.indexOf("\nfunction ", 10));
  assert.match(sendBody, /nameToSend\(nameBox\?\.value \|\| "", nameBox\?\.dataset\.prefill \|\| ""\)/);
  assert.doesNotMatch(RENDER, /\.replace\(\/\[\^A-Za-z0-9\._-\]\/g, "-"\)\s*\+ "-comment-"/, "no inline stem left");
  // no dialog derives a kernel-side name from the DECORATED display name any more (review, 2026-09-09)
  assert.doesNotMatch(RENDER, /\(sess0?\?\.name \|\| "session"\)\.replace\(/, "no decorated-name stem anywhere");
  // the box says what it holds: a suggestion the kernel may renumber, not the thread's granted name
  assert.match(RENDER, /nameBox\.title = "Suggested name; type to choose your own"/);
});

test("the fork dialog prefills through the module: a remote session's fork never wears the viewer's host label", () => {
  // the same defect one dialog over (review, 2026-09-09): the fork name is REQUIRED non-empty, so a labelled
  // prefill was always sent and the second fork of a remote session collided with the first
  const fk = RENDER.slice(RENDER.indexOf("function showForkPrompt("));
  const fkBody = fk.slice(0, fk.indexOf("\nfunction ", 10));
  assert.match(fkBody, /const base = defaultForkName\(sess\?\.name, sid\);/);
  assert.doesNotMatch(fkBody, /\.replace\(\/\[\^A-Za-z0-9\._-\]\/g, "-"\)/);
});

test("a typed name's draft dies with the ack, popover open or closed", () => {
  // drafts are keyed by the anchor message; a typed name that outlived a closed popover resurfaced as the
  // first thread's own (taken) name on the next comment on that message (review, 2026-09-09)
  assert.match(RENDER, /if \(m\.uuid\) commentDrafts\.delete\("new:" \+ String\(m\.uuid\)\);\s*\n\s*if \(m\.uuid\) commentDrafts\.delete\("newname:" \+ String\(m\.uuid\)\);/);
});

test("the break-out dialog prefills through the module", () => {
  const br = RENDER.slice(RENDER.indexOf("function showBreakoutPrompt("));
  const brBody = br.slice(0, br.indexOf("\nfunction ", 10));
  assert.match(brBody, /defaultBreakoutName\(sess\?\.name, sid\)/);
  assert.doesNotMatch(brBody, /\+ "-thread"\)/, "no inline stem left");
});
