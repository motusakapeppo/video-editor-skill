"""Il produttore dei deliverable: ogni intento dichiarato viene eseguito.

python tests/test_deliverables.py
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import deliverables  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402
from vx import probe as probe_mod  # noqa: E402

try:  # optional: only used to prove the empty cells are black
    from PIL import Image
except Exception:  # pragma: no cover - Pillow is optional
    Image = None

PASS = 0
FAIL = 0

TILE_W = 320
TILE_H = 180
COLS = 4
ROWS = 4
PADDING = 4
MARGIN = 4
EXPECTED_W = 2 * MARGIN + COLS * TILE_W + (COLS - 1) * PADDING
EXPECTED_H = 2 * MARGIN + ROWS * TILE_H + (ROWS - 1) * PADDING


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:52} {detail}")


def _make_clip(path: Path) -> None:
    proc = ff.run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=30:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )
    if not proc.ok:
        raise RuntimeError(proc.tail(20))


def _plan() -> dict:
    return {
        "output": {
            "aspect": "16:9",
            "width": 640,
            "height": 360,
            "thumbnail_at": 1.0,
        },
        "render": {"hook_time": 0.5},
        "brief": {"subtitles": "auto"},
        "treatments": [{"name": "subtitles", "enabled": True}],
    }


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-deliv-") as tmp_name:
        tmp = Path(tmp_name)
        master = tmp / "master.mp4"
        _make_clip(master)
        out = tmp / "out"
        plan = _plan()

        # -- the manifest is exactly the four intents ---------------------
        intents = deliverables.planned(plan)
        check("planned: quattro intenti", len(intents) == 4, str(len(intents)))
        check(
            "planned: nessun export per piattaforma",
            all(i["kind"] != "platform" for i in intents),
        )
        check(
            "planned: percorsi dichiarati",
            [i["path"] for i in intents]
            == [
                "out/master.mp4",
                "out/subtitles.srt",
                "out/thumbnail.jpg",
                "out/contact_sheet.jpg",
            ],
        )

        # -- run without speech: no empty srt may be invented -------------
        items = deliverables.produce(plan, master, out, srt_source=None)
        by_kind = {item["kind"]: item for item in items}

        master_out = out / "master.mp4"
        check("master prodotto", master_out.exists())
        master_probe = probe_mod.probe(master_out)
        check(
            "master durata ~6s",
            abs(master_probe.duration - 6.0) < 0.2,
            f"{master_probe.duration:.3f}s",
        )

        thumb = out / "thumbnail.jpg"
        check("thumbnail esiste", thumb.exists())
        head = thumb.read_bytes()[:3] if thumb.exists() else b""
        check("thumbnail inizia con FF D8 FF", head == b"\xff\xd8\xff", head.hex())
        thumb_probe = probe_mod.probe(thumb)
        check(
            "thumbnail e' un JPEG valido",
            thumb_probe.width > 0 and thumb_probe.height > 0,
            f"{thumb_probe.width}x{thumb_probe.height}",
        )

        sheet = out / "contact_sheet.jpg"
        check("contact sheet esiste", sheet.exists())
        sheet_probe = probe_mod.probe(sheet)
        check(
            "contact sheet 4x4 con padding e margini",
            (sheet_probe.width, sheet_probe.height) == (EXPECTED_W, EXPECTED_H),
            f"{sheet_probe.width}x{sheet_probe.height} atteso {EXPECTED_W}x{EXPECTED_H}",
        )
        if Image is not None:
            x0 = MARGIN + (COLS - 1) * (TILE_W + PADDING)
            y0 = MARGIN + (ROWS - 1) * (TILE_H + PADDING)
            with Image.open(sheet) as img:
                cell = img.crop((x0, y0, x0 + TILE_W, y0 + TILE_H)).convert("RGB")
                extrema = cell.getextrema()
            check(
                "le celle non riempite restano nere",
                all(high <= 16 for _, high in extrema),
                str(extrema),
            )

        sub = by_kind["subtitles"]
        check(
            "senza parlato i sottotitoli sono unavailable",
            sub.get("available") is False and sub.get("reason") == "no speech",
            str(sub.get("reason")),
        )
        check("nessun srt vuoto creato", not (out / "subtitles.srt").exists())

        produced = [item for item in items if item.get("available")]
        check(
            "ogni artefatto prodotto ha sha256",
            all(_is_sha256(item.get("sha256")) for item in produced),
        )
        check(
            "ogni artefatto prodotto ha bytes > 0",
            all(int(item.get("bytes") or 0) > 0 for item in produced),
        )

        # -- run with speech: the srt is copied byte for byte -------------
        srt = tmp / "captions.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nciao\n", encoding="utf-8")
        items2 = deliverables.produce(plan, master, out, srt_source=srt)
        by_kind2 = {item["kind"]: item for item in items2}
        srt_out = out / "subtitles.srt"
        check("srt copiato", srt_out.exists())
        check(
            "srt identico alla sorgente",
            srt_out.exists() and srt_out.read_bytes() == srt.read_bytes(),
        )
        check(
            "srt disponibile quando c'e' parlato",
            by_kind2["subtitles"].get("available") is True,
        )
        check(
            "tutti gli item hanno sha256",
            all(_is_sha256(item.get("sha256")) for item in items2),
        )
        check(
            "tutti gli item hanno bytes > 0",
            all(int(item.get("bytes") or 0) > 0 for item in items2),
        )

        # -- a missing master must fail loudly ----------------------------
        raised = False
        try:
            deliverables.produce(plan, tmp / "missing.mp4", tmp / "out2")
        except Exception:
            raised = True
        check("master mancante rifiutato", raised)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
