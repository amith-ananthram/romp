// EDITING a queued message IN PLACE (T306, the user 2026-09-10): a message that has not reached the session yet — held
// in the SDK backend's queue (idx), parked in romp's FIFO (park), or still at the optimistic "sending…" stage — is the
// user's to change until it goes. The ✎ turns the bubble's text into a field where it sits (same width, the dashed
// provisional look kept) with Save and Cancel; the composer is never touched (the 2026-09-08 design pulled the text
// into the composer). While the field is open the entry is HELD: the open posts holdQueued and the kernel's drains skip
// it until Save (the editQueued releases it), Cancel (holdQueued hold:false) or the page's socket closing. No jsdom
// harness → source pins, the repo's convention (queued-indicator.test.ts is the ✕'s twin of this file); the kernel and
// backend halves EXECUTE in tests/test_queued_edit_hold.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const SDKBE = fs.readFileSync(path.join(ROOT, "kernel", "sdk_backend.py"), "utf8");

function slice(from: string, to: string, src = RENDER): string {
  const a = src.indexOf(from);
  assert.ok(a >= 0, "anchor present: " + from.slice(0, 60));
  const b = src.indexOf(to, a);
  assert.ok(b > a, "end anchor present after it: " + to.slice(0, 60));
  return src.slice(a, b);
}
// the editor's code: its state, open / cancel / save, and the field's render
const EDITOR = slice("// ---- editing a QUEUED message IN PLACE (T306", "// The optimistic half of an edit:");
// the delegated handlers: the ✎, Save, Cancel
const DELEGATES = slice("    qedit: (el) => {", "    cmtopen: (elx) => {");
// the kernel's verdict handling
const VERDICT = slice('else if (m.type === "editResult" && typeof m.id === "string") {', "// The identity palette changed");
// the queued bubble's render
const BUBBLE = slice("function renderQueued(", "\nfunction restoreToComposer(");

test("the ✎ opens a field IN the bubble: the editor is keyed by the entry, the hold goes out with the open, nothing touches the composer", () => {
  assert.match(EDITOR, /type QueuedEditRef = \{ md: string; idx\?: number; park\?: number; qts\?: number; qid\?: string; optimistic\?: boolean \};/);
  assert.match(EDITOR, /type QueuedEditor = \{ eid: number; sid: string; key: string; ref: QueuedEditRef; text: string; sel: \[number, number\] \| null;\s*\n\s*focused: boolean; open: boolean; note: string; width: number; height: number \};/);
  assert.match(EDITOR, /const queuedEditors = new Map<string, QueuedEditor>\(\);/);
  assert.match(EDITOR, /const queuedEditorKey = \(sid: string, t: \{ md: string; qid\?: string; qts\?: number \}\): string =>\s*\n\s*sid \+ "\\u0001" \+ t\.md \+ "\\u0001" \+ \(t\.qid \? "id:" \+ t\.qid : "ts:" \+ \(t\.qts === undefined \? "" : String\(t\.qts\)\)\);/,
    "keyed by the copy's identity: its id, else the press stamp of an id-less optimistic copy; never the stamp beside an id (a kernel copy's stamp is its enqueue time, which the ✎ does not carry: review find)");
  assert.match(EDITOR, /function openQueuedEditor\(sid: string, ref: QueuedEditRef, width = 0\): void \{[\s\S]*?queuedEditors\.set\(key, \{ eid: \+\+queuedEditorSeq, sid, key, ref, text: ref\.md, sel: \[ref\.md\.length, ref\.md\.length\],\s*\n\s*focused: true, open: true, note: "", width: Math\.round\(width\), height: 0 \}\);\s*\n\s*if \(!isProvisionalId\(sid\) && !hostIsDown\(sid\)\) vscodeApi\?\.postMessage\(holdQueuedMsg\(sid, ref, true\)\);\s*\n\s*repaintQueuedFor\(sid\);/,
    "the open records the state (the words, the caret at the end, focus, the bubble's width) and posts the hold; the repaint is the acknowledgement");
  assert.match(EDITOR, /if \(ed\.width > 0\) bubble\.style\.width = ed\.width \+ "px";/, "the field keeps the bubble's width as it stood");
  assert.match(DELEGATES, /openQueuedEditor\(sidQ, ref, bub \? bub\.getBoundingClientRect\(\)\.width : 0\);/, "measured at the click");
  assert.match(CSS, /\.queued-bubble\.editing \{ --prov-ink: var\(--fg\); border-color: var\(--accent\); padding-right: 52px; box-sizing: border-box; \}/,
    "the idle bubble's padding stays, so the words do not re-wrap under the caret when the field opens (review find); full ink while editing (the ink lift, T337)");
  assert.match(KERNEL, /def _relocate_parked\(sid, ops, md, skip=-1, prefer_held=True\):/, "a drifted slot relocates to the twin the editor holds, and refuses when nobody can tell (review find)");
  assert.match(KERNEL, /_park_holds\.pop\(sid, None\)\s+# …and the editors' holds: an obj: key must not outlive its op/);
  assert.match(EDITOR, /function holdQueuedMsg\(sid: string, ref: QueuedEditRef, hold: boolean\): Record<string, unknown> \{\s*\n\s*const m: Record<string, unknown> = \{ type: "holdQueued", id: sid, md: ref\.md, hold \};\s*\n\s*if \(ref\.idx !== undefined\) m\.idx = ref\.idx;\s*\n\s*if \(ref\.park !== undefined\) m\.park = ref\.park;\s*\n\s*if \(ref\.qid\) m\.qid = ref\.qid;/,
    "the hold names the entry the way the ✎ and the ✕ do: body, slot, and the copy's id");
  assert.match(DELEGATES, /qedit: \(el\) => \{[\s\S]*?const sidQ = owningSidOf\(el\) \|\| activeId;[\s\S]*?if \(el\.dataset\.qid\) ref\.qid = el\.dataset\.qid;[\s\S]*?openQueuedEditor\(sidQ, ref, bub \? bub\.getBoundingClientRect\(\)\.width : 0\);/,
    "the ✎ opens the editor for the bubble's own session with the bubble's width");
  assert.match(DELEGATES, /if \(sidQ !== activeId\) \{ warnToast\("open that session's chat to edit its queued message"\); return; \}/,
    "a bubble owned by another session is declined with a pointer: the active chat's render paints the field, so nothing is held silently");
});

test("the field: the bubble keeps its width and dotted look, Save and Cancel are delegated, Enter saves, Shift+Enter breaks a line, Escape cancels, caret and focus survive the rebuild", () => {
  assert.match(EDITOR, /function renderQueuedEditor\(bubble: HTMLElement, ed: QueuedEditor\): void \{\s*\n\s*bubble\.classList\.add\("editing"\);/);
  assert.match(EDITOR, /field\.className = "queued-editbox";\s*\n\s*field\.value = ed\.text;/);
  assert.match(EDITOR, /field\.addEventListener\("input", \(\) => \{ ed\.text = field\.value; remember\(\); grow\(\); \}\);/, "every keystroke lands in the state the next rebuild paints from");
  assert.match(EDITOR, /if \(e\.key === "Enter" && !e\.shiftKey\) \{ e\.preventDefault\(\); saveQueuedEditor\(ed\); \}\s*\n\s*else if \(e\.key === "Escape"\) \{ e\.preventDefault\(\); cancelQueuedEditor\(ed\); \}/);
  assert.match(EDITOR, /cancel\.dataset\.act = "qcancel"; cancel\.dataset\.eid = String\(ed\.eid\);/, "delegated through the one body delegate, like the ✕ and the ✎");
  assert.match(EDITOR, /save\.dataset\.act = "qsave"; save\.dataset\.eid = String\(ed\.eid\);/);
  assert.match(EDITOR, /const wasFocused = !!prev && prev\.classList\.contains\("queued-editbox"\) && \(prev as any\)\._eid === ed\.eid;/,
    "focus is read off the DOM before the rebuild swaps the field: the old field's blur fires before it reads as disconnected");
  assert.match(EDITOR, /if \(\(wasFocused \|\| ed\.focused\) && document\.body\.contains\(field\)\) \{ ed\.focused = true; field\.focus\(\{ preventScroll: true \}\); if \(ed\.sel\) field\.setSelectionRange\(ed\.sel\[0\], ed\.sel\[1\]\); \}/,
    "the tail rebuilds on every push: the field comes back with its caret and focus, without moving the reader's scroll");
  assert.doesNotMatch(EDITOR, /addEventListener\("blur"/, "no blur listener: the rebuild's own blur cannot be told from the user's (seen in the served-page harness)");
  assert.match(EDITOR, /document\.addEventListener\("pointerdown", \(e\) => \{\s*\n\s*for \(const ed of queuedEditors\.values\(\)\) if \(ed\.open && ed\.focused && !insideBox\(e\.target, ed\)\) ed\.focused = false;/,
    "the user leaving the field is a press outside it…");
  assert.match(EDITOR, /document\.addEventListener\("focusin", \(e\) => \{\s*\n\s*for \(const ed of queuedEditors\.values\(\)\) if \(ed\.open && ed\.focused && !isField\(e\.target, ed\) && !insideBox\(e\.target, ed\)\) ed\.focused = false;/,
    "…or focus landing elsewhere; nothing else clears the flag");
  assert.match(EDITOR, /function openQueuedEditor\(sid: string, ref: QueuedEditRef, width = 0\): void \{\s*\n\s*installQueuedEditorListeners\(\);/, "installed once, on the first open (not at module level: other tests execute this slice under node)");
  assert.match(EDITOR, /field\.rows = Math\.max\(1, ed\.text\.split\("\\n"\)\.length\);[^\n]*\n\s*if \(ed\.height > 0\) field\.style\.height = ed\.height \+ "px";/,
    "sized on the spot from the state: no one-row flash and a grow a frame later on every push");
  assert.match(RENDER, /for \(const ed of queuedEditors\.values\(\)\) if \(ed\.open && ed\.focused\) return null;/,
    "type-from-anywhere stands down while a field is open: a keystroke in the rebuild gap does not land in the composer");
  assert.match(DELEGATES, /qsave: \(el\) => \{ const ed = queuedEditorByEid\(Number\(el\.dataset\.eid\)\); if \(ed\) saveQueuedEditor\(ed\); \},/);
  assert.match(DELEGATES, /qcancel: \(el\) => \{ const ed = queuedEditorByEid\(Number\(el\.dataset\.eid\)\); if \(ed\) cancelQueuedEditor\(ed\); \},/);
  // the render: an open editor paints its field instead of the words; the ✎ steps aside while it is open
  assert.match(BUBBLE, /const qsid = renderingSid \|\| activeId \|\| "";\s*\n\s*const qed = qsid && !t\.romp && !isCmd \? queuedEditorFor\(qsid, t\) : undefined;\s*\n\s*if \(qed && qed\.open\) \{ bubble\.innerHTML = ""; renderQueuedEditor\(bubble, qed\); \}/);
  assert.match(BUBBLE, /if \(t\.cancelable && !t\.romp && !isCmd && \(t\.idx !== undefined \|\| t\.park !== undefined \|\| t\.optimistic\) && !\(qed && qed\.open\)\) \{/);
  assert.match(BUBBLE, /if \(t\.qid\) ed\.dataset\.qid = t\.qid;/, "the ✎ names the copy by its id, as the ✕ does");
  // the dress: same bubble, accent ring while editing, room for the ✕ only; the field inherits the bubble's type
  assert.match(CSS, /\.queued-editbox \{\s*\n\s*width: 100%; box-sizing: border-box; display: block; resize: none; overflow: hidden;\s*\n\s*font: inherit; line-height: inherit; color: var\(--fg\); background: transparent; border: 0; padding: 0; margin: 0; outline: none;/);
  assert.match(CSS, /\.queued-editbtn \{\s*\n\s*font: inherit; font-size: 0\.82em;/, "the queued header's own size, not a new one");
  assert.match(CSS, /\.queued-editbtn\.save \{ background: var\(--accent\); color: var\(--accent-fg\); border-color: transparent; \}/);
  assert.match(CSS, /\.queued-held-label \{ font-size: 0\.82em; color: var\(--dim\);/);
  assert.match(CSS, /\.queued-editnote \{ font-size: 0\.82em; color: var\(--vscode-errorForeground, #f48771\);/);
});

test("Save posts editQueued with the old body and the new words, repaints at once, and drops the editor — the kernel's edit is the hold's release", () => {
  const SAVE = slice("function saveQueuedEditor(ed: QueuedEditor): void {", "function renderQueuedEditor(", EDITOR);
  assert.match(SAVE, /const qmsg: Record<string, unknown> = \{ type: "editQueued", id: ed\.sid, md: ed\.ref\.md, text: typed \};\s*\n\s*if \(ed\.ref\.idx !== undefined\) qmsg\.idx = ed\.ref\.idx;\s*\n\s*if \(ed\.ref\.park !== undefined\) qmsg\.park = ed\.ref\.park;\s*\n\s*if \(ed\.ref\.qid\) qmsg\.qid = ed\.ref\.qid;\s*\n\s*vscodeApi\?\.postMessage\(qmsg\);/);
  assert.match(SAVE, /pendingEditRestores\.set\(ed\.sid \+ " " \+ ed\.ref\.md, \{ typed, ref: ed\.ref \}\);\s*\n\s*queuedEditors\.delete\(ed\.key\);\s*\n\s*applyQueuedEditLocally\(ed\.sid, ed\.ref, typed\);/,
    "the bubble shows the new words at once (acknowledge the click); no separate release is posted");
  // the three refusals leave the field as it is and precede the post
  const post = SAVE.indexOf("vscodeApi?.postMessage(qmsg);");
  for (const guard of [
    'if (!typed) { ephemeralWarnToast("Nothing to send — to drop the message, use its ✕."); return; }',
    'if (SLASH_CMD_RE.test(typed)) { warnToast("A queued message cannot become a command. Cancel it with its ✕ and type the command."); return; }',
    'ephemeralWarnToast("Can\'t reach the session right now, so the edit wasn\'t saved. It\'s still in the message: save again when the link is back.");',
  ]) {
    const i = SAVE.indexOf(guard);
    assert.ok(i >= 0 && i < post, "refused before anything is posted: " + guard.slice(0, 40));
  }
  assert.match(SAVE, /if \(hostIsDown\(ed\.sid\)\) vscodeApi\?\.postMessage\(\{ type: "redial", host: String\(ed\.sid\)\.slice\(0, String\(ed\.sid\)\.indexOf\(":"\)\) \}\);/, "deliver()'s guard: a down host is re-dialled, the edit kept");
  assert.doesNotMatch(SAVE, /registerOptimistic/, "an edit is not a new send");
  // Cancel: the field goes, the bubble is as it was, the hold is released
  assert.match(EDITOR, /function cancelQueuedEditor\(ed: QueuedEditor\): void \{\s*\n\s*queuedEditors\.delete\(ed\.key\);\s*\n\s*if \(!isProvisionalId\(ed\.sid\) && !hostIsDown\(ed\.sid\)\) vscodeApi\?\.postMessage\(holdQueuedMsg\(ed\.sid, ed\.ref, false\)\);\s*\n\s*repaintQueuedFor\(ed\.sid\);/);
});

test("the hold: every other client reads 'editing' on a held copy; the kernel skips a held entry in both queues and releases on Save, Cancel and the socket closing", () => {
  assert.match(RENDER, /qts\?: number; qid\?: string; held\?: boolean; hiddenByPending\?: boolean;/, "the queued event carries the mark");
  assert.match(BUBBLE, /else if \(t\.held && !t\.romp && !isCmd\) \{\s*\n\s*bubble\.classList\.add\("held"\);\s*\n\s*const h = el\("span", "queued-held-label"\); h\.textContent = "editing";/);
  // the kernel: the op, the marks, the skips, the release on disconnect (executed in tests/test_queued_edit_hold.py)
  assert.match(KERNEL, /elif t == "holdQueued":/);
  assert.match(KERNEL, /"cancelQueued", "dismissEcho", "apiRetry", "editQueued", "holdQueued", "setModel"/, "routed to the owning kernel across linked machines");
  assert.match(KERNEL, /def _hold_parked\(sid, park, md, owner, qid=None, hold=True\):/);
  assert.match(KERNEL, /def _hold_backend_queued\(be, sid, idx, md, owner, qid=None, hold=True\):/);
  assert.match(KERNEL, /def _parked_held\(sid, op\):/);
  assert.match(KERNEL, /k = next\(\(j for j, o in enumerate\(ops\) if not _parked_held\(sid, o\)\), -1\)/, "the parked walk takes the first unheld op");
  assert.match(KERNEL, /def _inflight_slot\(sid, ops\):/, "the in-flight guard finds the op the backend holds wherever a held send left it");
  assert.match(KERNEL, /def _release_client_holds\(client\):/);
  assert.match(KERNEL, /return _op_qid\(op\) or \("obj:%x" % id\(op\)\)/, "an id-less parked send is keyed by its own object: two same-words sends hold apart");
  assert.match(KERNEL, /if ops\[k\]\[0\] != "send" and any\(_parked_held\(sid, o\) for o in ops\[:k\]\):/, "only messages pass a message being edited; a command, compaction or setting behind it waits");
  assert.match(KERNEL, /def _queued_held_by_other\(be, sid, idx, md, owner\):/, "a Save on a copy another connection is editing is refused");
  assert.match(KERNEL, /def _release_after_refusal\(be, sid, msg, client\):/, "a refused Save releases the saver's own hold, which its field no longer covers");
  assert.match(KERNEL, /the session isn't running right now, so this message can't be edited yet/, "a copy the persisted mirror lists but no session holds is not 'too late'");
  assert.match(SDKBE, /"holder": \(self\._pending_hold\[i\] if i < len\(self\._pending_hold\) else None\) or None/, "the chat's meta says whose hold it is");
  assert.match(SDKBE, /if cur and cur != owner:\s*\n\s*return False/, "the first editor keeps the copy");
  assert.match(KERNEL, /_clients\.remove\(client\)\s*\n\s*_release_client_holds\(client\)/, "the socket's finally is the release event; no timer");
  assert.match(KERNEL, /"cid": uuid\.uuid4\(\)\.hex\[:12\],/, "every connection owns its holds");
  assert.equal(KERNEL.split('m["held"] = True').length - 1, 2, "both queue builders mark a held entry for the chat");
  assert.equal(KERNEL.split('"op": "hold" if want else "release"').length - 1, 1, "one authoritative frame, editResult, per hold or release");
  // the backend: the feed pops the first unheld copy; Save clears the mark; a disconnect clears the owner's
  assert.match(SDKBE, /def _feed_index_locked\(self\) -> int:/);
  assert.match(SDKBE, /fi = self\._feed_index_locked\(\) if \(self\._pending and not blocked\) else -1\s*\n\s*item, _meta = self\._pop_for_feed_locked\(fi\) if fi >= 0 else \(None, None\)/);
  assert.match(SDKBE, /def hold_queued\(self, idx: int, expect, owner: str, qid: str \| None = None\) -> bool:/);
  assert.match(SDKBE, /def release_holds_by\(self, owner: str\) -> int:/);
  assert.match(SDKBE, /self\._pending_hold\[idx\] = None\s+# the Save is the hold's release \(T306\)/);
  assert.match(SDKBE, /"held": bool\(self\._pending_hold\[i\] if i < len\(self\._pending_hold\) else None\)/, "the chat reads the mark beside each copy");
});

test("the verdict: a refused Save reverses the repaint and hands the words back in a toast; a refused hold closes the field and the bubble says so", () => {
  assert.match(VERDICT, /const isSave = m\.op !== "hold" && m\.op !== "release";/, "a hold's or a release's acknowledgement never consumes a Save's restore stash (review find)");
  assert.match(VERDICT, /if \(m\.op === "hold"\) \{[\s\S]*?if \(ed\.sid !== m\.id \|\| ed\.ref\.md !== md \|\| !ed\.open\) continue;[\s\S]*?if \(typeof m\.qid === "string" && m\.qid && ed\.ref\.qid && ed\.ref\.qid !== m\.qid\) continue;[\s\S]*?ed\.open = false; ed\.note = why \|\| "too late to edit — the message already reached the session as it was";/);
  assert.match(VERDICT, /if \(edited\) stickyToast\(\(why \|\| "The message could not be held for editing\."\) \+ " Your edit: " \+ edited, edited\);[^\n]*\n\s*else if \(why\) warnToast\(why\);/,
    "a refused hold hands back the words typed so far, in a toast that never fades");
  assert.match(VERDICT, /\} else if \(m\.op !== "release"\) \{\s*\n\s*if \(stash\) applyQueuedEditLocally\(m\.id, stash\.ref, stash\.typed, true\);\s*\n\s*if \(stash && m\.gone\) \{[\s\S]*?stickyToast\(\(why \|\| "The edit was not applied\."\) \+ " Your edit: " \+ stash\.typed, stash\.typed\);/,
    "a copy that left the queue has no bubble: the words go to a toast that never fades, with a Copy button (review find: warnToast fades at 11 s)");
  assert.match(VERDICT, /\} else if \(stash\) \{[\s\S]*?queuedEditors\.set\(key, \{ eid: \+\+queuedEditorSeq, sid: m\.id, key, ref: stash\.ref, text: stash\.typed, sel: \[stash\.typed\.length, stash\.typed\.length\],\s*\n\s*focused: true, open: true, note: why \|\| "The edit was not applied\.", width: 0, height: 0 \}\);\s*\n\s*if \(!isProvisionalId\(m\.id\) && !hostIsDown\(m\.id\)\) vscodeApi\?\.postMessage\(holdQueuedMsg\(m\.id, stash\.ref, true\)\);/,
    "a copy still queued: the field reopens with the typed words and the refusal beside them, re-held");
  assert.match(EDITOR, /if \(ed\.note\) \{ const n = el\("div", "queued-editnote"\); n\.textContent = ed\.note; box\.appendChild\(n\); \}/, "the open field shows the refusal above its buttons");
  assert.match(VERDICT, /if \(edited\) stickyToast\(\(why \|\| "The message could not be held for editing\."\) \+ " Your edit: " \+ edited, edited\);/, "a refused hold's typed words never fade either");
  assert.match(RENDER, /^function stickyToast\(msg: string, copyText: string\): HTMLElement \{/m);
  assert.doesNotMatch(slice("function stickyToast(", "\n}\n", RENDER), /setTimeout/, "no timers on the sticky toast");
  assert.match(slice("function stickyToast(", "\n}\n", RENDER), /navigator\.clipboard\?\.writeText\(copyText\)/);
  assert.match(CSS, /\.warn-toast\.sticky \{ border-color: var\(--accent\); \}/);
  assert.match(KERNEL, /if err and err == _edit_miss_text\(md\):\s*\n\s*frame\["gone"\] = True/, "the kernel says when the copy is gone, and only then");
  assert.match(VERDICT, /repaintQueuedFor\(m\.id\);/);
  assert.match(BUBBLE, /else if \(qed && qed\.note && t\.held\) \{ const n = el\("div", "queued-editnote"\); n\.textContent = qed\.note; bubble\.appendChild\(n\); \}\s*\n\s*else if \(qed && qed\.note\) queuedEditors\.delete\(qed\.key\);/,
    "the bubble says so under its words for as long as the hold it speaks of stands, and stops when the other client lets go (review find)");
});

test("the hold survives the page's socket and a tab close: re-hold on socket-up, explicit release on close, the pending group's cache reads the editor, the ✕ hands back the edited words", () => {
  assert.match(EDITOR, /function reholdQueuedEditors\(remoteOnly = false\): void \{[\s\S]*?vscodeApi\?\.postMessage\(holdQueuedMsg\(ed\.sid, ed\.ref, true\)\);/,
    "the kernel released the old socket's holds with it; every open field re-holds");
  assert.match(RENDER, /else if \(m\.type === "wsup"\) \{ onSocketUp\(skeletonTabs\); skeletonDiagArmed = true; reholdQueuedEditors\(\); \}/, "…on the shim's socket-flip frame");
  assert.match(RENDER, /if \(h\) reshipPendingUploads\(\[h\]\);\s*\n\s*if \(h\) reholdQueuedEditors\(true\);/, "…and on a relay coming back, for the remote sessions (inside the existing handler: a module-level listener would break the slices other tests execute under node)");
  assert.match(EDITOR, /function closeQueuedEditorsFor\(sid: string\): void \{[\s\S]*?if \(ed\.open && !isProvisionalId\(sid\) && !hostIsDown\(sid\)\) vscodeApi\?\.postMessage\(holdQueuedMsg\(sid, ed\.ref, false\)\);/,
    "a closed tab leaves the session, its queue and this socket alive, so it releases what it drops (review find)");
  assert.match(RENDER, /\+ "\|" \+ JSON\.stringify\(ev\.texts\.map\(\(t\) => \{ const e = queuedEditorFor\(sid, t\); return e \? \[e\.eid, e\.open, e\.text, e\.note\] : null; \}\)\);/,
    "our own pending group's cached node is rebuilt when an editor opens, types or closes on one of its copies (review find: the ✎ on the user's own send painted nothing)");
  assert.match(RENDER, /restoreToComposer\(edx && edx\.open && edx\.text\.trim\(\) \? edx\.text : qmd\);/,
    "the ✕ on a bubble whose field is open hands back the words being edited, and the editor goes with the entry");
});

test("an edit of a queued message never writes the composer or its chips, and the composer-based edit is gone", () => {
  const COMPOSER = /composer-input|composer-chips|composer-chip|restoreToComposer|renderComposerChips|drafts\.set|drafts\.delete|persistDrafts|growComposer|composerCitations|composerManualH/;
  for (const [name, src] of [["the editor", EDITOR], ["the delegates", DELEGATES], ["the verdict", VERDICT]] as const) {
    assert.doesNotMatch(src, COMPOSER, name + " touches no composer state");
  }
  // the bubble's render: only the ✕'s own restore names the composer (a cancel returns the words to it, unchanged rule)
  assert.equal((BUBBLE.match(COMPOSER) || []).length, 0, "the queued bubble's render touches no composer state");
  for (const gone of ["beginQueuedEdit", "cancelQueuedEdit(", "queuedEditHeld", "restoreHeldDraft", "Editing queued message — send replaces it in the queue",
                      "const qedit = queuedEdits.get(activeId)", "This edit replaces a queued message. Send it normally."]) {
    assert.ok(!RENDER.includes(gone), "the composer-based edit is gone: " + gone);
  }
  assert.doesNotMatch(RENDER, /\bqueuedEdits\b/, "no composer-held queued edit state remains");
  const CHIPS = slice("function renderComposerChips(", "\nfunction ", RENDER);
  assert.doesNotMatch(CHIPS, /queued/i, "the composer's chip strip knows nothing of queued messages: the stray context chip cannot recur");
  assert.match(RENDER, /closeQueuedEditorsFor\(id\);\s+\/\/ its in-place queued editors go with the tab/, "a closed tab drops its editors; the kernel releases the holds with the socket");
  assert.match(RENDER, /composerEdits and the in-place queued editors are in memory alone/, "the reload note names the new state");
});
