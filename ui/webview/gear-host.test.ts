// The gear's host decision (gear-host.ts), executed: the kernel's feed page, flagged, mounts no gear and
// hands an open request to the shell; an unflagged document (VS Code's feed panel, the /settings page)
// mounts it and opens it in place. Plus the source pins that keep the two bundles on this seam: the
// kernel's feed page sets the flag before feed.js, feed.ts gates its mount on hostsGear, and the
// settings page's bundle is the gear's own host (the user 2026-09-10).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { hostsGear, openGear, GearWindow } from "./gear-host";

const ROOT = path.resolve(process.cwd(), "..");
const read = (...p: string[]) => fs.readFileSync(path.join(ROOT, ...p), "utf8");

function win(flag: boolean, withParent: boolean): { w: GearWindow; own: unknown[]; up: unknown[] } {
  const own: unknown[] = [], up: unknown[] = [];
  const w: GearWindow = { postMessage: (m) => own.push(m) };
  if (flag) w.__rompGearOnSettingsPage = true;
  w.parent = withParent ? { postMessage: (m) => up.push(m) } : w;   // a top-level window's parent is itself
  return { w, own, up };
}

test("an unflagged document hosts the gear and opens it in place (VS Code's feed panel, the /settings page)", () => {
  const { w, own, up } = win(false, true);
  assert.equal(hostsGear(w), true);
  openGear(w);
  assert.deepEqual(own, [{ romp: "openSettings" }]);
  assert.deepEqual(up, [], "nothing goes up: the modal is here");
});

test("the kernel's feed page (flagged) hosts no gear and asks the shell to open it", () => {
  const { w, own, up } = win(true, true);
  assert.equal(hostsGear(w), false);
  openGear(w);
  assert.deepEqual(own, [], "no same-document open: there is no modal to hear it");
  assert.deepEqual(up, [{ romp: "openSettings" }], "the shell forwards it into the settings iframe");
});

test("a flagged document with no shell drops the ask instead of throwing", () => {
  const { w, own, up } = win(true, false);
  assert.doesNotThrow(() => openGear(w));
  assert.deepEqual(own, []);
  assert.deepEqual(up, []);
});

test("the kernel's feed page sets the flag before feed.js, and feed.ts mounts the gear only where hostsGear says so", () => {
  const KERNEL = read("bin", "romp-kernel");
  const feedPage = KERNEL.slice(KERNEL.indexOf("def _feed_page():"), KERNEL.indexOf("\ndef ", KERNEL.indexOf("def _feed_page():") + 1));
  assert.ok(feedPage.includes("window.__rompGearOnSettingsPage=true;"), "the feed page names the flag");
  assert.ok(feedPage.indexOf("__rompGearOnSettingsPage") < feedPage.indexOf("/dist/feed.js"), "…before the bundle that reads it");
  const FEED = read("ui", "webview", "feed.ts");
  assert.ok(FEED.includes('import { hostsGear, openGear } from "./gear-host"'), "feed.ts reads the decision from the one module");
  assert.match(FEED, /if \(hostsGear\(window\)\) initGear\(/, "the mount is gated");
  assert.ok(!/window\.postMessage\(\{ romp: "openSettings" \}/.test(FEED), "no bare same-document open remains in feed.ts: every opener goes through openGear");
  assert.ok(FEED.includes("openGear(window)"), "the strip's gear and the login card open through openGear");
});

test("the settings page bundle is the gear's own host", () => {
  const PAGE = read("ui", "webview", "settings-page.ts");
  assert.ok(PAGE.includes('require("./gear.js")'));
  assert.match(PAGE, /initGear\(\(m[^)]*\) => api\?\.postMessage\(m\), \{ ownPage: true \}\)/, "mounted as the page's own content: no pane lift to measure");
  assert.ok(PAGE.includes("installSettingsSync()"), "a settings write from another document reaches it");
  assert.ok(PAGE.includes("applyTheme(document, loadSettings())"), "the modal wears the theme");
  const GEAR = read("ui", "webview", "gear.js");
  assert.ok(GEAR.includes("function initGear(post, opts)"));
  assert.ok(GEAR.includes("var ownPage = !!(opts && opts.ownPage);"));
  assert.ok(GEAR.includes("if (window.parent !== window && !ownPage) { document.body.classList.add('rs-lifted');"),
    "a page that is nothing but the gear never pins a pane rect (its body stays transparent under the dim)");
  const ESBUILD = read("vscode-extension", "esbuild.js");
  assert.ok(ESBUILD.includes('"../ui/webview/settings-page.ts"'), "the page's bundle is an entry point");
});
