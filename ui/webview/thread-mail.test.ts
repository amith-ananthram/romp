// A comment thread's mail is off until it is broken out (T356, the user 2026-09-11): the thread's whole surface is
// the comment popover, so the popover says so; the tab hover and the Sessions pane show a session's mail state, so
// a promoted thread's flip shows where a session is looked at; the kernel's frames carry the effective state.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const FLEET = ui("webview", "fleet.ts");
const COMMENTS = ui("webview", "comments.ts");
const CSS = ui("webview", "styles.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const POSTAL = fs.readFileSync(path.resolve(process.cwd(), "..", "postal", "postal_service.py"), "utf8");

test("the popover says the thread's mail is off, and the promoted view says it is on now", () => {
  assert.match(COMMENTS, /mailOff\?: boolean;/, "the frame's field on the thread type");
  // the user's ruling (2026-09-11, 3:05 PM PT): the comment box says nothing about mail being off; a line only for held mail
  assert.doesNotMatch(RENDER, /Mail off: this thread neither sends nor receives peer mail/);
  assert.match(RENDER, /if \(th && th\.mailOff && \(th\.heldMail \|\| 0\) > 0\) \{[\s\S]{0,700}?mail\.textContent = held \+ \(held === 1 \? " message waits in its box and lands" : " messages wait in its box and land"\) \+ " at the break-out\.";/);
  assert.match(RENDER, /note\.textContent = "The discussion continues there\.";\s*\n\s*pop\.appendChild\(note\);[\s\S]{0,600}mailOn\.textContent = th\.mailOff[\s\S]{0,200}: "Its mail is on now: peers can reach it and it can send\."/,
               "said once, in the promoted view, from the effective state");
  assert.match(CSS, /\.cmt-note\.cmt-mail \{ opacity: 0\.6; font-size: 0\.86em; \}/);
  // the follow-up: the promoted line reads the EFFECTIVE state (a mailbox toggled off since says so), and both lines
  // count the mail held in the box
  assert.match(COMMENTS, /heldMail\?: number;/);
  assert.match(RENDER, /mailOn\.textContent = th\.mailOff\s*\n\s*\? "Its mailbox is off: the lane's mailbox toggle turns peer mail back on\."/);
  assert.match(RENDER, /" in a moment\."/);
});

test("the tab hover and the Sessions pane show a session's mail state", () => {
  assert.match(RENDER, /rows\.push\(\["Mail", s\.postalServiceOff \? "off: this session neither sends nor receives peer mail" : "on"\]\);/);
  assert.match(FLEET, /postalServiceOff\?: boolean;/, "the Sessions pane row type carries it");
  assert.match(FLEET, /if \(s\.postalServiceOff\) \{[\s\S]*?const mo = el\("span", "fl-mail-off"\);\s*\n\s*mo\.textContent = "mail off";/);
  assert.match(CSS, /\.fl-mail-off \{/);
});

test("the kernel and the bus derive the same default from the thread's reg and the fresh key", () => {
  assert.match(KERNEL, /def _thread_mail_off\(sid\):[\s\S]*?if not sid or not _thread_reg\(sid\)\.get\("threadOf"\):\s*\n\s*return False\s*\n\s*f = _session_flags\(\)\.get\(sid\)\s*\n\s*return not \(isinstance\(f, dict\) and f\.get\("threadMail"\) is True\)/,
               "literal True only (the flip-a-default rule)");
  assert.match(KERNEL, /def _postal_isolated\(sid\):[\s\S]*?return _thread_mail_off\(sid\) or bool\(_session_flag\(sid, "postalServiceOff"\) or _session_flag\(sid, "postalOff"\)\)/);
  assert.match(KERNEL, /"mailOff": bool\(_postal_isolated\(tsid\)\),/, "the comments frame carries it");
  assert.match(KERNEL, /"postalServiceOff": _postal_isolated\(m\["id"\]\),/, "the Sessions pane rows carry it");
  assert.match(POSTAL, /t = _thread_of\(sid\)\s*\n\s*if t == THREAD_REG_UNREADABLE:\s*\n\s*return "unreadable"[^\n]*\n\s*if t and not \(isinstance\(f, dict\) and f\.get\("threadMail"\) is True\):\s*\n\s*return "thread"/,
               "an unreadable record is closed under its own reason, then the literal-True key");
  assert.match(POSTAL, /agents, listing_answered = local_agents_checked\(threads=True\)/, "the relay lists thread rows, so a thread recipient bounces instead of retrying forever");
  assert.match(POSTAL, /if why_off == "thread":[^\n]*\n\s*return self\._send\(\{"error": THREAD_MAIL_OFF_SENDER\}, 403\)/, "the thread's own send");
  assert.match(POSTAL, /whys = \{a\["id"\]: _mail_off_why\(a\["id"\]\) for a in direct_all\}[\s\S]{0,500}?if "unreadable" in whys\.values\(\):[\s\S]{0,500}?if "thread" in whys\.values\(\):/, "a send to the thread, and to a session whose record cannot be read, each with its own words");
});
