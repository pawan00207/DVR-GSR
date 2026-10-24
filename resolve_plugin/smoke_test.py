#!/usr/bin/env python3
"""Dry test for the CorridorKey Resolve integration.

Builds a fake repo layout in a temp folder with a stub infer.py (no torch),
runs corridorkey_worker.py end-to-end for two timeline clips sharing one
source file, and verifies done.json. Also unit-checks the common module's
pure helpers. Run with any stdlib-only Python:

    python resolve_plugin\\smoke_test.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


STUB_INFER = r'''
import sys
from pathlib import Path

args = sys.argv[1:]
def get(flag, default=None):
    return args[args.index(flag) + 1] if flag in args else default

inp = Path(get("--input"))
out_dir = Path(get("--output_dir"))
out_dir.mkdir(parents=True, exist_ok=True)
stem = inp.stem
(out_dir / f"{stem}_alpha.mp4").write_bytes(b"fake-alpha")
(out_dir / f"{stem}_cutout.mov").write_bytes(b"fake-cutout")
(out_dir / f"{stem}_settings.txt").write_text("stub log\n")
print("stub infer ok:", inp.name, "->", out_dir)
'''


def test_worker():
    print("Worker end-to-end (stub infer.py):")
    tmp = Path(tempfile.mkdtemp(prefix="ck_resolve_test_"))
    try:
        repo = tmp / "repo"
        (repo / "resolve_plugin").mkdir(parents=True)
        (repo / "infer.py").write_text(STUB_INFER, encoding="utf-8")
        shutil.copy2(HERE / "corridorkey_worker.py",
                     repo / "resolve_plugin" / "corridorkey_worker.py")

        src_a = tmp / "shot_a.mp4"
        src_a.write_bytes(b"fake-video-data")
        src_b = tmp / "shot b!.mp4"          # needs sanitizing
        src_b.write_bytes(b"fake-video-data")

        job_dir = repo / "resolve_jobs" / "active" / "20260101_000000_test"
        job_dir.mkdir(parents=True)
        job = {
            "version": 1,
            "timeline_name": "Main",
            "repo_root": str(repo),
            "job_dir": str(job_dir),
            "config": {
                "outputs": ["alpha", "cutout"],
                "despill_strength": 0.7,
                "hint_quality": 0.95,
                "low_vram": False,
                "hann_chunk": 0,
                "output_format": "video",
            },
            "clips": [
                {"name": "shotA_v1", "file_path": str(src_a),
                 "source_start": 12, "source_end": 100, "record_start": 3600},
                {"name": "shotA_v2", "file_path": str(src_a),
                 "source_start": 200, "source_end": 260, "record_start": 4000},
                {"name": "shotB_v1", "file_path": str(src_b),
                 "source_start": 0, "source_end": None, "record_start": 5000},
            ],
        }
        job_path = job_dir / "job.json"
        job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")

        proc = subprocess.run(
            [sys.executable,
             str(repo / "resolve_plugin" / "corridorkey_worker.py"),
             "--job", str(job_path)],
            capture_output=True, text=True, cwd=str(repo), timeout=120)
        check("worker exit code 0", proc.returncode == 0,
              f"stderr={proc.stderr[-500:]}")

        done_path = job_dir / "done.json"
        check("done.json written", done_path.exists())
        done = json.loads(done_path.read_text(encoding="utf-8"))
        check("status done", done.get("status") == "done")
        clips = done.get("clips") or []
        check("3 clip results", len(clips) == 3, f"got {len(clips)}")
        ok = [c for c in clips if not c.get("failed")]
        check("all clips succeeded", len(ok) == 3)
        check("timeline name carried", done.get("timeline_name") == "Main")

        # Dedup: two entries for shot_a -> same output files, one out_ dir.
        outs = {Path(c["outputs"]["alpha"]).parent.name for c in ok}
        check("source dedupe (2 out dirs for 3 clips)", len(outs) == 2,
              f"dirs={outs}")
        out_dirs = sorted(p.name for p in job_dir.iterdir()
                          if p.is_dir() and p.name.startswith("out_"))
        check("sanitized dir name", all(" " not in d and "!" not in d
                                        for d in out_dirs), f"{out_dirs}")

        a1 = next(c for c in clips if c["name"] == "shotA_v2")
        check("placement metadata kept",
              a1["source_start"] == 200 and a1["source_end"] == 260
              and a1["record_start"] == 4000)
        cut = a1["outputs"].get("cutout", "")
        check("cutout .mov found", cut.endswith("_cutout.mov"), cut)

        # worker.log captured stub output
        log = (job_dir / "worker.log").read_text(encoding="utf-8")
        check("worker.log has infer output", "stub infer ok" in log)
        check("worker.log has NEXT STEP", "Import Results" in log)

        # ---- failure path: missing input file ----
        job2_dir = repo / "resolve_jobs" / "active" / "20260101_000001_bad"
        job2_dir.mkdir(parents=True)
        job["clips"] = [{"name": "gone", "file_path": str(tmp / "missing.mp4"),
                         "source_start": 0, "source_end": 5,
                         "record_start": 100}]
        job["job_dir"] = str(job2_dir)
        (job2_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
        proc2 = subprocess.run(
            [sys.executable,
             str(repo / "resolve_plugin" / "corridorkey_worker.py"),
             "--job", str(job2_dir / "job.json")],
            capture_output=True, text=True, cwd=str(repo), timeout=120)
        done2 = json.loads((job2_dir / "done.json").read_text(encoding="utf-8"))
        check("missing input -> failed status", done2.get("status") == "failed")
        check("missing input -> error recorded",
              bool((done2.get("clips") or [{}])[0].get("error")))
        check("worker exit 1 on failure", proc2.returncode == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_common_module():
    print("Common module helpers:")
    tmp = Path(tempfile.mkdtemp(prefix="ck_common_test_"))
    try:
        # Build a mini installed-scripts folder with a config.
        scripts = tmp / "Utility"
        scripts.mkdir(parents=True)
        repo = tmp / "repo"
        (repo / "resolve_jobs").mkdir(parents=True)
        cfg = {"repo_root": str(repo), "python": sys.executable,
               "outputs": ["alpha", "cutout"], "place_on_timeline": True,
               "bin_name": "CorridorKey", "timeline_track_name": "CorridorKey"}
        (scripts / "resolve_config.json").write_text(
            json.dumps(cfg), encoding="utf-8")
        shutil.copy2(HERE / "corridorkey_resolve_common.py",
                     scripts / "corridorkey_resolve_common.py")

        sys.path.insert(0, str(scripts))
        try:
            import corridorkey_resolve_common as ck
            loaded = ck.load_config()
            check("load_config merges defaults",
                  loaded["despill_strength"] == 0.5
                  and loaded["repo_root"] == str(repo))
            check("sanitize_label", ck.sanitize_label("shot b!*") == "shot_b")

            jd = ck.new_job_dir(loaded, "Main 2clips")
            check("new_job_dir under active",
                  "active" in str(jd) and jd.exists())
            ck.write_json(jd / "done.json", {"status": "done", "clips": []})
            check("active_job_dirs sees job",
                  len(ck.active_job_dirs(loaded)) == 1)
            ck.archive_job(loaded, jd, "imported")
            check("archive moves to imported",
                  not jd.exists()
                  and (ck.jobs_root(loaded) / "imported").exists())
            check("active empty after archive",
                  len(ck.active_job_dirs(loaded)) == 0)

            # Fake Resolve objects for track/bin/import logic.
            class FakeTrackTL:
                def __init__(self):
                    self.tracks = ["Video 1"]
                    self.appended = []
                    self.selected = []

                def GetTrackCount(self, t):
                    return len(self.tracks)

                def GetTrackName(self, t, i):
                    return self.tracks[i - 1]

                def AddTrack(self, t):
                    self.tracks.append("CorridorKey")
                    return True

                def SetTrackName(self, t, i, n):
                    self.tracks[i - 1] = n

                def GetName(self):
                    return "Main"

            tl = FakeTrackTL()
            idx = ck.find_or_create_track(tl, "CorridorKey")
            check("find_or_create_track top index", idx == 2, f"idx={idx}")
            idx2 = ck.find_or_create_track(tl, "CorridorKey")
            check("second call reuses track", idx2 == 2 and len(tl.tracks) == 2)

            class FakeItem:
                def __init__(self, path):
                    self.path = path

                def GetClipProperty(self, key):
                    return self.path if key == "File Path" else None

                def SetClipColor(self, c):
                    pass

            class FakePool:
                def __init__(self):
                    self.items = []

                def GetRootFolder(self):
                    return self

                def GetSubFolderList(self):
                    return []

                def GetName(self):
                    return "Master"

                def AddSubFolder(self, root, name):
                    self.bin_name = name
                    return self

                def SetCurrentFolder(self, f):
                    pass

                def ImportMedia(self, files):
                    self.items = [FakeItem(f) for f in files]
                    return self.items

                def AppendToTimeline(self, entries):
                    self.appended = getattr(self, "appended", []) + entries
                    return [object()]

            # done.json with two outputs, one clip placement
            out_dir = tmp / "out"
            out_dir.mkdir()
            alpha = out_dir / "shot_alpha.mp4"
            cutout = out_dir / "shot_cutout.mov"
            alpha.write_bytes(b"x")
            cutout.write_bytes(b"x")
            jd2 = ck.new_job_dir(loaded, "job2")
            ck.write_json(jd2 / "done.json", {
                "status": "done", "timeline_name": "Main",
                "clips": [{
                    "name": "shot", "file_path": "x", "failed": False,
                    "source_start": 5, "source_end": 90,
                    "record_start": 3700,
                    "outputs": {"alpha": str(alpha), "cutout": str(cutout)},
                }]})

            class FakeProject:
                def GetCurrentTimeline(self):
                    return tl

            pool = FakePool()
            msgs = ck.import_one_job(object(), loaded, FakeProject(), pool, jd2)
            entry = pool.appended[0] if getattr(pool, "appended", None) else None
            check("AppendToTimeline called once", entry is not None)
            if entry:
                check("placement entry correct",
                      entry["startFrame"] == 5 and entry["endFrame"] == 90
                      and entry["recordFrame"] == 3700
                      and entry["trackIndex"] == 2
                      and entry.get("mediaType") == 1, str(entry))
                check("cutout preferred for placement",
                      entry["mediaPoolItem"].path == str(cutout))
            check("import summary mentions bin", any("media pool" in m for m in msgs),
                  str(msgs))
        finally:
            sys.path.remove(str(scripts))
            for mod in list(sys.modules):
                if mod == "corridorkey_resolve_common":
                    del sys.modules[mod]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print(f"CorridorKey Resolve integration smoke test  "
          f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]")
    print("-" * 60)
    test_worker()
    test_common_module()
    print("-" * 60)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("RESULT: ALL TESTS PASSED")


if __name__ == "__main__":
    main()
