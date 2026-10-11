"""layout_maper_ui.py — Desktop UI for layout_maper

Zero-dependency (tkinter only) front-end. Keep it in the SAME folder as
layout_maper.py, then launch it with lmap.cmd (or `python layout_maper_ui.py`).
You never need to copy anything into your projects.

    lmap.cmd                  -> opens the UI where you left off
    lmap.cmd "D:\\some\\folder" -> opens the UI with that folder as the root
"""

from __future__ import annotations

import json
import os
import queue
import string
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout_maper as lm  # noqa: E402

CONFIG_FILE = Path.home() / ".layout_maper_ui.json"
DEFAULT_IGNORE = "node_modules __pycache__ .venv venv .mypy_cache .pytest_cache"
PREVIEW_LIMIT = 5000  # max lines drawn in the preview box (exports are never cut)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def list_drives() -> list[str]:
    """Return available drive roots (Windows) or sensible roots (others)."""
    if os.name == "nt":
        try:
            import ctypes

            mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
            return [f"{c}:\\" for i, c in enumerate(string.ascii_uppercase) if mask & (1 << i)]
        except Exception:
            return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    return ["/", str(Path.home())]


def open_in_explorer(path: str) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def clean_path(raw: str) -> str:
    """Strip quotes/whitespace (cmd passes `"C:\\dir\\"` as `C:\\dir"`)."""
    return raw.strip().strip('"').strip("'").strip()


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------


class App(tk.Tk):
    def __init__(self, start_path: str | None = None) -> None:
        super().__init__()
        self.title("Layout Mapper")
        self.geometry("1040x720")
        self.minsize(860, 560)

        self.cfg = self._load_cfg()
        self.q: queue.Queue = queue.Queue()
        self.running = False
        self.last_lines: list[str] = []
        self.last_outdir = ""
        self.entries: list[str] = []  # parallel to listbox rows

        start = clean_path(start_path) if start_path else ""
        if not start or not os.path.isdir(start):
            start = self.cfg.get("root") or os.getcwd()
        if not os.path.isdir(start):
            start = str(Path.home())
        start = os.path.abspath(start)

        # state vars
        self.root_var = tk.StringVar(value=start if start_path else self.cfg.get("root", ""))
        self.browse_var = tk.StringVar(value=start)
        self.filter_var = tk.StringVar()
        self.hidden_var = tk.BooleanVar(value=self.cfg.get("hidden", False))
        self.limit_depth = tk.BooleanVar(value=self.cfg.get("limit_depth", False))
        self.depth_var = tk.IntVar(value=self.cfg.get("depth", 3))
        self.ignore_var = tk.StringVar(value=self.cfg.get("ignore", DEFAULT_IGNORE))
        self.preview_var = tk.BooleanVar(value=self.cfg.get("preview", True))
        self.text_var = tk.BooleanVar(value=self.cfg.get("text", False))
        self.md_var = tk.BooleanVar(value=self.cfg.get("md", True))
        self.json_var = tk.BooleanVar(value=self.cfg.get("json", False))
        self.size_var = tk.BooleanVar(value=self.cfg.get("size", False))
        self.meta_var = tk.BooleanVar(value=self.cfg.get("meta", False))
        self.name_var = tk.StringVar(value=self.cfg.get("name", "layout"))
        self.outdir_var = tk.StringVar(value=self.cfg.get("outdir", ""))
        self.status_var = tk.StringVar(value="Ready.")

        self._build()
        self.filter_var.trace_add("write", lambda *_: self.refresh())
        self.hidden_var.trace_add("write", lambda *_: self.refresh())
        self.go(start, quiet=True)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

    # ------------------------------------------------------------------ UI

    def _build(self) -> None:
        style = ttk.Style(self)
        for theme in ("vista", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break

        # --- Root row
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(top, text="Root folder:", font=("Segoe UI", 10, "bold")).pack(side="left")
        root_entry = ttk.Entry(top, textvariable=self.root_var)
        root_entry.pack(side="left", fill="x", expand=True, padx=8)
        root_entry.bind("<Return>", lambda _e: self.run())
        ttk.Button(top, text="Browse…", command=self.pick_root).pack(side="left")
        ttk.Label(
            self,
            text="Type/paste a path, browse below, or use the buttons under the folder list. "
            "If empty, the folder you're browsing is used.",
            foreground="#666",
        ).pack(anchor="w", padx=12)

        # --- Middle: browser | preview
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=10, pady=8)

        left = ttk.LabelFrame(pw, text="Find a folder")
        right = ttk.LabelFrame(pw, text="Preview")
        pw.add(left, weight=1)
        pw.add(right, weight=2)

        nav = ttk.Frame(left)
        nav.pack(fill="x", padx=6, pady=(6, 2))
        self.drive_cb = ttk.Combobox(nav, values=list_drives(), width=6, state="readonly")
        self.drive_cb.pack(side="left")
        self.drive_cb.bind("<<ComboboxSelected>>", lambda _e: self.go(self.drive_cb.get()))
        ttk.Button(nav, text="⬆ Up", width=6, command=self.up).pack(side="left", padx=4)
        path_entry = ttk.Entry(nav, textvariable=self.browse_var)
        path_entry.pack(side="left", fill="x", expand=True)
        path_entry.bind("<Return>", lambda _e: self.go(self.browse_var.get()))

        opts = ttk.Frame(left)
        opts.pack(fill="x", padx=6, pady=2)
        ttk.Label(opts, text="Filter:").pack(side="left")
        ttk.Entry(opts, textvariable=self.filter_var, width=16).pack(side="left", padx=4)
        ttk.Checkbutton(opts, text="Show hidden", variable=self.hidden_var).pack(side="left", padx=6)

        lf = ttk.Frame(left)
        lf.pack(fill="both", expand=True, padx=6, pady=4)
        sb = ttk.Scrollbar(lf, orient="vertical")
        self.listbox = tk.Listbox(lf, activestyle="dotbox", yscrollcommand=sb.set, exportselection=False)
        sb.config(command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda _e: self.open_selected())
        self.listbox.bind("<Return>", lambda _e: self.open_selected())
        self.listbox.bind("<BackSpace>", lambda _e: self.up())

        self.count_lbl = ttk.Label(left, text="", foreground="#666")
        self.count_lbl.pack(anchor="w", padx=8)

        btns = ttk.Frame(left)
        btns.pack(fill="x", padx=6, pady=(2, 8))
        ttk.Button(btns, text="Open ▸", command=self.open_selected).pack(side="left")
        ttk.Button(btns, text="Root = this folder", command=self.set_root_here).pack(side="left", padx=4)
        ttk.Button(btns, text="Root = this folder + Run", command=self.set_root_and_run).pack(side="left")

        # preview text
        pf = ttk.Frame(right)
        pf.pack(fill="both", expand=True, padx=6, pady=6)
        ysb = ttk.Scrollbar(pf, orient="vertical")
        xsb = ttk.Scrollbar(pf, orient="horizontal")
        self.preview = tk.Text(
            pf, wrap="none", font=("Consolas", 10), yscrollcommand=ysb.set, xscrollcommand=xsb.set,
            state="disabled",
        )
        ysb.config(command=self.preview.yview)
        xsb.config(command=self.preview.xview)
        ysb.pack(side="right", fill="y")
        xsb.pack(side="bottom", fill="x")
        self.preview.pack(side="left", fill="both", expand=True)

        # --- Options
        of = ttk.LabelFrame(self, text="Options")
        of.pack(fill="x", padx=10, pady=(0, 6))

        r1 = ttk.Frame(of)
        r1.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(r1, text="Output:").pack(side="left")
        for text, var in (("Preview", self.preview_var), (".txt", self.text_var),
                          (".md", self.md_var), (".json", self.json_var)):
            ttk.Checkbutton(r1, text=text, variable=var).pack(side="left", padx=6)
        ttk.Checkbutton(r1, text="JSON: sizes", variable=self.size_var).pack(side="left", padx=(14, 4))
        ttk.Checkbutton(r1, text="JSON: metadata", variable=self.meta_var).pack(side="left", padx=4)
        ttk.Checkbutton(r1, text="Limit depth:", variable=self.limit_depth).pack(side="left", padx=(24, 2))
        ttk.Spinbox(r1, from_=0, to=99, width=4, textvariable=self.depth_var).pack(side="left")

        r2 = ttk.Frame(of)
        r2.pack(fill="x", padx=8, pady=2)
        ttk.Label(r2, text="Also ignore:").pack(side="left")
        ttk.Entry(r2, textvariable=self.ignore_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(r2, text="(space-separated; .gitignore in root is always honored)",
                  foreground="#666").pack(side="left")

        r3 = ttk.Frame(of)
        r3.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Label(r3, text="File name:").pack(side="left")
        ttk.Entry(r3, textvariable=self.name_var, width=14).pack(side="left", padx=6)
        ttk.Label(r3, text="Save to:").pack(side="left", padx=(10, 0))
        ttk.Entry(r3, textvariable=self.outdir_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(r3, text="Browse…", command=self.pick_outdir).pack(side="left")
        ttk.Label(r3, text="(blank = inside the root folder)", foreground="#666").pack(side="left", padx=6)

        # --- Bottom bar
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        self.run_btn = ttk.Button(bar, text="▶  Run", command=self.run)
        self.run_btn.pack(side="left")
        self.copy_btn = ttk.Button(bar, text="Copy tree", command=self.copy_tree, state="disabled")
        self.copy_btn.pack(side="left", padx=6)
        self.open_btn = ttk.Button(bar, text="Open output folder", command=self.open_output, state="disabled")
        self.open_btn.pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=120)
        self.progress.pack(side="right")
        ttk.Label(bar, textvariable=self.status_var).pack(side="right", padx=10)

    # ------------------------------------------------------------ browsing

    def go(self, path: str, quiet: bool = False) -> None:
        path = clean_path(path)
        if not path:
            return
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            if not quiet:
                self.status_var.set(f"Not a folder: {path}")
            self.bell()
            return
        try:
            os.listdir(path)
        except OSError as e:
            self.status_var.set(f"Can't open: {e.strerror or e}")
            self.bell()
            return
        self.browse_var.set(path)
        if os.name == "nt":
            drive = os.path.splitdrive(path)[0] + "\\"
            if drive in self.drive_cb["values"]:
                self.drive_cb.set(drive)
        self.refresh()

    def refresh(self) -> None:
        path = self.browse_var.get()
        self.listbox.delete(0, "end")
        self.entries = []
        names: list[str] = []
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if e.is_dir():
                            names.append(e.name)
                    except OSError:
                        continue
        except OSError:
            return
        if not self.hidden_var.get():
            names = [n for n in names if not n.startswith(".")]
        flt = self.filter_var.get().strip().lower()
        if flt:
            names = [n for n in names if flt in n.lower()]
        names.sort(key=str.lower)

        p = Path(path)
        if p.parent != p:
            self.listbox.insert("end", "⬆  ..")
            self.entries.append("..")
        for n in names:
            self.listbox.insert("end", "📁  " + n)
            self.entries.append(n)
        self.count_lbl.config(text=f"{len(names)} folder(s)")

    def up(self) -> None:
        p = Path(self.browse_var.get())
        if p.parent != p:
            self.go(str(p.parent))

    def open_selected(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        name = self.entries[sel[0]]
        if name == "..":
            self.up()
        else:
            self.go(os.path.join(self.browse_var.get(), name))

    def set_root_here(self) -> None:
        self.root_var.set(self.browse_var.get())
        self.status_var.set("Root set.")

    def set_root_and_run(self) -> None:
        self.set_root_here()
        self.run()

    def pick_root(self) -> None:
        d = filedialog.askdirectory(initialdir=self.browse_var.get(), title="Choose root folder")
        if d:
            d = os.path.abspath(d)
            self.root_var.set(d)
            self.go(d)

    def pick_outdir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.outdir_var.get() or self.browse_var.get(),
                                    title="Choose output folder")
        if d:
            self.outdir_var.set(os.path.abspath(d))

    # ----------------------------------------------------------------- run

    def run(self) -> None:
        if self.running:
            return
        root = clean_path(self.root_var.get()) or self.browse_var.get()
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            messagebox.showerror("Layout Mapper", f"Not a folder:\n{root}")
            return

        depth = None
        if self.limit_depth.get():
            try:
                depth = max(0, int(self.depth_var.get()))
            except (tk.TclError, ValueError):
                messagebox.showerror("Layout Mapper", "Depth must be a whole number.")
                return

        if depth is None and Path(root).parent == Path(root):
            if not messagebox.askyesno(
                "Scan a whole drive?",
                f"{root} is a drive root with no depth limit — this can take a very long time "
                "and produce a huge file.\n\nContinue anyway?",
            ):
                return

        o = {
            "root": root,
            "depth": depth,
            "ignore": self.ignore_var.get().split(),
            "preview": self.preview_var.get(),
            "text": self.text_var.get(),
            "md": self.md_var.get(),
            "json": self.json_var.get(),
            "size": self.size_var.get(),
            "meta": self.meta_var.get(),
            "name": (self.name_var.get().strip() or "layout"),
            "outdir": clean_path(self.outdir_var.get()),
        }
        if not any(o[k] for k in ("preview", "text", "md", "json")):
            messagebox.showinfo("Layout Mapper", "Pick at least one output (Preview, .txt, .md, .json).")
            return

        self.root_var.set(root)
        self.running = True
        self.run_btn.config(state="disabled")
        self.status_var.set(f"Scanning {root} …")
        self.progress.start(12)
        threading.Thread(target=self._worker, args=(o,), daemon=True).start()

    def _worker(self, o: dict) -> None:
        try:
            node, stats = lm.scan(o["root"], depth=o["depth"], extra_ignore=o["ignore"] or None)
            if node is None:
                raise RuntimeError("Failed to build the filesystem tree.")
            if not node.name:  # drive roots have no name
                node.name = o["root"].rstrip("\\/") or "/"

            lines = lm.render_tree(node)
            saved: list[str] = []
            outdir = o["outdir"] or o["root"]
            if o["text"] or o["md"] or o["json"]:
                os.makedirs(outdir, exist_ok=True)
                base = os.path.join(outdir, o["name"])
                if o["text"]:
                    lm.save_text(node, base + ".txt")
                    saved.append(base + ".txt")
                if o["md"]:
                    lm.save_markdown(node, base + ".md")
                    saved.append(base + ".md")
                if o["json"]:
                    lm.save_json(node, base + ".json", include_size=o["size"],
                                 include_metadata=o["meta"], stats=stats, depth=o["depth"])
                    saved.append(base + ".json")
            self.q.put(("done", lines, stats, saved, outdir if saved else ""))
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", str(e)))

    def _poll(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _handle(self, msg: tuple) -> None:
        self.progress.stop()
        self.running = False
        self.run_btn.config(state="normal")

        if msg[0] == "error":
            self.status_var.set("Failed.")
            messagebox.showerror("Layout Mapper", msg[1])
            return

        _, lines, stats, saved, outdir = msg
        self.last_lines = lines
        self.last_outdir = outdir
        self.copy_btn.config(state="normal")
        self.open_btn.config(state="normal" if outdir else "disabled")

        if self.preview_var.get():
            shown = lines[:PREVIEW_LIMIT]
            self.preview.config(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", "\n".join(shown))
            if len(lines) > PREVIEW_LIMIT:
                self.preview.insert("end", f"\n\n… preview cut at {PREVIEW_LIMIT} of {len(lines)} lines "
                                    "(exports and 'Copy tree' contain everything)")
            self.preview.config(state="disabled")

        summary = f"{max(0, stats.directories - 1)} dirs, {stats.files} files, {stats.ignored} ignored"
        if stats.errors:
            summary += f", {stats.errors} errors"
        if saved:
            summary += f"  •  saved {', '.join(os.path.basename(s) for s in saved)}"
        self.status_var.set(summary)

    # --------------------------------------------------------------- misc

    def copy_tree(self) -> None:
        if not self.last_lines:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(self.last_lines))
        self.status_var.set("Tree copied to clipboard.")

    def open_output(self) -> None:
        if self.last_outdir:
            open_in_explorer(self.last_outdir)

    def _load_cfg(self) -> dict:
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _on_close(self) -> None:
        try:
            depth = int(self.depth_var.get())
        except (tk.TclError, ValueError):
            depth = 3
        cfg = {
            "root": clean_path(self.root_var.get()),
            "hidden": self.hidden_var.get(),
            "limit_depth": self.limit_depth.get(),
            "depth": depth,
            "ignore": self.ignore_var.get(),
            "preview": self.preview_var.get(),
            "text": self.text_var.get(),
            "md": self.md_var.get(),
            "json": self.json_var.get(),
            "size": self.size_var.get(),
            "meta": self.meta_var.get(),
            "name": self.name_var.get(),
            "outdir": self.outdir_var.get(),
        }
        try:
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except OSError:
            pass
        self.destroy()


def main() -> None:
    if os.name == "nt":  # crisp text on high-DPI screens
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:
            pass
    start = sys.argv[1] if len(sys.argv) > 1 else None
    App(start).mainloop()


if __name__ == "__main__":
    main()
