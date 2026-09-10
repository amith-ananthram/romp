// Links inside a shown file, in a browser (file-view-links.ts): headless Chromium boots the viewer's real module,
// opens synthetic files through it, and drives the mouse over the viewer's real DOM: the rows hljs built and the
// pass marked, the body's click listener and its gesture, the fetch a path link causes, the line a `:30` link scrolls
// to, the Raw view a markdown file takes for that open, the tab a URL anchor opens, and the clicks that must NOT open
// anything. This is the leg no stand-in can stand in for: the browser decides where a press-drag-release sends its
// click; DOMPurify decides which Markdown targets survive; hljs decides where a substitution span cuts a path. A
// second page runs the chat's own capture-phase anchor opener (its source, lifted from render.ts and installed over
// the same bundle) so both documents' routing is exercised, not pinned. Skips LOUDLY without a playwright browser
// (CI installs none), as queued-romp-layout.test.ts does. Synthetic values only: the notes-api world under
// /tmp/TESTHOST, a placeholder session id, example.invalid addresses.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const EXT = process.cwd();                                        // npm test runs in vscode-extension
const UI = path.resolve(EXT, "..", "ui", "webview");
const SID = "11111111-2222-3333-4444-555555555555";
const ROOT = "/tmp/TESTHOST/notes-api";
const APP = ROOT + "/src/app.py";
const GUIDE = ROOT + "/docs/guide.md";
const NOTES = ROOT + "/docs/notes.md";
const README = ROOT + "/README.md";
const CONFIG = ROOT + "/src/data/config.json";
const RUN = ROOT + "/scripts/run.sh";
const URL_SETUP = "https://example.invalid/docs/setup.html";
// the titles the module gives a dead link (file-view-links.ts), spelled here so this leg builds against a viewer that has none
const DEAD_LINK_TITLE = "Not a link the viewer can follow: its target is neither a web address nor a file on the session's machine";
const HOST_PORT_TITLE = "Not a link the viewer can follow: the target is a host with a port, not a file on the session's machine";
const noSectionTitle = (id: string): string => "No heading or anchor named “" + id + "” in this document";

const APP_TEXT = [
  "# notes-api: see " + URL_SETUP + ", then ../docs/guide.md:30.",
  "import json",
  'cfg = json.load(open("data/config.json"))',
  "from . import util  # and/or 24/7 x = 1/2.5 /api/users ./foo",
  "readme = 'file:///tmp/TESTHOST/notes-api/README.md'",
  "# see www.example.org/docs/index.html or git@github.invalid:user/repo.git",
  'far = "file://evil.invalid/share/x.md"',
  "",
].join("\n");
const GUIDE_TEXT = [
  "# Guide", "",
  "Read [the app](../src/app.py) and [the web](https://example.invalid/doc).",
  "Bare ../src/app.py:3 links too, and https://example.invalid/prose is a URL.",
  "docs/first.md starts a soft-broken line of the same paragraph, and a hard break follows  ",   // two trailing spaces: marked's <br>
  "docs/second.md starts the line after the break.", "",
  "Also [same](notes.md:7), [uri](file:///tmp/TESTHOST/notes-api/README.md), [far](file://evil.invalid/x.md), [q](?foo=1), [here](#section), [go](#top), [past](../src/app.py:400), [collide](#fileview-save-err), [install](#install), [api](api.example.com:8443), [spec](app.test.ts:12), [results](#results), [self](guide.md#install), [ip](127.0.0.1:3000).", "",
  "A glob `**/docs/glob.md` or `src/**/x.md` links nothing, nor does an operand 2*docs/times.md or 3*w/h.px.", "",
  'Copied from "docs/from.md", and the export "out/data.json" is stale.', "",   // the English from and export: prose that names files
  "**docs/strong.md** and *docs/em.md*, then ​docs/zwsp.md after a zero-width space.", "",
  '<svg width="200" height="30"><a href="https://example.invalid/s"><text y="11">svgweb</text></a><a href="x.md"><text x="60" y="11">svgfile</text></a><text y="26">label docs/label.md in the figure</text></svg>', "",
  "```bash", "curl https://example.invalid/dl -o data/x.json", "docs/fence2.md", "  ./docs/fence3.md", "```", "",
  ...Array.from({ length: 32 }, (_, i) => "line " + (i + 10) + "\n"),   // one paragraph each (a blank line between), so the rendered body scrolls
  "## Results", "",                                // a heading: the viewer mints it the id md-results, and `#results` finds it by that slug
  '<p id="top">Top</p>', "",                        // an author's id on an element that is not a heading
  '<p id="fileview-save-err">Collide</p>', "",     // an author's id spelled like the viewer's own notice
  '<a name="install"></a>', "", "## Install", "",   // the README idiom for a stable anchor: a named <a> above a heading
  '<span class="file-uri-link" data-path="/tmp/TESTHOST/secret.txt" data-line="3">dressed</span>', "",   // a document's own element dressed as a path link: its data-* must never reach the page
].join("\n");
const NOTES_TEXT = Array.from({ length: 12 }, (_, i) => "note " + (i + 1)).join("\n") + "\n";
// what a highlighter cuts: bash puts `$HOME` and `${ROOT}` in spans of their own; a URL a substitution cuts, with an absolute
// path as a query value, stays text and its path is the URL's; a glob's tail after a star and an operand after one are text
const RUN_TEXT = ["#!/bin/bash", 'cp "$HOME/docs/a.md" ./out/', "cat ${ROOT}/src/x.py", 'echo "see ./docs/b.md"', "curl https://example.invalid/q?x=/docs/a.md/$V",
  "find . -path '**/docs/a.md'", "cp src/**/index.ts out/", "ls packages/*/package.json", "export DATA=/data", '# copied from "docs/c.md"', ""].join("\n");
const FILES: Record<string, string> = { [APP]: APP_TEXT, [GUIDE]: GUIDE_TEXT, [NOTES]: NOTES_TEXT, [README]: "# notes-api\n", [CONFIG]: '{"a": 1}\n', [RUN]: RUN_TEXT };

/** The viewer, bundled the way vscode-extension/esbuild.js builds the webview (in memory, nothing on disk), with its
 *  openers handed to the page. */
function viewerBundle(): string {
  const esbuild = requireCjs("esbuild");
  const r = esbuild.buildSync({
    stdin: { contents: 'import { openFileView, initFileView } from "./file-view";\n(window as any).__romp = { openFileView, initFileView };\n', resolveDir: UI, loader: "ts", sourcefile: "viewer-probe.ts" },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(EXT, "node_modules")], external: ["*.png", "*.svg", "*.woff", "*.ttf", "../media/*.woff2"], logLevel: "silent",
  });
  return r.outputFiles[0].text;
}
/** The chat document's OWN click handler, lifted from its source: render.ts's document-level anchor opener (capture
 *  phase, window.open for a scheme href). Installed over the viewer bundle, so the viewer runs under it as it does in
 *  the chat page. Every name the lifted handler uses that render.ts imports or declares at its top level must be one the
 *  prelude defines: a missing one throws a ReferenceError at the first click that reaches its branch, and the leg then
 *  reads a tab that never opens where the viewer did nothing wrong. */
function chatHostBundle(): string {
  const esbuild = requireCjs("esbuild");
  const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
  const head = 'document.addEventListener("click", (e) => {\n  const a = (e.target as HTMLElement)?.closest?.("a[href]") as HTMLAnchorElement | null;';
  const start = RENDER.indexOf(head);
  const end = RENDER.indexOf("}, true);", start) + "}, true);".length;
  assert.ok(start > 0 && end > start, "render.ts's document-level anchor opener");
  const lifted = RENDER.slice(start, end);
  const prelude = 'import { selectionOpenIn } from "./path-links";\nimport { isMarkdownUrl } from "./md-links";\nimport { userContentTarget } from "./md-sanitize";\n'
    + "const vscodeApi: { postMessage(m: unknown): void } | null = null;\n"
    + "(window as any).__urlViews = [];\nconst openUrlView = (href: string): void => { (window as any).__urlViews.push(href); };\n"
    // render.ts's attributed mover of #content; this page has no #content and the viewer's links sit outside a message body, so the fragment arm never reaches it
    + 'const scrollElInto = (_content: HTMLElement, el: Element, block: "start" | "center" | "nearest"): void => { el.scrollIntoView({ block }); };\n';
  // the check: the lifted code with comments and strings removed, its own locals set aside, a property read (`.name`) not counted
  const code = lifted.replace(/\/\*[\s\S]*?\*\/|\/\/.*$|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`/gm, (m) => (m[0] === "/" ? "" : '""'));
  const locals = new Set(Array.from(code.matchAll(/\b(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)/g), (m) => m[1]));
  const named = new Set<string>();
  const addNamed = (list: string) => { for (const part of list.split(",")) { const name = part.trim().replace(/^type\s+/, "").split(/\s+as\s+/).pop()!.trim(); if (name) named.add(name); } };
  for (const m of RENDER.matchAll(/^import (?!type\b)(?:([A-Za-z_$][\w$]*)\s*,?\s*)?(?:\* as ([A-Za-z_$][\w$]*)|\{([^}]*)\})?\s*from "[^"]+";/gm)) { if (m[1]) named.add(m[1]); if (m[2]) named.add(m[2]); if (m[3]) addNamed(m[3]); }
  for (const m of RENDER.matchAll(/^(?:export )?(?:const|let|var|(?:async )?function\*?|class) ([A-Za-z_$][\w$]*)/gm)) named.add(m[1]);
  const declared = new Set(Array.from(prelude.matchAll(/(?:^const |\{ )([A-Za-z_$][\w$]*)/gm), (m) => m[1]));
  const free = Array.from(named).filter((n) => !locals.has(n) && !declared.has(n) && new RegExp("(?<![.\\w$])" + n.replace(/\$/g, "\\$") + "\\b").test(code));
  assert.deepEqual(free, [], "render.ts's opener names these and the prelude defines none of them: import or define each in chatHostBundle, or the lifted handler throws where a click reaches it");
  // a recorder on the window, after the host's own: a click that reaches it reached every document-level listener too
  const ts = prelude + lifted + "\n(window as any).__windowClicks = [];\nwindow.addEventListener(\"click\", (e) => { (window as any).__windowClicks.push((e.target as HTMLElement).textContent); });\n";
  const r = esbuild.buildSync({
    stdin: { contents: ts, resolveDir: UI, loader: "ts", sourcefile: "chat-host.ts" },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(EXT, "node_modules")], logLevel: "silent",
  });
  return r.outputFiles[0].text;
}
const PAGE = (host: "viewer" | "chat") => `<!DOCTYPE html><html><head><meta charset=utf-8><style>
${fs.readFileSync(path.join(UI, "styles.css"), "utf8")}
</style></head><body>${host === "chat" ? '<p id=chat-para><a href="https://example.invalid/pr">alpha beta gamma delta</a> is ready, and the prose after it runs on.</p>' : ""}
<script src=/dist/viewer.js></script>
<script>window.__posts = []; window.__romp.initFileView(function (m) { window.__posts.push(m); });</script>
${host === "chat" ? "<script src=/dist/host.js></script>" : ""}</body></html>`;

let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }

type Info = { text: string | null; path: string | undefined; line: string | undefined; frag: string | undefined; href: string | null; target: string | null; rel: string | null; cls: string; title: string | null; draggable: string | null };
type Harness = {
  page: any; ctx: any; served: string[]; errors: string[];
  open: (p: string, opts?: { line?: number | null; frag?: string | null }) => Promise<void>;
  linkInfo: (sel: string) => Promise<Info[]>; base: () => Promise<string | null>; newPages: () => any[]; rows: () => Promise<string[]>;
  inView: (sel: string) => Promise<boolean>;
  centerOffset: (sel: string) => Promise<{ d: number; h: number } | null>;
};
async function inBrowser(t: any, host: "viewer" | "chat", body: (h: Harness) => Promise<void>): Promise<void> {
  if (!pw) { t.skip("playwright is not installed under vscode-extension: the browser leg needs it (CI installs no browsers)"); return; }
  let browser: any;
  try { browser = await pw.chromium.launch(); }
  catch (e) { t.skip("no playwright browser on this machine, and the browser leg needs one (CI installs none): " + String((e as Error).message).split("\n")[0]); return; }
  const errors: string[] = [];
  const served: string[] = [];
  const opened: any[] = [];
  try {
    const viewerJs = viewerBundle();
    const hostJs = host === "chat" ? chatHostBundle() : "";
    const ctx = await browser.newContext({ viewport: { width: 900, height: 600 } });
    ctx.on("page", (p: any) => { opened.push(p); });
    const page = await ctx.newPage();
    opened.length = 0;                                             // the harness's own page is not an opened tab
    page.on("pageerror", (e: Error) => { errors.push(e.message); });
    await ctx.route("http://romp.test/**", (route: any) => {
      const u = new URL(route.request().url());
      const html = (b: string) => route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: b });
      const js = (b: string) => route.fulfill({ status: 200, contentType: "application/javascript", body: b });
      if (u.pathname === "/page") return html(PAGE(host));
      if (u.pathname === "/dist/viewer.js") return js(viewerJs);
      if (u.pathname === "/dist/host.js") return js(hostJs);
      if (u.pathname === "/file") {                                // the viewer's fetch: what the kernel would serve for a text file
        const p = u.searchParams.get("path") || "";
        served.push(p);
        const text = FILES[p];
        if (text === undefined) return route.fulfill({ status: 404, contentType: "text/plain; charset=utf-8", body: "not found: " + p });
        return route.fulfill({ status: 200, contentType: "text/plain; charset=utf-8", headers: { "X-Romp-Mtime-Ns": "1", "X-Romp-Text-Utf8": "1" }, body: text });
      }
      return route.fulfill({ status: 404, body: "" });
    });
    await ctx.route("https://example.invalid/**", (route: any) => route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: "<!DOCTYPE html><title>stub</title>" }));
    await page.goto("http://romp.test/page");
    const h: Harness = {
      page, ctx, served, errors,
      open: async (p, opts) => {
        await page.evaluate(([p, sid, opts]: any[]) => { (window as any).__romp.openFileView(p, sid, opts); }, [p, SID, opts || undefined]);
        await page.locator("#romp-fileview .fileview-body code.hljs .fv-cl, #romp-fileview .fileview-body .fileview-md").first().waitFor({ timeout: 10000 });
      },
      linkInfo: (sel) => page.evaluate((sel: string) => Array.from(document.querySelectorAll(sel)).map((e) => {
        const x = e as HTMLElement;
        return { text: x.textContent, path: x.dataset.path, line: x.dataset.line, frag: x.dataset.frag, href: x.getAttribute("href"), target: x.getAttribute("target"), rel: x.getAttribute("rel"),
          cls: x.getAttribute("class") || "", title: x.getAttribute("title"), draggable: x.getAttribute("draggable") };
      }), sel),
      base: () => page.evaluate(() => document.querySelector("#romp-fileview .fileview-base")?.textContent ?? null),
      newPages: () => opened,
      rows: () => page.evaluate(() => Array.from(document.querySelectorAll("#romp-fileview code.hljs .fv-cl")).map((r) => r.textContent || "")),
      inView: (sel) => page.evaluate((sel: string) => {
        const el = document.querySelector(sel), body = document.querySelector("#romp-fileview .fileview-body");
        if (!el || !body) return false;
        const r = el.getBoundingClientRect(), b = body.getBoundingClientRect();
        return r.top >= b.top - 1 && r.bottom <= b.bottom + 1;
      }, sel),
      centerOffset: (sel) => page.evaluate((sel: string) => {   // the element's centre against the body's, and its height
        const el = document.querySelector(sel), body = document.querySelector("#romp-fileview .fileview-body");
        if (!el || !body) return null;
        const r = el.getBoundingClientRect(), b = body.getBoundingClientRect();
        return { d: (r.top + r.bottom) / 2 - (b.top + b.bottom) / 2, h: r.height };
      }, sel),
    };
    await body(h);
    assert.deepEqual(errors, [], "no page errors");
  } finally { await browser.close(); }
}
const linkTexts = (info: Info[]) => info.map((x) => x.text);
/** A press-drag-release from the left edge of `fromSel` to the middle of `toSel` (the same element for a drag inside a
 *  link): what selects text in a browser. */
async function dragSelect(page: any, fromSel: string, toSel: string): Promise<void> {
  const a = await page.locator(fromSel).first().boundingBox(), b = await page.locator(toSel).first().boundingBox();
  assert.ok(a && b, "both ends of the drag are laid out");
  await page.mouse.move(a.x + 1, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 });
  await page.mouse.up();
}

test("in a browser: the code view's real hljs rows carry the links (a URL anchor in a comment, path links resolved against the file, a :30 riding along), the highlight's substitution spans expose no fabricated path, and every row's text is the file's", async (t) => {
  await inBrowser(t, "viewer", async (h) => {
    await h.open(APP);
    assert.deepEqual(await h.rows(), APP_TEXT.split("\n").slice(0, -1), "the rows read as the file's lines: the pass changed no character");
    const urls = await h.linkInfo("#romp-fileview a.fv-url");
    assert.deepEqual(urls.map((u) => [u.href, u.target, u.rel, u.draggable]), [[URL_SETUP, "_blank", "noopener noreferrer", "false"]]);
    assert.equal(await h.page.evaluate(() => !!document.querySelector("#romp-fileview a.fv-url")!.closest(".hljs-comment")), true, "inside the highlight's own comment span");
    const links = await h.linkInfo("#romp-fileview .file-uri-link");
    assert.deepEqual(linkTexts(links), ["../docs/guide.md:30", "data/config.json", "file:///tmp/TESTHOST/notes-api/README.md"],
      "and/or, 24/7, 1/2.5, /api/users, ./foo, the site, the git remote and the far file:// URI stay text");
    assert.deepEqual(links.map((l) => [l.path, l.line]), [[GUIDE, "30"], [CONFIG, undefined], [README, undefined]]);
    assert.ok(links.every((l) => l.cls.split(/\s+/).includes("file-uri-link") && l.title!.startsWith("Open ")));
    assert.equal(await h.page.evaluate(() => !!document.querySelector('#romp-fileview .file-uri-link[data-path$="config.json"]')!.closest(".hljs-string")), true, "inside the highlight's string span, the quotes outside");
    // the bash file: hljs cuts `"$HOME/docs/a.md"` and `${ROOT}/src/x.py` at the substitution, and the pass reads the LINE
    await h.open(RUN);
    assert.deepEqual(await h.rows(), RUN_TEXT.split("\n").slice(0, -1));
    assert.deepEqual(linkTexts(await h.linkInfo("#romp-fileview .file-uri-link")), ["./docs/b.md", "docs/c.md"],
      "no /docs/a.md or /src/x.py off a substitution's tail, no glob tail, no operand, no path inside the cut URL; the quoted path and the English from's file link");
    assert.deepEqual(await h.linkInfo("#romp-fileview a.fv-url"), [], "a URL the highlight cut through is not one");
  });
});

test("in a browser: a plain click on a path link opens that file in the viewer, in place; a :line link opens a markdown file in its Raw view for that open, with row 30 itself centred, and the saved preference untouched", async (t) => {
  await inBrowser(t, "viewer", async (h) => {
    await h.open(APP);
    await h.page.locator('#romp-fileview .file-uri-link[data-path$="config.json"]').click();
    await h.page.locator("#romp-fileview .fileview-base", { hasText: "config.json" }).waitFor({ timeout: 10000 });
    assert.deepEqual(h.served, [APP, CONFIG], "the click fetched the resolved sibling, for this session");
    assert.equal(await h.base(), "config.json");
    // the :30 link: the guide opens Raw (the Rendered view has no rows), on line 30
    await h.open(APP);
    await h.page.locator('#romp-fileview .file-uri-link[data-line="30"]').click();
    await h.page.locator("#romp-fileview .fileview-base", { hasText: "guide.md" }).waitFor({ timeout: 10000 });
    await h.page.locator("#romp-fileview code.hljs .fv-cl").first().waitFor({ timeout: 10000 });
    assert.equal(await h.page.evaluate(() => document.querySelector("#romp-fileview .fileview-btn.on")?.textContent), "Raw", "the Raw view, for this open");
    assert.equal(await h.page.evaluate(() => localStorage.getItem("romp:fileviewFmt")), null, "the preference was never written");
    // the row is scrolled to the body's centre (scrollIntoView block center), so the centred row is row 30 ITSELF: a
    // neighbour would be a row's height off, and merely being in view would not tell them apart
    const at = await h.centerOffset("#romp-fileview code.hljs .fv-cl:nth-child(30)");
    assert.ok(at && Math.abs(at.d) <= at.h / 2 + 1, "row 30 is the centred row, not a neighbour: " + JSON.stringify(at));
    assert.equal(await h.page.evaluate(() => !!document.getElementById("fileview-save-err")), false, "no notice: the line exists");
  });
});

test("in a browser: the rendered Markdown's links are sorted after the real sanitizer (a same-directory name.ext:line and a local file:// target survive as path links, a far file:// and a host with a port are dead links that say why, a section link scrolls the document under a plain and a middle click, an author's id is read under the sanitizer's prefix, a document's own data-* never reach the page), and a line past the end says so", async (t) => {
  await inBrowser(t, "viewer", async (h) => {
    await h.open(GUIDE);
    const byText = async (text: string): Promise<Info> => {
      const all = await h.linkInfo("#romp-fileview .fileview-md a");
      const hit = all.find((a) => a.text === text);
      assert.ok(hit, "an anchor labelled " + text);
      return hit!;
    };
    const same = await byText("same");
    assert.equal(same.path, NOTES); assert.equal(same.line, "7"); assert.equal(same.href, null, "the href comes off a path link");
    const uri = await byText("uri"); assert.equal(uri.path, README);
    const far = await byText("far"); assert.equal(far.title, DEAD_LINK_TITLE); assert.equal(far.href, null); assert.ok(far.cls.includes("fv-dead"));
    const ip = await byText("ip"); assert.equal(ip.title, HOST_PORT_TITLE); assert.equal(ip.href, null); assert.equal(ip.path, undefined);
    const api = await byText("api"); assert.equal(api.title, DEAD_LINK_TITLE, "a letter-led host:port reads as a scheme to the sanitizer, which strips it first: the generic title");
    const spec = await byText("spec"); assert.equal(spec.path, ROOT + "/docs/app.test.ts"); assert.equal(spec.line, "12");
    const web = await byText("the web"); assert.equal(web.target, "_blank"); assert.equal(web.href, "https://example.invalid/doc");
    const q = await byText("q"); assert.equal(q.target, "_blank"); assert.equal(q.href, "?foo=1");
    const here = await byText("here"); assert.ok(here.cls.includes("fv-frag") && here.cls.includes("fv-dead")); assert.equal(here.title, noSectionTitle("section"));
    for (const [label, frag] of [["results", "results"], ["go", "top"], ["install", "install"], ["collide", "fileview-save-err"]] as const) {
      const a = await byText(label); assert.ok(a.cls.includes("fv-frag") && !a.cls.includes("fv-dead"), label + ": " + a.cls); assert.equal(a.frag, frag);
    }
    const self = await byText("self"); assert.equal(self.path, GUIDE); assert.equal(self.frag, "install");
    // the sanitizer's verdict on an author's own id and name: prefixed user-content- (md-sanitize.ts); the links above are live because the lookup reads the prefix
    assert.equal(await h.page.evaluate(() => [!!document.querySelector('#romp-fileview .fileview-md [id="user-content-top"]'), !!document.querySelector('#romp-fileview .fileview-md [id="top"]'), !!document.querySelector('#romp-fileview .fileview-md a[name="user-content-install"]')].join()), "true,false,true", "an author's id and name reach the DOM prefixed, and only prefixed");
    // a document's own element dressed as a path link: the class survives, its data-* do not, so it names no file
    assert.deepEqual((await h.linkInfo("#romp-fileview .fileview-md span.file-uri-link:not([data-path])")).map((d) => [d.text, d.path, d.line]), [["dressed", undefined, undefined]], "the sanitizer dropped data-path and data-line");
    // the prose and the fence: bare paths under the viewer's gate, URLs as anchors, the SVG's label untouched and its anchors sorted
    const spans = await h.linkInfo("#romp-fileview .fileview-md span.file-uri-link[data-path]");
    assert.deepEqual(linkTexts(spans), ["../src/app.py:3", "docs/first.md", "docs/second.md", "docs/from.md", "out/data.json", "docs/strong.md", "docs/em.md", "docs/zwsp.md", "data/x.json", "docs/fence2.md", "./docs/fence3.md"],
      "a soft-broken line's first path, the line after a <br>, the English from and export, emphasis, a zero-width space, the fence's lines; no glob, no operand, no SVG label");
    assert.deepEqual((await h.linkInfo("#romp-fileview .fileview-md a.fv-url")).map((u) => u.href), ["https://example.invalid/dl"], "the fence's URL is the pass's; the prose's is marked's own autolink, not wrapped twice");
    const prose = (await h.linkInfo("#romp-fileview .fileview-md a")).find((a) => a.href === "https://example.invalid/prose");
    assert.ok(prose && prose.target === "_blank" && !prose.cls.includes("fv-url"), "marked's autolink, sorted as a web link");
    const svgFile = await byText("svgfile"); assert.ok(svgFile.cls.includes("file-uri-link"), "an SVG <a> is marked through attributes"); assert.equal(svgFile.path, ROOT + "/docs/x.md");
    const svgWeb = await byText("svgweb"); assert.equal(svgWeb.target, "_blank");
    // a section link scrolls THIS document, never the page: the heading by its minted slug, an author's id spelled like the viewer's notice (under its prefix, so it is no longer the same id)
    assert.equal(await h.inView("#romp-fileview #md-results"), false, "the heading starts out of view");
    await h.page.locator("#romp-fileview .fileview-md a", { hasText: "results" }).first().click();
    assert.equal(await h.inView("#romp-fileview #md-results"), true, "scrolled to the heading");
    assert.equal(await h.page.evaluate(() => location.hash), "", "the page's own location did not move");
    await h.page.locator("#romp-fileview .fileview-md a", { hasText: "collide" }).click();
    assert.equal(await h.inView('#romp-fileview .fileview-md [id="user-content-fileview-save-err"]'), true, "the document's own element of that id, under the sanitizer's prefix, not the viewer's notice");
    // a middle-click on a section link is this document's scroll too, to the named anchor, and no tab (the browser's own middle-click would open a second copy of the page)
    await h.page.evaluate(() => { document.querySelector("#romp-fileview .fileview-body")!.scrollTop = 0; });
    assert.equal(await h.inView("#romp-fileview #md-install"), false, "the last heading starts out of view");
    await h.page.locator("#romp-fileview .fileview-md a", { hasText: "install" }).first().click({ button: "middle" });
    assert.equal(await h.inView("#romp-fileview #md-install"), true, "the middle-click scrolled to the named anchor above the heading");
    // the dressed span: a click on it opens nothing and fetches nothing (checked below, once the next fetch has landed)
    const servedBefore = h.served.length;
    await h.page.locator("#romp-fileview .fileview-md span.file-uri-link", { hasText: "dressed" }).click();
    assert.equal(await h.base(), "guide.md", "the viewer stays on the guide");
    // a line past the end: the file opens, lands on its last row, and the notice says so
    await h.page.locator("#romp-fileview .fileview-md a", { hasText: "past" }).click();
    await h.page.locator("#romp-fileview .fileview-base", { hasText: "app.py" }).waitFor({ timeout: 10000 });
    await h.page.locator("#fileview-save-err").waitFor({ timeout: 10000 });
    assert.match(await h.page.evaluate(() => document.getElementById("fileview-save-err")!.textContent || ""), /^Line 400 is past the end of this file, which has 7 lines; showing the last line\.$/);
    assert.equal(await h.inView("#romp-fileview code.hljs .fv-cl:last-child"), true);
    assert.deepEqual(h.served.slice(servedBefore), [APP], "the dressed span fetched nothing; the past link fetched the one file it named");
    assert.equal(h.newPages().length, 0, "no click in this document opened a tab: the middle-click on the section link was this document's scroll alone");
  });
});

test("in a browser: the gesture: a Cmd-click or a middle-click on a path link opens the file in a tab of its own off the /file route and leaves the viewer where it was; a plain click on a URL anchor opens the URL in a tab; a drag that selects text inside a link, or that runs into one, opens nothing", async (t) => {
  await inBrowser(t, "viewer", async (h) => {
    await h.open(APP);
    const cfg = h.page.locator('#romp-fileview .file-uri-link[data-path$="config.json"]');
    await Promise.all([h.ctx.waitForEvent("page", { timeout: 10000 }), cfg.click({ modifiers: ["Meta"] })]);
    await Promise.all([h.ctx.waitForEvent("page", { timeout: 10000 }), cfg.click({ button: "middle" })]);
    assert.equal(h.newPages().length, 2, "one tab per modified click");
    for (const p of h.newPages()) assert.match(p.url(), /^http:\/\/romp\.test\/file\?path=%2Ftmp%2FTESTHOST%2Fnotes-api%2Fsrc%2Fdata%2Fconfig\.json&sid=/);
    assert.equal(await h.base(), "app.py", "the viewer stays on the file it showed");
    await Promise.all([h.ctx.waitForEvent("page", { timeout: 10000 }), h.page.locator("#romp-fileview a.fv-url").click()]);
    assert.equal(h.newPages()[2].url(), URL_SETUP, "the URL anchor's own tab");
    assert.equal(await h.base(), "app.py");
    // a press-drag-release INSIDE the guide link (its left edge to its middle) selects its text, and the click that ends
    // it (the press and the release both on the link, so the link is the click's target) opens nothing: the viewer's
    // listener finds the selection open and yields
    const before = h.served.length;
    const guide30 = '#romp-fileview .file-uri-link[data-line="30"]';
    await dragSelect(h.page, guide30, guide30);
    const picked = await h.page.evaluate(() => String(window.getSelection()));
    assert.ok(picked.length > 0 && "../docs/guide.md:30".includes(picked), "the drag selected text inside the link: " + JSON.stringify(picked));
    assert.equal(await h.base(), "app.py", "the click that ended the drag opened nothing");
    // a drag from the row's start into the link: the browser sends that click to the elements' common ancestor, so no
    // link sees it, and nothing opens either way
    await dragSelect(h.page, "#romp-fileview code.hljs .fv-cl:nth-child(1) .fv-ct", guide30);
    assert.ok((await h.page.evaluate(() => String(window.getSelection()))).length > 0, "the drag selected text");
    // the plain click after either drag does open: its press collapses the selection first
    await cfg.click();
    await h.page.locator("#romp-fileview .fileview-base", { hasText: "config.json" }).waitFor({ timeout: 10000 });
    assert.deepEqual(h.served.slice(before), [CONFIG], "neither drag fetched anything; the click that followed fetched the one file it named");
    assert.equal(h.newPages().length, 3);
  });
});

test("in a browser, under the chat's own anchor opener: a triple-click on a chat paragraph then a click on its link opens one tab (a chat anchor is draggable, so the selection around it is no drag on it); a click on the viewer's URL anchor while text is selected inside it opens nothing, and the plain click after it opens one tab", async (t) => {
  await inBrowser(t, "chat", async (h) => {
    await h.page.locator("#chat-para").click({ clickCount: 3 });
    assert.ok((await h.page.evaluate(() => String(window.getSelection()))).includes("alpha beta gamma delta"), "the paragraph is selected, the anchor's text inside it");
    await Promise.all([h.ctx.waitForEvent("page", { timeout: 10000 }), h.page.locator("#chat-para a").click()]);
    assert.equal(h.newPages().length, 1, "one tab, from the chat's opener alone");
    assert.equal(h.newPages()[0].url(), "https://example.invalid/pr");
    await h.open(APP);
    // a selection inside the viewer's URL anchor, open when the click arrives: what a press held on the anchor and then
    // dragged leaves (Chromium starts no selection under an immediate drag on a link, and the hold is a clock this leg
    // does not read), so the selection is made by script, over the anchor's whole text (a press OUTSIDE the selected
    // range collapses it at once, and the click lands on the anchor's middle); the capture-phase opener yields to it,
    // and so does the viewer
    await h.page.evaluate(() => { const tn = document.querySelector("#romp-fileview a.fv-url")!.firstChild as Text; window.getSelection()!.setBaseAndExtent(tn, 0, tn, tn.length); });
    assert.equal(await h.page.evaluate(() => String(window.getSelection())), URL_SETUP, "the selection is the anchor's text");
    await h.page.locator("#romp-fileview a.fv-url").click();
    // the plain click after it, its press elsewhere having collapsed the selection, opens one tab, not two
    await h.page.locator("#romp-fileview code.hljs .fv-cl:nth-child(2) .fv-ct").click();
    await Promise.all([h.ctx.waitForEvent("page", { timeout: 10000 }), h.page.locator("#romp-fileview a.fv-url").click()]);
    assert.equal(h.newPages().length, 2, "the click under the open selection opened nothing (the capture-phase opener yielded to it); the plain click opened one tab");
    assert.equal(h.newPages()[1].url(), URL_SETUP);
    // a plain click on a path link is not stopped: the document's own listeners still see it
    await h.page.locator('#romp-fileview .file-uri-link[data-path$="config.json"]').click();
    await h.page.locator("#romp-fileview .fileview-base", { hasText: "config.json" }).waitFor({ timeout: 10000 });
    assert.ok((await h.page.evaluate(() => (window as any).__windowClicks as string[])).includes("data/config.json"), "the click went on to the window's listener");
    // the viewer's own section links stay the viewer's under the chat's opener (its fragment arm reads a message's body, which the viewer is not in): the document scrolls, the page's location does not move
    await h.open(GUIDE);
    assert.equal(await h.inView('#romp-fileview .fileview-md [id="user-content-top"]'), false, "the author's element starts out of view");
    await h.page.locator("#romp-fileview .fileview-md a", { hasText: "go" }).first().click();
    assert.equal(await h.inView('#romp-fileview .fileview-md [id="user-content-top"]'), true, "the viewer landed its own section link, under the sanitizer's prefix");
    assert.equal(await h.page.evaluate(() => location.hash), "", "the page's own location did not move");
  });
});
