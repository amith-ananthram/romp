// The chat's markdown grammar, in ONE place, and the renderer for the user's OWN words.
//
// Two marked configurations render the chat, and they must agree on everything except line breaks:
//   - the shared `marked` singleton (render.ts, file-view.ts) — `breaks: false`: assistant output is real
//     markdown, where a single newline is a soft wrap and a paragraph break takes a blank line;
//   - `userMarked` below — `breaks: true`: what the user typed is not authored markdown. Shift+Enter in the
//     composer means "new line", and the bubble must show the line the person made (the user 2026-09-06:
//     a multi-line message ran together into one paragraph once it reached the chat). marked's `breaks`
//     option turns each newline inside a paragraph into <br> and touches nothing else — fenced code keeps
//     its literal newlines, lists stay lists, a blank line is still a paragraph break.
// Both take the SAME extensions from `chatMdExtensions`, so a user message with math or strikethrough
// renders exactly as it did before — only its newlines are kept. Pure (no DOM): the executed tests import
// it directly, the way render-math.test.ts exercises math.ts. Sanitizing is the caller's job: render.ts's
// userMd() runs the output through the same sanitizer md() uses (sanitizeMd, md-sanitize.ts) before it ever
// reaches innerHTML; the math fill rides that sanitize as a registered post-pass (below), so no caller
// renders it by hand.
import { Marked, type MarkedExtension } from "marked";
import { mathBlock, mathInline, renderMathPlaceholders } from "./math";
import { registerMdPostPass } from "./md-sanitize";
import { mdWikiExtensions } from "./md-wiki";   // [[wikilinks]] as text, callouts as labelled blockquotes (T351)

// Strikethrough requires DOUBLE tildes (the user 2026-06-26). marked's built-in GFM `del` tokenizer also
// fires on a SINGLE tilde, so prose like "near the ~21 Wh/day budget … gives ~1.5–2 days" renders as one big
// <del> struck through from the first ~ to the second. GitHub itself only strikes ~~double~~, so match that:
// a lone ~ (commonly "approximately") stays literal. Returning undefined lets marked treat the ~ as text.
export const delDoubleTilde = {
  tokenizer: {
    del(src: string) {
      const m = /^~~(?=\S)([\s\S]*?\S)~~/.exec(src);
      if (!m) return undefined;
      return { type: "del", raw: m[0], text: m[1], tokens: (this as { lexer: { inlineTokens(s: string): unknown[] } }).lexer.inlineTokens(m[1]) };
    },
  },
} as MarkedExtension;

// TeX math ($..$, $$..$$, \(..\), \[..\]) rendered via KaTeX. All delimiter heuristics (the
// $-vs-shell/price disambiguation) live in math.ts. The extensions emit an inert placeholder (the TeX as
// text under md-math-inline / md-math-display), and KaTeX is rendered into it AFTER the sanitizer (math.ts
// renderMathPlaceholders), because sanitizeMd keeps only colour in an inline style and KaTeX's layout is all
// inline style. The fill is registered here, once, as a sanitizeMd post-pass: the grammar and its fill travel
// together, so every sanitizeMd call in a bundle that parses with this grammar renders math (the chat's md()
// and userMd(); the viewer's mdBlock when it runs inside the chat page, whose marked singleton render.ts
// arms with these extensions), and a bundle that never imports this module (feed.js) has neither the
// grammar nor KaTeX.
export const chatMdExtensions: MarkedExtension[] = [delDoubleTilde, { extensions: [mathBlock, mathInline] }, ...mdWikiExtensions];
registerMdPostPass(renderMathPlaceholders);

// The user-text instance: the chat grammar with hard line breaks. Its own `Marked` so the singleton's
// `breaks: false` — every assistant message — is untouched.
export const userMarked = new Marked({ gfm: true, breaks: true }, ...chatMdExtensions);

/** marked's HTML for the user's own typed text: newlines kept as <br>, otherwise the chat grammar.
 *  UNSANITIZED: render.ts's userMd() is the only caller that reaches innerHTML; it purifies first, and the
 *  sanitize renders the math placeholders (the post-pass registered above) on the sanitized DOM. */
export function userMdHtml(src: string): string {
  return userMarked.parse(src) as string;
}
