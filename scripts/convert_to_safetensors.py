#!/usr/bin/env python3
"""Convert legacy .pt runtime assets to .safetensors for public release.

Writes `corridorkey_v2.safetensors` (state dict, with the model configuration
in the file header) and `foundation_stats.safetensors` (the six normalization
vectors). Training-side provenance stored in the .pt files is not carried over.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from safetensors.torch import save_file

STATS_KEYS = ("moge_mean", "moge_std", "rvm_mean", "rvm_std", "cradio_mean", "cradio_std")
PUBLIC_METADATA_KEYS = ("public_model_name", "pre_recurrence", "shape_quantum", "required_vfms")


def convert_checkpoint(src: Path, dst: Path) -> None:
    ckpt = torch.load(src, map_location="cpu", weights_only=False, mmap=True)
    if ckpt.get("format") != "corridorkey_v2_runtime_checkpoint":
        raise RuntimeError(f"{src} is not a CorridorKey v2 runtime checkpoint")
    metadata = {k: ckpt["metadata"][k] for k in PUBLIC_METADATA_KEYS if k in ckpt.get("metadata", {})}
    header = {
        "format": ckpt["format"],
        "format_version": str(ckpt.get("format_version", 1)),
        "model_config": json.dumps(ckpt["model_config"]),
        "metadata": json.dumps(metadata),
    }
    state = {k: v.contiguous() for k, v in ckpt["state_dict"].items()}
    save_file(state, str(dst), metadata=header)
    print(f"wrote {dst} ({dst.stat().st_size / 1e9:.2f} GB, {len(state)} tensors)")


def convert_stats(src: Path, dst: Path) -> None:
    stats = torch.load(src, map_location="cpu", weights_only=False)
    tensors = {k: stats[k].contiguous() for k in STATS_KEYS if k in stats}
    save_file(tensors, str(dst))
    print(f"wrote {dst} ({len(tensors)} tensors)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights_dir", default=str(Path(__file__).resolve().parents[1] / "weights"))
    ap.add_argument("--output_dir", default=None, help="Defaults to --weights_dir.")
    args = ap.parse_args()

    src_dir = Path(args.weights_dir)
    out_dir = Path(args.output_dir) if args.output_dir else src_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    convert_checkpoint(src_dir / "corridorkey_v2.pt", out_dir / "corridorkey_v2.safetensors")
    convert_stats(src_dir / "foundation_stats.pt", out_dir / "foundation_stats.safetensors")


if __name__ == "__main__":
    main()
