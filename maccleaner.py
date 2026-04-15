#!/usr/bin/env python3
"""
maccleaner — macOS storage usage analyzer
Scans and reports where disk space is being used across your Mac.
"""

import os
import sys
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.columns import Columns
from rich.rule import Rule

console = Console()
HOME = Path.home()


# ---------------------------------------------------------------------------
# Size helpers
# ---------------------------------------------------------------------------

def fmt_size(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n_bytes) < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} PB"


def dir_size(path: Path, follow_symlinks: bool = False) -> int:
    """Return total byte size of *path* using `du` for speed."""
    try:
        flags = ["-sk"]
        if not follow_symlinks:
            flags.append("-P")
        result = subprocess.run(
            ["du"] + flags + [str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 or result.stdout:
            # du -sk prints "<kbytes>\t<path>" — take the last summary line
            for line in reversed(result.stdout.strip().splitlines()):
                parts = line.split("\t", 1)
                if parts[0].isdigit():
                    return int(parts[0]) * 1024
    except (OSError, subprocess.TimeoutExpired):
        pass
    # fallback: stat only the top-level entry
    try:
        st = path.stat()
        return st.st_size
    except OSError:
        return 0


def top_children(path: Path, n: int = 10) -> list[tuple[int, Path]]:
    """Return the *n* largest direct children of *path*, sorted descending."""
    try:
        entries = [e for e in path.iterdir()]
    except (OSError, PermissionError):
        return []

    if not entries:
        return []

    # Run du on all children at once — one subprocess, much faster
    try:
        result = subprocess.run(
            ["du", "-skP"] + [str(e) for e in entries],
            capture_output=True, text=True, timeout=60,
        )
        sizes: dict[str, int] = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2 and parts[0].isdigit():
                sizes[parts[1]] = int(parts[0]) * 1024
    except (OSError, subprocess.TimeoutExpired):
        sizes = {}

    results: list[tuple[int, Path]] = []
    for e in entries:
        if str(e) in sizes:
            results.append((sizes[str(e)], e))
        else:
            try:
                results.append((e.stat().st_size, e))
            except OSError:
                results.append((0, e))

    results.sort(reverse=True)
    return results[:n]


# ---------------------------------------------------------------------------
# Category definitions
# ---------------------------------------------------------------------------

@dataclass
class Category:
    label: str
    paths: list[Path]
    description: str
    color: str = "cyan"
    size: int = field(default=0, init=False)
    missing: list[Path] = field(default_factory=list, init=False)
    existing: list[Path] = field(default_factory=list, init=False)

    def scan(self) -> None:
        for p in self.paths:
            if p.exists():
                self.existing.append(p)
                self.size += dir_size(p) if p.is_dir() else (p.stat().st_size if p.is_file() else 0)
            else:
                self.missing.append(p)


def build_categories() -> list[Category]:
    return [
        Category(
            "Applications",
            [Path("/Applications"), HOME / "Applications"],
            "Installed apps",
            "bright_blue",
        ),
        Category(
            "User Documents",
            [HOME / "Documents"],
            "~/Documents folder",
            "green",
        ),
        Category(
            "User Downloads",
            [HOME / "Downloads"],
            "~/Downloads folder",
            "yellow",
        ),
        Category(
            "User Desktop",
            [HOME / "Desktop"],
            "~/Desktop folder",
            "yellow",
        ),
        Category(
            "Photos Library",
            [HOME / "Pictures"],
            "~/Pictures (incl. Photos.app library)",
            "magenta",
        ),
        Category(
            "Music & Media",
            [HOME / "Music", HOME / "Movies"],
            "~/Music and ~/Movies",
            "magenta",
        ),
        Category(
            "iCloud Drive",
            [HOME / "Library/Mobile Documents"],
            "Files synced with iCloud Drive",
            "bright_cyan",
        ),
        Category(
            "User Caches",
            [HOME / "Library/Caches"],
            "Application caches (safe to clear)",
            "red",
        ),
        Category(
            "System Caches",
            [Path("/Library/Caches")],
            "System-wide caches",
            "red",
        ),
        Category(
            "User Logs",
            [HOME / "Library/Logs"],
            "Application log files",
            "red",
        ),
        Category(
            "System Logs",
            [Path("/private/var/log"), Path("/Library/Logs")],
            "System log files",
            "red",
        ),
        Category(
            "Trash",
            [HOME / ".Trash"],
            "Items in Trash",
            "bright_red",
        ),
        Category(
            "iOS / iPadOS Backups",
            [HOME / "Library/Application Support/MobileSync/Backup"],
            "iPhone/iPad backups via Finder",
            "bright_red",
        ),
        Category(
            "iOS Device Support",
            [HOME / "Library/Developer/Xcode/iOS DeviceSupport",
             HOME / "Library/Developer/Xcode/watchOS DeviceSupport",
             HOME / "Library/Developer/Xcode/tvOS DeviceSupport"],
            "Device symbol files used by Xcode",
            "bright_red",
        ),
        Category(
            "Xcode DerivedData",
            [HOME / "Library/Developer/Xcode/DerivedData"],
            "Xcode build intermediates",
            "bright_red",
        ),
        Category(
            "Xcode Archives",
            [HOME / "Library/Developer/Xcode/Archives"],
            "App archives created by Xcode",
            "red",
        ),
        Category(
            "Xcode Simulators",
            [HOME / "Library/Developer/CoreSimulator/Devices"],
            "iOS/macOS simulator images",
            "bright_red",
        ),
        Category(
            "Mail Downloads",
            [HOME / "Library/Mail",
             HOME / "Library/Containers/com.apple.mail"],
            "Mail.app data and attachments",
            "yellow",
        ),
        Category(
            "Time Machine Snapshots",
            [Path("/private/var/folders/.TimeMachineBackup"),
             Path("/.MobileBackups")],
            "Local Time Machine snapshots",
            "bright_red",
        ),
        Category(
            "Homebrew",
            [Path("/opt/homebrew"), Path("/usr/local/Homebrew"),
             Path("/usr/local/Cellar"), Path("/opt/homebrew/Cellar")],
            "Homebrew package manager files",
            "bright_yellow",
        ),
        Category(
            "Docker",
            [HOME / "Library/Containers/com.docker.docker",
             HOME / ".docker"],
            "Docker images, containers & volumes",
            "bright_blue",
        ),
        Category(
            "npm / node_modules",
            [HOME / ".npm", HOME / "Library/pnpm",
             HOME / ".pnpm-store", HOME / ".yarn"],
            "Node.js package caches",
            "bright_green",
        ),
        Category(
            "Python envs",
            [HOME / ".pyenv", HOME / ".conda", HOME / "opt/miniconda3",
             HOME / "opt/anaconda3", HOME / "miniconda3", HOME / "anaconda3"],
            "Python virtual environment managers",
            "bright_green",
        ),
        Category(
            "Rust toolchain",
            [HOME / ".rustup", HOME / ".cargo"],
            "Rust toolchain and cargo registry",
            "bright_green",
        ),
        Category(
            "VM & Parallels",
            [HOME / "Parallels", HOME / "Virtual Machines.localized",
             HOME / "Library/Containers/com.parallels.desktop.console"],
            "Virtual machine disk images",
            "bright_red",
        ),
        Category(
            "Fonts",
            [HOME / "Library/Fonts", Path("/Library/Fonts")],
            "Installed font files",
            "cyan",
        ),
        Category(
            "Other User Library",
            [HOME / "Library/Application Support"],
            "App support data (excl. items above)",
            "cyan",
        ),
    ]


# ---------------------------------------------------------------------------
# Disk info
# ---------------------------------------------------------------------------

def disk_info(path: Path = Path("/")) -> dict:
    total, used, free = shutil.disk_usage(path)
    return {"total": total, "used": used, "free": free}


def volume_list() -> list[dict]:
    """Return mounted volumes via df."""
    volumes = []
    try:
        result = subprocess.run(
            ["df", "-H"],
            capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.strip().splitlines()
        # macOS df -H columns: Filesystem Size Used Avail Capacity iused ifree %iused Mounted on
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 9:
                continue
            fs = parts[0]
            # skip pseudo/special filesystems
            if fs in {"devfs", "none", "map"} or fs.startswith("map "):
                continue
            volumes.append({
                "filesystem": fs,
                "size": parts[1],
                "used": parts[2],
                "avail": parts[3],
                "pct": parts[4],
                "mount": " ".join(parts[8:]),
            })
    except Exception:
        pass
    return volumes


# ---------------------------------------------------------------------------
# APFS helpers
# ---------------------------------------------------------------------------

def _data_volume_used() -> int:
    """Return bytes used on /System/Volumes/Data (the user-accessible APFS volume).

    On macOS Catalina+ the root mount (/) is the sealed read-only system
    snapshot; user data lives on /System/Volumes/Data.  shutil.disk_usage("/")
    actually returns the Data volume usage on most systems, but we query df
    explicitly to be sure.
    """
    try:
        result = subprocess.run(
            ["df", "-k", "/System/Volumes/Data"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.strip().splitlines()[1:]:
            parts = line.split()
            # df -k columns: Filesystem 1K-blocks Used Available Capacity iused ifree %iused Mounted
            if len(parts) >= 3 and parts[2].isdigit():
                return int(parts[2]) * 1024
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@click.group()
@click.version_option("1.0.0")
def cli():
    """maccleaner — find where your Mac's storage is being used."""


# ── overview ────────────────────────────────────────────────────────────────

@cli.command()
def overview():
    """Show disk usage summary and all category sizes."""
    info = disk_info()
    used_pct = info["used"] / info["total"] * 100

    bar_width = 40
    filled = int(bar_width * info["used"] / info["total"])
    bar_color = "red" if used_pct > 85 else ("yellow" if used_pct > 60 else "green")
    bar = f"[{bar_color}]{'█' * filled}[/{bar_color}]{'░' * (bar_width - filled)}"

    console.print()
    console.print(Panel(
        f"  [bold]Total:[/bold] {fmt_size(info['total'])}   "
        f"[bold]Used:[/bold] [{bar_color}]{fmt_size(info['used'])}[/{bar_color}]   "
        f"[bold]Free:[/bold] [green]{fmt_size(info['free'])}[/green]\n\n"
        f"  {bar}  [{bar_color}]{used_pct:.1f}%[/{bar_color}]",
        title="[bold white] Macintosh HD [/bold white]",
        border_style="bright_blue",
        padding=(0, 1),
    ))

    categories = build_categories()
    console.print(f"\n[dim]Scanning {len(categories)} categories...[/dim]\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Scanning...", total=len(categories))

        def scan_one(cat: Category) -> Category:
            cat.scan()
            progress.advance(task)
            return cat

        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(scan_one, c): c for c in categories}
            for f in as_completed(futures):
                f.result()

    categories.sort(key=lambda c: c.size, reverse=True)

    scanned_total = sum(c.size for c in categories)
    used_bytes    = info["used"]
    # shutil.disk_usage("/") on macOS reports /System/Volumes/Data used,
    # not the full APFS container. Use the Data volume for a fairer base.
    data_vol_used = _data_volume_used()
    base_used = data_vol_used if data_vol_used else used_bytes
    unaccounted = max(0, base_used - scanned_total)

    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white",
        border_style="bright_black",
        pad_edge=False,
        expand=True,
    )
    table.add_column("Category", style="bold", min_width=24)
    table.add_column("Size", justify="right", min_width=10)
    table.add_column("Bar", min_width=30)
    table.add_column("Description", style="dim")

    all_rows = list(categories) if categories else []
    max_size = max((c.size for c in all_rows if c.size), default=1)
    max_size = max(max_size, unaccounted)

    for cat in all_rows:
        if cat.size == 0:
            continue
        pct = cat.size / max_size
        bar_len = max(1, int(pct * 28))
        bar = f"[{cat.color}]{'█' * bar_len}[/{cat.color}]{'░' * (28 - bar_len)}"
        size_str = f"[{cat.color}]{fmt_size(cat.size)}[/{cat.color}]"
        table.add_row(cat.label, size_str, bar, cat.description)

    if unaccounted > 0:
        pct = unaccounted / max_size
        bar_len = max(1, int(pct * 28))
        bar = f"[dim]{'█' * bar_len}{'░' * (28 - bar_len)}[/dim]"
        table.add_section()
        table.add_row(
            "[dim]Unaccounted[/dim]",
            f"[dim]{fmt_size(unaccounted)}[/dim]",
            bar,
            "[dim]System files, APFS snapshots, sealed OS volume, hard-link dedup[/dim]",
        )

    console.print(table)

    # Explanation footer
    scanned_pct = scanned_total / base_used * 100 if base_used else 0
    console.print(
        f"\n[dim]Scanned [bold]{fmt_size(scanned_total)}[/bold] of "
        f"[bold]{fmt_size(base_used)}[/bold] used on /System/Volumes/Data "
        f"({scanned_pct:.0f}% explained).[/dim]"
    )
    if unaccounted > 0:
        console.print(
            "[dim]Unaccounted space is typically: sealed macOS system volume "
            "(/System ~15 GB), APFS local snapshots, /private/var system databases, "
            "and hard-linked files that du counts once across separate scans.[/dim]"
        )
    console.print()


# ── volumes ─────────────────────────────────────────────────────────────────

@cli.command()
def volumes():
    """List all mounted volumes with usage."""
    vols = volume_list()
    if not vols:
        console.print("[red]Could not retrieve volume list.[/red]")
        return

    table = Table(box=box.ROUNDED, header_style="bold white", border_style="bright_black", expand=True)
    table.add_column("Filesystem")
    table.add_column("Size", justify="right")
    table.add_column("Used", justify="right")
    table.add_column("Avail", justify="right")
    table.add_column("Use%", justify="right")
    table.add_column("Mount Point")

    for v in vols:
        pct_num = int(v["pct"].rstrip("%")) if v["pct"].rstrip("%").isdigit() else 0
        pct_color = "red" if pct_num > 85 else ("yellow" if pct_num > 60 else "green")
        table.add_row(
            v["filesystem"],
            v["size"],
            f"[{pct_color}]{v['used']}[/{pct_color}]",
            v["avail"],
            f"[{pct_color}]{v['pct']}[/{pct_color}]",
            v["mount"],
        )

    console.print()
    console.print(table)
    console.print()


# ── large-files ──────────────────────────────────────────────────────────────

@cli.command("large-files")
@click.option("--path", "-p", default=str(HOME), show_default=True, help="Directory to search.")
@click.option("--min-size", "-s", default=100, show_default=True, help="Minimum file size in MB.")
@click.option("--limit", "-n", default=30, show_default=True, help="Max results to show.")
def large_files(path: str, min_size: int, limit: int):
    """Find large files under a given directory."""
    root = Path(path).expanduser()
    min_bytes = min_size * 1024 * 1024
    results: list[tuple[int, Path]] = []

    console.print(f"\n[dim]Scanning [bold]{root}[/bold] for files ≥ {min_size} MB …[/dim]\n")

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as prog:
        prog.add_task("Scanning…")
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # skip common noisy directories
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".Trash") and d not in {
                    "proc", "sys", "dev",
                }
            ]
            for fname in filenames:
                fp = Path(dirpath) / fname
                try:
                    sz = fp.stat().st_size
                    if sz >= min_bytes:
                        results.append((sz, fp))
                except (OSError, PermissionError):
                    pass

    results.sort(reverse=True)
    results = results[:limit]

    if not results:
        console.print(f"[yellow]No files ≥ {min_size} MB found under {root}[/yellow]")
        return

    table = Table(box=box.ROUNDED, header_style="bold white", border_style="bright_black", expand=True)
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Size", justify="right", min_width=10)
    table.add_column("Path")

    for i, (sz, fp) in enumerate(results, 1):
        color = "red" if sz > 1 << 30 else ("yellow" if sz > 200 << 20 else "white")
        table.add_row(str(i), f"[{color}]{fmt_size(sz)}[/{color}]", str(fp))

    console.print(table)
    console.print(f"\n[dim]Found {len(results)} file(s)[/dim]\n")


# ── drill ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("path", default=str(HOME))
@click.option("--top", "-n", default=15, show_default=True, help="Number of children to show.")
def drill(path: str, top: int):
    """Drill into a directory and show its largest children."""
    root = Path(path).expanduser()
    if not root.exists():
        console.print(f"[red]Path not found:[/red] {root}")
        sys.exit(1)

    console.print(f"\n[dim]Measuring children of [bold]{root}[/bold]…[/dim]\n")

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as prog:
        prog.add_task("Measuring…")
        children = top_children(root, n=top)

    if not children:
        console.print("[yellow]No accessible children found.[/yellow]")
        return

    total = sum(s for s, _ in children)

    table = Table(box=box.ROUNDED, header_style="bold white", border_style="bright_black", expand=True)
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Size", justify="right", min_width=10)
    table.add_column("Bar", min_width=30)
    table.add_column("Name")

    max_sz = children[0][0] if children else 1
    for i, (sz, p) in enumerate(children, 1):
        pct = sz / max_sz if max_sz else 0
        bar_len = max(1, int(pct * 28))
        color = "red" if pct > 0.7 else ("yellow" if pct > 0.3 else "cyan")
        bar = f"[{color}]{'█' * bar_len}[/{color}]{'░' * (28 - bar_len)}"
        icon = "📁 " if p.is_dir() else "📄 "
        table.add_row(str(i), f"[{color}]{fmt_size(sz)}[/{color}]", bar, icon + p.name)

    console.print(table)
    console.print(f"\n[dim]Showing top {len(children)} of {len(list(root.iterdir()))} items "
                  f"(measured total: {fmt_size(total)})[/dim]\n")


# ── dev-junk ─────────────────────────────────────────────────────────────────

@cli.command("dev-junk")
@click.option("--path", "-p", default=str(HOME), show_default=True, help="Root directory to scan.")
@click.option("--limit", "-n", default=20, show_default=True, help="Top results per category.")
def dev_junk(path: str, limit: int):
    """Find common developer clutter (node_modules, build dirs, etc.)."""
    root = Path(path).expanduser()

    targets = {
        "node_modules": ("Node.js dependencies", "bright_yellow"),
        ".gradle":      ("Gradle build caches",  "yellow"),
        "build":        ("Generic build output",  "cyan"),
        "dist":         ("Distribution output",   "cyan"),
        ".next":        ("Next.js build cache",   "cyan"),
        ".nuxt":        ("Nuxt.js build cache",   "cyan"),
        "__pycache__":  ("Python bytecode cache", "bright_green"),
        ".pytest_cache":("pytest cache",          "bright_green"),
        ".mypy_cache":  ("mypy type cache",       "bright_green"),
        "target":       ("Rust/Java build dir",   "bright_red"),
        ".tox":         ("tox test envs",         "bright_green"),
        ".eggs":        ("Python egg artifacts",  "bright_green"),
        "Pods":         ("CocoaPods dependencies","magenta"),
        ".DS_Store":    ("macOS metadata files",  "dim"),
    }

    found: dict[str, list[tuple[int, Path]]] = {k: [] for k in targets}

    console.print(f"\n[dim]Scanning [bold]{root}[/bold] for dev clutter…[/dim]\n")

    skip = {".git", ".svn", "Library", ".Trash"}

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as prog:
        prog.add_task("Scanning…")
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in skip]
            dp = Path(dirpath)
            for name in list(dirnames):
                if name in targets:
                    full = dp / name
                    sz = dir_size(full)
                    found[name].append((sz, full))
                    dirnames.remove(name)  # don't descend into it
            # also catch files like .DS_Store
            for fname in filenames:
                if fname in targets:
                    fp = dp / fname
                    try:
                        found[fname].append((fp.stat().st_size, fp))
                    except OSError:
                        pass

    any_found = False
    for name, (desc, color) in targets.items():
        hits = sorted(found[name], reverse=True)[:limit]
        if not hits:
            continue
        any_found = True
        total = sum(s for s, _ in hits)
        console.print(Rule(f"[bold {color}]{name}[/bold {color}]  [dim]{desc}[/dim]  "
                           f"[bold]total: {fmt_size(total)}[/bold]"))
        table = Table(box=box.SIMPLE, header_style="bold white", show_header=False, pad_edge=False)
        table.add_column("Size", justify="right", min_width=10)
        table.add_column("Path")
        for sz, p in hits:
            table.add_row(f"[{color}]{fmt_size(sz)}[/{color}]", str(p))
        console.print(table)

    if not any_found:
        console.print("[green]No common developer clutter found.[/green]")
    console.print()


# ── caches ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--top", "-n", default=20, show_default=True, help="Number of caches to show.")
def caches(top: int):
    """Show the largest application caches."""
    cache_dirs = [HOME / "Library/Caches", Path("/Library/Caches")]
    entries: list[tuple[int, Path]] = []

    for cache_root in cache_dirs:
        if not cache_root.exists():
            continue
        try:
            for child in cache_root.iterdir():
                entries.append((dir_size(child) if child.is_dir() else child.stat().st_size, child))
        except (OSError, PermissionError):
            pass

    entries.sort(reverse=True)
    entries = entries[:top]

    if not entries:
        console.print("[yellow]No cache directories found.[/yellow]")
        return

    table = Table(box=box.ROUNDED, header_style="bold white", border_style="bright_black", expand=True)
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Size", justify="right", min_width=10)
    table.add_column("Cache")

    for i, (sz, p) in enumerate(entries, 1):
        color = "red" if sz > 500 << 20 else ("yellow" if sz > 100 << 20 else "white")
        table.add_row(str(i), f"[{color}]{fmt_size(sz)}[/{color}]", str(p))

    console.print()
    console.print(table)
    total = sum(s for s, _ in entries)
    console.print(f"\n[dim]Top {len(entries)} caches shown — total: [bold]{fmt_size(total)}[/bold][/dim]\n")


# ── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
