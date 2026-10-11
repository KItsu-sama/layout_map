"""layout_maper.py — Filesystem Snapshot Library

v0.0.2

A zero-dependency directory scanner that builds a tree model of a filesystem,
respects a lightweight subset of .gitignore rules, and exports to text,
Markdown, or JSON formats.

This library is completely independent of the Ritsu framework. It exposes a
simple, typed public API for scanning and querying directory structures.

Public API
----------
    scan(path, ...)          → Node
    save_json(node, path, ...)
    save_markdown(node, path)
    save_text(node, path)
    load_json(path)          → Node
    render_tree(node)        → list[str]
    node_to_dict(node, ...)  → dict
    summarize(node)          → dict

Usage
-----
    >>> from layout_maper import scan, summarize
    >>> root = scan(".")
    >>> info = summarize(root)
    >>> print(f"{info['files']} files, {info['dirs']} directories")
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


# =========================
# Node Model
# =========================


@dataclass
class Node:
    """A single entry in the filesystem tree."""

    name: str
    path: str
    is_dir: bool
    children: list[Node] = field(default_factory=list)

    # Derived metadata
    type: str = field(init=False)
    size: int | None = field(init=False)

    def __post_init__(self) -> None:
        self.type = "directory" if self.is_dir else "file"
        self.size = self._get_size()

    def _get_size(self) -> int | None:
        if self.is_dir:
            return None
        try:
            return os.path.getsize(self.path)
        except OSError:
            return None


# =========================
# .gitignore handling
# =========================


def load_gitignore(root_path: str) -> list[str]:
    """Load .gitignore-style patterns from *root_path*/.gitignore.

    Returns a list of patterns with trailing slashes stripped.  The pattern
    ``.git`` is always appended so that the repository metadata is excluded.
    """
    patterns: list[str] = []
    ignore_file = os.path.join(root_path, ".gitignore")

    if os.path.exists(ignore_file):
        try:
            with open(ignore_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    patterns.append(line.rstrip("/"))
        except (UnicodeDecodeError, OSError):
            # Fallback to system encoding if UTF-8 fails
            try:
                with open(ignore_file, "r", encoding=sys.getfilesystemencoding()) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        patterns.append(line.rstrip("/"))
            except OSError:
                pass  # Silently ignore unreadable .gitignore

    patterns.append(".git")
    return patterns


def is_ignored(path: str, root_path: str, patterns: list[str]) -> bool:
    """Check whether *path* matches any of the ignore *patterns*."""
    rel_path = os.path.relpath(path, root_path).replace("\\", "/")

    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        if fnmatch.fnmatch(rel_path, f"{pattern}/*"):
            return True
        if fnmatch.fnmatch(os.path.basename(rel_path), pattern):
            return True

    return False


# =========================
# Scan statistics
# =========================


@dataclass
class ScanStats:
    """Aggregated counters collected during a directory scan."""

    directories: int = 0
    files: int = 0
    ignored: int = 0
    errors: int = 0


# =========================
# Tree Builder (internal)
# =========================


def _build_tree(
    path: str,
    root_path: str,
    ignore_patterns: list[str],
    stats: ScanStats,
    *,
    depth: int | None = None,
    current_depth: int = 0,
) -> Node | None:
    """Recursively build a :class:`Node` tree for the filesystem at *path*.

    This is the internal workhorse --- prefer the public :func:`scan` wrapper.
    """
    if path != root_path and is_ignored(path, root_path, ignore_patterns):
        stats.ignored += 1
        return None

    name = Path(path).name or Path(root_path).resolve().name

    try:
        node = Node(name=name, path=path, is_dir=os.path.isdir(path))
    except OSError:
        stats.errors += 1
        return None

    if node.is_dir:
        stats.directories += 1
    else:
        stats.files += 1

    if node.is_dir and (depth is None or current_depth < depth):
        try:
            for item in sorted(os.listdir(path)):
                child_path = os.path.join(path, item)
                child = _build_tree(
                    child_path,
                    root_path,
                    ignore_patterns,
                    stats,
                    depth=depth,
                    current_depth=current_depth + 1,
                )
                if child:
                    node.children.append(child)
        except PermissionError:
            stats.errors += 1
            pass

    return node


# =========================
# Public scan API
# =========================


def scan(
    path: str = ".",
    *,
    depth: int | None = None,
    extra_ignore: list[str] | None = None,
) -> tuple[Node | None, ScanStats]:
    """Scan a directory and return the root :class:`Node` and scan statistics.

    Parameters
    ----------
    path:
        Directory to scan (default: current working directory).
    depth:
        Maximum recursion depth (``None`` = unlimited, ``0`` = root only).
    extra_ignore:
        Additional .gitignore-style patterns to ignore.

    Returns
    -------
    (root_node, stats)
        The root node is ``None`` when the tree could not be built.
    """
    root_path = os.path.abspath(path)
    ignore_patterns = load_gitignore(root_path)

    if extra_ignore:
        ignore_patterns.extend(extra_ignore)

    stats = ScanStats()
    root_node = _build_tree(
        root_path,
        root_path,
        ignore_patterns,
        stats,
        depth=depth,
        current_depth=0,
    )
    return root_node, stats


# =========================
# Tree Rendering
# =========================


def render_tree(node: Node) -> list[str]:
    """Render a :class:`Node` tree into a list of text lines.

    The output uses Unicode box-drawing characters (``├──``, ``└──``, ``│``).
    """

    def _walk(
        n: Node,
        is_root: bool = True,
        prefix: str = "",
        lines: list[str] | None = None,
    ) -> list[str]:
        if lines is None:
            lines = []

        if is_root:
            display_name = f"{n.name}/" if n.is_dir else n.name
            lines.append(display_name)

        for i, child in enumerate(n.children):
            is_last = i == len(n.children) - 1
            connector = "└── " if is_last else "├── "
            child_name = f"{child.name}/" if child.is_dir else child.name
            lines.append(f"{prefix}{connector}{child_name}")

            if child.is_dir and child.children:
                new_prefix = prefix + ("    " if is_last else "│   ")
                _walk(child, is_root=False, prefix=new_prefix, lines=lines)

        return lines

    return _walk(node)


# =========================
# Summarization
# =========================


def summarize(node: Node) -> dict[str, Any]:
    """Return a concise summary dictionary for a scanned tree.

    Includes total file / directory counts, extension breakdown, and top-level
    module names.
    """
    total_files = 0
    total_dirs = 0
    extension_counts: dict[str, int] = {}

    def _walk(n: Node) -> None:
        nonlocal total_files, total_dirs
        if n.is_dir:
            total_dirs += 1
        else:
            total_files += 1
            ext = Path(n.name).suffix or "(no extension)"
            extension_counts[ext] = extension_counts.get(ext, 0) + 1
        for child in n.children:
            _walk(child)

    _walk(node)

    return {
        "project": node.name.rstrip("/"),
        "directories": max(0, total_dirs - 1),  # exclude root
        "files": total_files,
        "extensions": dict(
            sorted(extension_counts.items(), key=lambda x: -x[1])
        ),
    }


# =========================
# Find helpers
# =========================


def find_file(root: Node, name: str) -> Node | None:
    """Find the first file or directory whose *name* matches exactly.

    This performs a depth-first search and returns the first match.
    """
    if root.name == name:
        return root
    for child in root.children:
        result = find_file(child, name)
        if result is not None:
            return result
    return None


def find_pattern(root: Node, pattern: str) -> list[Node]:
    """Find all nodes whose name matches the glob *pattern*."""
    matches: list[Node] = []
    if fnmatch.fnmatch(root.name, pattern):
        matches.append(root)
    for child in root.children:
        matches.extend(find_pattern(child, pattern))
    return matches


# =========================
# Serialization
# =========================


def node_to_dict(
    node: Node,
    root_path: str = "",
    *,
    include_size: bool = False,
) -> dict[str, Any]:
    """Convert a :class:`Node` tree into a plain nested dictionary.

    Paths are stored relative to *root_path* (default: absolute paths).
    """
    rel_path: str
    if root_path:
        rel_path = os.path.relpath(node.path, root_path).replace("\\", "/")
        if rel_path == ".":
            rel_path = node.name
    else:
        rel_path = node.path

    result: dict[str, Any] = {
        "name": node.name,
        "path": rel_path,
        "type": node.type,
    }

    if include_size and not node.is_dir:
        result["size"] = node.size

    if node.is_dir:
        result["children_count"] = len(node.children)
        result["children"] = [
            node_to_dict(child, root_path, include_size=include_size)
            for child in node.children
        ]

    return result


def _dict_to_node(data: dict[str, Any], parent_path: str = "") -> Node:
    """Recursively reconstruct a :class:`Node` from a dictionary."""
    node_path = data.get("path", data["name"])
    full_path = os.path.join(parent_path, node_path) if parent_path else node_path

    node = Node(
        name=data["name"],
        path=full_path,
        is_dir=data["type"] == "directory",
    )

    for child_data in data.get("children", []):
        child = _dict_to_node(child_data, full_path)
        node.children.append(child)

    return node


# =========================
# Save helpers
# =========================


def save_json(
    node: Node,
    path: str = "layout.json",
    *,
    include_size: bool = False,
    include_metadata: bool = False,
    stats: ScanStats | None = None,
    depth: int | None = None,
) -> None:
    """Export a :class:`Node` tree to a JSON file.

    Parameters
    ----------
    node:
        Root node to export.
    path:
        Output file path.
    include_size:
        Include file sizes in the output.
    include_metadata:
        Wrap the tree in a metadata envelope (project name, timestamp, stats).
    stats:
        Scan statistics (only used when *include_metadata* is ``True``).
    depth:
        Scan depth (only used when *include_metadata* is ``True``).
    """
    tree_data = node_to_dict(
        node, os.path.dirname(node.path) or ".", include_size=include_size
    )

    if include_metadata and stats is not None:
        data: dict[str, Any] = {
            "metadata": {
                "project": node.name.rstrip("/"),
                "generated": datetime.now(timezone.utc).isoformat(),
                "depth": depth,
                "directories": max(0, stats.directories - 1),
                "files": stats.files,
                "ignored": stats.ignored,
                "errors": stats.errors,
            },
            "tree": tree_data,
        }
    else:
        data = tree_data

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_text(node: Node, path: str = "layout.txt") -> None:
    """Export a :class:`Node` tree as a plain text directory listing."""
    lines = render_tree(node)
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def save_markdown(node: Node, path: str = "layout.md") -> None:
    """Export a :class:`Node` tree wrapped in a Markdown fenced code block."""
    lines = render_tree(node)
    with open(path, "w", encoding="utf-8") as f:
        f.write("```\n")
        for line in lines:
            f.write(line + "\n")
        f.write("```\n")


# =========================
# Load helpers
# =========================


def load_json(path: str) -> Node:
    """Load a JSON layout file back into a :class:`Node` tree.

    Supports both bare tree output and metadata-enveloped output.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Strip metadata envelope if present
    tree_data = data.get("tree", data)

    return _dict_to_node(tree_data)


# =========================
# CLI Entry Point
# =========================


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filesystem Snapshot Tool — scan a directory and export its layout.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory)",
    )
    parser.add_argument(
        "--ignore",
        type=str,
        nargs="*",
        default=[],
        help="Additional patterns to ignore (space-separated)",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Display layout in console",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="Export layout to text file (layout.txt)",
    )
    parser.add_argument(
        "--md",
        action="store_true",
        help="Export layout to Markdown file (layout.md)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Export layout to JSON file (layout.json)",
    )
    parser.add_argument(
        "--size",
        action="store_true",
        help="Include file sizes in JSON output",
    )
    parser.add_argument(
        "--metadata",
        action="store_true",
        help="Include metadata header in JSON output",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=None,
        help="Depth for traversal (0 = only root, omitted = unlimited)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="layout",
        help="Base filename for output files (default: layout)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.  Scans the given directory and exports as requested.

    When called without arguments it prompts interactively.
    """
    args = _parse_args(argv)

    # Determine output modes
    if args.console or args.text or args.md or args.json:
        show_console = args.console
        export_text = args.text
        export_md = args.md
        export_json = args.json
    else:
        show_console = _ask_yes_no("Display layout in console? (y/n): ")
        export_text = _ask_yes_no("Export layout to text file? (y/n): ")
        export_md = _ask_yes_no("Export layout to Markdown file? (y/n): ")
        export_json = _ask_yes_no("Export layout to JSON file? (y/n): ")

    if not any([show_console, export_text, export_md, export_json]):
        raise SystemExit("No output selected. Exiting.")

    root_node, stats = scan(
        args.path,
        depth=args.depth,
        extra_ignore=args.ignore or None,
    )

    if root_node is None:
        raise SystemExit("Failed to build filesystem tree.")

    base = args.output

    if show_console:
        print(f"\nFilesystem layout for: {os.path.abspath(args.path)}\n")
        for line in render_tree(root_node):
            print(line)

    if export_text:
        save_text(root_node, f"{base}.txt")
        print(f"  ✓  Text → {base}.txt")

    if export_md:
        save_markdown(root_node, f"{base}.md")
        print(f"  ✓  Markdown → {base}.md")

    if export_json:
        save_json(
            root_node,
            f"{base}.json",
            include_size=args.size,
            include_metadata=args.metadata,
            stats=stats,
            depth=args.depth,
        )
        print(f"  ✓  JSON → {base}.json")

    print(f"\nSummary: {stats.directories} dirs, {stats.files} files, "
          f"{stats.ignored} ignored, {stats.errors} errors.")


def _ask_yes_no(prompt: str) -> bool:
    while True:
        value = input(prompt).strip().lower()
        if value in ("y", "n"):
            return value == "y"
        print("Please enter 'y' or 'n'.")


# Allow running as a script or via ``python -m layout_maper``
if __name__ == "__main__":
    main()

