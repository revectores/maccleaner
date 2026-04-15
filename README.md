# maccleaner

A macOS storage usage analyzer. Scans and reports where disk space is being used across your Mac with a rich terminal UI.

## Features

- **Overview** — disk usage summary with colored bar chart across 27 predefined categories (caches, Xcode artifacts, Docker, language toolchains, etc.)
- **Volumes** — list all mounted volumes with size/used/available
- **Large files** — find files above a size threshold under any directory
- **Drill** — show the largest children of any directory
- **Dev junk** — scan for common developer clutter (`node_modules`, `build`, `__pycache__`, `target`, `Pods`, `.DS_Store`, etc.)
- **Caches** — list the largest application caches under `~/Library/Caches` and `/Library/Caches`

## Requirements

- macOS
- Python 3.10+
- [click](https://click.palletsprojects.com/) and [rich](https://github.com/Textualize/rich)

```
pip install click rich
```

## Usage

```
python maccleaner.py [COMMAND] [OPTIONS]
```

### Commands

| Command | Description |
|---|---|
| `overview` | Disk usage summary across all categories |
| `volumes` | List mounted volumes |
| `large-files` | Find large files in a directory |
| `drill [PATH]` | Show largest children of a directory |
| `dev-junk` | Find developer clutter |
| `caches` | Show largest application caches |

### Examples

```bash
# Full storage overview
python maccleaner.py overview

# Find files over 500 MB in Downloads
python maccleaner.py large-files --path ~/Downloads --min-size 500

# Drill into a specific directory
python maccleaner.py drill ~/Library --top 20

# Scan a project directory for dev junk
python maccleaner.py dev-junk --path ~/code

# Show top 10 caches
python maccleaner.py caches --top 10
```

### `large-files` options

| Flag | Default | Description |
|---|---|---|
| `--path` / `-p` | `~` | Directory to search |
| `--min-size` / `-s` | `100` | Minimum file size in MB |
| `--limit` / `-n` | `30` | Max results to show |

### `drill` options

| Flag | Default | Description |
|---|---|---|
| `--top` / `-n` | `15` | Number of children to show |

### `dev-junk` options

| Flag | Default | Description |
|---|---|---|
| `--path` / `-p` | `~` | Root directory to scan |
| `--limit` / `-n` | `20` | Top results per category |

### `caches` options

| Flag | Default | Description |
|---|---|---|
| `--top` / `-n` | `20` | Number of caches to show |
