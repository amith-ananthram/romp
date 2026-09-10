// A fence's text as the FILE holds it, for the viewer's Copy button. marked's lexer rewrites the source before it tokenizes
// anything: CR and CRLF become LF (Lexer.lex), and every run of tabs that begins a line, after any spaces, becomes four
// spaces a tab (Lexer.blockTokens, `^( *)(\t+)`), fences included. So a code token's `text`, the code element's textContent
// and the `raw` string mdBlock captures from it all carry spaces where the file has tabs, and Copy on a Makefile recipe or a
// tab-indented Go fence pasted back as spaces (make: "missing separator"); the Raw view of the same file shows the tabs. The
// lexer keeps no offsets, so this module finds each code token's lines in marked's own view of the source and reads them
// back through the rewrite:
//   - an expanded tab whose four spaces all lie in the fence's content is a tab again;
//   - one a container's indentation consumed part of (a tab-indented line inside a two-space list item: marked slices the
//     item's indent off the expanded line) is the spaces that remain, which is what CommonMark makes of a partially consumed
//     tab;
//   - a tab marked never expanded (mid-line, or right after a quote's `> `, which the blockquote strips before its inner
//     tokenization expands what is left) stays a tab.
// A fence is found by its content lines: consecutive lines of marked's view each ending in the token's line, past a prefix of
// spaces and quote markers (what the containers took), the line before them a fence opener (an indented code block has none
// and is matched by its lines alone); a fence whose text is empty by its opener and closer, since marked's text is empty for
// a fence of no lines and for one of a single blank line alike; fences are searched in document order, each from past the one
// before. A fence not found (an author's construction this reading does not follow) is null, and the caller copies the
// rendered text as before. Nothing here changes the parse: the source reaches marked untouched, and the rendered text is
// untouched too; the display's four spaces measure a tab at the sheet's `tab-size: 4`. file-view-fence-source.test.ts runs
// it over the real marked.

/** marked's code token, as mdBlock collects it from walkTokens: the text, and whether it is an indented block (no fence lines). */
export type Fence = { text: string; indented: boolean };

/** marked's view of `source` (Lexer.lex and Lexer.blockTokens: CR and CRLF to LF, then a line's leading run of tabs to four
 *  spaces a tab, one run per line, right after the line's leading spaces) and, for each character of the view, the index of
 *  the source character it stands for; the four spaces of an expanded tab share the tab's. */
export function markedView(source: string): { text: string; at: number[] } {
  let text = "";
  const at: number[] = [];
  let i = 0;
  for (;;) {
    let e = i;
    while (e < source.length && source[e] !== "\n" && source[e] !== "\r") e++;
    let k = i;
    while (k < e && source[k] === " ") k++;
    let p = i;
    if (k < e && source[k] === "\t") {
      for (; p < k; p++) { text += " "; at.push(p); }
      for (; p < e && source[p] === "\t"; p++) { text += "    "; at.push(p, p, p, p); }
    }
    for (; p < e; p++) { text += source[p]; at.push(p); }
    if (e >= source.length) break;
    text += "\n"; at.push(e);
    i = e + (source[e] === "\r" && source[e + 1] === "\n" ? 2 : 1);
  }
  return { text, at };
}

/** A code element's textContent for a code token's text, before any highlight: marked's code renderer drops one trailing
 *  newline and appends one. */
export const renderedFenceText = (text: string): string => text.replace(/\n$/, "") + "\n";

/** A fence opener as marked's view shows it, with whatever containers put before it on its line: spaces, quote markers, a
 *  list marker with its space (a fence may open on a list item's first line). */
const OPENER = /^(?:[ >]|[-*+][ \t]|\d{1,9}[.)][ \t])*(?:`{3,}|~{3,})/;
/** A fence closer as marked's view shows it: the fence run, any more fence characters, spaces, and before it only what a
 *  container puts there (spaces, quote markers; a list marker would open an item, not close a fence). */
const CLOSER = /^[ >]*(?:`{3,}|~{3,})[`~]* *$/;
/** The lexer's rewrite over one line, as an inner blockTokens (a quote's, a list item's) applies it to the text it was handed. */
const expandLeading = (s: string): string => s.replace(/^( *)(\t+)/, (_, l: string, t: string) => l + "    ".repeat(t.length));

/** Where the token's line `want` begins inside view line `line`: past the shortest prefix of spaces and quote markers from
 *  which the rest of the line, its own leading tabs expanded as an inner tokenization would expand them, is `want`; -1 when
 *  no such prefix exists. */
function contentStart(line: string, want: string): number {
  for (let b = 0; b <= line.length; b++) {
    if (expandLeading(line.slice(b)) === want) return b;
    if (line[b] !== " " && line[b] !== ">") break;
  }
  return -1;
}

/** The source text behind view indices [s, e): an expanded tab's spaces (they share a source index, and the source holds a
 *  tab there) read as the tab when all four lie in the span and as the spaces that remain when the span begins inside them;
 *  every other character as the view has it. */
function readBack(view: { text: string; at: number[] }, source: string, s: number, e: number): string {
  let out = "";
  for (let i = s; i < e;) {
    const a = view.at[i];
    if (view.text[i] === " " && source[a] === "\t") {
      let j = i + 1;
      while (j < e && view.at[j] === a) j++;
      out += j - i === 4 ? "\t" : " ".repeat(j - i);
      i = j;
    } else { out += view.text[i]; i++; }
  }
  return out;
}

/** For each code token, in document order, its text as the source holds it, in the renderer's shape (renderedFenceText), or
 *  null when its lines were not found in marked's view of the source. */
export function fenceSources(source: string, fences: Fence[]): (string | null)[] {
  const view = markedView(source);
  const lines: [number, number][] = [];
  for (let s = 0, i = 0; i <= view.text.length; i++) {
    if (i === view.text.length || view.text[i] === "\n") { lines.push([s, i]); s = i + 1; }
  }
  const lineText = (k: number): string => view.text.slice(lines[k][0], lines[k][1]);
  const out: (string | null)[] = [];
  let from = 0;
  for (const f of fences) {
    if (f.text === "") {
      // marked's text is "" for a fence of no lines and for one of a single blank line alike (its content match is lazy, and
      // the renderer's dropped newline is the lexer's too), and for an opener the file ends on (an unclosed fence runs to
      // the end). Read as one blank content line, a fence of no lines would be searched past its own closer, and the first
      // blank line after any opener-shaped line would match: the blank first line inside the next fence, whose lines then
      // lay behind `from` and made it null, so its Copy pasted marked's spaces. So the empty text is matched as its lines
      // are: an opener, then a closer, or a blank line and a closer, or a blank line the file ends on. (An indented block's
      // text is never empty: the lexer takes blank lines as space before its code rule sees them.)
      let end = -1;
      for (let j = from; j < lines.length && end < 0; j++) {
        if (j === 0 || !OPENER.test(lineText(j - 1))) continue;
        if (CLOSER.test(lineText(j))) end = j + 1;
        else if (contentStart(lineText(j), "") >= 0) {
          if (j + 1 === lines.length) end = j + 1;
          else if (CLOSER.test(lineText(j + 1))) end = j + 2;
        }
      }
      if (end < 0) { out.push(null); continue; }
      out.push(renderedFenceText(""));
      from = end;
      continue;
    }
    const want = f.text.split("\n");
    let found = -1;
    for (let j = from; j + want.length <= lines.length && found < 0; j++) {
      if (!f.indented && (j === 0 || !OPENER.test(lineText(j - 1)))) continue;
      let ok = true;
      for (let i = 0; i < want.length && ok; i++) ok = contentStart(lineText(j + i), want[i]) >= 0;
      if (ok) found = j;
    }
    if (found < 0) { out.push(null); continue; }
    const got = want.map((w, i) => { const [s, e] = lines[found + i]; return readBack(view, source, s + contentStart(lineText(found + i), w), e); });
    out.push(renderedFenceText(got.join("\n")));
    from = found + want.length;
  }
  return out;
}

/** What mdBlock's fence pass consumes: for each rendered text (a code element's textContent before the highlight), the source
 *  texts of the code tokens that render as it, in document order, null for one not found, so a code element takes the next
 *  entry under its own text and identical fences stay in step. Empty when the source holds no tab and no CR: marked's view is
 *  then the source itself and every token's text is already the file's. */
export function fenceCopyQueue(source: string, fences: Fence[]): Map<string, (string | null)[]> {
  const queue = new Map<string, (string | null)[]>();
  if (!fences.length || !/[\t\r]/.test(source)) return queue;
  const sources = fenceSources(source, fences);
  fences.forEach((f, i) => {
    const key = renderedFenceText(f.text);
    const list = queue.get(key);
    if (list) list.push(sources[i]); else queue.set(key, [sources[i]]);
  });
  return queue;
}
