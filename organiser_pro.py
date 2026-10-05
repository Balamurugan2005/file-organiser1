"""
File Organiser Pro (Streamlit)
Install:  pip install streamlit
Run:      streamlit run organiser_pro.py      (Streamlit 1.37+ for watch mode)
Standalone: does not need the other organiser files.

Features: editable rules, ignore patterns, min-age filter, per-run history with
undo, duplicate finder with safe "move to trash", CSV export, auto-watch mode.
Per-folder data lives in <folder>/.organiser/ ; rules in rules.json beside this file.
"""

import fnmatch
import hashlib
import json
import shutil
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

OTHER, META = "Others", ".organiser"
RULES_FILE = Path(__file__).parent / "rules.json"
DEFAULT_RULES = {
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".heic"],
    "Videos": [".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm"],
    "Audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"],
    "Documents": [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".epub"],
    "Spreadsheets": [".xls", ".xlsx", ".csv", ".ods"],
    "Presentations": [".ppt", ".pptx", ".odp", ".key"],
    "Archives": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"],
    "Code": [".py", ".js", ".ts", ".html", ".css", ".java", ".c", ".cpp", ".json", ".sh"],
    "Installers": [".exe", ".msi", ".dmg", ".pkg", ".deb", ".apk"],
}
DEFAULT_IGNORE = "*.crdownload, *.part, *.tmp, desktop.ini"


# ---------------- engine ----------------
def read_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def write_json(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def fmt_size(b: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024 or unit == "GB":
            return f"{b:.0f} {unit}" if unit == "B" else f"{b:.1f} {unit}"
        b /= 1024


def unique(dest: Path) -> Path:
    n, cand = 1, dest
    while cand.exists():
        cand = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        n += 1
    return cand


def hidden(rel: Path) -> bool:
    return any(p.startswith(".") for p in rel.parts)


def scan(folder, rules, by_date, recursive, ignore, min_age_s):
    ext_map = {e: c for c, exts in rules.items() for e in exts}
    cats, now, items = set(rules) | {OTHER}, time.time(), []
    for f in folder.rglob("*") if recursive else folder.glob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(folder)
        if hidden(rel) or (len(rel.parts) > 1 and rel.parts[0] in cats):
            continue
        if any(fnmatch.fnmatch(f.name.lower(), pat) for pat in ignore):
            continue
        s = f.stat()
        if now - s.st_mtime < min_age_s:
            continue
        cat = ext_map.get(f.suffix.lower(), OTHER)
        target = Path(cat)
        if by_date:
            target /= datetime.fromtimestamp(s.st_mtime).strftime("%Y-%m")
        items.append({"File": f.name, "Category": cat, "Moves to": f"{target}/",
                      "Size": s.st_size, "_src": str(f), "_target": str(target)})
    return items


def apply_moves(folder: Path, pairs, kind: str) -> int:
    moves = []
    for src, dest_dir in pairs:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = unique(dest_dir / src.name)
        shutil.move(str(src), str(dest))
        moves.append({"from": str(src), "to": str(dest)})
    if moves:
        hp = folder / META / "history.json"
        hist = read_json(hp, [])
        hist.append({"id": int(time.time() * 1000), "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "kind": kind, "undone": False, "moves": moves})
        write_json(hp, hist)
    return len(moves)


def undo_run(folder: Path, run_id: int, cats: set):
    hp = folder / META / "history.json"
    hist = read_json(hp, [])
    for run in hist:
        if run["id"] == run_id and not run["undone"]:
            for m in reversed(run["moves"]):
                src, dst = Path(m["to"]), Path(m["from"])
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(unique(dst)))
            run["undone"] = True
    write_json(hp, hist)
    for d in sorted(folder.rglob("*"), reverse=True):  # tidy empty category folders
        rel = d.relative_to(folder)
        if d.is_dir() and rel.parts[0] in cats and not hidden(rel) and not any(d.iterdir()):
            d.rmdir()


def file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_dupes(folder: Path):
    by_size = defaultdict(list)
    for f in folder.rglob("*"):
        if f.is_file() and not hidden(f.relative_to(folder)):
            by_size[f.stat().st_size].append(f)
    by_hash = defaultdict(list)
    for size, files in by_size.items():
        if size > 0 and len(files) > 1:
            for f in files:
                by_hash[file_hash(f)].append(f)
    return [sorted(g, key=lambda p: p.stat().st_mtime) for g in by_hash.values() if len(g) > 1]


# ---------------- UI ----------------
st.set_page_config(page_title="File Organiser Pro", page_icon="🗂️", layout="wide")
st.title("🗂️ File Organiser Pro")

with st.sidebar:
    st.header("Settings")
    folder = Path(st.text_input("Folder path", str(Path.home() / "Downloads"))).expanduser()
    by_date = st.checkbox("Group by month (YYYY-MM)")
    recursive = st.checkbox("Include subfolders")
    ignore = [p.strip().lower() for p in st.text_input("Ignore patterns", DEFAULT_IGNORE).split(",") if p.strip()]
    min_age = st.number_input("Skip files modified in last (minutes)", 0, 10080, 0)

if not folder.is_dir():
    st.error(f"Not a folder: {folder}")
    st.stop()

rules = read_json(RULES_FILE, DEFAULT_RULES)
cats = set(rules) | {OTHER}
tab_org, tab_dup, tab_hist, tab_rules, tab_watch = st.tabs(
    ["Organise", "Duplicates", "History", "Rules", "Auto-watch"])

# ----- Organise -----
with tab_org:
    items = scan(folder, rules, by_date, recursive, ignore, min_age * 60)
    df = pd.DataFrame(items)
    if df.empty:
        st.success("Nothing to organise with the current settings.")
    else:
        a, b, c = st.columns(3)
        a.metric("Files to move", len(df))
        b.metric("Total size", fmt_size(df["Size"].sum()))
        c.metric("Categories", df["Category"].nunique())
        l, r = st.columns(2)
        l.caption("Files per category")
        l.bar_chart(df["Category"].value_counts())
        r.caption("Size per category (MB)")
        r.bar_chart(df.groupby("Category")["Size"].sum() / 1e6)
        pick = st.multiselect("Filter preview by category", sorted(df["Category"].unique()))
        view = df[df["Category"].isin(pick)] if pick else df
        show = view.assign(Size=view["Size"].map(fmt_size))[["File", "Category", "Moves to", "Size"]]
        st.dataframe(show, hide_index=True)
        st.download_button("Export preview (CSV)", show.to_csv(index=False), "preview.csv", "text/csv")
        ok = st.checkbox("I've reviewed the preview")
        if st.button("Organise now", type="primary", disabled=not ok):
            pairs = [(Path(i["_src"]), folder / i["_target"]) for i in items]
            st.success(f"Moved {apply_moves(folder, pairs, 'organise')} file(s). See the History tab to undo.")

# ----- Duplicates -----
with tab_dup:
    st.write("Finds identical files. Extras are moved to a recoverable trash folder, never permanently deleted.")
    if st.button("Scan for duplicates"):
        with st.spinner("Hashing files..."):
            st.session_state["dupes"] = find_dupes(folder)
    groups = st.session_state.get("dupes")
    if groups is not None:
        if not groups:
            st.success("No duplicates found.")
        else:
            reclaim = sum(g[0].stat().st_size * (len(g) - 1) for g in groups if g[0].exists())
            st.metric("Reclaimable space", fmt_size(reclaim))
            for n, g in enumerate(groups, 1):
                with st.expander(f"Group {n}: {len(g)} copies of {g[0].name}"):
                    st.caption("Oldest copy is kept:")
                    for i, p in enumerate(g):
                        st.code(("KEEP  " if i == 0 else "TRASH ") + str(p), language=None)
            if st.button("Move all extra copies to trash", type="primary"):
                pairs = [(p, folder / META / "trash") for g in groups for p in g[1:] if p.exists()]
                n = apply_moves(folder, pairs, "dedupe")
                st.session_state["dupes"] = None
                st.success(f"Moved {n} duplicate(s) to {META}/trash. Undo from the History tab.")

# ----- History -----
with tab_hist:
    hist = read_json(folder / META / "history.json", [])
    if not hist:
        st.info("No runs yet.")
    for run in reversed(hist):
        state = "undone" if run["undone"] else "active"
        with st.expander(f"{run['time']} · {run['kind']} · {len(run['moves'])} file(s) · {state}"):
            st.dataframe(pd.DataFrame(run["moves"]), hide_index=True)
            if not run["undone"] and st.button("Undo this run", key=f"undo{run['id']}"):
                undo_run(folder, run["id"], cats)
                st.rerun()

# ----- Rules -----
with tab_rules:
    st.write("Edit categories and their extensions (comma-separated). Rules are saved globally.")
    rdf = pd.DataFrame({"Category": list(rules), "Extensions": [", ".join(v) for v in rules.values()]})
    edited = st.data_editor(rdf, num_rows="dynamic", hide_index=True)
    c1, c2 = st.columns(2)
    if c1.button("Save rules", type="primary"):
        new = {}
        for _, row in edited.iterrows():
            name = "" if pd.isna(row["Category"]) else str(row["Category"]).strip()
            raw = "" if pd.isna(row["Extensions"]) else str(row["Extensions"])
            if name:
                new[name] = [("." + e.strip().lstrip(".")).lower() for e in raw.split(",") if e.strip()]
        write_json(RULES_FILE, new)
        st.rerun()
    if c2.button("Reset to defaults"):
        write_json(RULES_FILE, DEFAULT_RULES)
        st.rerun()

# ----- Auto-watch -----
with tab_watch:
    st.write("Automatically organises new files while this page stays open.")
    watch = st.toggle("Enable auto-organise")
    interval = st.slider("Check every (seconds)", 5, 120, 15)
    settle = st.number_input("Only move files untouched for (seconds)", 0, 3600, 30,
                             help="Avoids moving files that are still downloading.")

    @st.fragment(run_every=interval if watch else None)
    def watcher():
        if watch:
            new = scan(folder, rules, by_date, recursive, ignore, settle)
            if new:
                pairs = [(Path(i["_src"]), folder / i["_target"]) for i in new]
                n = apply_moves(folder, pairs, "auto-watch")
                st.session_state.setdefault("log", []).append(f"{datetime.now():%H:%M:%S}  moved {n} file(s)")
        st.caption("Status: " + ("watching " + str(folder) if watch else "off"))
        for line in st.session_state.get("log", [])[-10:][::-1]:
            st.text(line)

    watcher()