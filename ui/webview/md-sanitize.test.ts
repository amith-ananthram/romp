// The shared markdown sanitizer's pure parts, in node. DOMPurify itself needs a window, so the sanitize call and
// the DOM post-passes are proven in headless Chromium: md-sanitize-browser.test.ts opens a note in the real file
// viewer, md-sanitize-postpass-browser.test.ts runs the chat's pipeline, md-sanitize-chat-fragment-browser.test.ts
// clicks a message's own `#` link over the real chat bundle. Here: the colour grammar an inline `style` is held to,
// the profile's forbidden tags and attributes, the hook body, the hook's install guard, and the source pins that
// make md-sanitize.ts the ONE sanitizer the dashboard has (the chat's md() and userMd(), the viewer's mdBlock).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { MD_FORBID_TAGS, MD_FORBID_ATTR, MD_PURIFY, USER_CONTENT_PREFIX, colorOnlyStyle, isLiteralColor, styleAttributeHook, installMdSanitizeHooks } from "./md-sanitize";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const read = (f: string) => fs.readFileSync(path.join(UI, f), "utf8");
const ROOT = path.resolve(UI, "..", "..");

// ── the colour grammar ──────────────────────────────────────────────────────────────────────────────

test("a literal colour: a keyword, #hex of 3 to 8 digits, or rgb/rgba/hsl/hsla over plain numeric arguments", () => {
  for (const v of ["red", "Red", "transparent", "currentcolor", "inherit", "#abc", "#abcd", "#aabbcc", "#AABBCCDD",
                   "rgb(200, 0, 0)", "rgb(200 0 0)", "rgb(200 0 0 / 50%)", "rgba(1,2,3,.5)", "rgba(1, 2, 3, 0.5)",
                   "hsl(120, 50%, 50%)", "hsl(120deg 50% 50%)", "hsla(120 50% 50% / 0.4)", "hsl(none 50% 50%)", "rgb(+10 -0 0.5)"]) {
    assert.ok(isLiteralColor(v), "accepted: " + v);
  }
});

test("not a literal colour: other functions, nested parentheses, escapes, quotes, !important, wrong arity", () => {
  for (const v of ["url(x)", "url(javascript:alert(1))", "var(--x)", "expression(alert(1))", "calc(1px)", "rgb(var(--x))",
                   "rgb(1,2)", "rgb(1,2,3,4,5)", "rgb()", "red !important", "red\"", "'red'", "#ab", "#abcdefabc", "#ggg",
                   "rgb(1,2,3)) ;", "red;color:blue", "rgb(1,2,3/**/)", "r\\65d", "rgb(1 2 3) x", "rgb(1px 2 3)", "red blue", "", " "]) {
    assert.ok(!isLiteralColor(v), "rejected: " + JSON.stringify(v));
  }
});

test("colorOnlyStyle keeps color and background-color with literal values, in order, and nothing else", () => {
  assert.equal(colorOnlyStyle("color: rgb(200, 0, 0); font-size: 80px"), "color: rgb(200, 0, 0)");
  assert.equal(colorOnlyStyle("position:fixed;inset:0;background:red"), "", "`background` is not `background-color`: the shorthand takes images and positions");
  assert.equal(colorOnlyStyle("COLOR: Red; Background-Color: #abc"), "color: Red; background-color: #abc", "property names case-folded, values as written");
  assert.equal(colorOnlyStyle("color: red !important"), "");
  assert.equal(colorOnlyStyle("color: url(javascript:alert(1))"), "");
  assert.equal(colorOnlyStyle("color: red; color: blue"), "color: red; color: blue", "a repeated property is two valid declarations");
  assert.equal(colorOnlyStyle("color"), "");
  assert.equal(colorOnlyStyle("color:"), "");
  assert.equal(colorOnlyStyle(""), "");
  assert.equal(colorOnlyStyle("background-color: rgb(0 0 0 / 50%)"), "background-color: rgb(0 0 0 / 50%)");
  assert.equal(colorOnlyStyle("color: red; background: url(x); background-color: var(--y); display: none"), "color: red");
  assert.equal(colorOnlyStyle("color: red; }; .fileview { display: none"), "color: red", "a declaration that tries to close the block is an invalid declaration");
  assert.equal(colorOnlyStyle("color: rgb(1,2,3); font-family: x; color: hsl(1 2% 3%)"), "color: rgb(1,2,3); color: hsl(1 2% 3%)");
});

// ── the hook ────────────────────────────────────────────────────────────────────────────────────────

test("the style hook rewrites a style attribute to its colours, drops it when none remain, and leaves other attributes to DOMPurify", () => {
  const kept = { attrName: "style", attrValue: "color: red; position: fixed; inset: 0", keepAttr: true };
  styleAttributeHook(kept);
  assert.deepEqual(kept, { attrName: "style", attrValue: "color: red", keepAttr: true });
  const dropped = { attrName: "style", attrValue: "position:fixed;inset:0;background:red", keepAttr: true };
  styleAttributeHook(dropped);
  assert.equal(dropped.keepAttr, false, "nothing survived: the attribute goes");
  const other = { attrName: "href", attrValue: "position:fixed", keepAttr: true };
  styleAttributeHook(other);
  assert.deepEqual(other, { attrName: "href", attrValue: "position:fixed", keepAttr: true }, "not a style attribute: untouched (DOMPurify's own URI rules apply)");
  const upper = { attrName: "style", attrValue: "COLOR: #fff", keepAttr: true };
  styleAttributeHook(upper);
  assert.equal(upper.attrValue, "color: #fff");
});

test("installMdSanitizeHooks registers ONE uponSanitizeAttribute hook however often it is called, and that hook is the style rewrite", () => {
  // The guard is module-global and never reset, so this test must be the module's FIRST installer: node runs a
  // file's tests in order, and nothing above calls installMdSanitizeHooks or sanitizeMd (which would need a window).
  const calls: { name: string; fn: Function }[] = [];
  const fake = { addHook: (name: string, fn: Function) => { calls.push({ name, fn }); } } as unknown as Parameters<typeof installMdSanitizeHooks>[0];
  installMdSanitizeHooks(fake);
  installMdSanitizeHooks(fake);
  installMdSanitizeHooks(fake);
  assert.equal(calls.length, 1, "idempotent: a second registration would run the rewrite twice per attribute");
  assert.equal(calls[0].name, "uponSanitizeAttribute");
  const ev = { attrName: "style", attrValue: "font-size: 80px; color: rgb(200, 0, 0)", keepAttr: true, allowedAttributes: {}, forceKeepAttr: undefined };
  calls[0].fn.call(fake, {} as Element, ev, {});
  assert.equal(ev.attrValue, "color: rgb(200, 0, 0)");
  assert.equal(ev.keepAttr, true);
});

// ── the profile ─────────────────────────────────────────────────────────────────────────────────────

test("the profile: html + svg, data: on img, no data-*, the forbidden tags, prefixed ids and names; input stays for the task checkbox", () => {
  assert.deepEqual(MD_PURIFY.USE_PROFILES, { html: true, svg: true });
  assert.deepEqual(MD_PURIFY.ADD_DATA_URI_TAGS, ["img"]);
  assert.equal(MD_PURIFY.ALLOW_DATA_ATTR, false);
  assert.equal(MD_PURIFY.SANITIZE_NAMED_PROPS, true, "an author's id and name are prefixed user-content- (GitHub's rule), never dropped");
  assert.equal(USER_CONTENT_PREFIX, "user-content-", "the prefix DOMPurify writes; the lookups compare against it");
  assert.deepEqual(MD_PURIFY.FORBID_TAGS, [...MD_FORBID_TAGS]);
  for (const tag of ["style", "dialog", "form", "button", "select", "option", "optgroup", "textarea", "fieldset", "legend", "label", "datalist", "output", "meter", "progress", "map", "area"]) {
    assert.ok(MD_FORBID_TAGS.includes(tag), tag + " is forbidden");
  }
  assert.ok(!MD_FORBID_TAGS.includes("input"), "input is allowed by the profile; sanitizeMd's post-pass keeps only a disabled checkbox");
  assert.ok(!MD_FORBID_TAGS.includes("details") && !MD_FORBID_TAGS.includes("summary"), "details/summary are prose structure GitHub keeps");
  assert.deepEqual(MD_PURIFY.FORBID_ATTR, [...MD_FORBID_ATTR]);
  assert.deepEqual([...MD_FORBID_ATTR], ["background", "usemap"], "two forbidden attributes: a background image fetches on render with no click; usemap binds an image map, which is dropped");
});

// ── source pins: one sanitizer ──────────────────────────────────────────────────────────────────────

test("md-sanitize.ts holds the dashboard's ONLY DOMPurify.sanitize call; render.ts and file-view.ts import sanitizeMd and no dompurify of their own", () => {
  const sources = fs.readdirSync(UI).filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts") && !f.endsWith(".d.ts"));
  const callers = sources.filter((f) => /DOMPurify\.sanitize\(/.test(read(f)));
  assert.deepEqual(callers, ["md-sanitize.ts"], "every other module goes through sanitizeMd");
  const SAN = read("md-sanitize.ts");
  assert.equal((SAN.match(/DOMPurify\.sanitize\(/g) || []).length, 1);
  assert.match(SAN, /export function sanitizeMd\(dirty: string\): HTMLElement \{\n\s*installMdSanitizeHooks\(\);\n\s*const clean = DOMPurify\.sanitize\(dirty, \{ \.\.\.MD_PURIFY, RETURN_DOM: true \}\) as HTMLElement;/,
    "the hook is installed before the first sanitize, and the profile is spread with RETURN_DOM");
  assert.match(SAN, /keepOnlyInertCheckboxes\(clean\);\n\s*for \(const pass of postPasses\) pass\(clean\);\n\s*return clean;/, "the input post-pass, then every registered post-pass (the math fill), on the sanitized DOM before it is handed back");
  const importers = sources.filter((f) => /from "dompurify"/.test(read(f)));
  assert.deepEqual(importers, ["md-sanitize.ts"]);
  assert.match(read("render.ts"), /import \{[^}]*\bsanitizeMd\b[^}]*\} from "\.\/md-sanitize";/);
  assert.match(read("file-view.ts"), /import \{ sanitizeMd \} from "\.\/md-sanitize";/);
  assert.equal((read("render.ts").match(/sanitizeMd\(/g) || []).length, 4, "md(), userMd(), and the file preview card's markdown (on the inert DOM, previewMdClean) and provider HTML (T351)");
  assert.equal((read("file-view.ts").match(/sanitizeMd\(/g) || []).length, 1, "mdBlock");
});

test("the math fill is registered as a sanitizeMd post-pass by the module that installs the grammar, so no renderer calls it by hand", () => {
  const grammar = read("chat-md.ts");
  assert.match(grammar, /import \{ mathBlock, mathInline, renderMathPlaceholders \} from "\.\/math";/);
  assert.match(grammar, /import \{ registerMdPostPass \} from "\.\/md-sanitize";/);
  assert.match(grammar, /^registerMdPostPass\(renderMathPlaceholders\);$/m);
  assert.doesNotMatch(read("render.ts"), /renderMathPlaceholders\(|from "\.\/math"/, "render.ts renders no math of its own: the fill rides every sanitizeMd call");
  assert.doesNotMatch(read("file-view.ts"), /renderMathPlaceholders|from "\.\/math"|from "\.\/chat-md"/, "the viewer imports no grammar: in the chat page the singleton carries it, and a bundle without it renders no math");
});

test("the feed bundle carries the sanitizer but neither the math grammar nor KaTeX: a bundle without the grammar has no fill", () => {
  // What the import pins above promise, checked on the built graph: esbuild's metafile lists every module the feed
  // entry pulls in. file-view.ts brings md-sanitize.ts (the viewer renders notes in the feed page too); chat-md.ts,
  // math.ts and the katex package are the chat bundle's alone.
  const EXT = process.cwd();                                      // npm test runs in vscode-extension
  const esbuild = createRequire(path.join(EXT, "package.json"))("esbuild");   // the extension's esbuild, wherever this bundle was written
  const r = esbuild.buildSync({
    entryPoints: [path.join(UI, "feed.ts")], bundle: true, write: false, metafile: true, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(EXT, "node_modules")], external: ["*.png", "*.svg", "*.woff", "*.ttf", "../media/*.woff2"], logLevel: "silent",
  });
  const inputs = Object.keys(r.metafile.inputs as Record<string, unknown>).map((f) => f.replace(/\\/g, "/"));
  assert.ok(inputs.some((f) => f.endsWith("ui/webview/md-sanitize.ts")), "the feed bundle sanitizes through md-sanitize.ts (via file-view.ts)");
  assert.ok(inputs.some((f) => f.endsWith("ui/webview/file-view.ts")));
  const stray = inputs.filter((f) => /ui\/webview\/(chat-md|math)\.ts$|node_modules\/katex\//.test(f));
  assert.deepEqual(stray, [], "no grammar, no fill, no KaTeX in the feed bundle");
});

test("the submit backstop: one preventDefault listener on the viewer body in openFileView AND openUrlView, installed before any render swaps the body's children", () => {
  const VIEW = read("file-view.ts");
  const local = VIEW.split("export function openFileView(")[1].split("\nexport function ")[0];
  const url = VIEW.split("export function openUrlView(")[1].split("\nexport function ")[0];
  for (const [name, fn] of [["openFileView", local], ["openUrlView", url]] as const) {
    assert.equal((fn.match(/body\.addEventListener\("submit", \(ev\) => \{ ev\.preventDefault\(\); \}\);/g) || []).length, 1, name + " installs the backstop once per open");
    assert.ok(fn.indexOf('body.addEventListener("submit"') < fn.indexOf("body.replaceChildren("), name + ": installed before any render swaps the body's children");
  }
});

test("contain: layout on .fileview-md in BOTH sheets, byte-equal, with the media caps and the table scroll; the rules are in the parity list", () => {
  const rule = (css: string, head: string) => { const at = css.indexOf(head); assert.ok(at >= 0, head + " present"); return css.slice(at, css.indexOf("}", at) + 1); };
  const chat = rule(read("styles.css"), ".fileview-md {"), feed = rule(read("feed.css"), ".fileview-md {");
  assert.equal(chat, feed);
  assert.match(chat, /contain: layout;/);
  assert.doesNotMatch(chat, /contain: (paint|strict|content|size)/, "layout only: paint containment would clip the body's scroll and a table's own horizontal scroll");
  const MEDIA = ":where(.fileview-md) svg, :where(.fileview-md) canvas, :where(.fileview-md) video {";
  assert.equal(rule(read("styles.css"), MEDIA), rule(read("feed.css"), MEDIA));
  assert.match(rule(read("styles.css"), MEDIA), /max-width: 100%;/);
  // a table wider than the column scrolls on its own: under layout containment the body cannot scroll to it
  const TABLE = ".fileview-md table {";
  assert.equal(rule(read("styles.css"), TABLE), rule(read("feed.css"), TABLE));
  assert.match(rule(read("styles.css"), TABLE), /display: block; width: max-content; max-width: 100%; overflow-x: auto;/);
  const parity = read("fileview-parity.test.ts");
  assert.match(parity, /"\.fileview-md \{"/);
  assert.ok(parity.includes(JSON.stringify(MEDIA)), "the media cap is pinned byte-equal too");
  assert.ok(parity.includes(JSON.stringify(TABLE)), "and the table rule");
});

// ── the documents ───────────────────────────────────────────────────────────────────────────────────

/** A phrase as a document wraps it: any run of whitespace between words. */
const prose = (words: string) => new RegExp(words.trim().split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+"));

test("the guide says what a file's own HTML may do, in the terms the code enforces", () => {
  const guide = fs.readFileSync(path.join(ROOT, "docs", "guide.md"), "utf8");
  const at = guide.indexOf("**A file's own HTML.**");
  assert.ok(at >= 0, "the guide has the paragraph");
  const para = guide.slice(at, guide.indexOf("\n\n", at));
  assert.match(para, /`<style>`/);
  assert.match(para, /user-content-/);
  assert.match(para, /`color` and `background-color`/);
  assert.match(para, /`background=`/);
  assert.match(para, prose("cannot be ticked"));
  assert.doesNotMatch(para, /\u2014/, "no em dash");
});

test("SECURITY.md's output-sanitization bullet names KaTeX as the renderer that writes after DOMPurify, under trust: false", () => {
  // KaTeX's markup used to be part of marked's output and went through DOMPurify with the rest. The fill now
  // renders AFTER the sanitizer (renderMathPlaceholders writes katex.render's DOM into the sanitized body), so
  // what keeps a formula from minting a link or a style is KaTeX's `trust: false`, not DOMPurify. SECURITY.md is
  // the document the repository points security readers at; its bullet says so.
  const security = fs.readFileSync(path.join(ROOT, "SECURITY.md"), "utf8");
  const hardened = security.slice(security.indexOf("\n## What is already hardened\n"));
  const at = hardened.indexOf("- **Output sanitization:**");
  assert.ok(at >= 0, "SECURITY.md's hardened list has an Output sanitization bullet");
  const rest = hardened.slice(at);
  const end = rest.indexOf("\n- ", 1);
  const bullet = end === -1 ? rest : rest.slice(0, end);
  assert.match(bullet, /KaTeX/, "the bullet names the renderer that writes to the sanitized DOM after DOMPurify");
  assert.match(bullet, prose("after DOMPurify has run"), "and says when it writes");
  assert.match(bullet, /`trust: false`/, "and the option its safety rests on");
  assert.match(bullet, prose("checked against the code by `ui/webview/md-sanitize-postpass-browser.test.ts`"), "and where that is checked, by path");
  assert.doesNotMatch(bullet, /\u2014/, "the bullet's prose carries no em dash");
  // the code has the boundary the bullet describes: DOMPurify first, the registered fill on its output, under the option
  const san = read("md-sanitize.ts");
  assert.ok(san.indexOf("DOMPurify.sanitize(dirty") < san.indexOf("for (const pass of postPasses) pass(clean);"), "the fill runs on the DOM DOMPurify has already returned");
  assert.match(read("math.ts"), /trust: false/, "math.ts renders under trust: false");
});
