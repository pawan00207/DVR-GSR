#!/usr/bin/env python3
"""Install the CorridorKey DaVinci Resolve menu scripts.

- Writes resolve_config.json (repo paths + defaults) into resolve_plugin/.
- Copies the menu scripts + config into:
    %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility\\
  so they appear in Resolve under Workspace > Scripts.

Run from anywhere: `python resolve_plugin\\install.py`
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "resolve_plugin"
MENU_FILES = [
    "CorridorKey_KeyClips.py",
    "CorridorKey_ImportResults.py",
    "corridorkey_resolve_common.py",
]
CFG_FILE = "resolve_config.json"

DEFAULTS = {
    "outputs": ["alpha", "cutout"],
    "despill_strength": 0.5,
    "hint_first_frame": "",
    "hint_video": "",
    "hint_quality": 0.95,
    "low_vram": False,
    "hann_chunk": 0,
    "output_format": "video",
    "place_on_timeline": True,
    "auto_import_on_start": True,
    "timeline_track_name": "CorridorKey",
    "bin_name": "CorridorKey",
}


def resolve_scripts_dir():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise SystemExit("APPDATA env var nahi mila — Windows par chalao.")
    return (Path(appdata) / "Blackmagic Design" / "DaVinci Resolve"
            / "Support" / "Fusion" / "Scripts" / "Utility")


def find_venv_python():
    venv_py = REPO_ROOT / "venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    return ""


def write_config():
    cfg_path = PLUGIN_DIR / CFG_FILE
    existing = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    cfg = dict(DEFAULTS)
    # Keep user tweaks from an earlier install, but always refresh paths.
    for key in DEFAULTS:
        if key in existing:
            cfg[key] = existing[key]
    cfg["repo_root"] = str(REPO_ROOT)
    cfg["python"] = find_venv_python() or str(sys.executable)
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg_path, cfg


def main():
    print("CorridorKey — DaVinci Resolve integration install")
    print(f"Repo root: {REPO_ROOT}")

    venv_missing = not (REPO_ROOT / "venv" / "Scripts" / "python.exe").exists()
    weights_missing = not any(
        (REPO_ROOT / "weights").glob("corridorkey_v2.safetensors*")) and not (
        REPO_ROOT / "weights" / "corridorkey_v2.pt").exists()

    cfg_path, cfg = write_config()
    print(f"Config likha: {cfg_path}")
    if venv_missing:
        print("  WARNING: venv nahi mila (repo root par 'venv' folder nahi hai).")
        print("           README.md ke Setup steps pehle complete karo, phir")
        print("           install dobara chalao.")
    if weights_missing:
        print("  WARNING: model weights nahi mile (weights/ folder khali hai).")
        print("           `python scripts/download_weights.py` chalao.")

    dst_dir = resolve_scripts_dir()
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in MENU_FILES + [CFG_FILE]:
        src = PLUGIN_DIR / name
        if not src.exists():
            raise SystemExit(f"file missing: {src}")
        shutil.copy2(str(src), str(dst_dir / name))
        copied.append(dst_dir / name)

    print("\nScripts install ho gaye:")
    for p in copied:
        print(f"  {p}")

    print("""
AAGE KA TARIKA (DaVinci Resolve mein):
  1. Resolve ko restart karo (ya Scripts menu refresh ho jane do).
  2. Edit page par timeline open karo, key karni wali clip SELECT karo.
  3. Workspace > Scripts > CorridorKey Key Clips
     -> naye console window mein inference chalegi.
  4. DONE hone par: Workspace > Scripts > CorridorKey Import Results
     -> outputs 'CorridorKey' bin + 'CorridorKey' top track par aa jayenge.

NOTE: DaVinci Resolve 21.1+ ke FREE version mein scripting band ho chuki
hai — ye integration ke liye Resolve 21.0 ya usse purana free version, ya
Studio version chahiye.
""")


if __name__ == "__main__":
    main()
