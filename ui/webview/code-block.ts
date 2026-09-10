// Fenced code, dressed the same wherever it renders: the per-line rows that make a soft-wrap read distinctly from a real
// newline (the user 2026-06-16) and the automatic Copy button (the user 2026-06-22). The chat's highlight() (render.ts)
// and the file viewer's mdBlock (file-view.ts) both call these. The module exists because the two functions lived
// module-private in render.ts and file-view.ts cannot import render.ts: render.ts imports file-view.ts, and the feed
// bundle must not carry the chat. They moved here as they were; the chat's behaviour is unchanged.
//
//   - wrapLinesHtml is the pure walk: highlighted HTML split into lines, each wrapped in
//     `<span class="cl"><span class="ct">...</span></span>`, an hljs span that straddles a newline re-opened on the next
//     line so the markup stays valid. The NEWLINES ARE DROPPED: a row stands for its line and the CSS counter on .cl
//     draws the number. Copy never reads the rows: its caller hands addCopyBtn the text to copy, settled before the
//     rewrite. For the chat that is the code element's textContent as captured (render.ts highlight); for the viewer it is
//     the fence's text as the FILE holds it (fence-source.ts: marked expanded the file's leading tabs to spaces before the
//     textContent existed), the captured textContent only for a fence the module does not find in the file.
//   - wrapCodeLines applies it to a code element and writes the digits of its last line number on the element
//     (`--ln-digits`): the sheets' gutter basis reads it, so every row of a 10000-line block carries a five-digit gutter
//     and the text column stays one line, where a basis a single row outgrew widened that row alone.
//   - addCopyBtn parks a Copy button top-right of the <pre>, idempotent (a re-render can run it again), copying the text
//     it was given: the on-screen textContent lost its newlines to the wrap and is not copy-safe.
//   - copyText: the async Clipboard API with a hidden-textarea execCommand fallback.
//
// The Copy button's action stays on the button. Delegating it to a stable ancestor would change nothing for the one way
// a press on it is lost, a surface that REPLACES the fence while the pointer is down: a pressed node removed before the
// mouseup dispatches no click at all, to the button or to any ancestor. The chat does that with no gesture behind it: a
// card re-renders on a kernel push, and a press on its fence's Copy can be under way when it does. The viewer's fences are
// replaced only by the reader's own clicks (the Rendered/Raw toggle rebuilds the body, Reload the whole viewer), which
// cannot overlap a press, but can fall between a press and the clipboard's answer. Two things follow:
//   - A KEYBOARD press has the same window: a button's Space activation is native to the keyup (Enter's to the keydown),
//     so a rebuild between the keydown and the keyup took the focused button and the keyup clicked nothing. Space acts on
//     the KEYDOWN here, as Enter does, with the key's default prevented on the keydown (the button is never put :active
//     by the key, so its keyup dispatches no click) and on the keyup; a held key's repeats copy nothing more.
//   - The acknowledgement (the label reads Copied, or Copy failed, for about 1.2s) goes to the button ON SCREEN at the
//     fence, not to the node the click closed over: the Clipboard API answers asynchronously, a rebuild inside that window
//     swaps the fence and its button out, and a label written to the pressed node showed on nothing while the copy itself
//     had succeeded. The fence is known by its SOURCE, the text its Copy copies: the button acknowledged is the one of the
//     fenced block with the pressed fence's source under the nearest ancestor still in the document (the ancestors are
//     read at the press, while the button is in the document), and a swap inside the window carries the label to the
//     button that replaced it on the swap's own event (a MutationObserver on the ancestors' child lists, alive for the
//     window). Of several fences with that source, the one at the pressed fence's ordinal among them, so a rebuild that
//     put a fence above the pressed one, or removed the pressed one, never marks a neighbour. A fence the swap did not
//     bring back, its text rewritten or the fence removed, has nothing to write on: the clipboard holds the text and no
//     button on screen claims it; a surface swapped out whole (the viewer closed or replaced) is nothing to acknowledge
//     on, so the walk stops under document.body. code-block.test.ts runs both over a DOM stand-in.
//
// The sheets: the chat's unscoped `pre code .cl` / `.ct` / `.code-copy` rules (styles.css) and the viewer's scoped
// `.fileview-md` copies in styles.css and feed.css (the feed page loads only feed.css), byte-equal (code-block.test.ts,
// fileview-parity.test.ts).

const el = (tag: string, cls: string): HTMLElement => { const e = document.createElement(tag); e.className = cls; return e; };

/** Highlighted HTML as per-line rows: `<span class="cl"><span class="ct">...</span></span>` per line, spans re-balanced
 *  across the line break, the newlines dropped. A trailing newline is not a blank line. */
export function wrapLinesHtml(html: string): string {
  const lines = html.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();   // a trailing newline isn't a blank line
  let open: string[] = [];
  return lines.map((ln) => {
    const prefix = open.join("");
    const re = /<span[^>]*>|<\/span>/g; let m; const stack = open.slice();
    while ((m = re.exec(ln))) { if (m[0] === "</span>") stack.pop(); else stack.push(m[0]); }
    const suffix = "</span>".repeat(Math.max(0, stack.length));
    open = stack;
    return `<span class="cl"><span class="ct">${prefix}${ln}${suffix}</span></span>`;
  }).join("");
}

/** Wrap each logical line of a (highlighted or plain) code element in the rows above, and write the digits of its last
 *  line number on the element (`--ln-digits`) for the sheets' gutter basis. The rows are the element's children, so
 *  childElementCount is the line count; both callers wrap after any sanitize, so the inline property stands. */
export function wrapCodeLines(code: HTMLElement): void {
  code.innerHTML = wrapLinesHtml(code.innerHTML);
  code.style.setProperty("--ln-digits", String(String(code.childElementCount).length));
}

// Copy text to the clipboard, falling back to a hidden-textarea execCommand when the async Clipboard API
// is unavailable (it needs a secure context; localhost counts, but stay safe). Returns whether it copied.
export function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).then(() => true, () => fallbackCopy(text));
  }
  return Promise.resolve(fallbackCopy(text));
}
function fallbackCopy(text: string): boolean {
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.top = "-9999px"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.focus(); ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}

// The fence the press was on, for the acknowledgement after a swap (the header): its SOURCE, the text its Copy copies,
// and under each ancestor below document.body its ordinal among the fenced blocks there with that source. After a swap
// the first ancestor still in the document is the surface that kept its place, and the fenced block under it with the
// pressed fence's source is the same fence wherever the rebuild moved it; the ordinal decides only between fences of one
// source, which nothing else tells apart. The walk stops under document.body: a surface swapped out whole is nothing to
// acknowledge on.
type Slot = { source: string; at: { root: Element; nth: number }[] };
const FENCED = "pre.has-copy";
/** Each fenced block's source (what its Copy copies), keyed by the <pre> addCopyBtn dressed. */
const SOURCES = new WeakMap<Element, string>();
/** The fenced blocks under `root` whose source is `source`, in document order. */
const sameSource = (root: Element, source: string): Element[] => Array.from(root.querySelectorAll(FENCED)).filter((p) => SOURCES.get(p) === source);
function fenceSlot(pre: HTMLElement, source: string): Slot {
  const at: Slot["at"] = [];
  for (let a = pre.parentElement; a && a !== document.body; a = a.parentElement) {
    at.push({ root: a, nth: sameSource(a, source).indexOf(pre) });
  }
  return { source, at };
}
/** The Copy button on screen for the slot: the pressed one while it is in the document, else the button of the fenced
 *  block with the pressed fence's source under the nearest ancestor still in the document (of several, the one at the
 *  pressed fence's ordinal among them, the last when fewer stand); null when no fence with that source stands there. */
function shownButton(pressed: HTMLButtonElement, slot: Slot): HTMLButtonElement | null {
  if (pressed.isConnected) return pressed;
  const s = slot.at.find((x) => x.root.isConnected);
  if (!s) return null;
  const same = sameSource(s.root, slot.source);
  const pre = same[Math.min(s.nth, same.length - 1)];
  return pre ? (pre.querySelector(":scope > .code-copy") as HTMLButtonElement | null) : null;
}
/** The acknowledgement: Copied (green) or Copy failed on the button on screen for the slot for about 1.2s, then Copy
 *  again. A swap inside the window moves the label to the button that replaced the acknowledged one, on the swap's own
 *  event: the observer watches the child list of every ancestor in the slot (the swap replaces a child of one of them)
 *  and is disconnected when the window closes. The reset goes to the button last acknowledged; if a swap took that one
 *  too, the write lands on a detached node and the button on screen already reads Copy. */
function acknowledge(pressed: HTMLButtonElement, slot: Slot, ok: boolean): void {
  let shown: HTMLButtonElement | null = null;
  const place = (): void => {
    const b = shownButton(pressed, slot);
    if (!b || b === shown) return;
    shown = b;
    b.textContent = ok ? "Copied" : "Copy failed";
    b.classList.toggle("copied", ok);
  };
  const swaps = new MutationObserver(place);
  for (const { root } of slot.at) swaps.observe(root, { childList: true });
  place();
  window.setTimeout(() => {
    swaps.disconnect();
    if (shown) { shown.textContent = "Copy"; shown.classList.remove("copied"); }
  }, 1200);
}

// An automatic "Copy" button parked top-right of every rendered code block (the user 2026-06-22). The text to copy
// is the caller's, closed over here and never read off the block: the on-screen markup adds a line-number gutter
// and drops the newline joins, so its textContent would be wrong. The chat passes the textContent it captured
// before the highlight rewrite; the viewer passes the fence's text as the file holds it (fence-source.ts), the
// captured textContent only for a fence not found there (the header). Faint until the block is hovered; flips to
// a green "Copied" for about 1.2s on success, on the button on screen of the fence with this one's source (acknowledge
// above). Idempotent (highlight can re-run on a re-render).
export function addCopyBtn(pre: HTMLElement, raw: string): void {
  if (pre.querySelector(":scope > .code-copy")) return;
  pre.classList.add("has-copy");
  SOURCES.set(pre, raw);   // the fence's identity for the acknowledgement after a swap
  const btn = el("button", "code-copy") as HTMLButtonElement;
  btn.type = "button"; btn.textContent = "Copy"; btn.title = "copy this code block";
  const copy = (): void => {
    const slot = fenceSlot(pre, raw);   // read now, while the press found the button in the document
    copyText(raw).then((ok) => acknowledge(btn, slot, ok));
  };
  btn.addEventListener("click", (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    copy();
  });
  // Space on the keydown, not the native keyup (the header): the keydown's default prevented keeps the button out of
  // :active for the key, so the keyup clicks nothing; the keyup's prevented too, for an engine that would click anyway.
  // Repeats of a held key are the same press. Enter is left to the button: its click is on the keydown already.
  btn.addEventListener("keydown", (ev) => {
    if (ev.key !== " ") return;
    ev.preventDefault();
    if (ev.repeat) return;
    copy();
  });
  btn.addEventListener("keyup", (ev) => { if (ev.key === " ") ev.preventDefault(); });
  pre.appendChild(btn);
}
