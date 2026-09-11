// The SHARED tag-lens menu (the user 2026-08-25): one component every webview surface mounts —
// the outline pane, the chat tab strip, the feed's local filter. (The timeline inlines its OWN copy
// of this menu in MENU_STYLE, since it may live in Obsidian's document and loads no modules; since
// T283b that copy builds the same chip rows from its palette's RESOLVED values — tagRow / TAG_CHIP_STYLE in
// ui/romp-timeline-view.js — and ui/timeline-tag-chips.test.ts pins the drift between the two.) The menu is
// multi-select toggles on ONE surface's lens: All a plain exclusive pick, (no tags) toggling with
// the ✓ when selected, and every name-keyed union tag as ITS OWN CHIP acting as a toggle button
// (the user 2026-09-09, T283: the pill every surface already wears, one tag per line with the chip
// at the left as the group-by-tag strip settled it; selected = full colour, unselected = faded, no
// colour change, aria-pressed on the chip) — the menu staying open across toggles (a settings
// panel, not a command). A CAPTIONED DIVIDER says which surface the selection governs
// ("filters these tabs") — the shared idiom, sub-line scale. One management entry, "Configure
// tags…", when the surface offers a route to the dialog.
//
// CROSS-PANE DISMISSAL is built in (the user's bug): sibling panes' pointer events never reach
// this document, so every pane WRITES a pointerdown echo (romp:menu-echo — the color-echo idiom)
// and every open menu LISTENS via the storage event, which fires only in OTHER same-origin panes:
// exactly the gap the local document closers can't cover. Mounting surfaces inherit the fix free.
//
// SUBMENU-LESS by design today; the caret/side rule for menus that DO expand lives with the
// model-version submenus: carets always face right (▸), expansion prefers the right side and
// falls left only when the right edge would clip (measured, never assumed).
import { TagUnion } from "./session-views";
import { TagLens, lensAll, toggleLens, lensChips } from "./tag-lens";

export interface TagMenuOpts {
  lens: () => TagLens;                       // the surface's current selection (re-read per repaint)
  unions: () => TagUnion[];                  // the name-keyed union rows (re-read per repaint)
  onApply: (l: TagLens, done: boolean) => void;  // done=true → the pick closes the menu (All)
  onConfigure?: () => void;                  // the one management entry, when the surface has a route
  /** a per-surface switch at the foot beside Configure tags… (the chat strip's "Group tabs by tag",
   *  tab groups 2026-09-04): ✓-marked when on; flips and repaints in place like the tag rows */
  groupToggle?: { label: string; on: () => boolean; toggle: () => void };
}

let echoInstalled = false;
/** Every pane writes the pointerdown echo ONCE per document — sibling panes' open menus close on it.
 *  Installed AT MODULE LOAD by the guard below, never lazily (the user 2026-08-26: a menu opened
 *  from the sessions panel stood through clicks in the chat — the chat imports this module but had
 *  never opened a menu, so its document held no writer). Exported for documents that mount no tag
 *  menu at all (the shell page's palette-main) to compose the same writer explicitly. */
export function installMenuEcho(): void {
  if (echoInstalled) return;
  echoInstalled = true;
  document.addEventListener("pointerdown", (e) => {
    // A press INSIDE a romp menu is interaction WITH that menu, never a click-away — broadcasting
    // it closes the very menu being used. The case that found this (T213, 2026-09-01): the
    // timeline lifts its menus into the SHELL document (same-origin top), so a tag-row press
    // landed there, the shell's copy of this writer echoed it, and the storage event bounced into
    // the timeline iframe, whose listener detached the row between its pointerdown and its click.
    // A human-length press (~100ms) always lost that race — the sessions pane's tag filter read
    // as dead — while an instant synthetic click still won it. The echo exists for presses on
    // pane CONTENT; menus mark themselves (data-tag-menu here, data-romp-menu in the timeline's
    // inlined mirror) and are skipped.
    const t = e.target as Element | null;
    if (t && typeof t.closest === "function" && t.closest("[data-tag-menu],[data-romp-menu]")) return;
    try { localStorage.setItem("romp:menu-echo", JSON.stringify({ t: Date.now() })); } catch { /* storage blocked */ }
  }, true);
}

let openMenu: HTMLElement | null = null;
export function closeTagMenu(): void {
  openMenu?.remove();
  openMenu = null;
}
// module-level closers, guarded: the model half of this module (and its constants) is importable
// from non-DOM contexts (the node test runner) — only a real document wires the listeners
if (typeof document !== "undefined") {
  installMenuEcho();   // the WRITER rides every bundle at load — a pane must broadcast before it ever opens a menu
  document.addEventListener("click", () => closeTagMenu());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeTagMenu(); });
  try {
    window.addEventListener("storage", (e) => { if (e.key === "romp:menu-echo" && e.newValue) closeTagMenu(); });
  } catch { /* no storage events (foreign host) — local closers still apply */ }
}

/** Open (or toggle shut) the lens menu anchored under `anchor`. The ctx-family skin — the chat
 *  pane's .ctx-menu is the reference spec (CLAUDE.md menu vocabulary), worn here through the menu
 *  TOKENS (--menu-bg/--menu-fg/--menu-border/--menu-hover + --radius-menu/--shadow-menu/--check-bg)
 *  with the dark literals as var() fallbacks — every mounting page loads styles.css or feed.css,
 *  so the light theme's block re-skins the card (T226, 2026-09-02). */
export function openTagMenu(anchor: HTMLElement, opts: TagMenuOpts): void {
  const reopen = !!openMenu && openMenu.dataset.tagMenu === "1";
  closeTagMenu();
  if (reopen) return;
  const menu = document.createElement("div");
  menu.dataset.tagMenu = "1";
  menu.setAttribute("style",
    "position:fixed;z-index:1001;min-width:180px;padding:4px;background:var(--menu-bg, #252526);" +
    "border:1px solid var(--menu-border, rgba(255,255,255,0.12));border-radius:var(--radius-menu, 6px);box-shadow:var(--shadow-menu, 0 4px 12px rgba(0,0,0,0.35));" +
    "font-size:12px;line-height:1.4;color:var(--menu-fg, #cccccc);user-select:none;");
  menu.addEventListener("click", (e) => e.stopPropagation());
  const build = () => {
    menu.textContent = "";
    const lens = opts.lens();
    // (the scope caption retired 2026-08-25 — the user: the button tooltip already names the
    // surface, so it is the ONE scope carrier and the menu opens straight onto its rows)
    // a plain row (All, (no tags), the group switch, Configure tags…): the label, the ✓ when current; the
    // tags are not rows any more but chips (below), so the colour dot the tag rows wore is gone (T283)
    const row = (label: string, current: boolean, dim?: boolean) => {
      const r = document.createElement("div");
      r.setAttribute("style", "padding:4px 22px 4px 8px;border-radius:4px;cursor:pointer;position:relative;white-space:nowrap;"
        + (dim ? "opacity:0.85;" : ""));
      r.appendChild(document.createTextNode(label));
      if (current) {
        const c = document.createElement("span");
        c.textContent = "✓";
        c.setAttribute("style", "position:absolute;right:6px;top:50%;transform:translateY(-50%);"
          + "background:var(--check-bg, #1EA1EB);color:#fff;border-radius:50%;width:13px;height:13px;font-size:9px;"
          + "font-weight:900;display:inline-flex;align-items:center;justify-content:center;line-height:1;");
      r.appendChild(c);
      }
      r.addEventListener("mouseenter", () => { r.style.background = "var(--menu-hover, rgba(255,255,255,0.09))"; });
      r.addEventListener("mouseleave", () => { r.style.background = "transparent"; });
      menu.appendChild(r);
      return r;
    };
    row("All", lensAll(lens)).addEventListener("click", () => opts.onApply({ all: true }, true));
    row("(no tags)", !lensAll(lens) && !!lens.none)
      .addEventListener("click", () => { opts.onApply(toggleLens(lens, "none"), false); build(); });
    for (const u of opts.unions()) {
      // one tag per line, the chip at the left: the chip IS the toggle (aria-pressed), full colour when
      // selected, faded when not — the same pill tagChip builds for every other surface
      const on = !lensAll(lens) && (lens.tags || []).includes(u.name);
      const r = document.createElement("div");
      r.setAttribute("style", "padding:3px 8px;border-radius:4px;cursor:pointer;white-space:nowrap;display:flex;align-items:center;");
      const chip = tagChip(u.name, u.color || null, { off: !on });
      chip.setAttribute("role", "button");
      chip.setAttribute("aria-pressed", on ? "true" : "false");
      chip.setAttribute("title", on ? "selected — click to drop it from the filter" : "click to add it to the filter");
      r.appendChild(chip);
      r.addEventListener("mouseenter", () => { r.style.background = "var(--menu-hover, rgba(255,255,255,0.09))"; });
      r.addEventListener("mouseleave", () => { r.style.background = "transparent"; });
      r.addEventListener("click", () => { opts.onApply(toggleLens(lens, { tag: u.name }), false); build(); });
      menu.appendChild(r);
    }
    if (opts.groupToggle || opts.onConfigure) {
      const s = document.createElement("div");
      s.setAttribute("style", "height:1px;margin:4px 6px;background:var(--menu-border, rgba(255,255,255,0.12));");
      menu.appendChild(s);
    }
    if (opts.groupToggle)
      row(opts.groupToggle.label, opts.groupToggle.on(), true).addEventListener("click", () => { opts.groupToggle!.toggle(); build(); });
    if (opts.onConfigure)
      row("Configure tags…", false, true).addEventListener("click", () => { closeTagMenu(); opts.onConfigure!(); });
  };
  build();
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  const mw = menu.offsetWidth || 200;
  menu.style.left = Math.max(6, Math.min(Math.round(r.left), window.innerWidth - mw - 8)) + "px";
  const mh = menu.offsetHeight || 0;
  menu.style.top = (r.bottom + 4 + mh > window.innerHeight - 8 ? Math.max(8, Math.round(r.top) - mh - 4) : Math.round(r.bottom + 4)) + "px";
  openMenu = menu;
}

/** The shared tag-icon button (the user chose a tag glyph): identical across surfaces. It wears
 * THE BUTTON OUTLINE (the user 2026-08-25, round two: the bare glyph read weird next to the feed's
 * dressed buttons) — the feed word-button's box, stated in the same literals the feed's classes
 * resolve to (--card-border, 6px radius, the #feed-foot 1px 9px padding), so a chat/outline mount
 * computes equal to the feed instance by value, not by shared class (the 678 lesson). */
export function tagMenuButton(title: string, open: (btn: HTMLElement) => void): HTMLElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.title = title;
  btn.setAttribute("style", "background:transparent;border:1px solid " + TAG_BTN_BORDER + ";"
    + "border-radius:6px;padding:4px 6px;cursor:pointer;color:#9aa0a6;display:inline-flex;align-items:center;");
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none">'
    + '<path d="M2 7.5 L7.5 2.5 H14 V9 L8.5 14 Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>'
    + '<circle cx="11" cy="5.5" r="1.2" fill="currentColor"/></svg>';
  btn.addEventListener("pointerdown", (e) => { e.preventDefault(); e.stopPropagation(); open(btn); });
  btn.addEventListener("click", (e) => e.stopPropagation());   // the click-and-hold rule: swallow the opener's own click
  return btn;
}

// THE BUTTON CONVENTION (the user 2026-08-25): at rest (All) the tag icon is GRAY and stands
// alone; narrowed, it wears the ACCENT and the chips of everything selected render beside it —
// each tag in its color, the no-tags bucket as its own chip, a dim ✕ per chip unselecting that
// one pick. One renderer, so every mount is identical by construction; the feed's footer keeps
// its class mechanics (mode: "class" — its .on/.--dim values are pinned equal to these literals).
export const TAG_BTN_GRAY = "#9aa0a6";
export const TAG_BTN_ACCENT = "#9cd2ff";   // the romp accent (--accent) — pinned equal in feed.css/styles.css
export const TAG_BTN_BORDER = "rgba(255,255,255,0.10)";   // the feed's --card-border, stated by value
export const TAG_BTN_WASH = "rgba(156,210,255,0.12)";     // the feed .on's faint accent wash, ditto

/** THE TAG CHIP (one vocabulary, T251 — the user 2026-09-07: a group header must show its tag the way
 *  the tag is shown everywhere else): the outline pill in the tag's own colour, the shape the strip's
 *  tags bar and the feed's tag chips wear. `inheritSize` drops the pill's own 0.82em for a host that
 *  already sits at the surface's sub-line size (the group header), so no em nests inside an em (the
 *  fonts rule). The uncoloured fallback is the theme's --dim (a token, so the light theme is never
 *  handed a dark gray), with the constant as the file:// fallback.
 *  `off` is the FADED state (T283): a chip standing for an unselected toggle keeps its colour and fades
 *  to TAG_CHIP_OFF_OPACITY — the class names the state for the sheets and the pins, the inline opacity
 *  paints it on a sheet-less host, the two equal by construction. */
export const TAG_CHIP_OFF_CLASS = "tag-chip-off";
export const TAG_CHIP_OFF_OPACITY = "0.45";   // pinned equal to .tag-chip-off in styles.css / feed.css
export const TAG_CHIP_STRUCK_CLASS = "tag-chip-struck";   // the sheets draw the diagonal on it (both carry the same rule)
export const TAG_CHIP_STRUCK_OPACITY = "0.7";              // lighter than the fade: the line carries "off", the colour stays readable
/** THE ONE TAG CHIP (T321, the user 2026-09-10: tags render the same everywhere, and never in bold, which is the
 *  session names' weight). Every surface that shows a tag builds it here: the tab strip's group rows and its filter
 *  chips, the feed's and the outline's filter chips, the tag-lens menu's rows, the tab menu's Tags flyout, the feed's
 *  session dialog, the new-session picker's Tags row; the two documents that load no module, the landing page's spend
 *  panel (kernel.py spTagChip) and the Obsidian timeline view, inline the same bytes under drift pins. The standard: a
 *  thin 1px border and the text in the tag's own
 *  colour on a transparent ground, weight 400 and normal tracking (inline, so a bold or letter-spaced host cannot
 *  restyle it), one size per context (the chip's own 0.82em, or the host's with `inheritSize` where a row sizes it),
 *  and off = the faded chip (`off`: TAG_CHIP_OFF_CLASS at TAG_CHIP_OFF_OPACITY) or, where a fade alone reads too faint,
 *  the STRUCK chip (`struck`, T321b, the user 2026-09-10 on the picker's Tags row): a 1px diagonal from the chip's
 *  bottom-left to its top-right in the chip's own colour (the sheets' TAG_CHIP_STRUCK_CLASS rule, currentColor, so it
 *  follows the tag on either theme) over a lighter fade; `struck` wins when both are passed. The picker uses `struck`;
 *  the tag-lens menu keeps `off` and the filter chips take neither (a selected filter is never off) until the user
 *  says otherwise, one word away. A document that loads neither sheet (the Obsidian view, the kernel's inline pages)
 *  must inline the rule before it uses `struck`, or its chip fades without the line. A host adds layout (flex,
 *  margins) and state cues the chip never sets inline (a filter, an underline), never a weight, size, border or colour. */
export function tagChip(label: string, color?: string | null, opts?: { inheritSize?: boolean; off?: boolean; struck?: boolean }): HTMLElement {
  const col = color || ("var(--dim, " + TAG_BTN_GRAY + ")");
  const chip = document.createElement("span");
  chip.setAttribute("style", "display:inline-flex;align-items:center;gap:5px;padding:2px 7px;"
    + "border-radius:9px;" + (opts && opts.inheritSize ? "" : "font-size:0.82em;")
    + "border:1px solid " + col + ";color:" + col + ";background:transparent;white-space:nowrap;"
    + "font-weight:400;letter-spacing:normal;"
    + (opts && opts.struck ? "position:relative;opacity:" + TAG_CHIP_STRUCK_OPACITY + ";"
       : opts && opts.off ? "opacity:" + TAG_CHIP_OFF_OPACITY + ";" : ""));
  if (opts && opts.struck) chip.setAttribute("class", TAG_CHIP_STRUCK_CLASS);   // its own off variant, never stacked on the fade
  else if (opts && opts.off) chip.setAttribute("class", TAG_CHIP_OFF_CLASS);
  chip.appendChild(document.createTextNode(label));
  return chip;
}

export function syncTagFilter(btn: HTMLElement, chipsHost: HTMLElement,
                              lens: TagLens, unions: { name: string; color?: string | null; members: string[]; }[],
                              onApply: (l: TagLens) => void,
                              mode: "inline" | "class" = "inline"): void {
  const narrowed = !lensAll(lens);
  if (mode === "class") btn.classList.toggle("on", narrowed);
  else {
    // inline mode mirrors the feed's .on by VALUE: accent glyph + accent border + the faint wash
    btn.style.color = narrowed ? TAG_BTN_ACCENT : TAG_BTN_GRAY;
    btn.style.borderColor = narrowed ? TAG_BTN_ACCENT : TAG_BTN_BORDER;
    btn.style.background = narrowed ? TAG_BTN_WASH : "transparent";
  }
  btn.setAttribute("aria-pressed", narrowed ? "true" : "false");
  chipsHost.textContent = "";
  for (const c of lensChips(lens, unions as never)) {
    const chip = tagChip(c.label, c.color);
    const x = document.createElement("span");
    x.textContent = "✕";
    x.setAttribute("style", "cursor:pointer;opacity:0.75;color:" + TAG_BTN_GRAY + ";font-size:0.9em;");
    x.title = "remove this from the filter";
    x.addEventListener("click", (e) => { e.stopPropagation(); onApply(toggleLens(lens, c.pick)); });
    chip.appendChild(x);
    chipsHost.appendChild(chip);
  }
}

