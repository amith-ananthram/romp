// File-preview helpers for the chat (render.ts path thumbnails and full-size renders) — the user
// 2026-07-08: when an agent produces a plot/PDF/screenshot, show the thing, not just
// its path. The bytes come from the kernel's `/file?path=…&sid=…` endpoint (extension-allowlisted,
// existence-checked, behind the same auth as every route), so a preview is only ever what the kernel
// can actually read RIGHT NOW — a deleted/hallucinated path 404s and the <img> onerror hides the thumb
// (event-based; no stale placeholders). Web dashboard only: the VS Code webview sandbox can't reach the
// kernel origin from an <img>, so callers gate on canPreview() and keep the plain click-to-open link.

import { hostOf, bareId } from "./host-prefix";
import { mediaSrc } from "./media";
import * as pz from "./pinch";

// Extensions the kernel's _PREVIEW_MIME serves — keep the two lists in step (tests pin both).
const IMG_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"]);

export type PreviewKind = "img" | "pdf";

export function previewKind(path: string): PreviewKind | null {
  const ext = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  if (IMG_EXT.has(ext)) return "img";
  if (ext === "pdf") return "pdf";
  return null;
}

// Previews load over the page's own origin, so they only work where the page IS the kernel
// (the web dashboard). In the VS Code webview (vscode-webview: origin) a relative /file URL
// resolves nowhere — callers keep the existing openFile behavior there.
export function canPreview(): boolean {
  return location.protocol === "http:" || location.protocol === "https:";
}

// Which click means "in a browser tab of its own"? The browser-wide idiom, so there is nothing new to
// learn and no setting to find: a Cmd/Ctrl-click (the `click` event carries the modifier) or a
// middle-button press (which arrives as `auxclick`, button 1). A plain click acts inside the dashboard
// (the user 2026-09-07, who wanted one opening rule for every file: a plain click keeps a PDF inside the
// dashboard exactly as an image opens; the modifier is the one signal that the browser's own tab, beside
// the dashboard, is what is wanted). The PDF and folder gestures read this signal, and so do the links
// inside a file the viewer shows (file-view.ts).
export function wantsOwnTab(ev?: { metaKey?: boolean; ctrlKey?: boolean; button?: number } | null): boolean {
  return !!ev && (!!ev.metaKey || !!ev.ctrlKey || ev.button === 1);
}

// A file in the browser's OWN tab, on the gesture above: ONE window.open aimed at the kernel's /file URL, the
// same-origin, cookie-authed route the viewer fetches from (a remote session's file relays exactly as the
// viewer's fetch does), so the browser renders what the kernel serves: a PDF in its viewer, an image, a text
// file as text. False when nothing opened, and the caller's in-app view takes over: the popup was blocked, or
// this is the VS Code webview, whose sandbox has no tabs and no kernel origin (canPreview). The tab's opener is
// severed on the handle: a link inside the opened file may navigate the tab to a foreign site, which must not
// hold the dashboard. Not the `noopener` feature, which returns null even on success. The one opener behind
// every own-tab gesture: the viewer's links hand it any file (file-view.ts), openPdfTab below hands it a PDF.
export function openFileTab(path: string, sid?: string | null): boolean {
  if (!canPreview()) return false;
  const w = window.open(fileUrl(path, sid), "_blank");
  if (!w) return false;                                   // blocked: the caller's in-app view takes over
  try { w.opener = null; } catch { /* unreachable while the tab is our own initial page */ }
  return true;
}

// LOADING CUE (the user 2026-07-31): a remote image's bytes arrive over the ssh tunnel, so for a
// beat the message showed only the path text and the picture "popped in" with nothing saying it was
// on the way. Per the loading-state rule the first thing up is the romp swirl: a mini spinning glyph
// holds the image's spot until its `load` event lands (event-based; an error still removes the whole
// box, spinner included — no backstop needed because the cue dies with its box either way). Memoized
// per URL for this page life: chat re-renders rebuild these elements constantly, and re-flashing a
// spinner over bytes the browser just painted would itself be flicker — only a URL's FIRST load spins.
const loadedOnce = new Set<string>();

// A manual retry's swirl stays up at least this long before a failure may swap the chip back in —
// an instant connection reset otherwise flashes it for one frame and the tap looks ignored (the
// user 2026-08-16). Presentation smoothing only: the failed state is already decided, this paces
// nothing but the paint.
const MIN_RETRY_SPIN_MS = 400;

// Blob types for the resumable retry's assembled bytes (an <img> renders a typed blob everywhere;
// untyped leans on sniffing). Keyed by extension, mirroring IMG_EXT.
const IMG_MIME: Record<string, string> = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif",
  webp: "image/webp", bmp: "image/bmp", svg: "image/svg+xml",
};

// Fully-fetched previews for this page life: original URL → object URL. The chat re-renders its
// messages constantly, and a resumable fetch bypasses the HTTP cache (no-store) — without this memo
// every re-render would re-pull the whole image over the very link that struggled to deliver it
// once. Bounded; the evicted entry's blob is released.
const resolvedUrls = new Map<string, string>();
function rememberResolved(url: string, objUrl: string): void {
  resolvedUrls.set(url, objUrl);
  if (resolvedUrls.size > 24) {
    const oldest = resolvedUrls.entries().next().value as [string, string];
    resolvedUrls.delete(oldest[0]);
    URL.revokeObjectURL(oldest[1]);
  }
}

function fmtBytes(got: number, total: number): string {
  const h = (n: number) => (n >= 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1e3)) + " KB");
  return total ? h(got) + " of " + h(total) : h(got);
}

// The fixed-footprint wait box shared by the retrying swirl AND the failure chip, so retry churn
// never shifts the layout under the reader (the user 2026-08-16: the chat scroll thrashed by about
// a line as the states swapped heights).
function mkWait(box: HTMLElement): HTMLElement {
  box.textContent = "";
  const wait = document.createElement("span");
  wait.className = "path-full-wait";
  box.appendChild(wait);
  return wait;
}

function withLoadCue(box: HTMLElement, img: HTMLImageElement, url: string): void {
  if (loadedOnce.has(url)) return;
  const spin = document.createElement("img");
  spin.className = "path-load-spin";
  spin.src = mediaSrc("romp-swirl-glyph.svg");
  spin.alt = "loading preview…";
  spin.title = "loading preview…";
  box.appendChild(spin);
  img.classList.add("path-img-loading");
  img.addEventListener("load", () => {
    loadedOnce.add(url);
    spin.remove();
    img.classList.remove("path-img-loading");
  });
}

// The kernel serves the bytes; sid lets it resolve a relative path against THAT session's cwd
// (same resolution as click-to-open — kernel _resolve_open_path). A FEDERATED session's file lives
// on the REMOTE machine's disk, so a host-prefixed sid (`gpu1:‹uuid›` — see federation.ts) routes
// through this kernel's /remote/<host>/file relay, the HTTP twin of the /remote/<host>/ws splice,
// with the bare sid the remote kernel actually knows (the user 2026-07-31: mentioned plots on a
// remote session's chat never rendered — /file read the LOCAL disk and 404'd). Still a same-origin
// URL, so it works wherever the dashboard is viewed from (the phone over `tailscale serve` included).
export function fileUrl(path: string, sid?: string | null): string {
  const host = sid ? hostOf(sid) : "";
  const base = host ? "/remote/" + encodeURIComponent(host) + "/file" : "/file";
  const bare = sid ? bareId(sid) : "";
  return base + "?path=" + encodeURIComponent(path) + (bare ? "&sid=" + encodeURIComponent(bare) : "");
}

// A PDF's OWN browser tab — on a Cmd/Ctrl- or middle-click (wantsOwnTab). A plain click keeps the PDF
// inside the dashboard like an image: the lightbox from the chat card, the in-pane viewer from a path or a
// file-browser row (the user 2026-09-07). The modified click opens the paper full size in the browser's own
// viewer, the way one opens from OpenReview or HotCRP (the user 2026-09-06), and it stays open beside the
// dashboard while the work goes on. Those sites do nothing more than link the PDF's URL with a new-tab
// target and serve it inline as application/pdf; the browser renders it from its own cache and nothing lands
// on disk unless the browser is set to download PDFs — no download-then-delete dance is needed. /file
// already serves exactly that (same-origin, cookie-authed, federation-aware through fileUrl), so the
// tab is ONE window.open inside the click gesture — synchronous, so a popup blocker reads it as
// user-initiated; a fetch-then-open would lose the gesture and be blocked everywhere. The opener
// link is severed by hand (w.opener = null) rather than with the noopener feature: with noopener
// window.open returns null even on success, which would hide the one signal needed — a null handle
// means the browser BLOCKED the tab, and the caller falls back to the in-app view (lightbox /
// viewer) so the PDF is never unreachable. Severed it must be (review find 2026-09-06, reproduced in
// a real browser): the tab starts on our origin, but a link inside the PDF navigates that SAME tab
// to a foreign site, and a document holding a live opener may replace the dashboard with a
// look-alike (reverse tabnabbing) — every other new tab this UI opens is disowned the same way.
// The flag is set while the tab is still its same-origin initial page and rides every later
// navigation. Web dashboard only: the VS Code webview cannot window.open, and previews are gated off
// there anyway (canPreview). The KIND check lives here, by extension — the only fact available inside
// the gesture — so a caller passes any path and a non-PDF is simply "not mine" (false): the viewer's own
// media branch keeps keying on the kernel's Content-Type verdict, never on an extension re-test
// (file-view.test.ts pins that). The tab itself is openFileTab's (above): one window.open, one severed
// opener, one blocked-popup verdict, shared with the viewer's links, which open any file that way.
export function openPdfTab(path: string, sid?: string | null): boolean {
  if (previewKind(path) !== "pdf") return false;
  return openFileTab(path, sid);
}

// The PDF card's click, with its gesture: a plain click opens the in-app lightbox, like an image; a
// Cmd/Ctrl- or middle-click opens the PDF in its own browser tab (openPdfTab), and a BLOCKED popup falls
// back to the lightbox so the PDF is never unreachable.
export function openPdf(path: string, sid?: string | null, ev?: MouseEvent | null): void {
  if (wantsOwnTab(ev) && openPdfTab(path, sid)) return;
  openLightbox(path, sid);
}

// Full-view lightbox: dark backdrop, the image at natural-but-capped size or the PDF in the browser's
// native viewer (a plain click's PDF view, and the fallback when a modified click's tab was popup-blocked — openPdf),
// filename caption. One singleton element; backdrop click / Esc / ✕ closes. Styles live
// in BOTH styles.css and feed.css (each page loads only its own sheet — the .romp-acted precedent).
// Pinch-zoom on the lightbox image (T162, the user 2026-08-28 on Android). Pointer events only —
// no gesture library: two pointers pinch around the gesture midpoint (the content point under it
// held fixed — pinch.ts owns the math, executable-tested), one pointer PANS while zoomed,
// double-tap toggles home/2.5× around the tap. touch-action:none on the stage (CSS) keeps the
// browser's own page-zoom out of it. The CLOSE gesture is untouched by construction: dismissal
// stays tap-on-BACKDROP (target === wrap) and the ✕ — every pinch/pan pointer is CAPTURED by the
// stage, so a drag that ends anywhere can never read as a backdrop tap.
function wirePinchZoom(stage: HTMLElement, img: HTMLImageElement): { retarget: (next: HTMLImageElement) => void } {
  let view = pz.identity();
  const ptrs = new Map<number, { x: number; y: number }>();
  let start: { view: pz.PinchView; d: number } | null = null;   // two-pointer gesture snapshot
  let rest: { left: number; top: number; w: number; h: number; vw: number; vh: number } | null = null;
  let lastTap = 0, lastTapAt = { x: 0, y: 0 };
  const apply = () => {
    img.style.transform = view.s === 1 && !view.tx && !view.ty
      ? "" : `translate(${view.tx}px, ${view.ty}px) scale(${view.s})`;
  };
  // the image's REST frame (its untransformed layout box, q-space origin) — measured at gesture
  // start by removing the current transform's offset from the live rect; origin 0 0 keeps the
  // top-left corner exactly translate() away from layout, so the inversion is exact
  const measure = () => {
    const r = img.getBoundingClientRect();
    const box = stage.getBoundingClientRect();
    rest = { left: r.left - view.tx, top: r.top - view.ty,
             w: r.width / view.s, h: r.height / view.s,
             vw: box.width, vh: box.height };
  };
  const q = (e: PointerEvent) => ({ x: e.clientX - rest!.left, y: e.clientY - rest!.top });
  stage.addEventListener("pointerdown", (e) => {
    if ((e.target as HTMLElement).closest(".romp-lightbox-bar")) return;   // the bar's buttons own their taps
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    try { stage.setPointerCapture(e.pointerId); } catch { /* a pointer gone between event and capture — the move/up handlers still see bubbled events */ }
    measure();
    if (ptrs.size === 2) {
      const [a, b] = [...ptrs.values()];
      start = { view, d: pz.dist(a, b) };
    } else if (ptrs.size === 1) {
      // double-tap: a second down within the platform window and radius toggles the zoom. The
      // 350ms is the GESTURE'S definition (like a double-click), not state logic.
      const now = e.timeStamp;
      const p = q(e);
      if (now - lastTap < 350 && Math.hypot(p.x - lastTapAt.x, p.y - lastTapAt.y) < 30) {
        view = pz.clampPan(pz.doubleTapToggle(view, p.x, p.y), rest!.w, rest!.h, rest!.vw, rest!.vh);
        apply();
        lastTap = 0;
      } else { lastTap = now; lastTapAt = p; }
    }
    if (view.s > 1 || ptrs.size === 2) e.preventDefault();
  });
  stage.addEventListener("pointermove", (e) => {
    const prev = ptrs.get(e.pointerId);
    if (!prev || !rest) return;
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (ptrs.size === 2 && start) {
      const [a, b] = [...ptrs.values()];
      const m = pz.midpoint(a, b);
      view = pz.clampPan(
        pz.zoomAt(start.view, m.x - rest.left, m.y - rest.top, pz.dist(a, b) / start.d),
        rest.w, rest.h, rest.vw, rest.vh);
      apply();
    } else if (ptrs.size === 1 && view.s > 1) {
      view = pz.clampPan(pz.pan(view, e.clientX - prev.x, e.clientY - prev.y), rest.w, rest.h, rest.vw, rest.vh);
      apply();
    }
  });
  const lift = (e: PointerEvent) => {
    ptrs.delete(e.pointerId);
    if (ptrs.size < 2) start = null;
    if (ptrs.size === 0) { view = pz.settle(view); apply(); }
  };
  stage.addEventListener("pointerup", lift);
  stage.addEventListener("pointercancel", lift);
  // stepping the lightbox swaps the img element: re-identity the view for the fresh element —
  // the stage's listeners are wired ONCE (steps must never stack handlers), only the target moves
  return { retarget: (next: HTMLImageElement) => { img = next; view = pz.identity(); ptrs.clear(); start = null; apply(); } };
}

// ── lightbox arrow navigation (the user 2026-08-29: arrow keys step to the previous/next picture
// in the chat, like a messaging app) ────────────────────────────────────────────────────────────
// The image sequence comes from a PROVIDER the chat registers (render.ts walks the session's
// EVENTS oldest→newest — the DOM misses virtualization-windowed images), each entry the same
// (path, sid, pin) triple the click path passes, so a step shows THAT message's pinned bytes and
// can never resurrect the history-rewrite the pin store prevents. Surfaces that register no
// provider (the feed) keep inert arrows.
export interface LightboxNavEntry { path: string; sid?: string | null; pin?: string; }
let lightboxNav: ((sid: string | null | undefined) => LightboxNavEntry[]) | null = null;
export function setLightboxNav(fn: (sid: string | null | undefined) => LightboxNavEntry[]): void {
  lightboxNav = fn;
}

export function openLightbox(path: string, sid?: string | null, pin?: string): void {
  document.getElementById("romp-lightbox")?.remove();
  const kind = previewKind(path);
  if (!kind) return;
  const wrap = document.createElement("div");
  wrap.id = "romp-lightbox";
  const inner = document.createElement("div");
  inner.className = "romp-lightbox-inner" + (kind === "pdf" ? " pdf" : "");
  let nav: LightboxNavEntry[] = [];
  let at = -1;
  let step: ((delta: number) => void) | null = null;     // arrows step the chat's image sequence (img kind only)
  let cue: HTMLElement | null = null;                    // the compact position mark ("3/17") in the bar
  let curImg: (() => HTMLImageElement) | null = null;    // the img on screen NOW (step rebinds it) — the copy source
  if (kind === "pdf") {
    // a plain click's view (openPdf), and the fallback when a modified click's tab was popup-blocked
    const frame = document.createElement("iframe");
    frame.className = "romp-lightbox-frame";
    frame.src = fileUrl(path, sid);
    frame.title = path;
    inner.appendChild(frame);
  } else {
    const mkImg = (e: LightboxNavEntry) => {
      const im = document.createElement("img");
      im.className = "romp-lightbox-img";
      im.src = fileUrl(e.path, e.sid) + (e.pin ? "&pin=" + encodeURIComponent(e.pin) : "");
      im.alt = e.path;
      return im;
    };
    let img = mkImg({ path, sid, pin });
    curImg = () => img;
    inner.appendChild(img);
    const pzc = wirePinchZoom(inner, img);
    // the chat's image sequence, oldest→newest (empty on surfaces with no provider): the current
    // position matches by (path, pin) — two messages embedding different VERSIONS of one path are
    // different entries — falling back to the path alone for pre-pin history
    nav = (lightboxNav ? lightboxNav(sid) : []).filter((e) => previewKind(e.path) === "img");
    at = nav.findIndex((e) => e.path === path && (e.pin || "") === (pin || ""));
    if (at < 0) at = nav.findIndex((e) => e.path === path);
    step = (delta: number) => {
      if (at < 0 || nav.length < 2) return;
      const n = at + delta;
      if (n < 0 || n >= nav.length) return;              // the ends END (messaging-app feel) — no wrap
      at = n;
      const e = nav[at];
      const next = mkImg(e);
      img.replaceWith(next);
      img = next;
      pzc.retarget(next);                                // a step lands on the fit view, zoom reset
      name.textContent = e.path; name.title = e.path;
      dl.href = fileUrl(e.path, e.sid) + (e.pin ? "&pin=" + encodeURIComponent(e.pin) : "");
      dl.download = e.path.slice(e.path.lastIndexOf("/") + 1) || "image";
      if (cue) cue.textContent = (at + 1) + "/" + nav.length;
    };
  }
  const bar = document.createElement("div");
  bar.className = "romp-lightbox-bar";
  const name = document.createElement("span");
  name.className = "romp-lightbox-name";
  name.textContent = path;
  name.title = path;
  // download rides an ANCHOR with the download attribute (the user 2026-08-19): the browser saves
  // the same bytes the lightbox is showing — the pinned URL when a pin rode in, so a re-generated
  // file can't swap the image between viewing and saving. The filename is the path's basename.
  if (nav.length > 1 && at >= 0) {
    cue = document.createElement("span");
    cue.className = "romp-lightbox-cue";
    cue.textContent = (at + 1) + "/" + nav.length;
    cue.title = "picture " + (at + 1) + " of " + nav.length + " in this chat — ←/→ to step";
  }
  const dl = document.createElement("a");
  dl.className = "romp-lightbox-dl";
  dl.href = fileUrl(path, sid) + (pin ? "&pin=" + encodeURIComponent(pin) : "");
  dl.download = path.slice(path.lastIndexOf("/") + 1) || "image";
  // the tray icon every download control should wear (the composer buttons' stroke family) as an
  // inline SVG: the old text glyph (U+2B73, arrow-to-bar) has no coverage in the mac system fonts
  // and rendered as a tofu box instead of an icon (the user 2026-08-19). A literal — no sanitize.
  dl.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
    + '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  dl.title = "download";
  dl.setAttribute("aria-label", "download");
  dl.onclick = (ev) => ev.stopPropagation();               // saving must not also dismiss
  // copy beside download (the user 2026-08-31): the image ITSELF onto the clipboard. It reads the
  // CURRENT img's src at CLICK time — mkImg bakes the pin param into src and an arrow step rebinds
  // `img`, so a pinned historical version copies as exactly what is on screen, and stepping needs no
  // retarget bookkeeping. Rendered only where the clipboard API exists (an insecure-context http
  // origin drops navigator.clipboard entirely — canPreview's honest-absence precedent: no dead
  // control). Clipboards take image/png; any other source re-encodes through a canvas. The
  // ClipboardItem takes the PROMISE form so write() runs synchronously inside the click gesture
  // (Safari refuses a write that awaits first — the tailnet phone case). Success and failure both
  // speak in place: the icon flips to a check, or to an × whose title names the reason, and the
  // button restores itself either way.
  const COPY_SVG = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>'
    + '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  let cp: HTMLButtonElement | null = null;
  if (curImg && typeof ClipboardItem !== "undefined" && navigator.clipboard && navigator.clipboard.write) {
    const btn = document.createElement("button");
    cp = btn;
    btn.className = "romp-lightbox-copy";
    btn.innerHTML = COPY_SVG;
    btn.title = "copy image";
    btn.setAttribute("aria-label", "copy image");
    const restore = () => { btn.innerHTML = COPY_SVG; btn.title = "copy image"; btn.classList.remove("ok", "err"); };
    btn.onclick = (ev) => {
      ev.stopPropagation();                                // copying must not also dismiss
      const src = curImg!().src;
      const png = (async () => {
        const blob = await (await fetch(src)).blob();
        if (blob.type === "image/png") return blob;
        const bmp = await createImageBitmap(blob);         // jpeg/webp → decode → png, the one type
        const cv = document.createElement("canvas");       // every clipboard accepts
        cv.width = bmp.width; cv.height = bmp.height;
        cv.getContext("2d")!.drawImage(bmp, 0, 0);
        return await new Promise<Blob>((res, rej) =>
          cv.toBlob((b) => (b ? res(b) : rej(new Error("png encode failed"))), "image/png"));
      })();
      navigator.clipboard.write([new ClipboardItem({ "image/png": png })]).then(() => {
        btn.textContent = "✓"; btn.classList.add("ok"); btn.title = "copied";
        window.setTimeout(restore, 1400);                  // the ack pulse, then back to a button
      }, (e) => {
        btn.textContent = "✕"; btn.classList.add("err");   // loud: the reason, never a silent no-op
        btn.title = "copy failed: " + ((e && (e as Error).message) || String(e));
        window.setTimeout(restore, 3000);
      });
    };
  }
  const close = document.createElement("button");
  close.className = "romp-lightbox-close";
  close.textContent = "✕";
  close.title = "close (Esc)";
  const controls = [dl, ...(cp ? [cp] : []), close];
  if (cue) bar.append(name, cue, ...controls); else bar.append(name, ...controls);
  inner.appendChild(bar);
  wrap.appendChild(inner);
  const dismiss = () => { wrap.remove(); document.removeEventListener("keydown", onKey, true); };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") { ev.stopPropagation(); dismiss(); return; }
    if ((ev.key === "ArrowLeft" || ev.key === "ArrowRight") && step) {
      ev.stopPropagation(); ev.preventDefault();         // the chat must not scroll under the lightbox
      step(ev.key === "ArrowLeft" ? -1 : 1);             // ← older, → newer — the transcript's own order
    }
  };
  close.onclick = (ev) => { ev.stopPropagation(); dismiss(); };
  wrap.onclick = (ev) => { if (ev.target === wrap) dismiss(); };   // backdrop closes; content clicks don't
  document.addEventListener("keydown", onKey, true);
  document.body.appendChild(wrap);
}

// FULL-SIZE inline render for a mentioned image in the CHAT (the user 2026-07-20, who wanted not even a
// thumbnail but a rendered image, like the user messages). Self-verifying —
// a path the kernel can't serve removes itself — and an image click still opens the lightbox. Images
// render at the user-image scale (.path-full-img mirrors .user-img's 320px cap, one size per
// information type). A PDF is a labeled CARD, not an auto-loading inline viewer (click → the
// lightbox; Cmd/Ctrl- or middle-click → its own browser tab, openPdf):
// the first cut embedded an <iframe> per mentioned PDF, and a browser set to "Download PDFs" (or one
// that declines to render inline) saved a FRESH COPY on every chat re-render — the user's Downloads
// folder silently filled with datasheet copies (2026-07-20). A fetch must be user-initiated, once.
// Web only — callers gate on canPreview and fall back per surface.
// `verified`: the KERNEL already stat'd this path (spacePaths / a pathLinks verdict), so a load
// error is TRANSIENT — the kernel restarting mid-fetch, a tunnel blip — not a dead path. Removing
// the box then erases the preview silently until some later re-render (the user 2026-08-15, who sat
// through exactly that: verified path pills, no images, no cue). A verified path's failure therefore
// stays VISIBLE — a "preview unavailable — tap to retry" chip in the figure's spot — per the
// fail-loudly rule. Only an UNVERIFIED path (old kernel, no pathLinks key) keeps self-removal:
// there the error really does mean "no such file".
export function previewFull(path: string, sid?: string | null, verified = false, pin?: string): HTMLElement | null {
  const kind = previewKind(path);
  if (!kind || !canPreview()) return null;
  const box = document.createElement("span");
  box.className = "path-full" + (kind === "pdf" ? " pdf" : "");
  box.title = path;
  if (kind === "pdf") {
    box.classList.add("path-full-pdfcard");
    const tag = document.createElement("span");
    tag.className = "path-thumb-tag";
    tag.textContent = "PDF";
    const nm = document.createElement("span");
    nm.className = "path-thumb-name";
    nm.textContent = path.slice(path.lastIndexOf("/") + 1);
    box.append(tag, nm);
    box.style.cursor = "pointer";
    box.title = "Open " + path + " (Cmd/Ctrl-click or middle-click: a browser tab of its own)";
    box.onclick = (ev) => { ev.stopPropagation(); openPdf(path, sid, ev); };   // plain: the lightbox; modified: its own tab
    box.onmousedown = (ev) => { if (ev.button === 1) ev.preventDefault(); };   // the middle press: no autoscroll (it would swallow the auxclick)
    box.onauxclick = (ev) => { if (ev.button !== 1) return; ev.stopPropagation(); openPdf(path, sid, ev); };
    // a chip can't self-verify like an <img> — HEAD-probe (headers only, no body — never a download)
    // so a missing PDF never shows a dead card. A kernel-VERIFIED card skips the probe: the kernel
    // said the file exists, and a transient probe failure must not erase the card.
    // an UNVERIFIED card's failed probe HIDES the card and keeps it registered for the heal
    // events — never removed from the DOM: one transient failure (a kernel-restart window, a tunnel blip)
    // used to erase the figure until a send's re-render minted a fresh box (the user 2026-08-24).
    // The hidden sentinel keeps the spot healable with zero visual noise when the mention really
    // is dead; a probe that later succeeds unhides the card in place.
    if (!verified) {
      // a 502 is the relay's link verdict (HEAD carries no body to read): that one waits for the reconnect-class
      // heal like an image's link failure (T291); any other miss keeps the per-message heal
      const probe = () => fetch(fileUrl(path, sid), { method: "HEAD" })
        .then((r) => { if (r.ok) { box.style.display = ""; } else { box.style.display = "none"; (r.status === 502 ? settledPreviews : failedPreviews).set(box, probe); } })
        .catch(() => { box.style.display = "none"; failedPreviews.set(box, probe); });
      probe();
    }
  } else {
    // `pin` freezes this MESSAGE's embed to its mention-time bytes (kernel _pin_mention): the sid
    // rides too (the pin store lives on the owning kernel; the relay forwards the query untouched),
    // and a pin whose blob was evicted falls back server-side to the live file.
    const url = fileUrl(path, sid) + (pin ? "&pin=" + encodeURIComponent(pin) : "");
    // A verified preview whose fetch died usually died because the KERNEL was away (a restart mid-
    // deploy — the 2026-08-15 report hit exactly the converge-restart window), and delta-send never
    // rebuilds an old turn's DOM, so the chip would otherwise sit until a human tapped it. Bounded so
    // a genuinely-dead file settles on the tap chip instead of re-fetching on every push forever —
    // but an attempt that MADE PROGRESS refills the budget (see the resumable retry below): forward
    // motion is the event proving the link works sometimes, and only truly dead attempts spend it.
    let autoRetries = 3;
    let chipHealedErr: string | null = null;    // the error a settled chip already spent its one heal on
    let fails = 0;                                   // total failed attempts — the chip's copy escalates
    // RESUMABLE RETRY STATE (the user 2026-08-16, on flaky wifi: every retry restarted the transfer
    // from byte 0, so a large figure never finished arriving — and the swirl gave no idea how far it
    // got). The happy path below stays a plain <img> (the browser cache makes the chat's constant
    // re-renders free); once a load has FAILED, retries switch to a managed fetch that keeps every
    // byte received so far and asks the kernel for the REST (Range: bytes=N-, honored by /file and
    // across the federation relay). A dropping link then finishes the picture ACROSS attempts, with
    // the swirl narrating real progress ("1.2 of 3.4 MB" — content-length makes it knowable). No
    // artificial deadline anywhere: only a real network error ends an attempt.
    let parts: Uint8Array[] = [];
    let got = 0;
    let total = 0;
    let fetching = false;                            // one managed attempt at a time (a tap mid-fetch no-ops)
    let lastErr = "";                                // the newest attempt's server-side reason, shown verbatim
    // The wait box is REUSED across attempts (T291, the user 2026-09-09: two figures on a remote session
    // whose relay was failing re-attempted on every kernel push, and each attempt swapped the box between
    // the one-line "fetching…" and the five-line failure note — the whole transcript above the reader
    // moved by four lines, ten times a second, for the length of the streaming turn). One wait element,
    // one swirl, one note, kept from attempt to attempt; only the note's TEXT changes, and only when the
    // words do. An attempt therefore never changes the box's layout; the picture landing does.
    const waitBox = (): { wait: HTMLElement; note: HTMLElement } => {
      let wait = box.firstElementChild as HTMLElement | null;
      if (!wait || !wait.classList.contains("path-full-wait")) wait = mkWait(box);
      const chip = wait.querySelector(".path-full-retry") as HTMLElement | null;
      if (chip) {
        // the one new-evidence heal (autoRetries 1) keeps the chip's shape: the chip carries the words while it
        // runs. A tap or a reconnect heal re-armed the budget (3): the loading persona returns, swirl and note,
        // inside the same wait element (the 2026-08-16 rule: the chip is the give-up state only)
        if (autoRetries <= 1) return { wait, note: chip };
        chip.remove();
      }
      let spin = wait.querySelector(".path-load-spin") as HTMLImageElement | null;
      if (!spin) {
        spin = document.createElement("img");
        spin.className = "path-load-spin";
        spin.src = "/media/romp-swirl-glyph.svg";
        spin.alt = "loading preview…";
        wait.appendChild(spin);
      }
      let note = wait.querySelector(".path-load-note") as HTMLElement | null;
      if (!note) {
        note = document.createElement("span");
        note.className = "path-load-note";
        wait.appendChild(note);
      }
      return { wait, note };
    };
    const setNote = (note: HTMLElement, text: string) => { if (note.textContent !== text) note.textContent = text; };
    const showChip = () => {
      if (!box.isConnected) return;                  // the turn re-rendered; a fresh box owns this spot now
      // ONE continuous narrative while the machinery is still going (the user 2026-08-16, third
      // report: the box flipped between "trying" and "unavailable" on every auto-retry cycle even
      // though it eventually loaded — the state bounced, so the UI read as impatient). While bounded
      // auto-retries remain, the wait box KEEPS its loading persona — swirl + a note carrying the
      // failure and the plan ("dropped at 1.2 MB of 3.4 MB — retrying · tap to retry now"), the
      // whole box tappable — and the ⚠ chip appears only when the budget is genuinely spent. A
      // repeat failure must still READ as a response to a tap: the note re-pulses on swap-in.
      // INFRASTRUCTURE-DOWN failures are FREE (the user 2026-08-17: figures gave up seconds after a
      // kernel restart — the tunnel re-dial window produces instant "no attached host" 404s and
      // "tunnel not answering" 502s, and three of those spent the whole budget right before the link
      // came back). A failure that names the LINK, not the image, doesn't decrement: the preview
      // re-attempts when the link itself comes back (the reconnect-class heal below, T291), and only
      // real verdicts — a true not-found from the owning kernel, a transfer that died with zero
      // progress — spend attempts.
      const transient = /tunnel to .* is not answering|no attached host|re-dialing/i.test(lastErr);
      if (autoRetries > 0 || transient) {
        // A LINK failure heals on the reconnect-class event alone (T291): romp:wsup when this page's
        // kernel socket comes back, hostUp when the federated tunnel does (the tunnel poll dispatches
        // it on the down→up transition), romp:hostRelayUp when the host's relay socket reopens (a
        // remote kernel's restart fires that one and neither of the others), never on the next kernel push. A streaming session pushes
        // many times a second, and a dead link answered every one of those instantly: the per-push
        // retry was the flicker. The budget stays untouched, as before; the tap still retries at once.
        if (transient) settledPreviews.set(box, () => build(true));
        else { autoRetries--; failedPreviews.set(box, () => build(true)); }
        const { wait, note } = waitBox();
        wait.title = path + " — tap to retry now";
        wait.style.cursor = "pointer";
        wait.onclick = (ev) => { ev.stopPropagation(); autoRetries = 3; ackTap(ev); build(true); };   // a tap re-arms persistence
        setNote(note, (got > 0 ? "connection dropped at " + fmtBytes(got, total)
                               : lastErr || "connection dropped")
                      + (transient ? " — retries when the link is back · tap to retry now" : " — retrying · tap to retry now"));
        if (fails > 1) {
          note.classList.add("path-retry-flash");
          note.addEventListener("animationend", () => note.classList.remove("path-retry-flash"), { once: true });
        }
        return;
      }
      const wait = mkWait(box);
      const chip = document.createElement("span");
      chip.className = "path-full-retry";
      // the budget is spent: three attempts gained nothing (progress refills it), so say so plainly
      chip.textContent =
        (got > 0 ? "⚠ connection dropped at " + fmtBytes(got, total)
                 : lastErr ? "⚠ " + lastErr
                 : (fails > 1 ? "⚠ still unavailable" : "⚠ preview unavailable"))
        + " — tap to retry";
      chip.title = path;
      chip.onclick = (ev) => { ev.stopPropagation(); autoRetries = 3; ackTap(ev); build(true); };   // a tap re-arms persistence
      wait.appendChild(chip);
      // A settled chip still rides the push-heal (the user 2026-08-18: "they never render on their
      // own — only when I send a message"): only the retrying branch registered for the heal, so a
      // spent budget dropped the box from the map forever — pushes and tunnel recovery ignored it,
      // and a send only "worked" because the tail re-render minted a FRESH box. One heal attempt
      // per registration, and the box re-registers ONLY when the error CHANGED (new information —
      // the same verdict re-answered is no reason to fetch again): a truly-dead figure costs one
      // extra fetch per new-evidence transition, never one per push.
      if (lastErr !== chipHealedErr) {
        failedPreviews.set(box, () => { chipHealedErr = lastErr; autoRetries = 1; build(true); });
      }
      // …and a RECONNECT-class event (romp:wsup / hostUp) heals a settled chip REGARDLESS of the
      // error text (the user 2026-08-24): a byte-identical 404 while the file was still being
      // written — or a constant connection-refused — parked the chip inert forever, though the
      // link coming back is new information even when the words didn't change. The budget refills
      // exactly like a send's fresh box; reconnects are rare, so this can't hammer.
      settledPreviews.set(box, () => { chipHealedErr = lastErr; autoRetries = 3; build(true); });
      if (fails > 1) {
        chip.classList.add("path-retry-flash");
        chip.addEventListener("animationend", () => chip.classList.remove("path-retry-flash"), { once: true });
      }
    };
    const failAfterBeat = (started: number) => {
      fails++;
      // A retry that dies instantly (a dead tunnel resets the connection in milliseconds) would
      // flash the swirl for one frame and put back an identical chip — an ignored-looking tap.
      // Hold the swirl to a perceivable beat before swapping. Presentation smoothing only: the
      // attempt has already failed, and the auto-heal registration rides the same swap.
      const left = MIN_RETRY_SPIN_MS - (Date.now() - started);
      if (left > 0) setTimeout(showChip, left); else showChip();
    };
    const mkImg = (src: string) => {
      const img = document.createElement("img");
      img.className = "path-full-img";
      img.src = src;
      img.alt = path;
      img.loading = "lazy";       // off-screen figures don't fetch until scrolled near (eager-all, 2026-08-30)
      img.decoding = "async";     // and never decode on the main thread mid-scroll
      img.onclick = (ev) => { ev.stopPropagation(); openLightbox(path, sid, pin); };
      return img;
    };
    // every tap READS as a tap even when the click lands mid-attempt and build() no-ops on its
    // `fetching` guard (the buttons-always-acknowledge rule: an unacknowledged tap gets re-tapped)
    const ackTap = (ev: Event) => {
      const t = ev.currentTarget as HTMLElement | null;
      if (!t) return;
      t.classList.add("path-retry-flash");
      t.addEventListener("animationend", () => t.classList.remove("path-retry-flash"), { once: true });
    };
    const resumeFetch = async (note: HTMLElement) => {
      const gotBefore = got;
      const r = await fetch(url, { cache: "no-store",
                                   headers: got > 0 ? { Range: "bytes=" + got + "-" } : {} });
      if (r.status === 206) {
        // the kernel continues our partial — the entity size rides Content-Range's "/<size>" tail
        total = parseInt((r.headers.get("Content-Range") || "").split("/")[1] || "0", 10) || total;
      } else if (r.ok) {
        parts = []; got = 0;                         // full body (no range asked, or the server restarted us)
        total = parseInt(r.headers.get("Content-Length") || "0", 10) || 0;
      } else {
        // the error BODY is the diagnostic (the kernel's 502 says "tunnel to <host> is not
        // answering") — a bare status code hid that the IMAGE was fine and the LINK was down
        let why = "";
        try { why = ((await r.text()) || "").split("\n")[0].slice(0, 120); } catch { /* body unavailable */ }
        // a refused status VOIDS the resume state (the user 2026-08-18, whose re-generated figures
        // never loaded): the file changed under our offset — an agent re-plotting the same name
        // shrinks it — and the kernel's 416 expects the client to RESTART cleanly. Keeping `got`
        // made every later attempt, tap and heal alike, replay the same stale Range and fail
        // deterministically fast, while a send's fresh box (got=0) rendered instantly.
        parts = []; got = 0;
        throw new Error(why || "http " + r.status);
      }
      const reader = r.body!.getReader();
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          parts.push(value);
          got += value.byteLength;
          note.textContent = "fetching… " + fmtBytes(got, total);
        }
      } finally {
        if (got > gotBefore) autoRetries = 3;        // progress refills the budget — the link works sometimes
      }
      if (total && got < total) throw new Error("cut at " + got);   // stream ended early → resume next attempt
      const blob = new Blob(parts as BlobPart[], { type: IMG_MIME[path.slice(path.lastIndexOf(".") + 1).toLowerCase()] || "" });
      return URL.createObjectURL(blob);
    };
    const build = (bust: boolean) => {
      const done = resolvedUrls.get(url);
      if (done) {                                    // already fully fetched this page-life → instant
        box.style.display = "";                      // a hidden unverified sentinel that healed comes back
        box.textContent = "";
        box.appendChild(mkImg(done));
        return;
      }
      if (!bust) {                                   // first attempt: the plain <img> happy path
        box.textContent = "";
        const img = mkImg(url);
        img.onerror = () => {
          // unverified → the SAME retry machinery as verified, just invisible while failed (see
          // the probe note above): the box hides instead of wearing the chip, and a later success
          // unhides it — never self-removed, which erased the spot until a send re-rendered
          if (!verified) box.style.display = "none";
          failAfterBeat(0);                          // no beat on the first attempt — the cue was already up
        };
        withLoadCue(box, img, url);   // mini swirl holds the spot until the load event (memo on the un-busted url)
        box.appendChild(img);
        return;
      }
      if (fetching) return;
      fetching = true;
      const started = Date.now();
      // the wait box stays as it is while the attempt runs (T291): a note already up keeps its words, so a
      // retry from a failure note does not shrink the box to one line and grow it back on the next failure
      const { note } = waitBox();
      if (!note.textContent) setNote(note, got > 0 ? "resuming… " + fmtBytes(got, total) : "fetching…");
      resumeFetch(note).then((objUrl) => {
        fetching = false;
        lastErr = "";
        rememberResolved(url, objUrl);
        loadedOnce.add(url);                         // re-renders skip the cue — the bytes are in hand
        if (!box.isConnected) return;
        box.style.display = "";                      // a hidden unverified sentinel that healed comes back
        box.textContent = "";
        box.appendChild(mkImg(objUrl));
      }).catch((e: unknown) => {
        fetching = false;
        lastErr = String((e as Error)?.message || "");
        if (lastErr.startsWith("cut at ")) lastErr = "";   // a mid-stream cut narrates via got/fmtBytes
        if (!verified) box.style.display = "none";         // hidden while failed, healable — never removed
        failAfterBeat(started);
      });
    };
    build(false);
  }
  return box;
}

// Failed VERIFIED previews awaiting recovery. A kernel push arriving IS the kernel-is-back event —
// no pushes arrive while it's down, so retrying on push can't spam — and render.ts calls this on
// every incoming kernel message, healing the chips without a tap (event-based; the tap chip stays
// as the manual path and the backstop once a box's bounded auto-retries are spent).
const failedPreviews = new Map<HTMLElement, () => void>();
export function retryFailedPreviews(): void {
  retryMdImgProbes();                                // parked markdown images with attempts left (T291c): the budget rides the URL
  if (!failedPreviews.size) return;
  for (const [box, rebuild] of Array.from(failedPreviews.entries())) {
    failedPreviews.delete(box);                      // one attempt per registration; re-registers on error
    if (box.isConnected) rebuild();                  // a re-rendered turn made a fresh box — let the old go
  }
}

// Settled chips (auto-retry budget spent) awaiting a RECONNECT-class heal — romp:wsup (this page's
// kernel socket came back) or hostUp (a federated tunnel recovered). Drained only by these events,
// never by the per-message heal above, so a dead figure costs one fetch per reconnect, not per push.
const settledPreviews = new Map<HTMLElement, () => void>();
export function refreshSettledPreviews(): void {
  healMdImgs();                                      // parked markdown images ride the same reconnect-class heal (T291c)
  if (!settledPreviews.size) return;
  for (const [box, rebuild] of Array.from(settledPreviews.entries())) {
    settledPreviews.delete(box);                     // one attempt per registration; re-registers on error
    if (box.isConnected) rebuild();
  }
}

// Markdown-inline <img> (a figure written as markdown in a message body): marked leaves `![fig](path)` as
// <img src="path"> (the chat rewrites no image source), the browser resolves the path against the page's
// origin, and the kernel answers 404, so the img fails and the browser shows its alt text. The 2026-08-24
// heal re-set that img's src on every kernel message; each reset hid the alt text while the reload was in
// flight and the 404 showed it again, so a streaming turn flipped every such caption on and off at the
// push rate (T291c, the user 2026-09-09: three captions, two to four lines each, ten times a second). A
// failed markdown image is PARKED instead: its src leaves (into data-md-src), the element wears
// .md-img-failed, and the browser shows the alt text as a stable caption: no src, no fetch, nothing to
// flip. No per-message path touches the img on the page: a URL the kernel serves gets a bounded probe OFF the
// DOM (servedByKernel and retryMdImgProbes, below), every other parked URL waits for a reconnect. A URL that
// failed is remembered for the page life, and the reconnect-class heal (refreshSettledPreviews: romp:wsup,
// hostUp, romp:hostRelayUp) probes every remembered URL once OFF the DOM with a detached Image, whether or not
// an img with it is on the page (a turn evicted from the rendered window, a closed tab): a failure changes
// nothing on screen, a success lands the picture on every parked img with that URL. The sanitizer post-pass
// (mdImgPostPass, registered by render.ts) parks a re-rendered img with a remembered URL BEFORE the browser
// fetches it, so a re-render of the same markdown reuses the state instead of fetching, failing and flipping
// once more. DOMPurify strips inline handlers
// (correctly), so the failures are caught by ONE document-level capture listener (error events do not
// bubble but do capture); previews' own <img>s are skipped: their machinery (budgets, resume, chips) owns those.
const mdImgFailed = new Set<string>();             // URLs that failed this page life: a re-render parks them before any fetch
const mdImgProbe = new Map<string, number>();      // served URLs with per-message attempts left: the budget rides the URL, not the img
const mdImgProbing = new Map<string, number>();    // served URLs with a per-message probe in flight, by that probe's token: one at a time per URL
let mdImgProbeSeq = 0;                             // the tokens: a failure counts only for the probe whose token is still the URL's
let mdImgHealOn = false;
function parkMdImg(img: HTMLImageElement, url: string): void {
  mdImgFailed.add(url);
  img.dataset.mdSrc = url;
  if (img.hasAttribute("src")) img.removeAttribute("src");   // no src: the browser shows the alt text and fetches nothing
  img.classList.add("md-img-failed");
}
/** The chat's post-pass (render.ts runs it on md() and userMd() output after the sanitize, NEVER through the shared
 *  sanitizer: the file viewer rewrites its images' paths to /file URLs after its own sanitize, and a park inside the
 *  sanitizer removed the src that rewrite keys on, the review's find): an img whose URL is already known to fail is
 *  parked at RENDER, before any fetch. */
export function mdImgPostPass(root: ParentNode): void {
  if (!mdImgFailed.size) return;
  for (const img of Array.from(root.querySelectorAll("img")) as HTMLImageElement[]) {
    const u = img.src || "";
    if (u && mdImgFailed.has(u)) parkMdImg(img, u);
  }
}
/** A URL the kernel actually answers (/file, a remote relay's /file, /media): a file an agent is still writing or a
 *  relay behind a blip may come back with no reconnect event, so those get a BOUNDED probe on the per-message path
 *  (three attempts, off the DOM, so the caption never moves for them). The budget is keyed on the URL, not on the img
 *  that failed: a streaming turn is re-rendered on every push, so that img is off the page by the next push and the
 *  post-pass has parked a fresh one, which inherits the attempts left. An origin-resolved path no route serves is
 *  park-only, since nothing but a reconnect could change its answer. */
function servedByKernel(u: string): boolean {
  try {
    const p = new URL(u, location.href).pathname;
    return p === "/file" || /^\/remote\/[^/]+\/file$/.test(p) || p.startsWith("/media/");
  } catch { return false; }
}
/** One detached probe of a parked URL. On load the picture lands on every parked img with that URL that is on the
 *  page NOW, re-queried, not a snapshot: a re-render during the probe parks a fresh img, which a snapshot missed
 *  (the review's find). On failure nothing on screen changes. */
function probeMdImgUrl(u: string, onFail?: () => void): void {
  const probe = new Image();
  probe.onload = () => {
    mdImgFailed.delete(u);                           // first: a rebuild from here on fetches normally
    mdImgProbe.delete(u); mdImgProbing.delete(u);    // a healed URL has no pending budget
    for (const img of Array.from(document.querySelectorAll("img.md-img-failed[data-md-src]")) as HTMLImageElement[]) {
      if (img.dataset.mdSrc !== u || !img.isConnected) continue;
      img.classList.remove("md-img-failed");
      delete img.dataset.mdSrc;
      img.src = u;                                   // the bytes are in the browser's cache: the picture lands
    }
  };
  probe.onerror = () => { if (onFail) onFail(); };  // still down: the parked captions stay exactly as they are
  probe.src = u;
}
/** The per-message path for the served URLs with attempts left: one detached probe per URL per push, the next armed
 *  only once the previous has failed (a push while it is pending fires nothing), whatever img carries the URL now and
 *  whether or not one is on the page (the probe is off the DOM; a load re-queries the page). Run from
 *  retryFailedPreviews, so it rides the kernel-message event like the figure previews' own retries. Each probe holds a
 *  token, and its failure counts only while that token is still the URL's: a load of the URL by any probe retires it,
 *  and the URL may since have failed again with a fresh budget and a newer probe in flight, so a stale failure spends
 *  nothing and clears nothing (a bare in-flight check is not enough: the newer probe has re-marked the URL). */
function retryMdImgProbes(): void {
  for (const [u, left] of Array.from(mdImgProbe.entries())) {
    if (mdImgProbing.has(u)) continue;
    const token = ++mdImgProbeSeq;
    mdImgProbing.set(u, token);
    probeMdImgUrl(u, () => {
      if (mdImgProbing.get(u) !== token) return;   // retired by a load meanwhile: the fresh budget and any newer probe stand
      mdImgProbing.delete(u);
      if (left > 1) mdImgProbe.set(u, left - 1);    // still down: one attempt spent
      else mdImgProbe.delete(u);                     // spent: the reconnect heal alone from here
    });
  }
}
/** The reconnect-class heal for parked markdown images: one detached probe per remembered URL. The remembered set is
 *  the source, since a URL whose turn left the rendered window or whose tab was closed has no img on the page, and a
 *  re-render would park it again for the page life; the page's parked imgs are read too, for one parked under a URL
 *  the memory no longer holds. */
export function healMdImgs(): void {
  const urls = new Set<string>(mdImgFailed);
  for (const img of Array.from(document.querySelectorAll("img.md-img-failed[data-md-src]")) as HTMLImageElement[]) {
    const u = img.dataset.mdSrc || "";
    if (u) urls.add(u);
  }
  for (const u of urls) probeMdImgUrl(u);
}
export function installMdImgHeal(): void {
  if (mdImgHealOn) return;                           // ensure-once (the click-safety installation rule)
  mdImgHealOn = true;
  document.addEventListener("error", (e) => {
    const img = e.target as HTMLImageElement | null;
    if (!img || img.tagName !== "IMG") return;
    const src = img.src || "";
    if (!src || src.startsWith("data:")) return;     // a broken data: URI has no server to heal
    if (img.onerror || img.closest(".path-full")) return;   // the preview machinery retries its own
    parkMdImg(img, src);
    if (servedByKernel(src)) {                       // bounded and off the DOM: the caption never moves for it
      if (!mdImgProbe.has(src)) mdImgProbe.set(src, 3);   // the budget rides the URL: a re-rendered turn inherits what is left
    }
  }, true);
}
