"""CorridorKey — Import Results (DaVinci Resolve menu script).

Import karta hai CorridorKey inference ke finished outputs ko:
  - videos "CorridorKey" media pool bin mein
  - cutout/foreground clips ko ek dedicated top video track ("CorridorKey")
    par, original clip ke exactly upar wahi position par.

Jab Key Clips script ne job chalu ki ho aur console par DONE aa chuka ho,
tab ye script chalao.
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
        ck.notify("DaVinci Resolve API se connect nahi ho paya.", kind="error")
        return

    project = None
    try:
        project = resolve.GetProjectManager().GetCurrentProject()
    except Exception:
        pass
    if project is None:
        ck.notify("Pehle koi project open karo.", kind="error")
        return

    try:
        imported, summaries = ck.import_finished_jobs(resolve, cfg, quiet_if_none=False)
    except Exception as exc:
        ck.notify(f"Import fail hua:\n\n{exc}", kind="error")
        return

    if imported == 0 and not any("FAILED" in s or "warning" in s.lower()
                                 for s in summaries):
        ck.notify(
            "Koi finished job nahi mila.\n\n"
            "Pehle 'CorridorKey Key Clips' chalao aur console window mein "
            "DONE ka wait karo.\n"
            "Agar console mein error tha, to <repo>\\resolve_jobs\\active\\..."
            "\\worker.log check karo.", kind="warn")
        return

    ck.notify("\n".join(summaries) or "Kuch bhi import nahi hua.", kind="info")


if __name__ == "__main__":
    main()
