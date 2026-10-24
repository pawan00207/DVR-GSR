"""Shared helpers for the CorridorKey DaVinci Resolve menu scripts.

This module runs INSIDE DaVinci Resolve's Python interpreter, so it must use
the standard library only (no torch / corridorkey imports). The heavy
inference work is done by corridorkey_worker.py in the repository's venv.
"""
from __future__ import annotations

import ctypes
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_NAME = "resolve_config.json"

DEFAULT_CONFIG = {
    # Filled in by install.py:
    "repo_root": "",
    "python": "",
    # Inference settings (see infer.py --help):
    "outputs": ["alpha", "cutout"],
    "despill_strength": 0.5,
    "hint_first_frame": "",
    "hint_video": "",
    "hint_quality": 0.95,
    "low_vram": False,
    "hann_chunk": 0,
    "output_format": "video",
    # Integration behavior:
    "place_on_timeline": True,
    "auto_import_on_start": True,
    "timeline_track_name": "CorridorKey",
    "bin_name": "CorridorKey",
}


def load_config():
    """Load resolve_config.json sitting next to this script."""
    path = SCRIPT_DIR / CONFIG_NAME
    if not path.exists():
        raise RuntimeError(
            f"{CONFIG_NAME} nahi mila: {path}\n"
            "Repo root se `resolve_plugin\\install.bat` chalao taaki scripts "
            "aur config Resolve mein install ho jayen.")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            user_cfg = json.load(fh)
    except Exception as exc:
        raise RuntimeError(f"{CONFIG_NAME} padh nahi saka: {exc}")
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(user_cfg)
    if not cfg.get("repo_root") or not Path(cfg["repo_root"]).exists():
        raise RuntimeError(
            f"config mein 'repo_root' galat hai: {cfg.get('repo_root')!r}\n"
            "install.bat dobara chalao.")
    if not cfg.get("python"):
        venv_py = Path(cfg["repo_root"]) / "venv" / "Scripts" / "python.exe"
        cfg["python"] = str(venv_py) if venv_py.exists() else "python"
    return cfg


def get_resolve(namespace=None):
    """Return the Resolve app object, whether or not it was pre-injected."""
    cand = (namespace or {}).get("resolve")
    if cand is not None:
        return cand
    try:
        import DaVinciResolveScript as dvr  # noqa: TID252
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


def notify(message, title="CorridorKey", kind="info"):
    """Show a message box (Windows) and always print for the console log."""
    print(f"[CorridorKey] {message}")
    try:
        icons = {"info": 0x40, "warn": 0x30, "error": 0x10}
        ctypes.windll.user32.MessageBoxW(
            None, str(message), title, 0x00 | icons.get(kind, 0x40))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Job folders: <repo>/resolve_jobs/active/<id>/ contains job.json plus, after
# the worker finishes, done.json. Import moves it to ../imported/<id>/.
# ---------------------------------------------------------------------------

def jobs_root(cfg):
    return Path(cfg["repo_root"]) / "resolve_jobs"


def active_job_dirs(cfg):
    active = jobs_root(cfg) / "active"
    if not active.exists():
        return []
    return sorted(p for p in active.iterdir() if p.is_dir())


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    tmp.replace(path)


def sanitize_label(name, fallback="clip"):
    label = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name)).strip("_")
    return (label[:60] or fallback)


def new_job_dir(cfg, label):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    job_dir = jobs_root(cfg) / "active" / f"{stamp}_{sanitize_label(label)}"
    n = 1
    while job_dir.exists():
        n += 1
        job_dir = jobs_root(cfg) / "active" / f"{stamp}_{sanitize_label(label)}_{n}"
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def archive_job(cfg, job_dir, status):
    dest_root = jobs_root(cfg) / ("imported" if status == "imported" else "failed")
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / Path(job_dir).name
    n = 1
    while dest.exists():
        n += 1
        dest = dest_root / f"{Path(job_dir).name}_{n}"
    try:
        shutil.move(str(job_dir), str(dest))
    except Exception as exc:
        print(f"[CorridorKey] warning: job folder archive nahi ho paya: {exc}")


# ---------------------------------------------------------------------------
# DaVinci Resolve API glue
# ---------------------------------------------------------------------------

def get_selected_timeline_items(timeline):
    """Selected timeline items, falling back to the clip under the playhead."""
    items = []
    getter = getattr(timeline, "GetSelectedClips", None)
    if callable(getter):
        try:
            raw = getter()
            if isinstance(raw, dict):
                raw = list(raw.values())
            elif raw is None:
                raw = []
            items = [it for it in raw if it is not None]
        except Exception as exc:
            print(f"[CorridorKey] GetSelectedClips failed: {exc}")
    if not items:
        try:
            current = timeline.GetCurrentVideoItem()
            if current is not None:
                items = [current]
        except Exception:
            pass
    return items


def safe_int(value):
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def find_or_create_track(timeline, track_name):
    """Index of a top video track with the given name, creating it if needed."""
    try:
        count = int(timeline.GetTrackCount("video"))
    except Exception:
        return None
    for idx in range(1, count + 1):
        try:
            if timeline.GetTrackName("video", idx) == track_name:
                return idx
        except Exception:
            break
    # AddTrack("video") adds the new track at the top of the stack, so its
    # index is the new track count.
    try:
        if timeline.AddTrack("video"):
            new_count = int(timeline.GetTrackCount("video"))
            try:
                timeline.SetTrackName("video", new_count, track_name)
            except Exception:
                pass
            return new_count
    except Exception as exc:
        print(f"[CorridorKey] AddTrack failed: {exc}")
    return count or None


def find_or_create_bin(media_pool, bin_name):
    root = media_pool.GetRootFolder()
    try:
        for folder in root.GetSubFolderList():
            if folder.GetName() == bin_name:
                return folder
    except Exception:
        pass
    try:
        folder = media_pool.AddSubFolder(root, bin_name)
        if folder is not None:
            return folder
    except Exception as exc:
        print(f"[CorridorKey] AddSubFolder failed: {exc}")
    return root


def _collect_import_files(clip_results):
    """Video files worth importing: every existing output except EXR dirs."""
    files = []
    for clip in clip_results:
        if clip.get("failed"):
            continue
        for kind, out_path in sorted((clip.get("outputs") or {}).items()):
            if not out_path:
                continue
            p = Path(out_path)
            if p.is_file() and p.suffix.lower() in (".mp4", ".mov"):
                files.append(str(p))
    return files


def import_one_job(resolve, cfg, project, media_pool, job_dir):
    """Import a finished job. Returns a human-readable summary line list."""
    done = read_json(job_dir / "done.json")
    timeline = project.GetCurrentTimeline()
    clip_results = done.get("clips") or []
    if not clip_results:
        raise RuntimeError("done.json mein koi clip result nahi hai")

    ok_clips = [c for c in clip_results if not c.get("failed")]
    failed_clips = [c for c in clip_results if c.get("failed")]

    messages = [f"{Path(job_dir).name}: {len(ok_clips)} clip(s) done"]
    for clip in failed_clips:
        messages.append(f"  FAILED {clip.get('name')}: {clip.get('error')}")

    files = _collect_import_files(clip_results)
    if files:
        target_bin = find_or_create_bin(media_pool, cfg.get("bin_name", "CorridorKey"))
        try:
            media_pool.SetCurrentFolder(target_bin)
        except Exception:
            pass
        imported = media_pool.ImportMedia(files) or []
        by_path = {}
        for item in imported:
            try:
                by_path[os_norm(item.GetClipProperty("File Path"))] = item
            except Exception:
                continue
        messages.append(f"  {len(imported)} file(s) media pool bin "
                        f"'{target_bin.GetName()}' mein import hui")

        # Place the best output of each clip back on the timeline, on the
        # dedicated CorridorKey top track, aligned with the original clip.
        if cfg.get("place_on_timeline") and timeline is not None:
            job_timeline = done.get("timeline_name")
            if job_timeline and timeline.GetName() != job_timeline:
                messages.append(
                    f"  WARNING: timeline badal gayi hai ({job_timeline} -> "
                    f"{timeline.GetName()}); clips timeline par place nahi ki "
                    "gayi, sirf media pool mein import hui hain.")
            else:
                track_index = find_or_create_track(
                    timeline, cfg.get("timeline_track_name", "CorridorKey"))
                placed = 0
                for clip in ok_clips:
                    item = _pick_placement_item(clip, by_path)
                    if item is None:
                        continue
                    entry = {"mediaPoolItem": item, "mediaType": 1}
                    if track_index:
                        entry["trackIndex"] = track_index
                    if clip.get("record_start") is not None:
                        entry["recordFrame"] = int(clip["record_start"])
                    if clip.get("source_start") is not None:
                        entry["startFrame"] = int(clip["source_start"])
                    if clip.get("source_end") is not None:
                        entry["endFrame"] = int(clip["source_end"])
                    try:
                        appended = media_pool.AppendToTimeline([entry]) or []
                        if appended:
                            placed += 1
                            try:
                                appended[0].SetClipColor("Orange")
                            except Exception:
                                pass
                    except Exception as exc:
                        messages.append(
                            f"  WARNING: {clip.get('name')} place nahi hui: {exc}")
                messages.append(
                    f"  {placed} clip(s) '{cfg.get('timeline_track_name')}' "
                    "track par place hui (orange color)")
    return messages


def _pick_placement_item(clip, by_path):
    outputs = clip.get("outputs") or {}
    for kind in ("cutout", "fg", "alpha"):  # prefer alpha-embedded cutout
        out_path = outputs.get(kind)
        if out_path:
            item = by_path.get(os_norm(out_path))
            if item is not None:
                return item
    return None


def os_norm(path):
    return str(path).replace("/", "\\").lower()


def import_finished_jobs(resolve, cfg, quiet_if_none=True):
    """Import every job that has a done.json. Returns (count, message list)."""
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        return 0, ["Koi project open nahi hai."]
    media_pool = project.GetMediaPool()
    if media_pool is None:
        return 0, ["Media pool available nahi hai."]

    summaries = []
    imported = 0
    for job_dir in active_job_dirs(cfg):
        done_path = job_dir / "done.json"
        if not done_path.exists():
            continue
        try:
            msgs = import_one_job(resolve, cfg, project, media_pool, job_dir)
            summaries.extend(msgs)
            archive_job(cfg, job_dir, "imported")
            imported += 1
        except Exception as exc:
            summaries.append(f"{job_dir.name}: import FAILED: {exc}")
            archive_job(cfg, job_dir, "failed")
    if not summaries and quiet_if_none:
        return 0, []
    return imported, summaries


def launch_worker(cfg, job_json_path):
    """Start the venv worker in its own console window; non-blocking."""
    worker = Path(cfg["repo_root"]) / "resolve_plugin" / "corridorkey_worker.py"
    cmd = [cfg["python"], str(worker), "--job", str(job_json_path), "--pause"]
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(cmd, cwd=str(cfg["repo_root"]), creationflags=creationflags)
