#!/usr/bin/env python3
"""CorridorKey -> DaVinci Resolve inference worker.

Runs OUTSIDE Resolve, in the repository's virtual environment, and executes
the CorridorKey inference jobs that the CorridorKey_KeyClips menu script
writes to <repo>/resolve_jobs/active/<job>/job.json.

For every unique source file it shells out to infer.py, captures its output
to worker.log, and finally writes done.json describing the produced files so
the CorridorKey_ImportResults menu script can pull them into Resolve.

Standard library only: this file is launched with the venv interpreter but
never imports torch itself.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Output filename patterns written by corridorkey.runtime (video format).
OUTPUT_GLOBS = {
    "alpha": ["*_alpha.mp4", "*_alpha.mov"],
    "fg": ["*_fg.mp4", "*_fg.mov"],
    "cutout": ["*_cutout.mov", "*_cutout.mp4"],
    "checker": ["*_checker_comp_despill*.mp4", "*_checker_comp_despill*.mov"],
}
EXR_DIR_SUFFIX = "_exr"


def sanitize_label(name, fallback="clip"):
    label = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name)).strip("_")
    return (label[:60] or fallback)


class Logger:
    def __init__(self, path):
        self._fh = open(path, "a", encoding="utf-8", errors="replace")

    def say(self, message=""):
        print(message, flush=True)
        try:
            self._fh.write(message + "\n")
            self._fh.flush()
        except Exception:
            pass

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


def build_infer_args(job, input_path, out_dir):
    cfg = job.get("config") or {}
    args = [
        sys.executable,
        str(Path(job["repo_root"]) / "infer.py"),
        "--input", str(input_path),
        "--output_dir", str(out_dir),
        "--outputs", *(cfg.get("outputs") or ["alpha", "cutout"]),
    ]
    if cfg.get("despill_strength") is not None:
        args += ["--despill_strength", str(cfg["despill_strength"])]
    if cfg.get("hint_first_frame"):
        args += ["--hint_first_frame", str(cfg["hint_first_frame"])]
    if cfg.get("hint_video"):
        args += ["--hint_video", str(cfg["hint_video"])]
    if cfg.get("hint_quality") is not None:
        args += ["--hint_quality", str(cfg["hint_quality"])]
    if cfg.get("low_vram"):
        args += ["--low_vram"]
    chunk = int(cfg.get("hann_chunk") or 0)
    if chunk > 0:
        args += ["--hann_chunk", str(chunk),
                 "--hann_stride", str(max(1, chunk // 2))]
    if cfg.get("output_format") == "exr":
        args += ["--output_format", "exr"]
        if cfg.get("exr_dtype"):
            args += ["--exr_dtype", str(cfg["exr_dtype"])]
    return args


def collect_outputs(out_dir, outputs_wanted):
    found = {}
    for kind in outputs_wanted:
        for pattern in OUTPUT_GLOBS.get(kind, []):
            matches = sorted(Path(out_dir).glob(pattern))
            if matches:
                found[kind] = str(matches[0])
                break
        else:
            for exr_dir in sorted(Path(out_dir).glob(f"*_{kind}{EXR_DIR_SUFFIX}")):
                if exr_dir.is_dir():
                    found[kind] = str(exr_dir)  # EXR sequence dir, manual import
                    break
    return found


def run_job(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    repo_root = Path(job["repo_root"])
    job_dir = Path(job["job_dir"]).resolve()
    infer_py = repo_root / "infer.py"
    if not infer_py.exists():
        raise RuntimeError(f"infer.py nahi mila: {infer_py}")

    log = Logger(job_dir / "worker.log")
    cfg = job.get("config") or {}
    outputs_wanted = list(cfg.get("outputs") or ["alpha", "cutout"])

    log.say("=" * 70)
    log.say(f"CorridorKey Resolve job start: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log.say(f"Job folder : {job_dir}")
    log.say(f"Outputs    : {', '.join(outputs_wanted)}")

    # Deduplicate by source file: several timeline clips may share one media.
    unique = {}
    for clip in job.get("clips") or []:
        key = os.path.normcase(str(clip.get("file_path") or ""))
        if not key:
            continue
        unique.setdefault(key, {"first": clip, "clips": []})["clips"].append(clip)
    if not unique:
        raise RuntimeError("job.json mein koi valid clip nahi mila")

    results = []
    for i, (key, group) in enumerate(unique.items(), 1):
        lead = group["first"]
        src = Path(lead["file_path"])
        log.say("")
        log.say(f"[{i}/{len(unique)}] {lead.get('name') or src.name}")
        log.say(f"    source: {src}")

        if not src.exists():
            error = f"input file missing: {src}"
            log.say(f"    ERROR: {error}")
            for clip in group["clips"]:
                results.append(_entry(clip, failed=True, error=error))
            continue

        stamp = sanitize_label(src.stem)
        out_dir = job_dir / f"out_{stamp}"
        n = 1
        while out_dir.exists():
            n += 1
            out_dir = job_dir / f"out_{stamp}_{n}"

        cmd = build_infer_args(job, src, out_dir)
        log.say(f"    infer: {' '.join(cmd[1:])}")
        log.say("-" * 70)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(repo_root), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
            for line in proc.stdout:
                log.say(f"    {line.rstrip()}")
            returncode = proc.wait()
        except Exception as exc:
            returncode = -1
            log.say(f"    ERROR launching infer.py: {exc}")

        if returncode != 0:
            error = f"infer.py exit code {returncode} (worker.log dekho)"
            log.say(f"    ERROR: {error}")
            for clip in group["clips"]:
                results.append(_entry(clip, failed=True, error=error))
            continue

        outputs = collect_outputs(out_dir, outputs_wanted)
        missing = [k for k in outputs_wanted if k not in outputs]
        if missing:
            log.say(f"    WARNING: expected outputs nahi mile: {missing}")
        log.say("    outputs:")
        for kind, path in sorted(outputs.items()):
            log.say(f"      {kind:8s} {path}")
        for clip in group["clips"]:
            results.append(_entry(clip, outputs=outputs))

    done = {
        "status": "done" if any(not r["failed"] for r in results) else "failed",
        "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timeline_name": job.get("timeline_name"),
        "clips": results,
    }
    done_path = job_dir / "done.json"
    done_path.write_text(json.dumps(done, indent=2), encoding="utf-8")

    log.say("")
    log.say("=" * 70)
    ok = sum(1 for r in results if not r["failed"])
    log.say(f"DONE: {ok}/{len(results)} timeline clip(s) keyed successfully.")
    log.say("")
    log.say("NEXT STEP (DaVinci Resolve mein):")
    log.say("  Workspace > Scripts > CorridorKey Import Results")
    log.say("  (ya 'CorridorKey Key Clips' script dobara chalao)")
    log.say("=" * 70)
    log.close()
    return 0 if done["status"] == "done" else 1


def _entry(clip, outputs=None, failed=False, error=None):
    return {
        "name": clip.get("name"),
        "file_path": clip.get("file_path"),
        "source_start": clip.get("source_start"),
        "source_end": clip.get("source_end"),
        "record_start": clip.get("record_start"),
        "outputs": outputs or {},
        "failed": failed,
        "error": error,
    }


def main():
    parser = argparse.ArgumentParser(
        description="CorridorKey DaVinci Resolve inference worker")
    parser.add_argument("--job", required=True, help="path to job.json")
    parser.add_argument("--pause", action="store_true",
                        help="wait for Enter before exiting (console window)")
    args = parser.parse_args()

    exit_code = 1
    try:
        exit_code = run_job(args.job)
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            job_dir = Path(json.loads(Path(args.job).read_text(
                encoding="utf-8"))["job_dir"])
            (job_dir / "done.json").write_text(json.dumps({
                "status": "failed",
                "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": traceback.format_exc(limit=3),
                "clips": [],
            }, indent=2), encoding="utf-8")
        except Exception:
            pass
    finally:
        if args.pause:
            try:
                input("\nPress Enter to close...")
            except Exception:
                pass
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
