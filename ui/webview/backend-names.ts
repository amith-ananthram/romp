// The backends as the user reads them (T288, the user 2026-09-09): the internal ids ("sdk", "codex") and the
// kernel protocol are unchanged; every label a person sees comes from here, so the picker toggles, the gear's
// Default backend list, the tab tooltip's Backend row and any chip agree. The default needs no qualifier, so
// the word "SDK" appears in no user-facing copy: it is "Claude Code"; the OpenAI agent is "Codex". The terminal
// backend ("Claude Code (tmux)") is being removed (the user 2026-09-10, T331 the UI stage): nothing offers it
// any more, and a saved default of it reads as Claude Code.
export type BackendId = "sdk" | "codex";
export const BACKEND_LABEL: Readonly<Record<BackendId, string>> = { sdk: "Claude Code", codex: "Codex" };

/** The label for a backend id; an unknown id reads as itself (never a lie, never blank). */
export function backendLabel(be: string | null | undefined): string {
  return (be && (BACKEND_LABEL as Record<string, string>)[be]) || String(be || "");
}

/** What the picker OFFERS: Claude Code and Codex, always. */
export function offeredBackends(): BackendId[] {
  return ["sdk", "codex"];
}

/** The default a new session starts from: the saved preference when it is on offer, else Claude Code. A saved
 *  default that is no longer offered (the retired terminal backend, an unknown value) reads as Claude Code,
 *  never as an undefined value. */
export function effectiveDefaultBackend(pref: string | null | undefined): BackendId {
  const p = (pref || "sdk") as BackendId;
  return (offeredBackends() as string[]).includes(p) ? p : "sdk";
}
