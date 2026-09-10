// Shared, persisted webview settings (the user 2026-06-14): one global settings store, surfaced via a
// gear → modal. localStorage-backed so same-origin views (the browser's /chat, /feed, /timeline tabs)
// share ONE setting, and a `storage` event live-syncs a change across the other open tabs. Keep this
// DOM-light: load/save are pure over localStorage (unit-tested); only the subscribe helper touches window.

export interface RompSettings {
  compact: boolean;   // chat transcript: collapse consecutive tool uses, hide thinking
  colormap: string;   // feed recency tint colormap (the user 2026-06-16): hawaii | viridis | magma | inferno | plasma | cividis | aurora
  subgoals: boolean;      // feed CARDS: show the inline sub-goal checklist (the user 2026-06-17); toggled from the feed FOOTER (the user 2026-06-18); the MODAL is unaffected
  // The timeline's judging band, split into its two judge SETS (the user 2026-06-29): index = the captioner +
  // archiver; triage = planner/grouper/closer/distiller/courier. Each toggle shows its set's rows on the band.
  // Replaces the old single `debug` toggle (kept optional below for migration). Both OFF by default.
  showIndexJudges: boolean;
  showTriageJudges: boolean;
  debug?: boolean;    // LEGACY (the user 2026-06-17): the old single judging-band toggle; read as the migration fallback for the two judge-set toggles when those are unset. The ↻ restart button is always-visible (decoupled).
  backend: "tmux" | "sdk" | "codex";   // which backend a NEWLY-created session uses (the user 2026-06-22): "tmux" (terminal), "sdk" (Agent SDK), "codex" (OpenAI Codex, docs/codex.md). Both coexist; this is only the default for the + button. Read at createSession time (render.ts). Default sdk (the user 2026-07-13).
  defaultDir: string;        // default working directory PREFILLED in the new-session field (the user 2026-06-22). A session starts there; the tab menu's "Move to folder…" can change it later. Empty → the kernel's serve dir. ~ / $VAR expanded server-side.
  showBranch: boolean;       // chat bottom-bar: show the session's git branch (if any) beside the dir (the user 2026-06-23). OFF by default (the user 2026-08-10, trimming the statusline for narrow panes; an explicit stored true keeps showing it).
  tabCtx: TabCtxMode;        // chat tabs: WHEN the context gauge shows beside each session name (the user 2026-08-08) — "over50" (default: only once half full, so quiet tabs stay clean), "always", or "never".
  fileLinkPane: FileLinkPane;   // where a chat file-link click opens on the WEB while the Files pane is CLOSED: "chat" (the default: the viewer over the pane you clicked) or "pane" (the Files pane, a column of its own that comes forward and stays up). An OPEN Files pane takes the click whatever this says (file-route.ts). Read at click time (render.ts openPath); VS Code (the host editor) and standalone /chat (no shell to relay to) are unaffected.
  stripGroupRows: boolean;   // chat tabs, grouped by tag: start EVERY tag group on its own row (T264, the row breaks in render.ts). ON by default; off, the groups follow one another across the strip and wrap as they need, the untagged trail behind its divider. Per browser profile, like every setting here. Read by renderTabs and part of the strip's rebuild signature, so a gear flip repaints at once.
  chatScheme: ChatScheme;    // chat TEXT scheme (the user 2026-08-24): raises body-text contrast without collapsing the tool-dimmer-than-prose hierarchy. A scheme = a text-tier variable set (styles.css body.scheme-*); "default" applies nothing — today's values exactly.
  chatTabTheme: ChatTabTheme;   // LEGACY, derived (2026-08-28): the chat TAB STRIP's appearance (T113). Now computed from `theme` on every load/save ("classic" -> classic strip, anything else -> the yatharth strip) so older panes/extension builds keep working; never set it directly.
  theme: Theme;   // the OVERALL dashboard theme (the user 2026-08-27, promoting the tab-strip setting): "classic" = the pre-720 dark look; "yatharth" = dark + the contributed strip aesthetic (what chatTabTheme:"yatharth" was); "yatharth-light" = the warm light theme (body.theme-light + the yatharth strip). Migration: a store written before `theme` existed seeds it from chatTabTheme.
  denseChrome: boolean;   // chat page: COMPACT TABS AND AGENTS (the user 2026-09-08: on a phone, the tab strip and the background-work panel left about three lines of transcript in view). Density only, as a body class (dense-chrome.ts applyDenseChrome, run with the scheme and theme appliers): smaller tabs and group headers in the strip, tighter rows in the #bg-tasks panel with its list capped at about four rows. OFF by default: the strip and the panel are unchanged until the gear opts in. Distinct from `compact`, the transcript's own tidy-up (tool runs collapsed, thinking hidden).
}
// Solarized LIGHT is deliberately absent (the user allowed skipping it): its text tiers are designed
// for a paper-light ground and invert into mud on romp's dark canvas — an unreadable preset is worse
// than none.
export type ChatScheme = "default" | "high-contrast" | "solarized-dark";
export type ChatTabTheme = "classic" | "yatharth";
export function chatTabTheme(v: unknown): ChatTabTheme {
  return v === "yatharth" ? "yatharth" : "classic";
}
export type Theme = "classic" | "yatharth" | "yatharth-light";
export function theme(v: unknown): Theme {
  return v === "yatharth" || v === "yatharth-light" ? v : "classic";
}
export function chatScheme(v: unknown): ChatScheme {
  return v === "high-contrast" || v === "solarized-dark" ? v : "default";
}
// Where a chat file-link click opens on the web while the Files pane is closed. tabCtxMode's normalization
// idiom: only the literal "pane" is the opt-in; anything else a store might hold reads as the default, so
// a corrupt entry may cost the preference, never the click.
export type FileLinkPane = "chat" | "pane";
export function fileLinkPane(v: unknown): FileLinkPane {
  return v === "pane" ? "pane" : "chat";
}
// When the tab strip's context gauge shows. "over50" is the default (the user 2026-08-08): a gauge
// on every tab is clutter while nothing is filling up — it should appear only when it has news.
export type TabCtxMode = "always" | "over50" | "never";
// The gauge shipped for a few hours as a boolean toggle (2026-08-08) — normalize a stored
// true/false (or anything else unrecognized) into the mode enum: false was an explicit "hide"
// → never; true was the shipped default nobody chose → the new default. loadSettings applies
// this, so consumers always see a mode.
export function tabCtxMode(v: unknown): TabCtxMode {
  return v === "always" || v === "never" ? v : v === false ? "never" : "over50";
}
// NOTE: the old `explanations` pref is GONE (the user 2026-06-18) — cards no longer show the planner's
// hand-written "why" as their line; they show the distiller's summary instead (the why demotes to a hover).
// compact defaults ON (the user 2026-07-14): a fresh install reads the tidy transcript
// (thinking hidden, tool runs folded); the gear opts back into the full stream.
export const DEFAULT_SETTINGS: RompSettings = { compact: true, colormap: "aurora", subgoals: true, showIndexJudges: false, showTriageJudges: false, backend: "sdk", defaultDir: "", showBranch: false, tabCtx: "over50", fileLinkPane: "chat", stripGroupRows: true, chatScheme: "default", chatTabTheme: "classic", theme: "classic", denseChrome: false };
const KEY = "romp:settings";

export function loadSettings(): RompSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      const s = { ...DEFAULT_SETTINGS, ...parsed };
      s.tabCtx = tabCtxMode(s.tabCtx);   // a store written by the boolean-era gear holds true/false
      s.fileLinkPane = fileLinkPane(s.fileLinkPane);   // only "pane" opts in; anything else reads as the default
      s.chatScheme = chatScheme(s.chatScheme);   // unknown/legacy values normalize to "default"
      // theme migration (2026-08-28): a store from before `theme` existed seeds it from the old
      // tab-strip pick, so a yatharth strip stays a yatharth strip. chatTabTheme itself is DERIVED
      // from theme ever after (one axis of truth; older readers keep working off the alias).
      s.theme = theme("theme" in parsed ? parsed.theme : chatTabTheme(parsed.chatTabTheme));
      s.chatTabTheme = s.theme === "classic" ? "classic" : "yatharth";
      return s;
    }
  } catch { /* corrupt / unavailable → defaults */ }
  return { ...DEFAULT_SETTINGS };
}

export function saveSettings(patch: Partial<RompSettings>): RompSettings {
  const next = { ...loadSettings(), ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* ignore */ }
  return next;
}

// Fire `cb` when the settings change ANYWHERE they can change:
// - another same-origin tab (the browser views share localStorage → `storage` event);
// - THIS document (the gear modal now lives in the same page — VS Code's chat and feed
//   each host their own copy — and a same-document write never fires `storage`, which
//   left the compact toggle dead in the VS Code chat; gear.js's save() dispatches the
//   'romp:settings' window event instead, the user 2026-07-14).
// No-op where there's no window (tests, headless).
export function onExternalSettingsChange(cb: (s: RompSettings) => void): void {
  if (typeof window === "undefined") return;
  window.addEventListener("storage", (e: StorageEvent) => { if (e.key === KEY) cb(loadSettings()); });
  window.addEventListener("romp:settings", () => cb(loadSettings()));
}

// VS Code cross-pane settings sync, inbound side: each webview owns a separate
// localStorage, so a gear save in one pane reaches the others as a host-relayed
// {settingsSync} message (gear.js save() posts it; extension.ts fans it out).
// Applying = write our copy of the store, then raise the same-document signal so
// every consumer above reacts. Never re-posts — the host already broadcast it.
export function installSettingsSync(): void {
  if (typeof window === "undefined") return;
  window.addEventListener("message", (ev: MessageEvent) => {
    const m = ev.data;
    if (!m || m.type !== "settingsSync" || !m.settings) return;
    try { localStorage.setItem(KEY, JSON.stringify(m.settings)); } catch { /* ignore */ }
    try { window.dispatchEvent(new Event("romp:settings")); } catch { /* ignore */ }
  });
}
