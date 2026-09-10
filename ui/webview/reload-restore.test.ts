// A page reload keeps the chat reader's place (T265, the user 2026-09-08: the dashboard reloads itself on a kernel
// restart and on a newer served bundle, superseding their 2026-07-13 preference for a banner the reader clicks, so
// the reload must not cost the reader their scroll position or follow mode). The decisions are pure and execute
// here; render.ts has import-time DOM side effects, so its wiring — the synchronous persist hook the reload core
// calls, the pagehide belt, the load-time take, landActive's one-shot consume — is pinned to source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { reloadScrollRecord, takeReloadScroll, reloadLandTarget } from "./reload-restore";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const SID = "11111111-2222-4333-8444-000000000201";

test("the record names the tab, the position, the follow mode and the anchor; no tab → nothing to keep", () => {
  assert.deepEqual(reloadScrollRecord(SID, 1234, false, { uuid: "u1", y: 40 }), { id: SID, top: 1234, stick: false, anchor: { uuid: "u1", y: 40 } });
  assert.deepEqual(reloadScrollRecord(SID, 5000, true, null), { id: SID, top: 5000, stick: true, anchor: null });
  assert.equal(reloadScrollRecord(null, 10, false, null), null);
  assert.equal(reloadScrollRecord("", 10, false, null), null);
});

test("the record applies to the tab it was saved for, once; another tab or a malformed record gets nothing", () => {
  const rec = reloadScrollRecord(SID, 1234, false, null);
  assert.equal(takeReloadScroll(rec, SID), rec);
  assert.equal(takeReloadScroll(rec, "11111111-2222-4333-8444-000000000202"), null, "a different tab lands by the ordinary rule");
  assert.equal(takeReloadScroll(rec, null), null);
  assert.equal(takeReloadScroll(null, SID), null);
  assert.equal(takeReloadScroll({ id: SID }, SID), null, "no position → no restore");
  assert.equal(takeReloadScroll("junk", SID), null);
});

test("where the restored land goes: bottom for follow mode, the anchor when honoured, else the raw top", () => {
  assert.equal(reloadLandTarget({ id: SID, top: 999, stick: true, anchor: { uuid: "u", y: 0 } }, true), "bottom");
  assert.equal(reloadLandTarget({ id: SID, top: 999, stick: false, anchor: { uuid: "u", y: 0 } }, true), "anchor");
  assert.equal(reloadLandTarget({ id: SID, top: 999, stick: false, anchor: { uuid: "u", y: 0 } }, false), 999, "the anchor turn is not in the rebuilt DOM: raw scrollTop");
  assert.equal(reloadLandTarget({ id: SID, top: 999, stick: false, anchor: null }, false), 999);
});

test("render.ts persists SYNCHRONOUSLY for the reload core and on pagehide, into THIS tab's sessionStorage", () => {
  assert.match(RENDER, /import \{ reloadScrollRecord, takeReloadScroll, type ReloadScroll \} from "\.\/reload-restore";/);
  // the core's hook writes the scroll record and then the notices on screen (reload-notices.test.ts pins that half);
  // pagehide writes the scroll record alone
  assert.match(RENDER, /^function persistForReload\(\): void \{ persistScrollForReload\(\); persistNoticesForReload\(\); \}/m);
  assert.match(RENDER, /\(window as any\)\.__rompPersistForReload = persistForReload;/);
  assert.match(RENDER, /window\.addEventListener\("pagehide", persistScrollForReload\);/);
  const m = RENDER.match(/^function persistScrollForReload\(\): void \{([\s\S]*?)\n\}/m);
  assert.ok(m, "persistScrollForReload");
  const body = m![1];
  assert.match(body, /const stick = content\.scrollHeight - content\.scrollTop - content\.clientHeight <= 2;/, "follow mode is the true bottom");
  assert.match(body, /reloadScrollRecord\(activeId, content\.scrollTop, stick, stick \? null : captureScrollAnchor\(content, v\)\)/);
  assert.match(body, /sessionStorage\.setItem\(RELOAD_SCROLL_KEY, JSON\.stringify\(rec\)\)/, "per tab: the persisted webview state is localStorage on the served page, shared by every dashboard tab");
  assert.match(RENDER, /const RELOAD_SCROLL_KEY = "romp:reloadScroll";/);
  // nothing to keep for a hidden pane or a tab never shown
  assert.match(body, /if \(!content \|\| !v \|\| !v\.shown \|\| content\.clientHeight <= 0\) return;/);
});

test("render.ts takes the record out of sessionStorage at load (one reload, one restore)", () => {
  assert.match(RENDER, /let pendingReloadScroll: ReloadScroll \| null = \(\(\) => \{[\s\S]*?const raw = sessionStorage\.getItem\(RELOAD_SCROLL_KEY\);\s*\n\s*if \(raw\) sessionStorage\.removeItem\(RELOAD_SCROLL_KEY\);/);
});

test("landActive's landing consumes the record for the active tab first, then falls to the ordinary rule", () => {
  const m = RENDER.match(/^function landActive\(content: HTMLElement \| null, v: View\): void \{([\s\S]*?)\n\}/m);
  assert.ok(m, "landActive");
  const body = m![1];
  assert.match(body, /const rs = takeReloadScroll\(pendingReloadScroll, activeId\);\s*\n\s*if \(rs\) \{\s*\n\s*pendingReloadScroll = null;\s*\n\s*v\.stick = rs\.stick;\s*\n\s*if \(rs\.stick\) writeScroll\(content, content\.scrollHeight, "reload-restore", true\);\s*\n\s*else if \(!\(rs\.anchor && restoreScrollAnchor\(content, v, rs\.anchor\)\)\) \{/);
  // the anchor turn outside the fresh window: the raw top is the first guess and the deep-link land finishes it
  assert.match(body, /writeScroll\(content, rs\.top, "reload-restore"\);\s*\n\s*if \(rs\.anchor\) \{ pendingAnchor = rs\.anchor\.uuid; pendingAnchorKeepY = rs\.anchor\.y; \}/);
  assert.match(body, /else if \(!v\.shown \|\| v\.stick\) writeScroll\(content, content\.scrollHeight, "land-bottom", true\);\s*\n\s*else writeScroll\(content, v\.scrollTop, "land-saved"\);/);
});
