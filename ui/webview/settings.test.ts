import { test } from "node:test";
import * as assert from "node:assert/strict";

// Minimal localStorage shim BEFORE importing the module (load/save read it at call time).
const store: Record<string, string> = {};
(globalThis as any).localStorage = {
  getItem: (k: string) => (k in store ? store[k] : null),
  setItem: (k: string, v: string) => { store[k] = v; },
  removeItem: (k: string) => { delete store[k]; },
};
import { loadSettings, saveSettings, DEFAULT_SETTINGS, fileLinkPane, paneSet, OPTIONAL_PANES } from "./settings";

test("loadSettings returns defaults when nothing is stored", () => {
  delete store["romp:settings"];
  assert.deepEqual(loadSettings(), DEFAULT_SETTINGS);
});

test("the Sub-goals card pref defaults ON; the old Explanations pref is gone (the user 2026-06-18)", () => {
  assert.equal(DEFAULT_SETTINGS.subgoals, true);
  assert.equal((DEFAULT_SETTINGS as any).explanations, undefined);
});

test("both judge-set toggles default OFF (the user 2026-06-29): the timeline's judging band stays hidden", () => {
  assert.equal(DEFAULT_SETTINGS.showIndexJudges, false);
  assert.equal(DEFAULT_SETTINGS.showTriageJudges, false);
});

test("Default backend defaults to sdk (the user 2026-07-13); Claude Code and Codex coexist", () => {
  assert.equal(DEFAULT_SETTINGS.backend, "sdk");
});

test("a saved default of the retired terminal backend reads as sdk, never an undefined value (T331)", () => {
  localStorage.setItem("romp:settings", JSON.stringify({ ...DEFAULT_SETTINGS, backend: "tmux" }));
  assert.equal(loadSettings().backend, "sdk");
  localStorage.setItem("romp:settings", JSON.stringify({ ...DEFAULT_SETTINGS, backend: "nonsense" }));
  assert.equal(loadSettings().backend, "sdk", "any value no longer offered reads as the default");
  localStorage.setItem("romp:settings", JSON.stringify({ ...DEFAULT_SETTINGS, backend: "codex" }));
  assert.equal(loadSettings().backend, "codex", "an offered pick stands");
  saveSettings({ backend: "sdk" });
});

test("Compact transcript defaults ON (the user 2026-07-14): fresh installs read the tidy transcript", () => {
  assert.equal(DEFAULT_SETTINGS.compact, true);
});

// The tab strip's one-group-per-row layout (T264) is the default and a per-device pick: an explicit false
// lets the groups follow one another across the strip and wrap as they need (render.ts renderTabs).
test("stripGroupRows defaults ON: every tag group on its own row; an explicit false round-trips, and a store from before the key reads as on", () => {
  assert.equal(DEFAULT_SETTINGS.stripGroupRows, true);
  store["romp:settings"] = JSON.stringify({ stripGroupRows: false });
  assert.equal(loadSettings().stripGroupRows, false, "the opt-out round-trips");
  store["romp:settings"] = JSON.stringify({});
  assert.equal(loadSettings().stripGroupRows, true, "a store from before the key reads as on");
  delete store["romp:settings"];
});

// The settings change signal must cover every way a change can happen: another
// same-origin tab (storage event), THIS document (the gear now lives in the same
// page — same-document writes never fire storage), and another VS Code webview
// (separate origin + localStorage → the host relays {settingsSync}, applied by
// installSettingsSync). The dead compact toggle (the user 2026-07-14) was the
// same-document gap.
test("settings changes propagate same-document and cross-webview, not just cross-tab", () => {
  const fs = require("node:fs") as typeof import("node:fs");
  const path = require("node:path") as typeof import("node:path");
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "settings.ts"), "utf8");
  assert.ok(src.includes('addEventListener("storage"'), "cross-tab: the storage event");
  assert.ok(src.includes('addEventListener("romp:settings"'), "same-document: the gear's save() signal");
  assert.ok(src.includes('"settingsSync"'), "cross-webview: the host-relayed sync applies here");
  const gear = fs.readFileSync(path.join(ROOT, "ui", "webview", "gear.js"), "utf8");
  const save = gear.slice(gear.indexOf("function save(s)"), gear.indexOf("cc.addEventListener"));
  assert.ok(save.includes("dispatchEvent(new Event('romp:settings'))"), "save() always raises the same-doc signal");
  assert.ok(save.includes("settingsSync"), "save() always posts the cross-webview sync");
  const ext = fs.readFileSync(path.join(ROOT, "vscode-extension", "src", "extension.ts"), "utf8");
  assert.ok(ext.includes("function broadcastSettings"), "the host fans a gear save out to the other panes");
  const intercepts = ext.match(/m\.type === "settingsSync"/g) || [];
  assert.equal(intercepts.length, 2, "chat AND feed handlers intercept settingsSync (the two gear hosts)");
});

test("the backend pref roundtrips through storage (the gear writes it; createSession reads it fresh)", () => {
  delete store["romp:settings"];
  saveSettings({ backend: "sdk" });
  assert.equal(loadSettings().backend, "sdk");
});

test("saveSettings persists a patch and merges over defaults", () => {
  delete store["romp:settings"];
  const next = saveSettings({ compact: true });
  assert.equal(next.compact, true);
  assert.equal(loadSettings().compact, true, "the change is read back from storage");
});

test("loadSettings tolerates corrupt JSON → defaults", () => {
  store["romp:settings"] = "{not json";
  assert.deepEqual(loadSettings(), DEFAULT_SETTINGS);
});

test("an unknown key in storage is ignored, known keys still merge", () => {
  store["romp:settings"] = JSON.stringify({ compact: true, future: 42 });
  const s = loadSettings();
  assert.equal(s.compact, true);
  assert.equal((s as any).future, 42, "merge is shallow — extra keys pass through harmlessly");
});

// Compact tabs and agents (the user 2026-09-08: on a phone, the tab strip and the background-work panel
// left about three lines of transcript in view): OFF by default, so the strip and the panel render exactly
// as before the setting existed until the gear opts in. Distinct from `compact`, the transcript's own
// tidy-up (tool runs collapsed, thinking hidden). The class it drives is dense-chrome.test.ts's subject.
test("Compact tabs and agents defaults OFF (the user 2026-09-08); the opt-in round-trips, and a store from before the key reads as off", () => {
  assert.equal(DEFAULT_SETTINGS.denseChrome, false);
  delete store["romp:settings"];
  assert.equal(loadSettings().denseChrome, false, "a fresh install reads off");
  saveSettings({ denseChrome: true });
  assert.equal(loadSettings().denseChrome, true, "the opt-in survives a reload (localStorage)");
  store["romp:settings"] = JSON.stringify({ compact: true });
  assert.equal(loadSettings().denseChrome, false, "a store written before the key reads as off");
  delete store["romp:settings"];
});

// Where a chat file-link click opens on the web (the Files pane, a column of its own, or the viewer over
// the pane you clicked): OFF by default, so a dashboard that never turns it on changes nothing. Only the
// literal "pane" opts in; anything else a store might hold reads as the default, so a corrupt entry may
// cost the preference, never the click (tabCtxMode's normalization idiom). Read at click time
// (render.ts openPath through file-route.ts fileLinkRoute).
test("File links open in defaults to the pane you clicked; the Files pane opt-in round-trips, and a foreign value reads as the default", () => {
  assert.equal(DEFAULT_SETTINGS.fileLinkPane, "chat");
  delete store["romp:settings"];
  assert.equal(loadSettings().fileLinkPane, "chat", "a fresh install opens in place");
  saveSettings({ fileLinkPane: "pane" });
  assert.equal(loadSettings().fileLinkPane, "pane", "the opt-in survives a reload (localStorage)");
  store["romp:settings"] = JSON.stringify({ compact: true });
  assert.equal(loadSettings().fileLinkPane, "chat", "a store written before the key reads as the default");
  store["romp:settings"] = JSON.stringify({ fileLinkPane: "purple" });
  assert.equal(loadSettings().fileLinkPane, "chat", "a foreign stored value normalizes to the default");
  assert.equal(fileLinkPane("pane"), "pane");
  assert.equal(fileLinkPane("feed"), "chat", "no other target exists here");
  assert.equal(fileLinkPane(undefined), "chat");
  delete store["romp:settings"];
});

// The optional dashboard panes (the user 2026-09-10): Sessions (key timeline), Outline (key fleet) and Feed
// can be hidden from the dashboard in the gear, per browser. All shown by default, so a dashboard that never
// opens the section is unchanged; only an explicit stored false hides a pane (a missing key, a store from
// before the setting, or a corrupt value reads as shown), and every key is present after a load so the
// shell's controller (_LANDING_COLLAPSE_JS) never has to guess. The chat is required and not listed.
test("the optional panes default to shown, a hide round-trips, and only an explicit false hides (the user 2026-09-10)", () => {
  assert.deepEqual(DEFAULT_SETTINGS.panes, { timeline: true, fleet: true, feed: true });
  assert.deepEqual([...OPTIONAL_PANES], ["timeline", "fleet", "feed"], "the chat is required and is not an optional pane");
  delete store["romp:settings"];
  assert.deepEqual(loadSettings().panes, { timeline: true, fleet: true, feed: true }, "a fresh install shows every pane");
  saveSettings({ panes: { timeline: true, fleet: true, feed: false } });
  assert.deepEqual(loadSettings().panes, { timeline: true, fleet: true, feed: false }, "hiding the feed survives a reload");
  store["romp:settings"] = JSON.stringify({ compact: true });
  assert.deepEqual(loadSettings().panes, { timeline: true, fleet: true, feed: true }, "a store from before the key shows every pane");
  store["romp:settings"] = JSON.stringify({ panes: { feed: false } });
  assert.deepEqual(loadSettings().panes, { timeline: true, fleet: true, feed: false }, "a partial set fills the missing panes in as shown");
  store["romp:settings"] = JSON.stringify({ panes: "purple" });
  assert.deepEqual(loadSettings().panes, { timeline: true, fleet: true, feed: true }, "a corrupt value costs nothing but the preference");
  assert.deepEqual(paneSet({ timeline: 0, fleet: "no", feed: null }), { timeline: true, fleet: true, feed: true }, "falsy but not false is not a hide");
  assert.deepEqual(paneSet(undefined), { timeline: true, fleet: true, feed: true });
  delete store["romp:settings"];
});

// The Files CONTROL's own setting (T317, the user 2026-09-10): whether the dashboard bar's Files toggle and the
// phone's Files tab show at all. OFF by default (T317b, the user the same day: the control is asked for, not
// shipped); only the literal true shows them, so a corrupt entry may cost the preference, never surprise the user
// with a control. The shell reads the store key itself (kernel.py _LANDING_COLLAPSE_JS filesCtl), so the key and
// the true-only rule are the contract.
test("the Files control is hidden by default; showing it round-trips, and only the literal true under the fresh key shows it", () => {
  assert.equal(DEFAULT_SETTINGS.showFilesControl, false);
  delete store["romp:settings"];
  assert.equal(loadSettings().showFilesControl, false, "a fresh install hides the control");
  saveSettings({ showFilesControl: true });
  assert.equal(loadSettings().showFilesControl, true, "showing it survives a reload (localStorage)");
  assert.equal(JSON.parse(store["romp:settings"]).showFilesControl, true, "the key the shell reads, the literal true");
  saveSettings({ showFilesControl: false });
  assert.equal(loadSettings().showFilesControl, false, "turning it off again round-trips too");
  assert.equal(JSON.parse(store["romp:settings"]).showFilesControl, false, "on-then-off leaves the literal false");
  store["romp:settings"] = JSON.stringify({ compact: true });
  assert.equal(loadSettings().showFilesControl, false, "a store written before the key hides the control");
  store["romp:settings"] = JSON.stringify({ showFilesControl: "yes" });
  assert.equal(loadSettings().showFilesControl, false, "a foreign stored value hides it: only true shows");
  // the T317-era key: that gear merged its default filesControl: true into the object and saved the whole object on
  // ANY change, so a profile that touched any setting in that window carries filesControl: true without touching the
  // Files box. It is never read, and the next save drops it.
  store["romp:settings"] = JSON.stringify({ compact: true, filesControl: true });
  assert.equal(loadSettings().showFilesControl, false, "the old key's true is a merged-in default, not an opt-in: hidden");
  assert.equal("filesControl" in loadSettings(), false, "the old key is dropped from the loaded object");
  saveSettings({ compact: false });
  assert.deepEqual(Object.keys(JSON.parse(store["romp:settings"])).filter((k) => /filesControl/i.test(k)), ["showFilesControl"], "the next save leaves the old key behind and writes the fresh one");
  delete store["romp:settings"];
});
