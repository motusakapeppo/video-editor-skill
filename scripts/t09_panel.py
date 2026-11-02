"""t09_panel — costruire i fogli per il panel, contare i voti, scrivere il corpus.

Il panel non e' una giuria di persone: sono modelli che **guardano** i fotogrammi
e dicono, con i token del registro, quanto tremolio, esposizione, dominante e
rumore vedono. Il valore sta in due cose misurabili:

* il giudizio arriva dalle immagini, non dai numeri che poi si calibrano;
* il disaccordo fra annotatori si misura: dove non sono d'accordo il clip resta
  ``contested`` ed esce dal conteggio, invece di essere mediato a caso.

Uso:

    python scripts/t09_panel.py sheets <out_dir>
    python scripts/t09_panel.py tally <a.json> <b.json> <c.json> --out panel.json
    python scripts/t09_panel.py build <panel.json> --out tests/fixtures/corpus_annotations.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import ffmpeg as ff  # noqa: E402
from vx import registry  # noqa: E402

CLIPS: Tuple[Tuple[str, str], ...] = (
    ("c01", "t09_01_VID-20260412-WA0019.mp4"),
    ("c02", "t09_02_WhatsApp_Video_2026-10-26_at_23.02.09.mp4"),
    ("c03", "t09_03_WhatsApp_Video_2026-10-26_at_22.59.10_(1).mp4"),
    ("c04", "t09_04_WhatsApp_Video_2026-10-26_at_23.08.00.mp4"),
    ("c05", "t09_05_WhatsApp_Video_2026-10-26_at_23.08.01.mp4"),
    ("c06", "t09_06_video_2026-04-21_12-58-55.mp4"),
    ("c07", "t09_07_WhatsApp_Video_2026-10-26_at_23.08.01_(1).mp4"),
    ("c08", "t09_08_WhatsApp_Video_2026-10-26_at_23.02.21.mp4"),
    ("c09", "t09_09_VID-20250530-WA0072.mp4"),
    ("c10", "t09_10_WhatsApp_Video_2026-10-26_at_23.02.09_(1).mp4"),
)

EXTRAS: Tuple[Tuple[str, str], ...] = (
    ("x01", r"<WORK_DIR>/reel_amici/reel_raw.mp4"),
    ("x02", r"<WORK_DIR>/vox/stress/video.mp4"),
    ("x03", r"<WORK_DIR>/vox/v3/joined.mp4"),
    ("x04", r"<WORK_DIR>/ulw_live/coro_stress.mp4"),
    ("x05", r"<WORK_DIR>/ulw_live/coro_preset.mp4"),
    ("x06", r"<WORK_DIR>/friends45/out/master.mp4"),
    ("x07", r"<WORK_DIR>/accept/master.mp4"),
    ("x08", r"<WORK_DIR>/accept3/master.mp4"),
    ("x09", r"<WORK_DIR>/accept4/master.mp4"),
    ("x10", r"<WORK_DIR>/ulw_live/coro_stress_chromium.mp4"),
)

VISUAL_AXES = ("shake", "exposure", "cast", "noise")
SHEET_COLS = 3
SHEET_ROWS = 5
TILE = (320, 180)
AT = (0.25, 0.50, 0.75)


def work_dir() -> Path:
    """The work root, resolved from the environment or from the clips themselves."""
    return Path(os.environ.get("VX_WORK_DIR", r"<WORK_DIR>"))


def clips() -> List[Tuple[str, Path]]:
    base = work_dir()
    out: List[Tuple[str, Path]] = []
    for name, filename in CLIPS:
        out.append((name, base / "t09" / "clips" / filename))
    for name, path in EXTRAS:
        out.append((name, Path(path.replace("<WORK_DIR>", str(base)))))
    return out


def duration(path: Path) -> float:
    proc = subprocess.run(
        [ff.ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
    )
    try:
        return float(proc.stdout.decode().strip())
    except ValueError:
        return 0.0


def frame(path: Path, at: float, out: Path) -> Path:
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
            "-vf", f"scale={TILE[0]}:{TILE[1]}:force_original_aspect_ratio=decrease", str(out),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not out.is_file():
        raise SystemExit(f"frame failed for {path}: {proc.stderr.decode('utf-8', 'replace')[-200:]}")
    return out


def sheets(out_dir: Path, per_sheet: int = SHEET_ROWS) -> Dict[str, Any]:
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    pairs = [(name, path) for name, path in clips() if path.is_file()]
    missing = [name for name, path in clips() if not path.is_file()]
    index: Dict[str, Any] = {"sheets": [], "missing": missing, "clips": {}}
    for start in range(0, len(pairs), per_sheet):
        group = pairs[start : start + per_sheet]
        canvas = Image.new("RGB", (TILE[0] * SHEET_COLS, TILE[1] * len(group)), (12, 12, 12))
        draw = ImageDraw.Draw(canvas)
        for row, (name, path) in enumerate(group):
            total = duration(path)
            for column, fraction in enumerate(AT):
                at = max(0.0, min(total - 0.05, total * fraction))
                tile = out_dir / f"{name}_{column}.png"
                frame(path, at, tile)
                with Image.open(tile) as image:
                    canvas.paste(image.convert("RGB"), (column * TILE[0], row * TILE[1]))
                tile.unlink(missing_ok=True)
            draw.text((6, row * TILE[1] + 6), name, fill=(255, 255, 0))
        sheet_path = out_dir / f"sheet_{len(index['sheets']) + 1}.png"
        canvas.save(sheet_path)
        index["sheets"].append({"file": sheet_path.name, "clips": [name for name, _ in group]})
        for name, path in group:
            index["clips"][name] = str(path)
    (out_dir / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return index


def _majority(values: Sequence[Any]) -> Tuple[Any, int]:
    counts: Dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    best = max(counts, key=lambda key: (counts[key], -sorted(counts).index(key)))
    return best, counts[best]


def tally(files: Sequence[Path], *, min_agreement: int = 2) -> Dict[str, Any]:
    """Merge the annotators: majority per axis, contested where they split."""
    votes: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
    annotators: List[str] = []
    for path in files:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        annotators.append(str(data.get("annotator") or Path(path).stem))
        for name, clip in (data.get("clips") or {}).items():
            for axis in VISUAL_AXES:
                token = (clip.get("axes") or {}).get(axis)
                if token is None:
                    continue
                votes.setdefault(name, {}).setdefault(axis, {}).setdefault(token, []).append(
                    data.get("annotator") or Path(path).stem
                )
    clips_out: Dict[str, Any] = {}
    contested: List[Dict[str, Any]] = []
    agreements: List[float] = []
    for name in sorted(votes):
        axes: Dict[str, Any] = {}
        for axis in VISUAL_AXES:
            counts = votes[name].get(axis) or {}
            flat = [(token, len(people)) for token, people in counts.items()]
            if not flat:
                axes[axis] = "na"
                continue
            best_token, best_count = max(flat, key=lambda item: item[1])
            total = sum(count for _, count in flat)
            agreements.append(best_count / total if total else 0.0)
            if best_count < min_agreement:
                axes[axis] = "contested"
                contested.append({"clip": name, "axis": axis, "votes": dict(counts)})
            else:
                axes[axis] = best_token
        if any(value == "contested" for value in axes.values()):
            clips_out[name] = {"axes": axes, "contested": True}
        else:
            clips_out[name] = {"axes": axes, "contested": False}
    usable = [name for name, clip in clips_out.items() if not clip["contested"]]
    return {
        "annotators": annotators,
        "clips": clips_out,
        "contested": contested,
        "usable": len(usable),
        "agreement": round(sum(agreements) / len(agreements), 4) if agreements else 0.0,
    }


HOLDOUT: Dict[str, Dict[str, str]] = {
    # c02: giudicata "scarsa", tremolio misurato 3,59.
    "c02": {"shake": "evident|unwatchable"},
    # c09: giudicata "la migliore", tremolio misurato 0,85.
    "c09": {"shake": "ok|light"},
    # c08: giudicata "buona", nitidezza 4,74 (la piu' alta): se il panel vede
    # rumore dove l'umano vede dettaglio, le due letture non guardano lo stesso.
    "c08": {"noise": "absent|light"},
}


def build(panel: Mapping[str, Any], *, out: Path, holdout: Mapping[str, Mapping[str, str]]) -> Dict[str, Any]:
    """Write the annotation corpus, with provenance, and check the human anchors."""
    clips_out: Dict[str, Any] = {}
    for name, clip in (panel.get("clips") or {}).items():
        axes = dict(clip.get("axes") or {})
        if clip.get("contested"):
            # Un'annotazione contestata non entra: si dichiara e si scarta.
            for axis, token in list(axes.items()):
                if token == "contested":
                    axes[axis] = "na"
        axes.update({"volume": "na", "silences": "na", "music": "na"})
        clips_out[name] = {
            "file": name,
            "duration_sec": 0,
            "type": "panel",
            "axes": axes,
            "hook": "na",
            "publishable": "unjudged",
            "notes": "annotato dal panel visivo; audio fuori dal perimetro del panel",
        }
    percentiles = registry.annotated_percentiles(clips_out)
    degenerate = {
        axis: entry
        for axis, entry in percentiles.items()
        if entry.get("n") and entry.get("p33") == entry.get("p67")
    }
    calibratable = not degenerate
    reason = ""
    if degenerate:
        reason = (
            "distribuzione degenere su "
            + ", ".join(sorted(degenerate))
            + ": p33 e p67 coincidono, il corpus non contiene una popolazione di "
            "difetti e non sa separare. Le soglie non si possono derivare da qui."
        )
    anchors = []
    for name, expectations in holdout.items():
        axes = (clips_out.get(name) or {}).get("axes") or {}
        for axis, expected in expectations.items():
            got = axes.get(axis)
            anchors.append(
                {
                    "clip": name,
                    "axis": axis,
                    "expected": expected,
                    "got": got,
                    "ok": got in expected.split("|"),
                }
            )
    payload = {
        "schema": "vx.corpus_annotations/1",
        "provenance": {
            "annotators": list(panel.get("annotators") or []),
            "kind": "model panel (vision), not human: the judges are models reading frames",
            "axes": list(VISUAL_AXES),
            "agreement": panel.get("agreement"),
            "usable": panel.get("usable"),
            "contested": panel.get("contested"),
            "holdout": "7 human judgments from PLAN/T09_CALIBRATION.md (shake anchors)",
        },
        "clips": clips_out,
        "percentiles": percentiles,
        "calibratable": calibratable,
        "calibratable_reason": reason,
        "anchors": anchors,
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="t09 panel")
    sub = parser.add_subparsers(dest="command", required=True)
    p_sheets = sub.add_parser("sheets")
    p_sheets.add_argument("out_dir", type=Path)
    p_tally = sub.add_parser("tally")
    p_tally.add_argument("files", nargs="+", type=Path)
    p_tally.add_argument("--out", type=Path, required=True)
    p_build = sub.add_parser("build")
    p_build.add_argument("panel", type=Path)
    p_build.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "sheets":
        index = sheets(args.out_dir)
        print(json.dumps({"sheets": [item["file"] for item in index["sheets"]], "missing": index["missing"]}))
        return 0
    if args.command == "tally":
        panel = tally(args.files)
        args.out.write_text(json.dumps(panel, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({k: panel[k] for k in ("annotators", "usable", "agreement")}, ensure_ascii=False))
        return 0
    panel = json.loads(args.panel.read_text(encoding="utf-8"))
    payload = build(panel, out=args.out, holdout=HOLDOUT)
    bad = [item for item in payload["anchors"] if not item["ok"]]
    print(
        json.dumps(
            {
                "percentiles": payload["percentiles"],
                "calibratable": payload["calibratable"],
                "anchors_failed": bad,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
