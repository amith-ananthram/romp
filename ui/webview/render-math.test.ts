// Behavior tests for the TeX math extension (math.ts). The delimiter rules are the
// load-bearing part: in chat text a bare `$` means shell variables and prices far more often
// than math, so the stay-literal cases matter as much as the rendered ones. marked runs for
// real here (plain JS, no DOM needed). The extension emits a PLACEHOLDER per formula, an
// element carrying the TeX as text, and KaTeX renders into it AFTER the sanitizer
// (renderMathPlaceholders, a DOM post-pass sanitizeMd runs), so what this file can execute is
// the placeholder contract: which text becomes a formula, which stays prose, and what the
// placeholder carries. That KaTeX's layout survives the sanitizer is executed in headless
// Chromium over the real modules by md-sanitize-postpass-browser.test.ts (a fraction stacks, a
// superscript is raised, the radical has height, and the rendered root is katex.render's own
// output byte for byte). The wiring (math.ts, chat-md.ts, render.ts) is source-pinned below.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { Marked } from "marked";
import katex from "katex";
import { mathBlock, mathInline, mathPlaceholder, MATH_INLINE_CLASS, MATH_DISPLAY_CLASS } from "./math";

const m = new Marked({ gfm: true, extensions: [mathBlock, mathInline] });
const html = (src: string) => m.parse(src) as string;
const PLACEHOLDER = /<(span|div) class="(md-math-inline|md-math-display)">([\s\S]*?)<\/\1>/g;
const unescape = (s: string) => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
/** Every placeholder in marked's output, in order: its tag, its mode and the TeX it carries. */
const formulas = (out: string) => Array.from(out.matchAll(PLACEHOLDER)).map((x) => ({ tag: x[1], display: x[2] === MATH_DISPLAY_CLASS, tex: unescape(x[3]) }));
const hasMath = (s: string) => formulas(s).length > 0;
// the options renderMathPlaceholders hands katex.render (pinned against math.ts below)
const KATEX = { throwOnError: false, output: "html", trust: false } as const;

// --- becomes a formula: marked emits the placeholder, KaTeX does not run here ---

test("inline $..$ becomes an inline placeholder carrying the TeX", () => {
  const out = html("Euler: $e^{i\\pi}+1=0$ holds.");
  assert.deepEqual(formulas(out), [{ tag: "span", display: false, tex: "e^{i\\pi}+1=0" }]);
  assert.ok(!out.includes("$e^"), "the delimiters must not leak through");
  assert.ok(!out.includes('class="katex"'), "KaTeX runs after the sanitizer, never inside marked");
  assert.equal(MATH_INLINE_CLASS, "md-math-inline");
});

test("inline \\(..\\) becomes an inline placeholder", () => {
  assert.deepEqual(formulas(html("Ratio \\(\\tfrac{a}{b}\\) here.")), [{ tag: "span", display: false, tex: "\\tfrac{a}{b}" }]);
});

test("$$..$$ and \\[..\\] become display placeholders: a span inside a paragraph, a div for a paragraph of their own", () => {
  assert.deepEqual(formulas(html("Total: $$\\sum_i x_i$$ done.")), [{ tag: "span", display: true, tex: "\\sum_i x_i" }]);
  assert.deepEqual(formulas(html("\\[x^2\\]")), [{ tag: "div", display: true, tex: "x^2" }]);
  assert.ok(!html("\\[x^2\\]").includes("<p>"), "the block form is not wrapped in a paragraph");
});

test("a multi-line $$ paragraph beats markdown's block rules", () => {
  // The "- x" line would become a <li> if block tokenization carved the formula up first.
  const out = html("$$\n- x\n$$");
  assert.deepEqual(formulas(out), [{ tag: "div", display: true, tex: "- x" }]);
  assert.ok(!out.includes("<li>"), "list rule must not fire inside display math");
});

test("the TeX is text: markup inside a formula is escaped, never emitted as HTML", () => {
  // The placeholder is the one place marked writes a formula, and the sanitizer sees it as an element
  // with text; a formula can therefore never smuggle an element past it.
  const out = html("$a<b \\& c>d$ and $<img src=x onerror=1>$");
  assert.deepEqual(formulas(out).map((f) => f.tex), ["a<b \\& c>d", "<img src=x onerror=1>"]);
  assert.ok(!out.includes("<img"), "escaped: the placeholder's text is not markup");
  assert.ok(out.includes("&lt;img src=x onerror=1&gt;"));
  assert.equal(mathPlaceholder("a<b", false, false), '<span class="md-math-inline">a&lt;b</span>');
  assert.equal(mathPlaceholder("x", true, true), '<div class="md-math-display">x</div>');
  assert.equal(mathPlaceholder("x", true, false), '<span class="md-math-display">x</span>', "display math inside a paragraph is a span: the placeholder stays in the flow it came from");
});

test("marked's output holds no KaTeX markup, no inline style and no svg: nothing for the colour-only style rule to strip", () => {
  for (const src of ["$\\frac{a}{b}$", "$$\\sum_{i=0}^{n} i^2$$", "$\\sqrt{d}$", "$x^2$", "\\[\\int_0^1 f\\]"]) {
    const out = html(src);
    assert.ok(hasMath(out), "placeholder expected for " + src + ": " + out);
    assert.doesNotMatch(out, /class="katex/, "no KaTeX markup before the sanitizer: " + out);
    assert.doesNotMatch(out, /style=/, "no inline style before the sanitizer: " + out);
    assert.doesNotMatch(out, /<svg/, "no svg before the sanitizer: " + out);
  }
});

test("closing $ may touch trailing punctuation and emphasis", () => {
  assert.ok(hasMath(html("the $x$-axis")));
  const strong = html("**$O(n)$** cost");
  assert.ok(strong.includes("<strong>") && hasMath(strong));
});

test("invalid TeX renders as flagged output under the options the post-pass uses, never a throw; trust: false mints no URL", () => {
  // renderMathPlaceholders needs a DOM (katex.render); the same options through renderToString are
  // KaTeX's same pipeline minus the node building, and the browser leg runs the real call.
  const out = katex.renderToString("\\frac{1}{", { ...KATEX, displayMode: false });
  assert.ok(out.includes("katex-error"), "throwOnError: false paints bad TeX as flagged source");
  const untrusted = katex.renderToString("\\href{javascript:alert(1)}{x}", { ...KATEX, displayMode: false });
  assert.ok(!untrusted.includes("<a ") && !untrusted.includes("javascript:"), "trust: false: no TeX command mints a URL");
});

// --- stays literal ---

const LITERAL = [
  ["prices: closer followed by a digit", "costs $5 and $10 today"],
  ["shell vars joined by a slash", "paths $HOME/$USER here"],
  ["shell vars joined by a comma (closer followed by a letter)", "set $FOO,$BAR now"],
  ["shell vars with space-preceded closers", "echo $FOO and $BAR"],
  ["opener must touch its content", "not math: $ x$ spaced"],
  ["content may not span lines", "a $x\ny$ b"],
] as const;

for (const [why, src] of LITERAL) {
  test(`literal: ${why}`, () => {
    const out = html(src);
    assert.ok(!hasMath(out), `must stay literal: ${src}\ngot: ${out}`);
    assert.ok(out.includes("$"), "the $ itself must survive as text");
  });
}

test("a code span is a wall: $ cannot pair across it", () => {
  const out = html("price $5 `a $b` end");
  assert.ok(!hasMath(out));
  assert.ok(out.includes("<code>a $b</code>"), "code span must render intact");
});

test("$..$ inside a code span or fence stays code", () => {
  const span = html("`$x$`");
  assert.ok(!hasMath(span) && span.includes("<code>$x$</code>"));
  const fence = html("```sh\necho $X and $Y$\n```");
  assert.ok(!hasMath(fence) && fence.includes("echo $X"));
});

test("escaped \\$ never opens math", () => {
  const out = html("\\$5 vs \\$10");
  assert.ok(!hasMath(out) && out.includes("$5"));
});

// --- source pins (the wiring that behavior tests can't reach) ---

const UI = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");

test("render.ts wires the math extensions into marked, through the shared chat grammar", () => {
  // chat-md.ts owns the extension list (shared with the breaks:true user-text instance, so a user
  // message with math renders exactly as before); render.ts applies it to the singleton.
  const grammar = UI("chat-md.ts");
  assert.match(grammar, /import \{ mathBlock, mathInline, renderMathPlaceholders \} from "\.\/math";/);
  assert.match(grammar, /export const chatMdExtensions: MarkedExtension\[\] = \[delDoubleTilde, \{ extensions: \[mathBlock, mathInline\] \}, \.\.\.mdWikiExtensions\];/);   // + the wikilink and callout grammar (T351)
  const src = UI("render.ts");
  assert.match(src, /import \{ chatMdExtensions, userMdHtml \} from "\.\/chat-md";/);
  assert.match(src, /marked\.use\(\.\.\.chatMdExtensions\);/);
});

test("math.ts renders KaTeX into the placeholders after the sanitizer, html-only, under trust: false", () => {
  // output: "html" means no MathML twin. KaTeX's output no longer passes through DOMPurify at all: the
  // fill writes katex.render's DOM into the sanitized body, so its inline styles and stretchy <svg>
  // glyphs (\sqrt radicals, wide accents) reach the page whole, and trust: false is what keeps a
  // formula from minting a link, an image or a style of the author's choosing. The sanitizer's svg
  // profile stays for an author's own inline SVG.
  const math = UI("math.ts");
  assert.match(math, /export function renderMathPlaceholders\(root: ParentNode\): void \{/);
  assert.match(math, /katex\.render\(tex, el, \{ displayMode: display, throwOnError: false, output: "html", trust: false \}\);/);
  assert.doesNotMatch(math, /katex\.renderToString\(/, "the string renderer is gone: marked emits placeholders, the fill renders into the DOM");
  assert.match(UI("md-sanitize.ts"), /USE_PROFILES: \{ html: true, svg: true \}/);
  assert.doesNotMatch(UI("render.ts"), /USE_PROFILES/, "render.ts spells no profile of its own");
});

test("executed: \\sqrt really does emit inline svg, the glyph the fill writes into the sanitized DOM", () => {
  const out = katex.renderToString("\\sqrt{d}", { ...KATEX, displayMode: false });
  assert.ok(out.includes('class="katex"'));
  assert.ok(out.includes("<svg"), "the radical is an inline svg even with output:html");
  assert.ok(out.includes("sqrt"), "KaTeX marks the construct");
});

test("styles.css imports the KaTeX layout css", () => {
  assert.match(UI("styles.css"), /@import "katex\/dist\/katex\.min\.css";/);
});

test("esbuild emits KaTeX woff2 fonts under dist/fonts/", () => {
  const build = fs.readFileSync(path.resolve(process.cwd(), "esbuild.js"), "utf8");
  assert.match(build, /"\.woff2": "file"/);
  assert.match(build, /assetNames: "fonts\/\[name\]-\[hash\]"/);
});

test("katex is a declared dependency", () => {
  const pkg = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), "package.json"), "utf8"));
  assert.ok(pkg.dependencies && pkg.dependencies.katex, "katex must be in dependencies");
});
