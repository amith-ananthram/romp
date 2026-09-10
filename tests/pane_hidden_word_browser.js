// The browser driver for tests/test_pane_hidden_word_browser.py: bundles the chat page's publisher from
// ui/webview/chat-visibility.ts with esbuild, opens a shell page in the named playwright browser and walks one chat
// frame through shown, hidden the shell's way, shown again, reading what the frame's shim decides and its two
// inputs at each step; a second frame is never shown. Prints one JSON line: the readings per shell, or
// {skipped: reason} when playwright or the browser is absent. NODE_PATH names the node_modules that hold
// playwright and esbuild (the Python side sets it). Synthetic pages only; nothing is served from a kernel.
"use strict";
const fs = require("fs");
const spec = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const browserName = process.argv[3];

// the publisher as render.ts installs it (chat-visibility.test.ts pins the call), bundled from the source tree
function bundleInstall() {
  const esbuild = require("esbuild");
  const r = esbuild.buildSync({
    stdin: {
      contents: 'import { watchChatVisibility, browserChatVisibilityDeps } from "./chat-visibility";\nwatchChatVisibility(document.body, browserChatVisibilityDeps());\n',
      resolveDir: spec.uiDir, loader: "ts", sourcefile: "chat-visibility-install.ts",
    },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020", logLevel: "silent",
  });
  return r.outputFiles[0].text;
}

// Both shells carry the desktop rule (the served sheet does; on the phone its media block overrides it), show the
// chat first (po-chat on the body, m-on on its iframe: the phone's default tab) and hold two panes the shell never
// shows. The `iframe` rule below sizes the desktop shell's frames (600 x 400); on the phone shell the served
// `.pane>iframe` rule outranks it by specificity, so a shown frame there is the body's width by the default iframe
// height. The assertions read only that a shown frame has a viewport, and what a hidden one kept.
const shellHtml = (shell) => `<!DOCTYPE html><html><head><meta charset=utf-8>
<style>${spec.desktopRule}${shell === "phone" ? spec.phoneRules : ".pane{display:inline-block;vertical-align:top}"}iframe{width:600px;height:400px;border:0}</style></head>
<body class="po-chat">
<div id=chat-pane class=pane><iframe id=f-chat class=m-on src=/chat></iframe></div>
<div id=feed-pane class=pane><iframe id=f-feed src=/chat></iframe></div>
<div id=fleet-pane class=pane><iframe id=f-fleet src=/chat></iframe></div>
</body></html>`;
// the chat page: the served chat body, the served shim's paneHidden() at top level (so the test can call it), the publisher
const chatHtml = () => `<!DOCTYPE html><html><head><meta charset=utf-8></head><body>${spec.chatBody}
<script>${spec.shim}</script>
<script src=/dist/chat-visibility.js></script></body></html>`;

async function walk(browser, shell, install) {
  const errors = [];
  const page = await browser.newPage({ viewport: { width: 1000, height: 600 } });
  page.on("pageerror", (e) => { errors.push(String((e && e.message) || e)); });
  await page.route("http://romp.test/**", (route) => {
    const u = new URL(route.request().url());
    if (u.pathname === "/shell") return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: shellHtml(shell) });
    if (u.pathname === "/chat") return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: chatHtml() });
    if (u.pathname === "/dist/chat-visibility.js") return route.fulfill({ status: 200, contentType: "application/javascript", body: install });
    return route.fulfill({ status: 404, body: "" });
  });
  await page.goto("http://romp.test/shell");
  // what the frame's shim decides, and its inputs: the published word, the viewport, the probe alone
  const read = (id) => page.evaluate((fid) => {
    const w = document.getElementById(fid).contentWindow;
    return { word: w.__rompPaneHidden, iw: w.innerWidth, ih: w.innerHeight, shim: w.paneHidden(),
             probe: w.parent !== w && (w.innerWidth === 0 || w.innerHeight === 0),
             body: !!w.document.getElementById("composer") };
  }, id);
  const wordIs = (id, v) => page.waitForFunction(([fid, want]) => document.getElementById(fid).contentWindow.__rompPaneHidden === want, [id, v], { timeout: 10000 });
  const shimIs = (id, v) => page.waitForFunction(([fid, want]) => document.getElementById(fid).contentWindow.paneHidden() === want, [id, v], { timeout: 10000 });
  const display = (id) => page.evaluate((i) => getComputedStyle(document.getElementById(i)).display, id);
  // the shell's two switches, as the landing page makes them: the rail takes po-chat off the body; the phone's
  // show(p) sets data-tab and toggles m-on across the iframes
  const tab = (p) => page.evaluate((want) => {
    document.body.setAttribute("data-tab", want);
    for (const f of Array.from(document.querySelectorAll("iframe"))) f.classList.toggle("m-on", f.id === "f-" + want);
  }, p);
  const hideChat = () => (shell === "desktop" ? page.evaluate(() => document.body.classList.remove("po-chat")) : tab("feed"));
  const showChat = () => (shell === "desktop" ? page.evaluate(() => document.body.classList.add("po-chat")) : tab("chat"));
  const out = { errors, display: {} };
  await wordIs("f-chat", false);                 // shown first: the observer's first word says on screen
  out.shown = await read("f-chat");
  await hideChat();
  await shimIs("f-chat", true);
  out.hidden = await read("f-chat");
  out.display["chat-pane"] = await display("chat-pane");
  out.display["f-chat"] = await display("f-chat");
  out.display["f-feed"] = await display("f-feed");
  await showChat();                              // a same-size re-show: no resize, so the observer's callback (or the viewport) is the event
  await shimIs("f-chat", false);
  out.reshown = await read("f-chat");
  out.neverShown = await read("f-fleet");        // no po-fleet on the body, no m-on on its iframe: hidden since load
  await page.close();
  return out;
}

(async () => {
  let pw;
  try { pw = require("playwright"); } catch (e) { process.stdout.write(JSON.stringify({ skipped: "playwright is not installed: " + e.message })); return; }
  let browser;
  try { browser = await pw[browserName].launch(); }
  catch (e) { process.stdout.write(JSON.stringify({ skipped: String((e && e.message) || e).split("\n")[0] })); return; }
  try {
    const install = bundleInstall();
    const out = {};
    for (const shell of ["desktop", "phone"]) out[shell] = await walk(browser, shell, install);
    process.stdout.write(JSON.stringify(out));
  } finally { await browser.close(); }
})().catch((e) => { console.error((e && e.stack) || e); process.exit(1); });
