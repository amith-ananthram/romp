// Shared shape for a parsed live AskUserQuestion picker. The PARSER now lives in Python
// (the kernel pushes askLive from the SDK's own control request); the webview only needs these TYPES to render
// the picker the kernel pushes (askLive). The kernel emits exactly this shape (camelCase keys).

export interface AskOption {
  n: number;            // 1-based ordinal AS THE TUI NUMBERS IT — equals its arrow-nav position
  label: string;
  desc?: string;
  selected: boolean;    // the ❯ cursor is currently on this row
  checked?: boolean;    // multi-select checkbox state ([✔]/[ ]); undefined for non-checkbox rows
  preview?: string;     // THIS option's own preview box (SDK backend carries one per option, so the webview
                        // can swap previews on ↑/↓ locally — no round-trip). undefined only from an older
                        // path, which only scrapes ONE preview (the focused option) into ParsedAsk.preview.
}

export type AskKind = "single" | "multi" | "submit";

export interface ParsedAsk {
  kind: AskKind;
  header?: string;      // the ☐ chip / tab-bar name
  question?: string;
  options: AskOption[];
  cursor: number;       // n of the ❯ row (defaults to the first option)
  cursorFound: boolean; // false when no ❯ was detected — capture unreliable, don't send blind keys
  chosen?: string[];    // submit screen: the answers under review (all questions, flattened)
  pairs?: { q: string; a: string }[]; // submit screen: every ● question / → answer pair, in order
  multiSelect: boolean; // back-compat: kind === "multi"
  preview?: string;     // the side-by-side preview box (verbatim monospace text, with its border) the
                        // focused option draws to the RIGHT of the option list — undefined when none.
  previewKind?: "diff" | "plan"; // how to render `preview`: "diff" → colorize +/- lines (Edit/Write
                        // permission on the SDK backend), "plan" → plain (ExitPlanMode). undefined → verbatim
                        // monospace (a verbatim box). (the user 2026-06-27.)
  sig: string;          // change-signature, so the host only re-posts when it actually changed
}
