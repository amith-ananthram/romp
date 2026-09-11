// The quick-pick overlay behind BOTH shell hotkeys — Obsidian's shape: a card centered near
// the top, a type-ahead input, fuzzy-filtered rows with highlighted matches, arrows + Enter,
// Esc or a backdrop click to close. Cmd/Ctrl+P opens it over the command registry; Cmd/Ctrl+O
// opens it over the sessions (the jump switcher — palette-main.ts builds those items). It
// lives in the SHELL document, so like the network panel it composites over the real panes:
// one centered card, the standard 0.55 dim, and the dashboard unchanged behind it (the one
// modal treatment, the user 2026-08-08).
import { commandList, PaletteCommand } from "./commands";
import { fuzzyMatch, FuzzyHit, FuzzyRange } from "./fuzzy";

// One row of a pick list. Commands map onto this 1:1; session rows wear the TAB's identity
// language (the user 2026-08-08: visual consistency across surfaces) — the name bold in the
// session color, a remote's "host:" prefix in the dim italic, exactly like .tab-label /
// .host-prefix — plus a dim directory tail. run() is the whole contract — the palette closes
// itself, then runs it.
export type PickItem = {
  title: string;    // what's shown and fuzzy-matched — for a remote session "host:name", host included
  kbd?: string;     // display-only hotkey chip ("⌘O")
  hostLen?: number; // leading chars of title that are the "host:" prefix (0/absent = local)
  color?: string;   // session identity color for the name (the tab's --chip-bg)
  dim?: string;     // dim tail after the title (a session's directory basename)
  run: () => void;
};

export type PickSpec = {
  placeholder: string;
  items: PickItem[];
  // Shift+Enter, when the mode has a secondary action (the switcher's "new session…").
  // Rendered as a footer hint so the key is discoverable, Obsidian-style.
  altEnter?: { label: string; run: () => void };
};

// The modal vocabulary the shell's panels share (#rnet-panel / #rerr-panel): #252526 card,
// 1px #3a3a3a border, radius 10, 13px system-ui body, 11px chips/dims. Injected as a <style>
// tag by ensure() so the palette ships as ONE dist bundle with no separate <link> to plumb
// through _landing. z-index 300: over every shell panel (net 200, log 210, report 220, usage 290).
const CSS =
  "#rpal-back{position:fixed;inset:0;z-index:300;display:flex;align-items:flex-start;justify-content:center;" +
  "padding:14vh 16px 16px;background:rgba(0,0,0,0.55);box-sizing:border-box}" +
  "#rpal-back[hidden]{display:none}" +
  "#rpal{width:min(560px,94%);max-height:60vh;display:flex;flex-direction:column;background:#252526;" +
  "border:1px solid #3a3a3a;border-radius:10px;box-shadow:0 12px 36px #000000aa;padding:8px;" +
  "color:#ccc;font:13px/1.6 'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;box-sizing:border-box}" +
  "#rpal-in{flex:0 0 auto;background:#1b1b1c;border:1px solid #3a3a3a;border-radius:6px;color:#e8eaed;" +
  "font:inherit;padding:7px 10px;outline:none;box-sizing:border-box;width:100%}" +
  "#rpal-in:focus{border-color:var(--accent,#9cd2ff)}" +
  "#rpal-list{flex:1 1 auto;overflow-y:auto;margin-top:6px}" +
  ".rpal-row{display:flex;align-items:center;gap:10px;padding:5px 10px;border-radius:6px;cursor:pointer}" +
  ".rpal-row.active{background:rgba(156,210,255,0.12)}" +   // accent-blue focus cue, not a status color
  ".rpal-title{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
  ".rpal-title b{color:var(--accent,#9cd2ff);font-weight:600}" +
  // the tabs' identity language, verbatim: .tab.colored .tab-label is 600-weight in the session
  // color; .host-prefix is dim italic at 0.86em (the six-rung ladder, 2026-08-26). Matched characters keep the row's own colors
  // (underline marks them) — an accent-blue <b> inside a colored name would fight the identity.
  ".rpal-name{font-weight:600}" +
  ".rpal-host{color:#9aa0a6;font-weight:400;font-style:italic;font-size:0.86em}" +
  ".rpal-name b,.rpal-host b{color:inherit;font-weight:inherit;text-decoration:underline}" +
  ".rpal-dim{flex:0 1 auto;color:#9aa0a6;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
  ".rpal-kbd{flex:0 0 auto;color:#9aa0a6;font-size:11px;border:1px solid #3a3a3a;border-radius:4px;padding:0 5px}" +
  ".rpal-empty{padding:8px 10px;color:#9aa0a6}" +
  ".rpal-hint{flex:0 0 auto;padding:4px 10px 0;color:#9aa0a6;font-size:11px}";

export type Palette = {
  open(): void;                    // command mode, over the registry (Cmd+P)
  openPick(spec: PickSpec): void;  // any mode — the session switcher passes its own items
  close(): void;
  toggle(): void;                  // command-mode toggle (Cmd+P)
  isOpen(): boolean;
};

// kbdFor computes a command's hotkey chip at OPEN time from the live keybindings store, so the
// palette always shows what the key actually does today, never a hardcoded default (the user
// 2026-08-09, with the configurable shortcuts).
export function initPalette(opts?: { onClose?: () => void; kbdFor?: (c: PaletteCommand) => string | undefined }, doc: Document = document): Palette {
  let back: HTMLElement | null = null;
  let input: HTMLInputElement;
  let list: HTMLElement;
  let hint: HTMLElement;
  let spec: PickSpec = { placeholder: "", items: [] };
  let rows: { item: PickItem; el: HTMLElement }[] = [];
  let active = 0;

  // Built once, lazily; the palette is not subject to the dashboard's re-render pushes (the
  // shell document never rebuilds), so rows stay click-safe without delegation gymnastics.
  function ensure(): void {
    if (back) return;
    const style = doc.createElement("style");
    style.textContent = CSS;
    doc.head.appendChild(style);
    back = doc.createElement("div");
    back.id = "rpal-back";
    back.hidden = true;
    const panel = doc.createElement("div");
    panel.id = "rpal";
    input = doc.createElement("input");
    input.id = "rpal-in";
    input.spellcheck = false;
    list = doc.createElement("div");
    list.id = "rpal-list";
    hint = doc.createElement("div");
    hint.className = "rpal-hint";
    hint.hidden = true;
    panel.appendChild(input);
    panel.appendChild(list);
    panel.appendChild(hint);
    back.appendChild(panel);
    doc.body.appendChild(back);
    input.addEventListener("input", () => render(input.value));
    back.addEventListener("keydown", onKey);
    back.addEventListener("click", (e) => { if (e.target === back) close(); });   // the dim, not the card
    list.addEventListener("mouseover", (e) => {
      const row = (e.target as HTMLElement).closest(".rpal-row");
      const i = rows.findIndex((r) => r.el === row);
      if (i >= 0) setActive(i);   // hover and keyboard share one active row, like the picker
    });
    list.addEventListener("click", (e) => {
      const row = (e.target as HTMLElement).closest(".rpal-row");
      const hit = rows.find((r) => r.el === row);
      if (hit) run(hit.item);
    });
  }

  function highlight(title: string, ranges: FuzzyRange[]): DocumentFragment {
    const frag = doc.createDocumentFragment();
    let at = 0;
    for (const [s, e] of ranges) {
      if (s > at) frag.appendChild(doc.createTextNode(title.slice(at, s)));
      const b = doc.createElement("b");
      b.textContent = title.slice(s, e);
      frag.appendChild(b);
      at = e;
    }
    if (at < title.length) frag.appendChild(doc.createTextNode(title.slice(at)));
    return frag;
  }

  // Clip highlight ranges to [start,end) and re-base them on start — one fuzzy match over the
  // whole "host:name" string, split across the two differently-styled spans.
  function clipRanges(ranges: FuzzyRange[], start: number, end: number): FuzzyRange[] {
    const out: FuzzyRange[] = [];
    for (const [s, e] of ranges) {
      const cs = Math.max(s, start), ce = Math.min(e, end);
      if (cs < ce) out.push([cs - start, ce - start]);
    }
    return out;
  }

  function render(query: string): void {
    const hits = spec.items
      .map((item) => ({ item, hit: fuzzyMatch(query, item.title) }))
      .filter((x): x is { item: PickItem; hit: FuzzyHit } => !!x.hit);
    hits.sort((a, b) => b.hit.score - a.hit.score);   // stable sort: ties keep the given order
    list.textContent = "";
    rows = [];
    for (const { item, hit } of hits) {
      const row = doc.createElement("div");
      row.className = "rpal-row";
      const title = doc.createElement("span");
      title.className = "rpal-title";
      const hl = item.hostLen || 0;
      if (hl > 0 || item.color) {
        // a session row: "host:" dim italic + the name bold in its identity color — the tab treatment
        if (hl > 0) {
          const h = doc.createElement("span");
          h.className = "rpal-host";
          h.appendChild(highlight(item.title.slice(0, hl), clipRanges(hit.ranges, 0, hl)));
          title.appendChild(h);
        }
        const n = doc.createElement("span");
        n.className = "rpal-name";
        if (item.color) n.style.color = item.color;
        n.appendChild(highlight(item.title.slice(hl), clipRanges(hit.ranges, hl, item.title.length)));
        title.appendChild(n);
      } else {
        title.appendChild(highlight(item.title, hit.ranges));
      }
      row.appendChild(title);
      if (item.dim) {
        const m = doc.createElement("span");
        m.className = "rpal-dim";
        m.textContent = item.dim;
        row.appendChild(m);
      }
      if (item.kbd) {
        const k = doc.createElement("span");
        k.className = "rpal-kbd";
        k.textContent = item.kbd;
        row.appendChild(k);
      }
      list.appendChild(row);
      rows.push({ item, el: row });
    }
    if (!rows.length) {
      const empty = doc.createElement("div");
      empty.className = "rpal-empty";
      empty.textContent = "No matches";
      list.appendChild(empty);
    }
    setActive(0);
  }

  function setActive(i: number): void {
    active = i;
    rows.forEach((r, j) => r.el.classList.toggle("active", j === i));
    const el = rows[i]?.el;
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
  }

  function onKey(e: KeyboardEvent): void {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); if (rows.length) setActive((active + 1) % rows.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (rows.length) setActive((active - 1 + rows.length) % rows.length); }
    else if (e.key === "Enter" && e.shiftKey && spec.altEnter) {
      e.preventDefault();
      const alt = spec.altEnter;
      close();   // close FIRST, same as run(): the secondary action opens its own surface
      alt.run();
    }
    else if (e.key === "Enter") { e.preventDefault(); const r = rows[active]; if (r) run(r.item); }
  }

  function run(item: PickItem): void {
    close();   // close FIRST: an action that opens its own modal must not land under the palette
    item.run();
  }

  function openPick(s: PickSpec): void {
    ensure();
    spec = s;
    input.placeholder = s.placeholder;
    hint.hidden = !s.altEnter;
    if (s.altEnter) hint.textContent = "↵ open · shift ↵ " + s.altEnter.label;
    back!.hidden = false;
    input.value = "";
    render("");
    input.focus();
  }
  function open(): void {
    openPick({
      placeholder: "Type a command…",
      // hidden commands are bindable but not listed (palette.toggle from the palette just blinks it)
      items: commandList().filter((c) => !c.hidden && (!c.when || c.when()))
        .map((c) => ({ title: c.title, kbd: opts && opts.kbdFor ? opts.kbdFor(c) : undefined, run: c.run })),
    });
  }
  function close(): void {
    if (!back || back.hidden) return;
    back.hidden = true;
    if (opts && opts.onClose) opts.onClose();
  }
  function isOpen(): boolean { return !!back && !back.hidden; }
  function toggle(): void { if (isOpen()) close(); else open(); }

  return { open, openPick, close, toggle, isOpen };
}
