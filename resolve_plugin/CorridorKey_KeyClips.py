"""CorridorKey — Key Selected Clips (DaVinci Resolve menu script).

Install: run resolve_plugin\\install.bat from the repository root. After that
this appears in Resolve under Workspace > Scripts.

Usage (Edit page):
  1. Timeline par jo clip(s) key karni hain unhe select karo.
  2. Workspace > Scripts > CorridorKey Key Clips chalao.
  3. Ek console window khulegi jisme inference progress dikhega.
  4. Jab 'DONE' aa jaye, ye script dobara chalao (ya 'CorridorKey Import
     Results') — results media pool bin + timeline par aa jayenge.

Stdlib only; inference itself runs in the repo venv via corridorkey_worker.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import corridorkey_resolve_common as ck


def main():
    try:
        cfg = ck.load_config()
    except Exception as exc:
        ck.notify(f"Config load nahi hua:\n\n{exc}", kind="error")
        return

    resolve = ck.get_resolve(globals())
    if resolve is None:
        ck.notify(
            "DaVinci Resolve API se connect nahi ho paya.\n"
            "Script ko Workspace > Scripts se hi chalao.", kind="error")
        return

    project = None
    try:
        project = resolve.GetProjectManager().GetCurrentProject()
    except Exception:
        pass
    if project is None:
        ck.notify("Pehle koi project open karo.", kind="error")
        return

    timeline = None
    try:
        timeline = project.GetCurrentTimeline()
    except Exception:
        pass
    if timeline is None:
        ck.notify("Pehle Edit page par ek timeline open karo.", kind="error")
        return

    # Pull in any earlier finished jobs first, so the common flow is:
    # run once to start a job, run again to place the finished results.
    try:
        if cfg.get("auto_import_on_start", True):
            imported, summaries = ck.import_finished_jobs(resolve, cfg)
            if imported:
                ck.notify(
                    "Purane finished job import ho gaye:\n\n"
                    + "\n".join(summaries), kind="info")
    except Exception as exc:
        ck.notify(f"Purane results import karte waqt error:\n\n{exc}", kind="warn")

    items = ck.get_selected_timeline_items(timeline)
    if not items:
        ck.notify(
            "Timeline par koi clip select nahi hai.\n\n"
            "Edit page par jo clip(s) key karni hain unpar click karke select "
            "karo, phir ye script dobara chalao.", kind="error")
        return

    clips = []
    skipped = []
    for item in items:
        name = None
        try:
            name = item.GetName()
        except Exception:
            pass
        mpi = None
        try:
            mpi = item.GetMediaPoolItem()
        except Exception:
            pass
        file_path = None
        if mpi is not None:
            try:
                file_path = mpi.GetClipProperty("File Path")
            except Exception:
                pass
        if not file_path:
            skipped.append(name or "(unknown)")
            continue
        clips.append({
            "name": name or Path(str(file_path)).stem,
            "file_path": str(file_path),
            "source_start": ck.safe_int(_get(item, "GetSourceStartFrame")),
            "source_end": ck.safe_int(_get(item, "GetSourceEndFrame")),
            "record_start": ck.safe_int(_get(item, "GetStart")),
        })

    if skipped:
        print(f"[CorridorKey] skipped (no media file): {skipped}")
    if not clips:
        ck.notify(
            "Selected item(s) mein koi media file nahi mili "
            f"({', '.join(skipped)}).\n\nTitles, generators aur Fusion-only "
            "items key nahi ho sakte — ek video clip select karo.", kind="error")
        return

    try:
        timeline_name = timeline.GetName()
    except Exception:
        timeline_name = "timeline"

    job_dir = ck.new_job_dir(cfg, f"{timeline_name}_{len(clips)}clips")
    job = {
        "version": 1,
        "created": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "timeline_name": timeline_name,
        "repo_root": cfg["repo_root"],
        "job_dir": str(job_dir),
        "config": {
            "outputs": cfg.get("outputs") or ["alpha", "cutout"],
            "despill_strength": cfg.get("despill_strength"),
            "hint_first_frame": cfg.get("hint_first_frame") or "",
            "hint_video": cfg.get("hint_video") or "",
            "hint_quality": cfg.get("hint_quality"),
            "low_vram": bool(cfg.get("low_vram")),
            "hann_chunk": int(cfg.get("hann_chunk") or 0),
            "output_format": cfg.get("output_format", "video"),
            "exr_dtype": cfg.get("exr_dtype", "half"),
        },
        "clips": clips,
    }
    job_path = job_dir / "job.json"
    ck.write_json(job_path, job)

    try:
        ck.launch_worker(cfg, job_path)
    except Exception as exc:
        ck.notify(f"Worker launch nahi hua:\n\n{exc}", kind="error")
        return

    names = ", ".join(c["name"] or "?" for c in clips)
    ck.notify(
        f"Job start ho gaya — {len(clips)} clip(s): {names}\n\n"
        "Inference ek naye console window mein chal rahi hai (GPU + model "
        "load hone mein ek minute lag sakta hai).\n\n"
        "Jab console par 'DONE' aa jaye:\n"
        "  Workspace > Scripts > CorridorKey Import Results chalao\n"
        "(ya isi script ko dobara chalao).", kind="info")


def _get(item, attr):
    try:
        return getattr(item, attr)()
    except Exception:
        return None


if __name__ == "__main__":
    main()
