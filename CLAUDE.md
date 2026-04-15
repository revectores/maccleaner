# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running

```bash
pip install click rich
python maccleaner.py [COMMAND]
```

Commands: `overview`, `volumes`, `large-files`, `drill`, `dev-junk`, `caches`

## Architecture

This is a single-file CLI tool (`maccleaner.py`). Key structure:

- **Size helpers** (`fmt_size`, `dir_size`, `top_children`) — formatting and `du`-based directory measurement, with stat fallback
- **`Category` dataclass + `build_categories()`** — defines the 27 storage categories scanned by `overview`; each category holds a list of paths and calls `dir_size` on them
- **`disk_info` / `volume_list` / `_data_volume_used()`** — disk/volume introspection via `shutil` and `df`
- **CLI commands** — each is a `@cli.command()` decorated with `click`; all output is rendered via `rich`

Scanning in `overview` is parallelized with `ThreadPoolExecutor(max_workers=6)`. The `dev-junk` command uses `os.walk` and prunes descent into matched directories to avoid double-counting.
