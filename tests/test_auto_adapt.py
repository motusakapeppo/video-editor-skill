"""The machine adapts to the material, and the opening is chosen by measurement.

Three defects are pinned here, with numbers:

* a 360x640 phone clip on a 1080x1920 canvas needs 3x of growth, so the old
  triage refused *every* asset and the run died on "nessun video selezionato".
  Now the refusal is turned into a declared adaptation (raise the limit, or lower
  the canvas) and the report says which one and why;
* the opening a_roll was the alphabetically first file, which is not a decision.
  It is now the clip with the highest measured sharpness among the first N, and
  ``opening.quality`` says ``below_threshold`` when every candidate is unusable;
* the cover was a fixed frame at 0.5 s, i.e. the blurry tail of a zoom. It is now
  the sharpest usable frame of the opening shot, and it is never a frame the
  coherence gate would call unusable while a usable one exists.

The last block is one real ``vx auto`` run on a synthetic 360x640 pool: no
override, exit 0, ``triage.adapted`` in ``auto_report.json``.

python tests/test_auto_adapt.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import asset_triage as at  # noqa: E402
from vx import auto  # noqa: E402
from vx import contract  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"
TARGET = (1080, 1920)
SHARP = float(contract.THRESHOLDS["triage_sharpness_low"])


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:58} {reason}")


def _asset(
    asset_id: str,
    *,
    width: int = 1080,
    height: int = 1920,
    duration: float = 3.0,
    kind: str = "video",
    has_audio: bool = True,
    order_index: int = -1,
) -> at.AssetInfo:
    return at.AssetInfo(
        asset_id=asset_id,
        kind=kind,
        path=f"/pool/{asset_id}",
        width=int(width),
        height=int(height),
        duration_sec=float(duration),
        has_audio=bool(has_audio),
        order_index=int(order_index),
    )


def _triage(assets, target=TARGET, **kwargs):
    kwargs.setdefault("montage", True)
    return at.triage_assets(
        assets, target, measure=lambda _asset: {}, **kwargs
    )


def _sharpness_table(table):
    """Replace the file measurement with a declared one: no media needed."""

    def _fake(asset):
        value = table.get(asset.asset_id)
        if value is None:
            return None
        return {"sharpness": float(value), "samples": 5, "mean_luma": 120.0,
                "method": "laplacian_variance"}

    return _fake


# --------------------------------------------------------------------------- #
# 1. the canvas adapts, and says so
# --------------------------------------------------------------------------- #


def adaptation_checks() -> None:
    print("\n-- adattamento della risoluzione -------------------------------------")
    phone = _asset("phone.mp4", width=360, height=640, duration=5.0)
    result = _triage([phone])
    adapted = dict(result.adapted or {})
    check(
        "360x640 su 1080x1920: il video entra invece di essere rifiutato",
        [item.asset_id for item in result.selected] == ["phone.mp4"]
        and not result.rejected,
        ([item.asset_id for item in result.selected],),
    )
    check(
        "triage.adapted dichiara il motivo e il limite usato",
        adapted.get("reason") == "all_rejections_resolution_too_low"
        and adapted.get("mode") == "upscale_limit"
        and abs(float(adapted.get("needed_upscale") or 0.0) - 3.0) <= 1e-6
        and abs(float(adapted.get("max_upscale_used") or 0.0) - 3.0) <= 1e-6,
        adapted,
    )
    check(
        "l'adattamento tiene la risoluzione richiesta e conta gli asset recuperati",
        adapted.get("resolution") == [1080, 1920]
        and adapted.get("requested_resolution") == [1080, 1920]
        and adapted.get("assets_recovered") == 1,
        (adapted.get("resolution"), adapted.get("assets_recovered")),
    )
    check(
        "il warning e' leggibile e porta i numeri",
        "3.00x" in str(adapted.get("warning") or "")
        and "1.50x" in str(adapted.get("warning") or "")
        and "1 asset recuperati" in str(adapted.get("warning") or ""),
        adapted.get("warning"),
    )

    # Beyond the 4x ceiling the canvas is lowered instead, aiming at 2x.
    tiny = _asset("tiny.mp4", width=200, height=355, duration=4.0)
    lowered = _triage([tiny])
    low = dict(lowered.adapted or {})
    check(
        "oltre il tetto 4x: la risoluzione di uscita scende, non il limite",
        low.get("mode") == "resolution"
        and low.get("resolution") == [540, 960]
        and float(low.get("max_upscale_used") or 0.0) <= 4.0,
        low,
    )
    check(
        "la scala delle risoluzioni e' quella dichiarata",
        low.get("ladder") == [[1080, 1920], [720, 1280], [540, 960]],
        low.get("ladder"),
    )
    block = lowered.payload_block()
    check(
        "il blocco del payload porta la risoluzione adattata",
        block.get("adapted", {}).get("resolution") == [540, 960],
        sorted(block),
    )

    # Mixed refusals: a short asset is not a canvas problem, so nothing adapts.
    mixed = _triage(
        [
            _asset("phone.mp4", width=360, height=640, duration=5.0),
            _asset("blip.mp4", width=90, height=160, duration=0.2),
        ]
    )
    reasons = {item.rejection_reason for item in mixed.rejected}
    check(
        "rifiuti misti: nessun adattamento",
        mixed.adapted is None and reasons == {"RESOLUTION_TOO_LOW", "TOO_SHORT"},
        (mixed.adapted, sorted(reasons)),
    )
    check(
        "il blocco non guadagna la chiave adapted quando non serve",
        "adapted" not in mixed.payload_block(),
        sorted(mixed.payload_block()),
    )

    # The material that already fits keeps the block shape of every consumer.
    big = _triage([_asset("big.mp4", width=1080, height=1920, duration=3.0)])
    check(
        "materiale gia' buono: nessun adattamento, nessuna regressione",
        big.adapted is None
        and [item.asset_id for item in big.selected] == ["big.mp4"]
        and sorted(big.payload_block())
        == ["not_evaluated", "rejected_assets", "role_source", "selected_assets", "thresholds"],
        sorted(big.payload_block()),
    )


# --------------------------------------------------------------------------- #
# 2. the error names the cause, with numbers
# --------------------------------------------------------------------------- #


def message_checks() -> None:
    print("\n-- messaggi che dicono la causa --------------------------------------")
    resolution = [
        {
            "asset_id": "phone.mp4",
            "rejection_reason": "RESOLUTION_TOO_LOW",
            "details": {
                "upscale": 3.0,
                "max_upscale": 1.5,
                "src_w": 360,
                "src_h": 640,
                "target_w": 1080,
                "target_h": 1920,
            },
        },
        {
            "asset_id": "wide.mp4",
            "rejection_reason": "RESOLUTION_TOO_LOW",
            "details": {
                "upscale": 2.2594,
                "max_upscale": 1.5,
                "src_w": 478,
                "src_h": 850,
                "target_w": 1080,
                "target_h": 1920,
            },
        },
    ]
    message = str(auto._no_video_error([], resolution))
    check(
        "l'errore nomina il motivo con il conteggio",
        "2x RESOLUTION_TOO_LOW" in message,
        message,
    )
    check(
        "l'errore porta l'upscale necessario e il limite",
        "3.00x" in message and "1.50x" in message and "360x640" in message,
        message,
    )
    check(
        "l'errore nomina il rimedio con il numero",
        "--override thresholds.triage_max_upscale=3.10" in message,
        message,
    )
    mixed = resolution + [
        {"asset_id": "blip.mp4", "rejection_reason": "TOO_SHORT", "details": {}},
        {"asset_id": "copy.mp4", "rejection_reason": "DUPLICATE_ASSET", "details": {}},
    ]
    mixed_message = str(auto._no_video_error([], mixed))
    check(
        "con rifiuti misti elenca tutti i motivi",
        "2x RESOLUTION_TOO_LOW" in mixed_message
        and "1x TOO_SHORT" in mixed_message
        and "1x DUPLICATE_ASSET" in mixed_message,
        mixed_message,
    )
    check(
        "senza rifiuti il messaggio resta quello di sempre",
        "nessun video selezionato" in str(auto._no_video_error([], [])),
        str(auto._no_video_error([], [])),
    )


# --------------------------------------------------------------------------- #
# 3. the opening is chosen by measured sharpness
# --------------------------------------------------------------------------- #


def opening_checks() -> None:
    print("\n-- apertura scelta per nitidezza -------------------------------------")
    original = auto._clip_sharpness
    try:
        table = {"b.mp4": 120.0, "a.mp4": 60.0}
        auto._clip_sharpness = _sharpness_table(table)
        pool = [_asset("a.mp4", order_index=0), _asset("b.mp4", order_index=1)]
        by_id = {item.asset_id: item for item in pool}
        triage = _triage(pool)
        block = triage.payload_block()
        selected, section = auto.apply_opening(block, by_id, threshold=SHARP)
        section = dict(section or {})
        check(
            "la clip piu' nitida apre, non la prima per nome",
            section.get("chosen", {}).get("asset_id") == "b.mp4"
            and section.get("moved") is True,
            section,
        )
        check(
            "l'ordine del blocco segue la scelta, il resto resta",
            [item["asset_id"] for item in selected] == ["b.mp4", "a.mp4"]
            and [item["asset_id"] for item in block["selected_assets"]]
            == ["b.mp4", "a.mp4"],
            [item["asset_id"] for item in selected],
        )
        check(
            "la scelta dichiara la regola e la misura",
            section.get("rule") == "sharpness"
            and section.get("metric") == "laplacian_variance_median"
            and section.get("quality") == "measured"
            and section.get("chosen", {}).get("sharpness") == 120.0,
            section.get("chosen"),
        )

        # A clip under the threshold is unusable as an opening.
        auto._clip_sharpness = _sharpness_table({"a.mp4": 20.0, "b.mp4": 90.0})
        block = _triage(pool).payload_block()
        selected, section = auto.apply_opening(block, by_id, threshold=SHARP)
        check(
            "la clip inutilizzabile non apre",
            (section or {}).get("chosen", {}).get("asset_id") == "b.mp4",
            section,
        )

        # Every candidate below the threshold: the best one still opens.
        auto._clip_sharpness = _sharpness_table({"a.mp4": 20.0, "b.mp4": 30.0})
        block = _triage(pool).payload_block()
        selected, section = auto.apply_opening(block, by_id, threshold=SHARP)
        check(
            "tutte sotto soglia: apre la migliore, e il report lo dice",
            (section or {}).get("chosen", {}).get("asset_id") == "b.mp4"
            and (section or {}).get("quality") == "below_threshold"
            and selected[0]["asset_id"] == "b.mp4",
            section,
        )

        # Ties keep the declared order: the manifest wins on equal quality.
        auto._clip_sharpness = _sharpness_table({"a.mp4": 90.0, "b.mp4": 90.0})
        block = _triage(pool).payload_block()
        selected, section = auto.apply_opening(block, by_id, threshold=SHARP)
        check(
            "a pari nitidezza l'ordine dichiarato resta",
            (section or {}).get("chosen", {}).get("asset_id") == "a.mp4"
            and (section or {}).get("moved") is False
            and selected[0]["asset_id"] == "a.mp4",
            section,
        )

        # The b_roll/music slots do not move when the opening does.
        pool3 = [
            _asset("a.mp4", order_index=0),
            _asset("b.mp4", order_index=1),
            _asset("still.jpg", kind="image", width=1080, height=1920, has_audio=False,
                   order_index=2),
        ]
        by3 = {item.asset_id: item for item in pool3}
        auto._clip_sharpness = _sharpness_table({"a.mp4": 60.0, "b.mp4": 140.0})
        block = _triage(pool3).payload_block()
        selected, _section = auto.apply_opening(block, by3, threshold=SHARP)
        check(
            "spostare l'apertura non tocca b_roll ne' musica",
            [item["asset_id"] for item in selected]
            == ["b.mp4", "a.mp4", "still.jpg"],
            [item["asset_id"] for item in selected],
        )
    finally:
        auto._clip_sharpness = original


# --------------------------------------------------------------------------- #
# 4. the cover is a usable frame of the opening
# --------------------------------------------------------------------------- #


def cover_checks() -> None:
    print("\n-- copertina dalla stessa logica -------------------------------------")
    frames = [
        {"t_src": 0.2, "sharpness": 5.0, "faces": 1},
        {"t_src": 2.0, "sharpness": 180.0, "faces": 0},
        {"t_src": 3.0, "sharpness": 140.0, "faces": 1},
    ]
    best, quality, face = auto.pick_cover_frame(frames, SHARP)
    check(
        "tra frame usabili vince quello col volto",
        best["t_src"] == 3.0 and face is True and quality == "measured",
        (best, quality, face),
    )
    blurry_face = [
        {"t_src": 0.2, "sharpness": 5.0, "faces": 1},
        {"t_src": 2.0, "sharpness": 180.0, "faces": 0},
    ]
    best, quality, face = auto.pick_cover_frame(blurry_face, SHARP)
    check(
        "un volto sotto soglia non salva un frame inutilizzabile",
        best["t_src"] == 2.0 and quality == "measured",
        (best, quality),
    )
    all_bad = [
        {"t_src": 0.2, "sharpness": 20.0, "faces": 1},
        {"t_src": 2.0, "sharpness": 30.0, "faces": 0},
    ]
    best, quality, _face = auto.pick_cover_frame(all_bad, SHARP)
    check(
        "tutto sotto soglia: la migliore, mai la peggiore, e dichiarato",
        best["t_src"] == 2.0 and quality == "below_threshold",
        (best, quality),
    )


# --------------------------------------------------------------------------- #
# 5. the manifest wins over the alphabetical name
# --------------------------------------------------------------------------- #


def order_checks() -> None:
    print("\n-- ordine: manifest contro nome --------------------------------------")
    with tempfile.TemporaryDirectory(prefix="vx-order-") as raw:
        folder = Path(raw)
        for name in ("a.mp4", "b.mp4"):
            (folder / name).write_text("not a video\n", encoding="utf-8")
        manifest = {"assets": [{"path": "b.mp4"}, {"path": "a.mp4"}]}
        declared = [item.asset_id for item in at.discover_assets(folder, manifest=manifest)]
        by_name = [item.asset_id for item in at.discover_assets(folder)]
        check(
            "con manifest l'ordine e' quello dichiarato",
            declared == ["b.mp4", "a.mp4"],
            declared,
        )
        check(
            "senza manifest resta l'ordine per nome",
            by_name == ["a.mp4", "b.mp4"],
            by_name,
        )
        check(
            "la posizione di scoperta viaggia con l'asset",
            [item.order_index for item in at.discover_assets(folder, manifest=manifest)]
            == [0, 1],
            [item.order_index for item in at.discover_assets(folder, manifest=manifest)],
        )

    marked = [
        _asset("z.mp4", order_index=0),
        _asset("a.mp4", order_index=1),
    ]
    ordered = _triage(marked)
    check(
        "il triage rispetta l'ordine del manifest",
        [item.asset_id for item in ordered.selected] == ["z.mp4", "a.mp4"],
        [item.asset_id for item in ordered.selected],
    )
    plain = [_asset("z.mp4"), _asset("a.mp4")]
    alphabetical = _triage(plain)
    check(
        "senza ordine dichiarato il triage torna alfabetico",
        [item.asset_id for item in alphabetical.selected] == ["a.mp4", "z.mp4"],
        [item.asset_id for item in alphabetical.selected],
    )


# --------------------------------------------------------------------------- #
# 6. one real run: no override, exit 0, the report carries the adaptation
# --------------------------------------------------------------------------- #


def _cli(args: list) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts")
    env["PYTHONIOENCODING"] = "utf-8"
    env["VX_FFMPEG"] = FFMPEG
    return subprocess.run(
        [sys.executable, "-m", "vx.cli", *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _make_clip(path: Path, width: int, height: int, seconds: float) -> bool:
    proc = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate=25:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={seconds}",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(path),
        ],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file() and path.stat().st_size > 0


def e2e_checks() -> None:
    print("\n-- una corsa vera: 360x640, nessun override --------------------------")
    with tempfile.TemporaryDirectory(prefix="vx-adapt-") as raw:
        tmp = Path(raw)
        pool = tmp / "src"
        pool.mkdir()
        if not _make_clip(pool / "phone.mp4", 360, 640, 2.0):
            skip("corsa e2e", "ffmpeg non disponibile")
            return
        manifest = pool / "manifest.json"
        manifest.write_text(
            json.dumps({"assets": [{"path": "phone.mp4", "role": "a_roll"}]}),
            encoding="utf-8",
        )
        out = tmp / "master.mp4"
        work = tmp / "work"
        proc = _cli(
            [
                "auto",
                "--prompt",
                "reel verticale 9:16, ritmo serrato",
                "--assets",
                str(pool),
                "--manifest",
                str(manifest),
                "--out",
                str(out),
                "--workdir",
                str(work),
                "--no-subs",
                "--no-panel",
                "--no-font-network",
                "--json",
            ]
        )
        report_path = work / "auto_report.json"
        report = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if report_path.is_file()
            else {}
        )
        check(
            "senza override la corsa esce 0",
            proc.returncode == 0 and out.is_file(),
            (proc.returncode, (proc.stderr or "")[-300:]),
        )
        adapted = (report.get("triage") or {}).get("adapted") or {}
        check(
            "il report porta triage.adapted con i numeri",
            adapted.get("mode") == "upscale_limit"
            and adapted.get("resolution") == [1080, 1920]
            and float(adapted.get("needed_upscale") or 0.0) >= 3.0
            and int(adapted.get("assets_recovered") or 0) >= 1,
            adapted,
        )
        check(
            "il report dichiara la sorgente dell'ordine",
            report.get("order_source") == "manifest"
            and (report.get("triage") or {}).get("order_source") == "manifest",
            report.get("order_source"),
        )
        opening = report.get("opening") or {}
        check(
            "il report dichiara l'apertura e la sua qualita'",
            (opening.get("chosen") or {}).get("asset_id") == "phone.mp4"
            and opening.get("quality") in ("measured", "below_threshold"),
            opening,
        )
        thumbnail = next(
            (
                item
                for item in report.get("deliverables") or []
                if item.get("kind") == "thumbnail"
            ),
            {},
        )
        check(
            "la copertina dichiara clip, istante e nitidezza",
            bool(thumbnail.get("clip_id"))
            and isinstance(thumbnail.get("t"), (int, float))
            and isinstance(thumbnail.get("sharpness"), (int, float)),
            thumbnail,
        )
        check(
            "la copertina non viene da un frame inutilizzabile",
            float(thumbnail.get("sharpness") or 0.0) >= SHARP,
            thumbnail.get("sharpness"),
        )


def main() -> int:
    adaptation_checks()
    message_checks()
    opening_checks()
    cover_checks()
    order_checks()
    e2e_checks()
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
