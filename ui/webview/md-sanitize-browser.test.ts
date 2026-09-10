// A file's own HTML in the rendered file view, over the REAL viewer module (file-view.ts) in headless Chromium.
// A markdown file is arbitrary bytes off a disk, and DOMPurify's default html profile keeps <style>, <form>,
// <button>, <dialog>, inline `style`, `id`, `name` and the `background` attribute. Four things followed, each
// a test below: a `<style>` block hid the whole viewer and a `position: fixed` div covered its close button; a
// `<form action=...><button>` navigated the pane's document to the action URL; an author's `id` shadowed the
// page's own ids, an inline style set any property, `<td background=URL>` fetched the URL on render, and a text
// input or a select was a live control; and an element that reached `position: fixed` escaped the note. The
// sanitizer (md-sanitize.ts), the body's submit backstop (file-view.ts) and `contain: layout` on .fileview-md
// (both sheets) close them together, as the browser lays them out. Each test opens the viewer on a synthetic
// note through openFileView and reads the rendered DOM: styles resolved, boxes measured, elementFromPoint at the
// close button's centre. Content wider than the column is measured too: under containment the body cannot scroll
// to it, so an inline svg, canvas or video is capped at the column and a table scrolls sideways on its own. Skips
// with a stated reason when no playwright browser is installed (CI installs none; tests/test_spend_modal_headless.py
// is the precedent, skipping without a playwright install). Synthetic values only: an invented note under a
// TESTHOST path, a placeholder sid.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const EXT = process.cwd();                                        // npm test runs in vscode-extension
// resolve playwright and esbuild from the extension, not from wherever this bundle was written
const requireCjs = createRequire(path.join(EXT, "package.json"));
const UI = path.resolve(EXT, "..", "ui", "webview");
// the chat's sheet, its one @import dropped (KaTeX's sheet is not what is measured here, and a bare specifier
// fetched from the test origin resolves to nothing)
const STYLES = fs.readFileSync(path.join(UI, "styles.css"), "utf8").replace(/^@import [^\n]*\n/m, "");

const SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const NOTE_PATH = "/tmp/TESTHOST/notes-api/report.md";
const NOTE_URL = "http://romp.test/docs/report.md";           // the same note as a same-origin document (openUrlView)
const FILLER = Array.from({ length: 120 }, (_, i) => "Paragraph " + (i + 1) + " of the long tail, so the body has to scroll.").join("\n\n");

// the viewer alone: the module every hosting page imports, opened the way a path click opens it (openFileView) or
// a same-origin .md link does (openUrlView)
const ENTRY = `
import { openFileView, openUrlView } from "./file-view";
(window as any).__openNote = (p: string, sid: string) => { openFileView(p, sid); };
(window as any).__openUrl = (href: string) => { openUrlView(href); };
`;

function bundle(): string {
  const esbuild = requireCjs("esbuild");
  const r = esbuild.buildSync({
    stdin: { contents: ENTRY, resolveDir: UI, loader: "ts", sourcefile: "md-sanitize-viewer-entry.ts" },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(EXT, "node_modules")], external: ["*.png", "*.svg", "*.woff", "*.ttf", "../media/*.woff2"], logLevel: "silent",
  });
  return r.outputFiles[0].text;
}

const PAGE_HTML = `<!DOCTYPE html><html><head><meta charset=utf-8><style>${STYLES}</style></head><body>
<script>window.acquireVsCodeApi=function(){return{postMessage:function(){}}};</script>
<script src=/dist/viewer.js></script></body></html>`;

let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }

// What the page's own events say happened over a test: every main-frame navigation (the load is one), and every
// request whose host is not the page's (a submitted `<form action=https://example.invalid/go>` would be one)
type Seen = { navigations: string[]; foreignRequests: string[] };

async function withNote(t: any, note: string, body: (page: any, errors: string[], seen: Seen) => Promise<void>, via: "file" | "url" = "file"): Promise<void> {
  if (!pw) { t.skip("playwright is not installed under vscode-extension; the browser leg needs it (CI installs no browsers)"); return; }
  let browser: any;
  try { browser = await pw.chromium.launch(); }
  catch (e) { t.skip("no playwright browser on this machine; the browser leg needs one (CI installs none): " + String((e as Error).message).split("\n")[0]); return; }
  const errors: string[] = [];
  const seen: Seen = { navigations: [], foreignRequests: [] };
  try {
    const js = bundle();
    const page = await browser.newPage({ viewport: { width: 900, height: 600 } });
    page.on("pageerror", (e: Error) => { errors.push(e.message); });
    page.on("framenavigated", (f: any) => { if (f === page.mainFrame()) seen.navigations.push(f.url()); });
    page.on("request", (r: any) => { const u = new URL(r.url()); if (u.protocol !== "data:" && u.host !== "romp.test") seen.foreignRequests.push(r.url()); });
    await page.route("**/*", (route: any) => {
      const u = new URL(route.request().url());
      // the action's host answers, so a navigation that does happen commits and is seen
      if (u.hostname === "example.invalid") return route.fulfill({ status: 200, contentType: "text/html", body: "<title>elsewhere</title>elsewhere" });
      if (u.hostname !== "romp.test") return route.fulfill({ status: 404, body: "" });
      if (u.pathname === "/viewer") return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: PAGE_HTML });
      if (u.pathname === "/dist/viewer.js") return route.fulfill({ status: 200, contentType: "application/javascript", body: js });
      if (u.pathname === "/file" && u.searchParams.get("path") === NOTE_PATH) {
        return route.fulfill({ status: 200, contentType: "text/plain; charset=utf-8", headers: { "X-Romp-Mtime-Ns": "1", "X-Romp-Text-Utf8": "1" }, body: note });
      }
      if (u.href === NOTE_URL) return route.fulfill({ status: 200, contentType: "text/markdown; charset=utf-8", body: note });
      return route.fulfill({ status: 404, body: "" });
    });
    await page.goto("http://romp.test/viewer");
    await page.waitForFunction(() => typeof (window as any).__openNote === "function", null, { timeout: 10000 });
    if (via === "url") await page.evaluate((href: string) => { (window as any).__openUrl(href); }, NOTE_URL);
    else await page.evaluate(([p, sid]: [string, string]) => { (window as any).__openNote(p, sid); }, [NOTE_PATH, SID] as [string, string]);
    // attached, not visible: a note whose <style> hides the viewer is one of the cases under test
    await page.waitForSelector("#romp-fileview .fileview-body .fileview-md", { state: "attached", timeout: 10000 });
    await body(page, errors, seen);
  } finally {
    await browser.close();
  }
}

type Rect = { left: number; top: number; right: number; bottom: number; width: number; height: number };
const near = (a: number, b: number, msg: string, tol = 1) => assert.ok(Math.abs(a - b) <= tol, msg + ": " + a + " vs " + b);

test("a file's <style> block and a position: fixed div neither hide the viewer nor cover its close button", { timeout: 60000 }, async (t) => {
  const note = [
    "# Report", "",
    "<style>.fileview, #romp-fileview { display: none !important; } body { background: red; }</style>", "",
    '<div class="fx-fixed" style="position:fixed;inset:0;background:red">cover</div>', "",
    "Prose after.", "",
  ].join("\n");
  await withNote(t, note, async (page, errors) => {
    const facts = await page.evaluate(() => {
      const md = document.querySelector("#romp-fileview .fileview-md") as HTMLElement;
      const card = document.querySelector("#romp-fileview .fileview") as HTMLElement;
      const close = document.querySelector("#romp-fileview .fileview-close") as HTMLElement;
      const fixed = md.querySelector(".fx-fixed") as HTMLElement;
      const r = close.getBoundingClientRect();
      const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return {
        styleElements: document.querySelectorAll("#romp-fileview style").length,
        cardDisplay: getComputedStyle(card).display,
        bodyBackground: getComputedStyle(document.body).backgroundColor,
        closeArea: r.width * r.height,
        atCloseCentre: at === close ? "close" : (at ? at.tagName + "." + at.className : "nothing"),
        fixedStyleAttr: fixed.getAttribute("style"),
        fixedPosition: getComputedStyle(fixed).position,
        fixedText: fixed.textContent,
      };
    });
    assert.equal(facts.styleElements, 0, "the note's <style> is gone");
    assert.notEqual(facts.cardDisplay, "none", "the note's <style> did not blank the viewer");
    assert.notEqual(facts.bodyBackground, "rgb(255, 0, 0)", "nor restyle the page");
    assert.ok(facts.closeArea > 0, "the close button has a box");
    assert.equal(facts.atCloseCentre, "close", "the element at the close button's centre is the close button, not the note's div");
    assert.equal(facts.fixedStyleAttr, null, "position/inset/background all dropped: the attribute goes");
    assert.equal(facts.fixedPosition, "static");
    assert.equal(facts.fixedText, "cover", "the div's text stays as prose");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("a form in a file is prose, and a submit inside the body never navigates the pane's document", { timeout: 60000 }, async (t) => {
  const note = [
    "# Report", "",
    '<form action="https://example.invalid/go"><button>Go</button></form>', "",
    "Prose after.", "",
  ].join("\n");
  await withNote(t, note, async (page, errors, seen) => {
    const before = page.url();
    // 1. no form and no control in the rendered note; the button's text landed as prose, and the point where it landed
    //    is prose (elementFromPoint names no control above it)
    const goAt = await page.evaluate(() => {
      const md = document.querySelector("#romp-fileview .fileview-md") as HTMLElement;
      const controls = md.querySelectorAll("form, button, input, select, textarea").length;
      const walker = document.createTreeWalker(md, NodeFilter.SHOW_TEXT);
      let node: Node | null;
      while ((node = walker.nextNode())) {
        if ((node.textContent || "").trim() === "Go") {
          const range = document.createRange(); range.selectNodeContents(node);
          const r = range.getBoundingClientRect();
          const x = r.left + r.width / 2, y = r.top + r.height / 2;
          const e = document.elementFromPoint(x, y);
          return { controls, x, y, w: r.width, h: r.height, hit: e ? e.tagName + "." + e.className : "nothing", inControl: !!(e && e.closest("form, button, input, select, textarea")) };
        }
      }
      return { controls, x: 0, y: 0, w: 0, h: 0, hit: "no Go text", inControl: false };
    });
    assert.equal(goAt.controls, 0, "no form, button, input, select or textarea in the rendered note");
    assert.ok(goAt.w > 0 && goAt.h > 0, "the form's text is laid out in the note as prose: " + JSON.stringify(goAt));
    assert.equal(goAt.inControl, false, "the point to click is prose: " + goAt.hit);
    await page.mouse.click(goAt.x, goAt.y);
    assert.equal(await page.evaluate(() => location.href), before, "location.href is unchanged after clicking where the button was");
    // 2. the submit backstop itself: a REAL form put into the note after render (the sanitizer never lets one through, so
    //    this is the only way to exercise the listener) submits nowhere, and the event reaches the document defaultPrevented
    const submit = await page.evaluate(() => {
      const md = document.querySelector("#romp-fileview .fileview-md") as HTMLElement;
      const form = document.createElement("form"); form.action = "https://example.invalid/go";
      const btn = document.createElement("button"); btn.type = "submit"; btn.textContent = "Submit"; form.appendChild(btn);
      md.insertBefore(form, md.firstChild);
      let prevented: boolean | null = null;
      document.addEventListener("submit", (ev) => { prevented = ev.defaultPrevented; }, { once: true });
      btn.click();
      form.remove();
      return { prevented, href: location.href };
    });
    assert.equal(submit.prevented, true, "the body's submit listener called preventDefault before the event reached the document");
    assert.equal(submit.href, before);
    // 3. over the whole test, from the page's own events: the document navigated once (its load), and no request left its host
    assert.deepEqual(seen.navigations, [before], "the document navigated exactly once, at its load");
    assert.deepEqual(seen.foreignRequests, [], "no request left romp.test (a submitted form would have asked example.invalid)");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("an author's id and name are prefixed, an inline style keeps only its colour, a background attribute fetches nothing, an image map is dropped, and the task checkbox is the one control left", { timeout: 60000 }, async (t) => {
  const note = [
    "# Report", "",
    'A <span class="fx-span" style="color: rgb(200, 0, 0); font-size: 80px">x</span> in prose.', "",
    'A <span class="fx-tagged" data-act="stopRetrying" data-fx="1">tagged</span> span.', "",
    '<p id="top">Top paragraph.</p>', "",
    '<a name="install"></a>', "",
    "- [x] done", "- [ ] later", "",
    '<input type="text" value="typed">', "",
    '<input type="checkbox" class="fx-hand">', "",
    "<select><option>a</option></select>", "",
    "<dialog open>hi</dialog>", "",
    "<textarea>notes</textarea>", "",
    "| Left | Centre | Right |", "|:-----|:------:|------:|", "| a    |   b    |     c |", "",
    '<table><tr><td class="fx-bg" background="https://example.invalid/px.gif">cell</td></tr></table>', "",
    '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width=120 height=40 alt="dot">', "",
    '<map name="nav"><area shape="rect" coords="0,0,10,10" href="https://example.invalid/a" alt="a"></map>', "",
    '<img class="fx-mapped" usemap="#nav" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width=100 height=30 alt="mapped">', "",
  ].join("\n");
  await withNote(t, note, async (page, errors, seen) => {
    const facts = await page.evaluate(() => {
      const md = document.querySelector("#romp-fileview .fileview-md") as HTMLElement;
      const count = (sel: string) => md.querySelectorAll(sel).length;
      const span = md.querySelector(".fx-span") as HTMLElement;
      const cell = md.querySelector(".fx-bg") as HTMLElement;
      const hand = md.querySelector(".fx-hand") as HTMLInputElement | null;
      if (hand) hand.click();
      return {
        forbidden: count("style, form, button, select, option, dialog, textarea"),
        spanStyleAttr: span.getAttribute("style"),
        spanColor: getComputedStyle(span).color,
        spanFontSize: getComputedStyle(span).fontSize,
        proseFontSize: getComputedStyle(span.parentElement as HTMLElement).fontSize,
        taggedSpans: count(".fx-tagged"),
        authorDataAttrs: count("[data-fx], .fx-tagged[data-act]"),
        userContentTop: count("p#user-content-top"),
        rawTop: count("#top"),
        userContentInstall: count('a[name="user-content-install"]'),
        rawInstall: count('a[name="install"]'),
        heading: count("h1#md-report"),
        prefixedHeadings: count('h1[id^="user-content-"], h2[id^="user-content-"]'),
        checkboxes: count('input[type="checkbox"][disabled]'),
        checked: count('input[type="checkbox"]:checked'),
        otherInputs: count('input:not([type="checkbox"])'),
        handDisabled: hand ? hand.disabled : null,
        handChecked: hand ? hand.checked : null,
        alignedCells: count('td[align="center"]'),
        cellBackgroundAttr: cell.getAttribute("background"),
        cellBackgroundImage: getComputedStyle(cell).backgroundImage,
        cellText: cell.textContent,
        sizedImg: count('img[width="120"][height="40"]'),
        dialogText: (md.textContent || "").includes("hi"),
        imageMap: count("map, area, [usemap]"),
        mappedImg: count("img.fx-mapped"),
      };
    });
    assert.equal(facts.forbidden, 0, "no style, form, button, select, option, dialog or textarea in the rendered note");
    assert.equal(facts.spanStyleAttr, "color: rgb(200, 0, 0)", "the span keeps only its colour declaration");
    assert.equal(facts.spanColor, "rgb(200, 0, 0)");
    assert.equal(facts.spanFontSize, facts.proseFontSize, "font-size: 80px was dropped: the span reads at the prose size");
    assert.equal(facts.taggedSpans, 1, "the span that wore data-* is still there");
    assert.equal(facts.authorDataAttrs, 0, "an author's data-* never rides in: the page's delegates key off data-act");
    assert.equal(facts.userContentTop, 1, "an author's id is prefixed user-content-");
    assert.equal(facts.rawTop, 0);
    assert.equal(facts.userContentInstall, 1, "and so is an author's name");
    assert.equal(facts.rawInstall, 0);
    assert.equal(facts.heading, 1, "the viewer's own heading ids are minted after the sanitize and never prefixed");
    assert.equal(facts.prefixedHeadings, 0);
    assert.equal(facts.checkboxes, 3, "the two task checkboxes and the hand-written one all survive, disabled");
    assert.equal(facts.checked, 1, "the ticked task item is still ticked");
    assert.equal(facts.handDisabled, true, "a checkbox written by hand is forced disabled");
    assert.equal(facts.handChecked, false, "and a click leaves it unchecked");
    assert.equal(facts.otherInputs, 0, "no other input survives");
    assert.equal(facts.alignedCells, 1, "table alignment (the align attribute) survives");
    assert.equal(facts.cellBackgroundAttr, null, "the background attribute is gone");
    assert.equal(facts.cellBackgroundImage, "none", "so the cell resolves to no background image");
    assert.equal(facts.cellText, "cell", "the cell's text stays");
    assert.equal(facts.sizedImg, 1, "an image's width/height survive");
    assert.ok(facts.dialogText, "the dialog's text stays as prose");
    assert.equal(facts.imageMap, 0, "no image map: <map>, <area> and the usemap attribute are all gone");
    assert.equal(facts.mappedImg, 1, "the picture the map was bound to stays");
    assert.deepEqual(seen.foreignRequests, [], "no request left romp.test: the background URL was never fetched");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("layout containment: a fixed box inside the note spans the note, not the viewport, and the body still scrolls to the end", { timeout: 60000 }, async (t) => {
  const note = ["# Report", "", FILLER, "", "Last line.", ""].join("\n");
  await withNote(t, note, async (page, errors) => {
    // a `position: fixed; inset: 0` box injected after render (a style no author can write past the hook) is contained
    // by the note: it spans the note's box, and the close button stays the element at its own centre, scrolled or not
    const contained = await page.evaluate(() => {
      const md = document.querySelector("#romp-fileview .fileview-md") as HTMLElement;
      const body = document.querySelector("#romp-fileview .fileview-body") as HTMLElement;
      const close = document.querySelector("#romp-fileview .fileview-close") as HTMLElement;
      const rect = (e: Element) => { const b = e.getBoundingClientRect(); return { left: b.left, top: b.top, right: b.right, bottom: b.bottom, width: b.width, height: b.height }; };
      const hitClose = () => { const c = rect(close); const e = document.elementFromPoint(c.left + c.width / 2, c.top + c.height / 2); return e === close ? "close" : (e ? e.tagName + "." + e.className : "nothing"); };
      body.scrollTop = 0;
      const mdAt0 = rect(md);
      const inject = document.createElement("div"); inject.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:red;";
      (md.querySelector("p") as HTMLElement).appendChild(inject);
      const injectAt0 = rect(inject);
      const hitAt0 = hitClose();
      body.scrollTop = 300;
      const scrolled = body.scrollTop;
      const hitScrolled = hitClose();
      inject.remove();
      body.scrollTop = 0;
      return { mdContain: getComputedStyle(md).contain, mdAt0, injectAt0, hitAt0, scrolled, hitScrolled };
    });
    assert.equal(contained.mdContain, "layout", ".fileview-md is layout-contained under the real sheet");
    for (const k of ["left", "top", "right", "bottom"] as const) near((contained.injectAt0 as Rect)[k], (contained.mdAt0 as Rect)[k], "an injected inset:0 fixed box spans exactly the note's box (" + k + ")");
    assert.equal(contained.hitAt0, "close", "elementFromPoint at the close button's centre is the close button, with a full-inset fixed box in the note");
    assert.ok(contained.scrolled >= 299, "the body scrolled: " + contained.scrolled);
    assert.equal(contained.hitScrolled, "close", "and still after the body scrolled under it");
    // the body still scrolls to the end of a long note: layout containment does not eat vertical overflow
    const scroll = await page.evaluate(() => {
      const body = document.querySelector("#romp-fileview .fileview-body") as HTMLElement;
      const md = document.querySelector("#romp-fileview .fileview-md") as HTMLElement;
      const last = Array.from(md.querySelectorAll("p")).pop() as HTMLElement;
      body.scrollTop = body.scrollHeight;
      const b = body.getBoundingClientRect(), l = last.getBoundingClientRect();
      return { scrollable: body.scrollHeight > body.clientHeight + 50, atEnd: body.scrollTop + body.clientHeight >= body.scrollHeight - 1,
               lastVisible: l.bottom <= b.bottom + 1 && l.top >= b.top - 1, lastText: last.textContent };
    });
    assert.ok(scroll.scrollable, "the fixture is longer than the body");
    assert.ok(scroll.atEnd, "the body scrolled to its end");
    assert.ok(scroll.lastVisible, "the last paragraph is in view at the end");
    assert.equal(scroll.lastText, "Last line.");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("content wider than the column: an inline svg, canvas or video shrinks to the column with its shape kept, a table scrolls sideways on its own, and the body has nothing to scroll to", { timeout: 60000 }, async (t) => {
  const words = Array.from({ length: 40 }, (_, i) => "word" + (i + 1)).join(" ");
  const note = [
    "# Report", "",
    '<svg class="fx-svg" width="3000" height="300" viewBox="0 0 3000 300"><rect width="3000" height="300" fill="teal"/></svg>', "",
    '<canvas class="fx-canvas" width="2500" height="100"></canvas>', "",
    '<video class="fx-video" width="2400" height="200"></video>', "",
    '<table class="fx-table"><tr><td nowrap>' + words + "</td></tr></table>", "",
    "Prose after.", "",
  ].join("\n");
  await withNote(t, note, async (page, errors) => {
    const facts = await page.evaluate(() => {
      const md = document.querySelector("#romp-fileview .fileview-md") as HTMLElement;
      const body = document.querySelector("#romp-fileview .fileview-body") as HTMLElement;
      const cs = getComputedStyle(md);
      const column = md.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      const box = (sel: string) => { const r = (md.querySelector(sel) as HTMLElement).getBoundingClientRect(); return { width: r.width, height: r.height }; };
      const table = md.querySelector(".fx-table") as HTMLElement;
      table.scrollLeft = 200;
      const tr = table.getBoundingClientRect(), br = body.getBoundingClientRect();
      return {
        column, svg: box(".fx-svg"), canvas: box(".fx-canvas"), video: box(".fx-video"),
        tableClient: table.clientWidth, tableScroll: table.scrollWidth, tableOverflowX: getComputedStyle(table).overflowX, tableScrolled: table.scrollLeft,
        tableInset: { left: tr.left - br.left, right: br.right - tr.right },
        cellBorders: getComputedStyle(md.querySelector(".fx-table td") as HTMLElement).borderLeftWidth,
        bodyClient: body.clientWidth, bodyScroll: body.scrollWidth,
      };
    });
    assert.ok(facts.column > 100 && facts.column < 3000, "the column is narrower than the fixtures: " + facts.column);
    assert.ok(facts.svg.width <= facts.column + 0.5 && facts.svg.width > facts.column - 2, "the svg fills the column, no wider: " + JSON.stringify(facts.svg));
    near(facts.svg.height, facts.svg.width * 300 / 3000, "the svg keeps its viewBox ratio as it shrinks", 1.5);
    assert.ok(facts.canvas.width <= facts.column + 0.5 && facts.canvas.width > facts.column - 2, "the canvas fills the column, no wider: " + JSON.stringify(facts.canvas));
    near(facts.canvas.height, facts.canvas.width * 100 / 2500, "the canvas keeps its own ratio", 1.5);
    assert.ok(facts.video.width <= facts.column + 0.5, "the video is no wider than the column: " + JSON.stringify(facts.video));
    // a table of the page's own (a direct child of the root) may grow out of the prose column, evenly into both gutters, but
    // never past the body's content width less the root's 18px inset (`.fileview-md > table`, both sheets; the cap is the
    // column itself until the viewer's width observer has reported), so its box stays inside the body and nothing is clipped
    assert.ok(facts.tableClient >= facts.column - 0.5 && facts.tableClient <= facts.bodyClient - 36 + 0.5, "the table's box is the column or wider, up to the body less the inset: " + facts.tableClient + " (column " + facts.column + ", body " + facts.bodyClient + ")");
    assert.ok(facts.tableInset.left >= 18 - 0.5 && facts.tableInset.right >= 18 - 0.5, "the table's box keeps the root's inset on both sides of the body: " + JSON.stringify(facts.tableInset));
    assert.ok(facts.tableScroll > facts.tableClient + 100, "the table's content runs past its box: " + facts.tableScroll + " vs " + facts.tableClient);
    assert.equal(facts.tableOverflowX, "auto", "and the table scrolls it");
    assert.ok(facts.tableScrolled > 0, "the table did scroll: " + facts.tableScrolled);
    assert.equal(facts.cellBorders, "1px", "the cell borders still collapse to the sheet's 1px");
    assert.ok(facts.bodyScroll <= facts.bodyClient, "the body has no horizontal overflow of its own: nothing is clipped out of reach");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("the URL viewer's body carries the same submit backstop: a form put into a rendered document submits nowhere", { timeout: 60000 }, async (t) => {
  const note = ["# Report", "", "Prose.", ""].join("\n");
  await withNote(t, note, async (page, errors, seen) => {
    const before = page.url();
    const facts = await page.evaluate(() => {
      const md = document.querySelector("#romp-fileview .fileview-md") as HTMLElement;
      const form = document.createElement("form"); form.action = "https://example.invalid/go";
      const btn = document.createElement("button"); btn.type = "submit"; btn.textContent = "Submit"; form.appendChild(btn);
      md.insertBefore(form, md.firstChild);
      let prevented: boolean | null = null;
      document.addEventListener("submit", (ev) => { prevented = ev.defaultPrevented; }, { once: true });
      btn.click();
      form.remove();
      return { rendered: md.querySelectorAll("h1#md-report").length, prevented, href: location.href };
    });
    assert.equal(facts.rendered, 1, "the document rendered through mdBlock in the URL viewer");
    assert.equal(facts.prevented, true, "the URL viewer's body cancelled the submit before it reached the document");
    assert.equal(facts.href, before);
    assert.deepEqual(seen.navigations, [before], "the document navigated exactly once, at its load");
    assert.deepEqual(seen.foreignRequests, [], "no request left romp.test");
    assert.deepEqual(errors, [], "no page errors");
  }, "url");
});
