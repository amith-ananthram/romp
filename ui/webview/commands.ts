// The command registry behind the palette (Cmd/Ctrl+P). Anything the dashboard can do gets
// registered here as a command; the palette is a fuzzy view over this list, so a new action
// becomes keyboard-reachable by registering it — never by minting another hotkey.

export type PaletteCommand = {
  id: string;       // stable, dot-namespaced ("session.open")
  title: string;    // what the palette shows and matches on — the user's words, verb first
  chord?: string;   // DEFAULT key binding, "Mod" form ("Mod+O" — Meta on a Mac, Ctrl elsewhere).
                    // The user's overrides live in the keybindings store (romp:keys); what a command
                    // actually answers to is effectiveChord(), and the palette's hotkey chip shows
                    // that, so a rebound command never advertises a stale default (the user 2026-08-09).
  hidden?: boolean; // bindable but not listed in the palette (palette.toggle: running "toggle the
  when?: () => boolean;   // listed only while true, re-read at every open (a pane whose control the gear hid; T317)
                    // palette" FROM the palette would just blink it)
  run: () => void;
};

// Every DEFAULT key binding, by command id — ONE table, so the palette registration below, the shell
// dispatcher, and the hover hints (keybindings' titleWithKey) can never disagree about what a command
// answers to out of the box. A command absent here ships unbound; the shortcuts dialog can still bind
// it, and every surface reads the result from the overrides store.
export const DEFAULT_CHORDS: Record<string, string> = {
  "session.jump": "Mod+O",
  "session.new": "Mod+Shift+O",
  "palette.toggle": "Mod+P",
  // LITERAL Ctrl, not Mod: these mirror the user's own Obsidian nav bindings (their vault's
  // hotkeys.json, verified 2026-08-14), where "Ctrl" is the Control key on every platform.
  "chat.navBack": "Ctrl+M",
  "chat.navForward": "Ctrl+,",
};

const commands = new Map<string, PaletteCommand>();

export function registerCommand(cmd: PaletteCommand): void {
  // re-registering an id replaces it, so a re-boot never duplicates; the default chord comes from the
  // one table above unless the caller carries its own
  commands.set(cmd.id, cmd.chord === undefined ? { ...cmd, chord: DEFAULT_CHORDS[cmd.id] } : cmd);
}

export function commandList(): PaletteCommand[] {
  return Array.from(commands.values());   // registration order — the palette's empty-query order
}

export function runCommand(id: string): boolean {
  const c = commands.get(id);
  if (!c) return false;
  c.run();
  return true;
}
