# cli/ — terminal tools

Python implementations of the terminal-facing romp commands. Run them via
their `bin/` symlinks (`romp version`, `romp update`)
— see `bin/README.md` for the command surface.

| File | Command | What it is |
|---|---|---|
| `version.py` | `romp version` | Version report across the moving parts (working tree vs running kernel vs built bundles). |
| `update.py` | `romp update [host]` | Pushes this machine's committed romp to attached remote kernels over ssh and restarts them. |
| `spend_rebuild.py` | `romp spend-rebuild` | Recounts the token columns of the spend ledger (`spend.json`) from the transcripts' own per-call usage — the recorder mis-diffed the CLI's per-turn `usage` until 2026-09-06. Dollars and turn counts untouched; dry run by default, `--apply` writes and keeps a backup. |
