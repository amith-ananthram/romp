// The notices a page is showing when the reload core takes it (reload-notices.ts). The core's restart reload follows
// the last pending ship's retirement on the next task (render.ts endReloadHoldIfIdle, __rompReload.ended()), and the
// nack, the dismissal or the other-tab ack raised in that same task is appended one task before the page goes: the
// toast was never read, and the fresh page's loss toast reads shipsInFlight, which the retirement already emptied. So
// render.ts keeps the
// texts of the toasts on screen in this tab's sessionStorage on the core's synchronous hook and the fresh page shows
// them once. The readings are pure and execute here; render.ts has import-time DOM side effects, so its wiring is
// pinned to source the way reload-restore.test.ts pins the scroll record's. The served scenario (a nack on the last
// ship across a kernel restart; the fresh page says it again, once) is tests/test_ship_reship.py
// NackNoticeSurvivesReload. Synthetic only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { RELOAD_NOTICES_KEY, liveNotices, keepReloadNotices, takeReloadNotices } from "./reload-notices";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const SEL = ".warn-toast:not([data-ephemeral]) .warn-toast-msg";
const box = (texts: (string | null)[]) => ({
  querySelectorAll: (sel: string) => { assert.equal(sel, SEL); return texts.map((t) => ({ textContent: t })); },
});

/** A sessionStorage stand-in: the three calls the module makes over a Map, with a log of them. */
function fakeStore(init: Record<string, string> = {}) {
  const m = new Map(Object.entries(init));
  const calls: string[] = [];
  return {
    m, calls,
    getItem: (k: string) => { calls.push("get " + k); return m.has(k) ? m.get(k)! : null; },
    setItem: (k: string, v: string) => { calls.push("set " + k); m.set(k, v); },
    removeItem: (k: string) => { calls.push("remove " + k); m.delete(k); },
  };
}

test("the toasts on screen read as their texts, in order, blanks dropped; none without the container", () => {
  assert.deepEqual(liveNotices(null), [], "the container is created by the first toast; none yet");
  assert.deepEqual(liveNotices(undefined), []);
  assert.deepEqual(liveNotices(box([])), []);
  assert.deepEqual(liveNotices(box(["shot.png couldn't be saved on the kernel, so it was not attached. Your message was NOT sent.",
                                    "  ", null, "The pending upload was dismissed. Your held message was NOT sent."])),
    ["shot.png couldn't be saved on the kernel, so it was not attached. Your message was NOT sent.",
     "The pending upload was dismissed. Your held message was NOT sent."]);
  assert.deepEqual(liveNotices(box(["  padded  "])), ["padded"], "the text as the toast shows it");
});

test("the reading asks for the toasts without the ephemeral mark: a refusal about a state the fresh page shows for itself stays behind", () => {
  // the staged sends' "Can't send yet" reports a state (the host unreachable, the tab still being created), which the
  // fresh page shows for itself; render.ts marks that toast where it is raised and the selector skips the mark. The
  // mark's effect on a real DOM is executed by tests/test_ship_reship.py NackNoticeSurvivesReload.
  const asked: string[] = [];
  assert.deepEqual(liveNotices({ querySelectorAll: (sel: string) => { asked.push(sel); return [{ textContent: "kept" }]; } }), ["kept"]);
  assert.deepEqual(asked, [SEL]);
});

test("the record is kept only when there is something to say; a page with no toast clears a record left behind", () => {
  const s = fakeStore();
  keepReloadNotices(s, ["one", "two"]);
  assert.deepEqual([...s.m.entries()], [[RELOAD_NOTICES_KEY, JSON.stringify(["one", "two"])]]);
  assert.equal(RELOAD_NOTICES_KEY, "romp:reloadNotices", "this tab's sessionStorage, beside the scroll record's key");
  // a reload the browser refused leaves the record in place; the next core reload with nothing on screen clears it
  keepReloadNotices(s, []);
  assert.equal(s.m.has(RELOAD_NOTICES_KEY), false, "cleared, not written empty");
  assert.deepEqual(s.calls, ["set " + RELOAD_NOTICES_KEY, "remove " + RELOAD_NOTICES_KEY]);
  keepReloadNotices(null, ["x"]);
  keepReloadNotices(undefined, []);
});

test("the record comes out once: strings only, and the key is gone whatever it held", () => {
  const s = fakeStore({ [RELOAD_NOTICES_KEY]: JSON.stringify(["one", "", 3, null, "two"]), other: "kept" });
  assert.deepEqual(takeReloadNotices(s), ["one", "two"]);
  assert.deepEqual([...s.m.keys()], ["other"], "the key is removed; nothing else is touched");
  assert.deepEqual(takeReloadNotices(s), [], "a second take finds nothing");
  assert.deepEqual(s.calls, ["get " + RELOAD_NOTICES_KEY, "remove " + RELOAD_NOTICES_KEY, "get " + RELOAD_NOTICES_KEY],
    "no record, no removal");
  for (const junk of ["not json", JSON.stringify("a string"), JSON.stringify({ a: 1 }), JSON.stringify(null), ""]) {
    const j = fakeStore({ [RELOAD_NOTICES_KEY]: junk });
    assert.deepEqual(takeReloadNotices(j), [], JSON.stringify(junk) + " reads as none");
    assert.equal(j.m.has(RELOAD_NOTICES_KEY), false, JSON.stringify(junk) + " is still cleared");
  }
  assert.deepEqual(takeReloadNotices(null), []);
  assert.deepEqual(takeReloadNotices(undefined), []);
});

test("kept on the page that reloads, taken on the page that follows: the same texts, then nothing", () => {
  const s = fakeStore();
  const texts = liveNotices(box(["shot.png couldn't be saved on the kernel, so it was not attached. Your message was NOT sent."]));
  keepReloadNotices(s, texts);
  assert.deepEqual(takeReloadNotices(s), texts);
  assert.deepEqual(takeReloadNotices(s), []);
});

test("a store that refuses is left alone: nothing thrown from either side", () => {
  const broken = {
    getItem: () => { throw new Error("SecurityError"); },
    setItem: () => { throw new Error("QuotaExceededError"); },
    removeItem: () => { throw new Error("SecurityError"); },
  };
  assert.doesNotThrow(() => keepReloadNotices(broken, ["x"]));
  assert.doesNotThrow(() => keepReloadNotices(broken, []));
  assert.deepEqual(takeReloadNotices(broken), []);
});

// The wiring, pinned to source (render.ts executes nothing under node --test).
test("render.ts: warnToast hands back its toast, and the send refusal about reachability is marked ephemeral where it is raised", () => {
  assert.match(RENDER, /^function warnToast\(msg: string\): HTMLElement \{/m);
  assert.match(RENDER, /setTimeout\(\(\) => t\.remove\(\), 12000\);\n\s*return t;/);
  assert.match(RENDER, /^function ephemeralWarnToast\(msg: string\): void \{ warnToast\(msg\)\.dataset\.ephemeral = "1"; \}/m);
  assert.equal((RENDER.match(/dataset\.ephemeral/g) || []).length, 1, "marked in one place");
  assert.equal((RENDER.match(/ephemeralWarnToast\("Can't send yet — the session isn't reachable\. They stay staged\."\);/g) || []).length, 2,
    "the staged sends' refusal at both of its sites (the strip's Send now and the empty send)");
  assert.equal((RENDER.match(/ephemeralWarnToast\(/g) || []).length, 3, "the definition and the two sites");
  // what the nack, the dismissal and the other-tab ack say stays true after the reload, so they ride it unmarked
  assert.match(RENDER, /warnToast\(m\.name \+ " couldn't be saved on the kernel, so it was not attached/);
  assert.match(RENDER, /warnToast\("The pending upload was dismissed — your held message was NOT sent\."\)/);
  assert.match(RENDER, /warnToast\("attachments finished uploading on another tab — the held message was not sent; review it there\."\)/);
});

test("render.ts keeps the notices on the core's hook alone, pagehide keeps the scroll record alone, and the fresh page shows them once after the loss toast", () => {
  assert.match(RENDER, /^import \{ liveNotices, keepReloadNotices, takeReloadNotices \} from "\.\/reload-notices";/m);
  assert.match(RENDER, /^function persistNoticesForReload\(\): void \{\n\s*try \{ keepReloadNotices\(sessionStorage, liveNotices\(document\.getElementById\("warn-toasts"\)\)\); \} catch \{ \/\* ignore \*\/ \}\n\}/m);
  // the core's synchronous hook writes both records; a navigation of the user's own (pagehide) writes the scroll record
  // alone, so a load they asked for does not replay a toast they were already looking at
  assert.match(RENDER, /^function persistForReload\(\): void \{ persistScrollForReload\(\); persistNoticesForReload\(\); \}[^\n]*\n\(window as any\)\.__rompPersistForReload = persistForReload;\nwindow\.addEventListener\("pagehide", persistScrollForReload\);/m);
  assert.equal((RENDER.match(/persistNoticesForReload\(\)/g) || []).length, 2, "defined once, called from the core's hook alone");
  const scroll = RENDER.match(/^function persistScrollForReload\(\): void \{([\s\S]*?)\n\}/m);
  assert.ok(scroll && !scroll[1].includes("Notices"), "the scroll record is untouched");
  // the replay follows the loss toast's block directly: the loss first, then what the last page was saying
  assert.match(RENDER, /shipsInFlight: \[\] \}\);\n\s*\}\n\s*\}\n\} catch \{ \/\* ignore \*\/ \}\n(\/\/[^\n]*\n)*try \{ for \(const text of takeReloadNotices\(sessionStorage\)\) warnToast\(text\); \} catch \{ \/\* ignore \*\/ \}/);
  assert.equal((RENDER.match(/takeReloadNotices\(/g) || []).length, 1, "consumed once, at load");
  assert.equal((RENDER.match(/keepReloadNotices\(/g) || []).length, 1, "written from one place");
});
