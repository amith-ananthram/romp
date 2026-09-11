// Two small additions to the shared markdown grammar (T351 stage 1, for the lab team's glossary files, which
// arrive with Obsidian's conventions): a [[wikilink]] renders as its plain text, and a callout blockquote
// (`> [!NOTE] title`) renders as a blockquote with the callout's kind as a small label. Pure marked extensions
// (no DOM), applied to every bundle's `marked` singleton that renders documents: the chat's grammar (chat-md.ts
// chatMdExtensions) and the file viewer's (file-view.ts), so the same file reads the same in the chat's preview
// popover, in a message and in the viewer. Executed in md-wiki.test.ts.
import type { MarkedExtension } from "marked";

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** `[[Page Name]]` → "Page Name"; `[[Page Name|alias]]` → "alias". Text, never a link: a wikilink names a page in
 *  a vault the chat cannot open, and a dead link is worse than the words. */
export const wikilinkText = {
  extensions: [{
    name: "wikilinkText",
    level: "inline",
    start(src: string) { const i = src.indexOf("[["); return i < 0 ? undefined : i; },
    tokenizer(src: string) {
      const m = /^\[\[([^\]|\n]+?)(?:\|([^\]\n]+?))?\]\]/.exec(src);
      if (!m) return undefined;
      return { type: "wikilinkText", raw: m[0], text: (m[2] || m[1]).trim() };
    },
    renderer(tok: { text: string }) { return escapeHtml(tok.text); },
  }],
} as unknown as MarkedExtension;

/** A blockquote whose first line opens with `[!KIND]` (a callout: NOTE, TIP, WARNING, …, with an optional fold mark
 *  `+`/`-` and an optional title on the same line) stays a blockquote; the marker becomes a small label naming the
 *  kind (`.md-callout-label`, plus `.md-callout-<kind>`), and the rest of the line stays as the first paragraph. The
 *  label is an `html` token, which marked emits verbatim and the sanitizer keeps (a div with classes). */
export const calloutBlockquote = {
  walkTokens(token: { type: string; tokens?: unknown[] }) {
    if (token.type !== "blockquote" || !Array.isArray(token.tokens) || !token.tokens.length) return;
    const para = token.tokens[0] as { type: string; tokens?: unknown[]; raw?: string; text?: string };
    if (para.type !== "paragraph" || !Array.isArray(para.tokens) || !para.tokens.length) return;
    const first = para.tokens[0] as { type: string; text?: string; raw?: string };
    if (first.type !== "text" || typeof first.text !== "string") return;
    const m = /^\[!([A-Za-z][\w-]*)\][+-]?[ \t]*/.exec(first.text);
    if (!m) return;
    const kind = m[1].toLowerCase();
    const label = kind.charAt(0).toUpperCase() + kind.slice(1);
    first.text = first.text.slice(m[0].length).replace(/^\n/, "");        // the marker, and the line break after a marker alone on its line
    if (typeof first.raw === "string") first.raw = first.raw.slice(Math.min(first.raw.length, m[0].length)).replace(/^\n/, "");
    if (!first.text.trim()) {
      para.tokens.shift();                                      // the marker stood alone on its line
      if (!para.tokens.length) token.tokens.shift();            // …and was the whole first paragraph
    }
    token.tokens.unshift({ type: "html", raw: "", pre: false, block: true,
                           text: '<div class="md-callout-label md-callout-' + escapeHtml(kind) + '">' + escapeHtml(label) + "</div>\n" });
  },
} as unknown as MarkedExtension;

export const mdWikiExtensions: MarkedExtension[] = [wikilinkText, calloutBlockquote];
