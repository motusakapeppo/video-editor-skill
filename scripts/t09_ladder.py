"""t09_ladder — la scala di difetti: livelli noti, misurati, e un panel cieco.

Per calibrare una soglia non serve un campione grande: servono **campioni di
riferimento**. Qui si costruisce, da una clip pulita, una scala per asse:

    livello 0 = la base, nessun difetto aggiunto
    livello 1 = difetto leggero
    livello 2 = difetto evidente
    livello 3 = difetto grave

Ogni clip viene misurata con la metrica del suo asse (`analyze.measure_*`). Il
panel guarda le clip con identificativi **neutri** (s01..s12, ordine fisso ma non
per livello) e non sa quale livello sta giudicando: se lo sapesse, la scala
diventerebbe una profezia.

La soglia e' il valore misurato del **primo livello che il panel chiama
`evident`** (o `blown` per l'esposizione, `evident` per la dominante): il punto in
cui il difetto smette di essere sopportabile, misurato dove succede.

    python scripts/t09_ladder.py build <out_dir> --base "<clip>"
    python scripts/t09_ladder.py report <out_dir>/ladder.json --panel <panel.json> --out <corpus.json>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import analyze  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

AXES = ("shake", "exposure", "cast")
#: level -> the judgement that makes that level the threshold, per axis.
EVIDENT = {
    "shake": ("evident", "unwatchable"),
    # La scala copre il lato scuro. Il clipping e' un'altra faccia dello stesso
    # asse e resta dichiarato come non coperto: mescolarli in una sola soglia
    # significherebbe sommare due difetti opposti.
    "exposure": ("dark", "very_dark"),
    "cast": ("evident",),
}
FPS = 25
DURATION = 2.5
SIZE = (512, 288)


def _run(args: Sequence[str]) -> None:
    proc = subprocess.run([ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.decode("utf-8", "replace")[-400:])


def _filter(axis: str, level: int) -> str:
    if axis == "shake":
        amplitude = {0: 0, 1: 6, 2: 14, 3: 26}[level]
        if amplitude == 0:
            return f"scale={SIZE[0]}:{SIZE[1]},setsar=1"
        return (
            f"scale={SIZE[0] + 2 * amplitude}:{SIZE[1] + 2 * amplitude},"
            f"crop={SIZE[0]}:{SIZE[1]}:"
            f"{amplitude}+{amplitude}*sin(9.1*t):{amplitude}+{amplitude}*sin(6.3*t+1.1),"
            f"scale={SIZE[0]}:{SIZE[1]},setsar=1"
        )
    if axis == "exposure":
        brightness = {0: 0.0, 1: -0.16, 2: -0.30, 3: -0.46}[level]
        return f"scale={SIZE[0]}:{SIZE[1]},eq=brightness={brightness},setsar=1"
    shift = {0: 0.0, 1: 0.10, 2: 0.22, 3: 0.34}[level]
    return (
        f"scale={SIZE[0]}:{SIZE[1]},"
        f"colorbalance=rm={shift:.3f}:bm={-shift:.3f}:gm={-shift * 0.5:.3f},setsar=1"
    )


def build(base: Path, out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    clips: List[Dict[str, Any]] = []
    for axis in AXES:
        for level in range(4):
            name = f"s{len(clips) + 1:02d}"
            target = out_dir / f"{name}.mp4"
            _run(
                [
                    "-ss", "1.0", "-i", str(base), "-t", f"{DURATION}", "-an",
                    "-vf", _filter(axis, level),
                    "-r", str(FPS), "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                    str(target),
                ]
            )
            measured = _measure(axis, target)
            clips.append(
                {"id": name, "axis": axis, "level": level, "path": str(target), "measured": measured}
            )
    payload = {"base": str(base), "clips": clips}
    (out_dir / "ladder.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return payload


def _measure(axis: str, path: Path) -> Dict[str, float]:
    if axis == "shake":
        data = analyze.measure_jitter(path)
        return {"value": float(data.get("score") or 0.0), "raw": data}
    if axis == "exposure":
        data = analyze.measure_exposure(path)
        return {
            "value": float(data.get("mean_luma") or 0.0),
            "clip_high_pct": float(data.get("clip_high_pct") or 0.0),
            "raw": data,
        }
    data = analyze.measure_color(path)
    rg = float(data.get("cast_rg") or 0.0)
    by = float(data.get("cast_by") or 0.0)
    return {"value": max(abs(rg), abs(by)), "raw": data}


def sheet(ladder: Mapping[str, Any], out_dir: Path) -> Path:
    """One strip per clip, three frames, neutral ids, order fixed by id."""
    from PIL import Image, ImageDraw

    tile = (300, 169)
    rows = len(ladder["clips"])
    canvas = Image.new("RGB", (tile[0] * 3, tile[1] * rows), (12, 12, 12))
    draw = ImageDraw.Draw(canvas)
    for row, clip in enumerate(ladder["clips"]):
        path = Path(clip["path"])
        for column, fraction in enumerate((0.2, 0.5, 0.8)):
            at = DURATION * fraction
            tile_path = out_dir / f"{clip['id']}_{column}.png"
            _run(["-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1", "-vf", f"scale={tile[0]}:{tile[1]}", str(tile_path)])
            with Image.open(tile_path) as image:
                canvas.paste(image.convert("RGB"), (column * tile[0], row * tile[1]))
            tile_path.unlink(missing_ok=True)
        draw.text((6, row * tile[1] + 6), clip["id"], fill=(255, 255, 0))
    target = out_dir / "ladder_sheet.png"
    canvas.save(target)
    return target


def thresholds(ladder: Mapping[str, Any], panel: Mapping[str, Any]) -> Dict[str, Any]:
    """The threshold is the measured value of the first level judged evident."""
    tokens = (panel.get("clips") or {})
    out: Dict[str, Any] = {}
    for axis in AXES:
        levels = sorted(
            (clip for clip in ladder["clips"] if clip["axis"] == axis),
            key=lambda clip: clip["level"],
        )
        chosen = None
        for clip in levels:
            token = ((tokens.get(clip["id"]) or {}).get("axes") or {}).get(axis)
            if token in EVIDENT[axis]:
                chosen = {"level": clip["level"], "token": token, "value": clip["measured"]["value"]}
                break
        values = [
            {"level": clip["level"], "token": ((tokens.get(clip["id"]) or {}).get("axes") or {}).get(axis), "value": clip["measured"]["value"]}
            for clip in levels
        ]
        out[axis] = {
            "threshold": None if chosen is None else chosen["value"],
            "level": None if chosen is None else chosen["level"],
            "token": None if chosen is None else chosen["token"],
            "ladder": values,
            "basis": "valore misurato del primo livello giudicato 'evident' dal panel cieco",
        }
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="t09 defect ladder")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("out_dir", type=Path)
    b.add_argument("--base", type=Path, required=True)
    s = sub.add_parser("sheet")
    s.add_argument("ladder", type=Path)
    s.add_argument("out_dir", type=Path)
    r = sub.add_parser("report")
    r.add_argument("ladder", type=Path)
    r.add_argument("--panel", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "build":
        payload = build(args.base, args.out_dir)
        print(json.dumps([{k: clip[k] for k in ("id", "axis", "level")} for clip in payload["clips"]], ensure_ascii=False))
        return 0
    if args.command == "sheet":
        ladder = json.loads(args.ladder.read_text(encoding="utf-8"))
        print(str(sheet(ladder, args.out_dir)))
        return 0
    ladder = json.loads(args.ladder.read_text(encoding="utf-8"))
    panel = json.loads(args.panel.read_text(encoding="utf-8"))
    found = thresholds(ladder, panel)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(found, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({axis: found[axis]["threshold"] for axis in AXES}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
