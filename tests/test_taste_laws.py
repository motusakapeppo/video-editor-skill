"""C8 -- le due leggi del gusto sono imposte dal codice, non solo scritte.

    python tests/test_taste_laws.py

Legge A: nessun effetto senza prova. ``efx.generate(strict=True)`` emette una
spec solo se la clip porta una misura (jitter, cast, volto, bordo, saturazione)
e i trattamenti creativi distinti non superano il tetto del contratto.

Legge B: meglio corto che ripetuto. ``coherence.shorten_plan`` toglie le corse
di inquadrature quasi identiche, il report porta ``variety`` con le due durate,
e sotto il minimo dichiarabile il lavoro non si consegna.

Gli scenari S1-S4 del verbale sono qui sotto, misurati: niente mock delle
misure, solo piani e fotogrammi veri (PNG generati, e il materiale reale di
``D:\\video_work\\recipe_accept\\src`` quando c'e').
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, coherence, contract, efx, reel  # noqa: E402
from vx.edl_plan import ClipSegment, EDLPlan, SourceRef, Track  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:62} {reason}")


def _png(path: Path, kind: int) -> str:
    """Un fotogramma sintetico: ``kind=0`` e' sempre lo stesso volto finto."""
    import cv2

    image = np.full((180, 320), 60, dtype=np.uint8)
    if kind == 0:
        cv2.circle(image, (160, 90), 60, 220, -1)
        cv2.circle(image, (140, 75), 8, 20, -1)
        cv2.circle(image, (180, 75), 8, 20, -1)
    else:
        # Rumore deterministico: due frame "vari" non possono collidere nel
        # dHash, che e' esattamente cio' che S2 deve dimostrare sul materiale.
        rng = np.random.default_rng(kind * 7919)
        image = rng.integers(0, 256, size=(180, 320), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
    return str(path)


def _plan(rows, sources, duration: float) -> EDLPlan:
    clips = [
        ClipSegment(
            f"v0_{index}",
            source_id,
            src_in,
            src_out,
            index * length,
            (index + 1) * length,
            0,
            "main_talking_head",
        )
        for index, (source_id, src_in, src_out, length) in enumerate(rows)
    ]
    return EDLPlan(
        fps=30.0,
        duration_sec=duration,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources={
            key: SourceRef(path=path, sha256="0" * 64, duration=max(1.0, duration))
            for key, path in sources.items()
        },
        video_tracks=(Track(0, tuple(clips), "a_roll"),),
    )


def _repeat_plan(shots: int = 10, varied: int = 4, shot: float = 1.0):
    """``shots`` inquadrature uguali (sorgente con buchi) + ``varied`` diverse."""
    rows = [("same", index * 2.0, index * 2.0 + shot, shot) for index in range(shots)]
    sources = {"same": "same.mp4"}
    for index in range(varied):
        key = f"var{index}"
        rows.append((key, 0.0, shot, shot))
        sources[key] = f"{key}.mp4"
    plan = _plan(rows, sources, (shots + varied) * shot)
    return plan, shots, varied


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vx-taste-laws-"))
    try:
        print("\n0. le soglie sono nel contratto, dichiarate")
        required = {
            "max_creative_treatments": 3,
            "variety_repeat_max": 2,
            "variety_min_duration_sec": 3.0,
            "effects_shake_jitter_min": 0.35,
            "effects_color_cast_min": 12.0,
            "effects_vignette_border_ratio": 1.06,
            "effects_saturation_max": 0.22,
        }
        for key, value in required.items():
            check(
                f"contract.THRESHOLDS[{key!r}] == {value}",
                contract.THRESHOLDS.get(key) == value,
                contract.THRESHOLDS.get(key),
            )

        # ------------------------------------------------------------------ #
        # S3 / legge A -- nessun effetto senza prova
        # ------------------------------------------------------------------ #
        print("\n1. S3: effects_per_shot 1.0, le spec senza prova cadono")
        total_clips = 16
        with_measure = 12
        rows = [(f"s{index}", 0.0, 0.6, 0.6) for index in range(total_clips)]
        plan = _plan(
            rows,
            {f"s{index}": f"s{index}.mp4" for index in range(total_clips)},
            total_clips * 0.6,
        )
        full = {
            "faces": 2,
            "jitter": 0.5,
            "cast_rg": 18.0,
            "cast_by": 18.0,
            "saturation": 0.1,
            "border_ratio": 1.2,
        }
        evidence = {f"v0_{index}": dict(full) for index in range(with_measure)}
        (tmp / "s3").mkdir(parents=True, exist_ok=True)
        section, plan = auto._recipe_effects(
            {"applied": True, "name": "taste", "explicit": ["effects_per_shot"],
             "params": {"effects_per_shot": 1.0}},
            plan,
            tmp / "s3",
            evidence=evidence,
        )
        table = getattr(plan, "effects", {}) or {}
        applied = [spec for specs in table.values() for spec in specs]
        check("S3: la ricetta resta applicata", section.get("applied") is True, section.get("applied"))
        check(
            "S3: ogni spec applicata porta evidence non vuota",
            bool(applied) and all(spec.get("evidence") for spec in applied),
            [(spec.get("id"), len(spec.get("evidence") or ())) for spec in applied],
        )
        check(
            "S3: le clip senza misura cadono e si contano",
            section.get("dropped_no_evidence") == total_clips - with_measure,
            section.get("dropped_no_evidence"),
        )
        check(
            "S3: i posti richiesti tornano (applicate + cadute + rifiutate == 16)",
            section.get("specs", 0)
            + section.get("dropped_no_evidence", 0)
            + len(section.get("rejected_creative") or ())
            == total_clips,
            (section.get("specs"), section.get("dropped_no_evidence"),
             len(section.get("rejected_creative") or ())),
        )
        check(
            "S3: trattamenti creativi distinti <= 3",
            0 < section.get("creative_distinct", 99) <= 3,
            (section.get("creative_distinct"), section.get("creative_max")),
        )
        check(
            "S3: il quarto trattamento e' rifiutato e motivato",
            bool(section.get("rejected_creative"))
            and all("tetto" in row.get("reason", "") for row in section["rejected_creative"]),
            (section.get("rejected_creative") or [{}])[0].get("reason"),
        )

        print("\n2. legge A: l'audit del piano finale vede le violazioni forzate")
        forged = {
            "c1": [{"id": "zoom", "evidence": [{"kind": "face_track"}]}],
            "c2": [{"id": "warm", "evidence": [{"kind": "color_cast"}]}],
            "c3": [{"id": "shake", "evidence": [{"kind": "jitter"}]}],
            "c4": [{"id": "noir"}],
            "c5": [{"id": "vignette"}],
        }
        audit = efx.audit(forged)
        codes = {row.get("code") for row in audit.get("violations") or ()}
        check("audit: legge A non e' ok", audit.get("ok") is False, codes)
        check("audit: spec senza prova contate", "effect_without_evidence" in codes, codes)
        check("audit: quarto/quinto nome oltre il tetto", "creative_over_cap" in codes, codes)
        check(
            "audit: il tetto e' quello del contratto",
            audit.get("creative_max") == 3
            and audit.get("creative_distinct") == 5,
            (audit.get("creative_distinct"), audit.get("creative_max")),
        )

        # ------------------------------------------------------------------ #
        # S1 / S2 / legge B -- meglio corto che ripetuto
        # ------------------------------------------------------------------ #
        print("\n3. S1: dieci inquadrature simili -> il reel si accorcia")
        repeat_plan, similar, varied = _repeat_plan()
        frames = {index: _png(tmp / "frames" / "same.png", 0) for index in range(similar)}
        for offset in range(varied):
            frames[similar + offset] = _png(
                tmp / "frames" / f"var{offset}.png", offset + 1
            )
        short, block = coherence.shorten_plan(repeat_plan, frames)
        check("S1: removed >= 8", block.get("removed", 0) >= 8, block.get("removed"))
        check(
            "S1: la durata scende e il report la dichiara",
            block.get("duration_after", 99) < block.get("duration_before", 0)
            and block.get("duration_before") == 14.0,
            (block.get("duration_before"), block.get("duration_after")),
        )
        check(
            "S1: nessuna corsa di simili oltre soglia",
            block.get("remaining_runs") == 0,
            block.get("remaining_runs"),
        )
        check(
            "S1: il piano accorciato tiene 1 simile + le 4 varie",
            sum(len(track.clips) for track in short.video_tracks) == 5
            and abs(short.duration_sec - 5.0) < 1e-6,
            (sum(len(track.clips) for track in short.video_tracks), short.duration_sec),
        )
        check(
            "S1: la legge B e' rispettata (resta sopra il minimo)",
            coherence.audit_variety(block).get("ok") is True,
            coherence.audit_variety(block),
        )

        print("\n4. S2: materiale vario -> il piano non cambia")
        real_src = Path(r"D:\video_work\recipe_accept\src")
        if real_src.is_dir():
            videos = sorted(
                path
                for path in real_src.iterdir()
                if path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")
            )
            if len(videos) >= 2:
                rows = [
                    (f"r{index}", 0.0, 0.8, 0.8)
                    for index in range(len(videos))
                ]
                real_plan = _plan(
                    rows,
                    {f"r{index}": str(path) for index, path in enumerate(videos)},
                    len(videos) * 0.8,
                )
                real_frames = coherence.sample_frames(real_plan, tmp / "real-frames")
                if len(real_frames) >= 2:
                    before = float(real_plan.duration_sec)
                    unchanged, varied_block = coherence.shorten_plan(
                        real_plan, real_frames
                    )
                    check(
                        "S2: removed == 0 sul materiale vario",
                        varied_block.get("removed") == 0,
                        varied_block,
                    )
                    check(
                        "S2: la durata prima/dopo e' la stessa",
                        varied_block.get("duration_before") == round(before, 3)
                        and varied_block.get("duration_after") == round(before, 3)
                        and abs(unchanged.duration_sec - before) < 1e-9,
                        (before, varied_block.get("duration_after")),
                    )
                    check(
                        "S2: il piano torna identico (stesso oggetto)",
                        unchanged is real_plan,
                        type(unchanged).__name__,
                    )
                    check(
                        "S2: nessun fotogramma reale e' stato inventato",
                        len(real_frames) == len(videos),
                        (len(real_frames), len(videos)),
                    )
                else:
                    skip("S2", "sample_frames non ha letto il materiale reale")
            else:
                skip("S2", "meno di due video in D:\\video_work\\recipe_accept\\src")
        else:
            skip("S2", "D:\\video_work\\recipe_accept\\src assente")

        print("\n5. legge B: sotto il minimo dichiarabile non si consegna")
        tiny = _plan(
            [("same", index * 0.5, index * 0.5 + 0.25, 0.25) for index in range(12)],
            {"same": "same.mp4"},
            3.0,
        )
        tiny_frames = {index: _png(tmp / "tiny.png", 0) for index in range(12)}
        _tiny_short, tiny_block = coherence.shorten_plan(tiny, tiny_frames)
        tiny_audit = coherence.audit_variety(tiny_block)
        check(
            "legge B: il reel scende sotto 3.0 s",
            tiny_block.get("below_minimum") is True
            and tiny_block.get("duration_after", 9) < 3.0,
            (tiny_block.get("duration_after"), tiny_block.get("min_duration")),
        )
        check(
            "legge B: la violazione ha un codice dichiarato",
            "law_b_below_minimum"
            in {row.get("code") for row in tiny_audit.get("violations") or ()},
            [row.get("code") for row in tiny_audit.get("violations") or ()],
        )

        # ------------------------------------------------------------------ #
        # S4 -- le violazioni sono hard: reel non consegna e rinomina
        # ------------------------------------------------------------------ #
        print("\n6. S4: le due violazioni forzate fermano la consegna")
        law_b = coherence.audit_variety(tiny_block)
        laws = {"ok": False, "law_a": {"ok": True, "violations": []}, "law_b": law_b}
        checks = auto.taste_law_checks(laws)
        auto_report = {"ok": True, "delivered": True, "master": "x"}
        auto.apply_verification(auto_report, {"ok": False, "checks": checks})
        check(
            "S4/auto: la violazione di legge B ferma delivered",
            auto_report.get("delivered") is False
            and auto_report.get("outcome") == "failed",
            (auto_report.get("delivered"), auto_report.get("outcome")),
        )
        check(
            "S4/auto: il motivo e' in chiaro in failures",
            any(
                "taste_law_b_below_minimum" in str(item)
                for item in auto_report.get("failures") or ()
            ),
            auto_report.get("failures"),
        )

        # Il report che auto scrive quando la legge e' violata: contrasto non
        # firmato (nessun master consegnabile) e durata sotto la soglia.
        work = tmp / "reel-work"
        work.mkdir(parents=True, exist_ok=True)
        master = tmp / "master.mp4"
        master.write_bytes(b"not-a-real-master")
        below = {
            "fps": 30.0,
            "loudness_target": {"lufs": -14.0},
            "measured": {
                "duration_sec": 2.4,
                "requested_duration_sec": 3.0,
                "lufs": -14.0,
                "contrast": 9.0,
                "clips": 12,
                "sources_measured": 2,
                "assets_used": 2,
                "gates_wait_sec": 0.0,
            },
            "master": str(master),
            "laws": {"ok": False, "law_a": {"ok": True}, "law_b": law_b},
        }
        stages = reel.measure_stages(
            below, work, doctor_report={"verdict": "ok"}
        )
        delivered = all(stages[stage]["ok"] for stage in reel.STAGES)
        check(
            "S4/reel: lo stadio render firma fallito (2.4 s < 3.0 s)",
            stages["render"]["ok"] is False
            and "2.400" in stages["render"]["why"]
            and "3.000" in stages["render"]["why"],
            stages["render"]["why"],
        )
        check("S4/reel: delivered == False", delivered is False, delivered)
        renamed = reel._rename_for_failure(below)
        check(
            "S4/reel: il master diventa NON-PUBBLICABILE-*",
            renamed is not None
            and renamed.name.startswith("NON-PUBBLICABILE-")
            and renamed.is_file(),
            renamed.name if renamed else None,
        )

        # Legge A forzata: spec senza prova e nomi oltre il tetto.
        with_evidence = [
            {"id": name, "evidence": [{"kind": "measured"}]}
            for name in ("zoom", "warm", "shake")
        ]
        forged_table = {
            "c1": with_evidence,
            "c2": [{"id": "noir"}],
        }
        law_a = efx.audit(forged_table)
        checks = auto.taste_law_checks(
            {"ok": False, "law_a": law_a, "law_b": {"ok": True, "violations": []}}
        )
        forged_report = {"ok": True, "delivered": True, "master": "x"}
        auto.apply_verification(forged_report, {"ok": False, "checks": checks})
        check(
            "S4/auto: la violazione di legge A ferma delivered",
            forged_report.get("delivered") is False
            and any(
                "taste_effect_without_evidence" in str(item)
                for item in forged_report.get("failures") or ()
            ),
            forged_report.get("failures"),
        )

        master_a = tmp / "master-a.mp4"
        master_a.write_bytes(b"not-a-real-master")
        no_contrast = {
            "fps": 30.0,
            "loudness_target": {"lufs": -14.0},
            "measured": {
                "duration_sec": 10.0,
                "requested_duration_sec": 10.0,
                "lufs": -14.0,
                # una legge violata non firma il contrasto del consegnabile
                "contrast": None,
                "clips": 12,
                "sources_measured": 2,
                "assets_used": 2,
                "gates_wait_sec": 0.0,
            },
            "master": str(master_a),
            "laws": {"ok": False, "law_a": law_a, "law_b": {"ok": True}},
        }
        stages_a = reel.measure_stages(
            no_contrast, work, doctor_report={"verdict": "ok"}
        )
        delivered_a = all(stages_a[stage]["ok"] for stage in reel.STAGES)
        check(
            "S4/reel: lo stadio deliver firma fallito (contrasto non firmato)",
            stages_a["deliver"]["ok"] is False,
            stages_a["deliver"]["why"],
        )
        check("S4/reel legge A: delivered == False", delivered_a is False, delivered_a)
        renamed_a = reel._rename_for_failure(no_contrast)
        check(
            "S4/reel legge A: master NON-PUBBLICABILE-*",
            renamed_a is not None
            and renamed_a.name.startswith("NON-PUBBLICABILE-"),
            renamed_a.name if renamed_a else None,
        )

        print("\n7. il blocco variety e' nel report di auto")
        source = (ROOT / "scripts" / "vx" / "auto.py").read_text(encoding="utf-8")
        check(
            "auto scrive report['variety']",
            '"variety": variety_block' in source,
            "report['variety']",
        )
        check(
            "auto scrive report['laws'] e i check bloccanti",
            '"laws": laws_block' in source and "taste_law_checks(laws_block)" in source,
            "report['laws']",
        )
        check(
            "la soglia dei creativi e' letta dal contratto",
            'max_creative=contract.THRESHOLDS["max_creative_treatments"]' in source,
            "contract",
        )
        effects_json = tmp / "s3" / "effects.json"
        try:
            effects_payload = json.loads(effects_json.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - il dettaglio deve dire perche'
            effects_payload = {"error": f"{type(exc).__name__}: {exc}"}
        check(
            "effects.json dichiara cadute e trattamenti distinti",
            "dropped_no_evidence" in (effects_payload.get("summary") or {}),
            effects_payload.get("error") or sorted((effects_payload.get("summary") or {})),
        )

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
