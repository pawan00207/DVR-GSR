# CorridorKey → DaVinci Resolve Integration

Ye folder CorridorKey 2.0 ko **DaVinci Resolve ke andar** ek menu-script
workflow mein convert karta hai. Timeline par clip select karo → script AI
inference chalayegi → keyed output (alpha + cutout) wapas **media pool bin
aur timeline par** aa jayega.

Ye koi OFX/C++ plugin nahi hai — ye Resolve ki official **scripting API** par
based hai, isliye ye CorridorKey ke Python engine ko as-is use kar pata hai.

## Requirements

- Wahi jo main repo ke: NVIDIA RTX 30-series+ GPU, CUDA PyTorch venv, ffmpeg
  on PATH, downloaded weights (`scripts/download_weights.py` done).
- **DaVinci Resolve 21.0 ya older (free), ya koi bhi Studio version.**
  ⚠️ Resolve 21.1+ ke **free** version mein Blackmagic ne scripting poori
  tarah hata di hai — wahan ye scripts kaam nahi karenge (Studio chahiye).
- Windows (MessageBox + console-launch Windows-oriented hain).

## Install (ek baar)

```
resolve_plugin\install.bat
```

Ye do kaam karta hai:

1. `resolve_plugin\resolve_config.json` likhta hai (repo path + settings).
2. Menu scripts copy karta hai:
   `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\`

Install ke baad **Resolve restart karo**.

## Use (har baar)

1. DaVinci Resolve → **Edit page** → jo clip key karni hai usse **select**
   karo (multiple clips bhi chalegi).
2. **Workspace > Scripts > CorridorKey Key Clips**
3. Ek nayi **console window** khulegi — model load + inference wahin chalega
   (pehli baar 1 minute+ lag sakta hai). Progress aur errors yahin dikhenge.
4. Console par `DONE` aane par:
   **Workspace > Scripts > CorridorKey Import Results**
   (ya `Key Clips` script dobara chalao — wo bhi finished jobs import kar deta hai.)

Result:

- **Media Pool** → `CorridorKey` bin mein `<clip>_alpha.mp4` (matte) aur
  `<clip>_cutout.mov` (ProRes 4444, **alpha embedded** — hamesha green ke
  upar drop karne ke liye ready).
- **Timeline** → `CorridorKey` naam ki top video track par cutout clip,
  original clip ke theek upar wahi frame position par (orange color coded).
  Background layer neeche daal do, comp ready.

### Alpha ko matte ke roop mein use karna (Delta Keyer / Fusion)

`cutout.mov` mein alpha already embedded hai. Agar sirf alpha video se kaam
karna ho (jaise Delta Keyer ka matte input), to alpha ko timeline par daalo
aur MediaIn → Delta Keyer ya `SetMatte` se wire karo, ya clip ke Fusion comp
mein `LumaKeyer` chain karo.

## Settings

`resolve_plugin\resolve_config.json` kisi bhi text editor mein edit karke
**installer dobara chalao** (taaki Resolve wali copy update ho). Important keys:

| Key | Default | Matlab |
|---|---|---|
| `outputs` | `["alpha", "cutout"]` | `alpha`, `fg`, `checker`, `cutout` ka combination |
| `despill_strength` | `0.5` | cutout/checker par despill (0.0–1.0) |
| `hint_first_frame` | `""` | first-frame matte hint ka path (PNG) |
| `hint_video` | `""` | full hint video ka path |
| `hint_quality` | `0.95` | hint par trust (0.5 rough, 0.95 clean) |
| `low_vram` | `false` | staged low-VRAM inference force karo |
| `hann_chunk` | `0` | 0 = auto (GPU memory se khud choose), warna jaise `8` |
| `output_format` | `"video"` | `"exr"` = HDR linear EXR sequences (manual import — Resolve auto-import sirf video files karta hai) |
| `place_on_timeline` | `true` | import par timeline track par bhi place karo |
| `auto_import_on_start` | `true` | Key Clips purane finished jobs pehle import kare |

## Kaise kaam karta hai (architecture)

```
Resolve (menu script, stdlib only)          Repo venv (worker, naya console)
┌────────────────────────────┐              ┌─────────────────────────────┐
│ CorridorKey_KeyClips.py    │  job.json    │ corridorkey_worker.py       │
│  selected clips + ranges   │ ───────────▶ │  dedupe by source file      │
│  launch worker             │              │  infer.py per clip          │
│                            │              │  worker.log + done.json     │
│ CorridorKey_ImportResults  │ ◀─────────── │                             │
│  ImportMedia -> bin        │  done.json   │                             │
│  AppendToTimeline -> track │              │                             │
└────────────────────────────┘              └─────────────────────────────┘
```

- Job folders: `<repo>\resolve_jobs\active\...` (running), `imported\...`
  (placed in Resolve), `failed\...` (error cases). `worker.log` mein poora
  inference output rehta hai.
- Inference **poore source file** par chalti hai; import ke waqt
  `startFrame`/`endFrame`/`recordFrame` se sirf timeline wala hissa place
  hota hai — isliye trimmed clips bhi sahi align hoti hain.
- Ek hi media file se multiple timeline clips → inference sirf ek baar.

## Limitations (v1)

- **Retime/speed-changed** clips par placed output retime pick nahi karta
  (normal speed par place hoga).
- Multicam/nested timeline items `GetMediaPoolItem()` None dete hain — skip.
- EXR output mode files bin mein auto-import nahi hoti (folders manual
  import karo — Media Pool mein EXR sequence select karna hota hai).
- Agar timeline ka FPS source se different hai to Resolve default conform
  use karega.

## Troubleshooting

- **Scripts menu mein kuch nahi dikhta** → Resolve restart karo; path check
  karo: `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\`
- **"venv nahi mila" warning** → pehle main README ka Setup complete karo.
- **Console window error** → `<repo>\resolve_jobs\active\<job>\worker.log`
  kholo — poora trace wahan hai. Common: weights missing, CUDA OOM
  (`low_vram: true` + `hann_chunk: 8` try karo), ffmpeg PATH par nahi.
- **Free Resolve 21.1+** → scripting hi nahi hai; 21.0 ya Studio use karo.
