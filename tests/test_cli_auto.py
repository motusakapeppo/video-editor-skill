"""vx auto: prompt + cartella -> triage -> EDL -> render -> QC -> consegna.

The e2e pool is built at the delivery canvas (1080x1920) on purpose. The triage
gate refuses any source that would need more than 1.5x upscale to reach the
payload canvas, so a 320x180 fixture is rejected as RESOLUTION_TOO_LOW before
it can ever become an a_roll: the pipeline would stop at "no a_roll" and the
proof would be about the gate, not about the chain. Building the pool at the
canvas exercises the gate at its real threshold instead of bypassing it with
an override.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import contract, probe  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"

W, H = 1080, 1920
VAGUE = "un reel 9:16 120bpm font Inter taglio ogni battuta 3s vintage"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:56} {reason}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _make(path: Path, args: list) -> bool:
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args, str(path)],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file() and path.stat().st_size > 0


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


def _auto(prompt: str, assets: Path, out: Path, work: Path, *extra: str):
    """``--no-subs`` keeps the e2e hermetic: no transcript, no whisper, no model."""
    return _cli(
        [
            "auto",
            "--prompt",
            prompt,
            "--assets",
            str(assets),
            "--out",
            str(out),
            "--workdir",
            str(work),
            "--no-subs",
            *extra,
            "--json",
        ]
    )


def _normalised_plan(path: Path) -> dict:
    """The plan minus the workdir prefix: staged paths are the only per-run value."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for ref in (data.get("sources") or {}).values():
        ref["path"] = Path(str(ref.get("path", ""))).name
    return data


def _build_pool(pool: Path) -> bool:
    clip = pool / "clip.mp4"
    ok_clip = _make(
        clip,
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={W}x{H}:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
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
        ],
    )
    ok_still = _make(
        pool / "still.png",
        ["-f", "lavfi", "-i", f"testsrc2=size={W}x{H}", "-frames:v", "1"],
    )
    ok_music = _make(
        pool / "music.m4a",
        ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"],
    )
    ok_music2 = _make(
        pool / "music2.m4a",
        ["-f", "lavfi", "-i", "sine=frequency=660:duration=1", "-c:a", "aac"],
    )
    if not (ok_clip and ok_still and ok_music and ok_music2):
        return False
    shutil.copy2(clip, pool / "copy.mp4")
    (pool / "broken.mp4").write_text("this is not a video\n", encoding="utf-8")
    return True


def main() -> int:
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="vx-auto-") as raw:
        tmp = Path(raw)
        pool = tmp / "pool"
        pool.mkdir()
        if not _build_pool(pool):
            skip("fixture", "ffmpeg non disponibile")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        # -- unit: usage errors ------------------------------------------- #
        missing = _cli(
            ["auto", "--prompt", "un reel", "--out", str(tmp / "x.mp4"), "--json"]
        )
        check("--assets mancante rc 2", missing.returncode == 2, missing.returncode)

        ghost = _cli(
            [
                "auto",
                "--prompt",
                "un reel",
                "--assets",
                str(tmp / "nope"),
                "--out",
                str(tmp / "x.mp4"),
                "--json",
            ]
        )
        check("cartella inesistente rc 2", ghost.returncode == 2, ghost.returncode)
        check(
            "cartella inesistente nominata",
            "nope" in (ghost.stderr + ghost.stdout),
            (ghost.stderr + ghost.stdout)[-160:],
        )

        stills = tmp / "stills"
        stills.mkdir()
        shutil.copy2(pool / "still.png", stills / "still.png")
        no_fps = _auto(
            "un reel", stills, tmp / "stills.mp4", tmp / "stills_w", "--autonomous"
        )
        check(
            "solo immagini senza --fps rc 2", no_fps.returncode == 2, no_fps.returncode
        )
        check(
            "messaggio fps",
            "fps" in (no_fps.stderr + no_fps.stdout).lower(),
            (no_fps.stderr + no_fps.stdout)[-160:],
        )

        small = tmp / "small"
        small.mkdir()
        _make(
            small / "clip.mp4",
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=25:duration=2",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=2",
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
            ],
        )
        low_res = _auto(
            "un reel",
            small,
            tmp / "small.mp4",
            tmp / "small_w",
            "--autonomous",
            "--fps",
            "25",
        )
        check(
            "pool 320x180 rifiutato dal gate di risoluzione",
            low_res.returncode == 2 and "a_roll" in (low_res.stderr + low_res.stdout),
            (low_res.stderr + low_res.stdout)[-160:],
        )

        strict = _auto(VAGUE, pool, tmp / "strict.mp4", tmp / "strict_w")
        check(
            "prompt vago senza --autonomous rc 2",
            strict.returncode == 2,
            strict.returncode,
        )
        check(
            "strict nomina vintage",
            "vintage" in (strict.stderr + strict.stdout).lower(),
            (strict.stderr + strict.stdout)[-160:],
        )

        mini = tmp / "mini"
        mini.mkdir()
        shutil.copy2(pool / "clip.mp4", mini / "clip.mp4")
        autonomous = _auto(
            VAGUE, mini, tmp / "mini.mp4", tmp / "mini_w", "--autonomous"
        )
        check(
            "prompt vago con --autonomous rc 0",
            autonomous.returncode == 0,
            autonomous.stderr[-300:],
        )

        # The lock must be released on success and on failure: re-running the
        # same workdir would say "workdir busy" if it leaked.
        again = _auto(VAGUE, mini, tmp / "mini.mp4", tmp / "mini_w", "--autonomous")
        check(
            "lock rilasciato dopo il successo",
            again.returncode == 0,
            again.stderr[-200:],
        )
        stills_again = _auto(
            "un reel", stills, tmp / "stills.mp4", tmp / "stills_w", "--autonomous"
        )
        check(
            "lock rilasciato dopo l'errore",
            stills_again.returncode == 2
            and "busy" not in (stills_again.stderr + stills_again.stdout).lower(),
            stills_again.returncode,
        )

        # -- e2e: the proof ----------------------------------------------- #
        before = {p.name: _sha256(p) for p in pool.iterdir() if p.is_file()}
        master = tmp / "master.mp4"
        work = tmp / "work"
        run = _auto("un reel veloce", pool, master, work, "--autonomous")
        check("e2e rc 0", run.returncode == 0, run.stderr[-400:])

        try:
            report = json.loads(run.stdout)
        except Exception as exc:  # noqa: BLE001
            check("stdout e' solo il report", False, f"{type(exc).__name__}: {exc}")
            report = {}
        else:
            check(
                "stdout e' solo il report",
                isinstance(report, dict) and report.get("ok") is True,
                sorted(report)[:6],
            )

        check(
            "master esiste e non e' vuoto",
            master.is_file() and master.stat().st_size > 0,
            master.stat().st_size if master.is_file() else "assente",
        )
        info = probe.probe(master) if master.is_file() else None
        check("master ha una traccia audio", bool(info and info.has_audio), info)
        check(
            "master durata ~ timeline",
            bool(info)
            and abs(float(info.duration) - float(report.get("timeline_sec") or 0.0))
            <= 0.3,
            (info.duration if info else None, report.get("timeline_sec")),
        )

        report_path = work / "auto_report.json"
        check("auto_report.json esiste", report_path.is_file(), report_path)
        on_disk = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if report_path.is_file()
            else {}
        )
        check("auto_report ok", on_disk.get("ok") is True, on_disk.get("ok"))
        check("report su disco == stdout", on_disk == report, "identici")

        # La composizione e' un blocco del report, non una promessa: schema,
        # proposta, declini e misure sui due piani devono esserci, e le cifre
        # devono venire dai piani (shots_after >= shots_before, survived booleano).
        composition = report.get("composition") or {}
        check(
            "report.composition: schema e blocchi",
            composition.get("schema") == "vx.composition/1"
            and isinstance(composition.get("proposed"), dict)
            and isinstance(composition.get("declined"), list)
            and isinstance(composition.get("applied"), dict),
            sorted(composition),
        )
        applied = composition.get("applied") or {}
        check(
            "composition: le cifre vengono dai due piani",
            isinstance(applied.get("shots_before"), int)
            and isinstance(applied.get("shots_after"), int)
            and applied["shots_after"] >= applied["shots_before"]
            and isinstance(applied.get("survived"), bool),
            {key: applied.get(key) for key in ("shots_before", "shots_after", "survived")},
        )
        check(
            "composition: la sopravvivenza e' ricontrollata sul piano finale",
            isinstance((composition.get("survival") or {}).get("preserved"), bool)
            and isinstance((composition.get("survival") or {}).get("why"), str),
            composition.get("survival"),
        )
        sections_block = report.get("sections") or {}
        hook_row = next(
            (
                row
                for row in sections_block.get("sections") or []
                if isinstance(row, dict) and row.get("id") == "hook"
            ),
            {},
        )
        check(
            "sections: sei sezioni, gancio compilato con un numero",
            sections_block.get("total") == 6
            and hook_row.get("state") == "filled"
            and any(char.isdigit() for char in str(hook_row.get("why") or "")),
            (sections_block.get("filled"), hook_row.get("why")),
        )

        # Il profilo e' parte del referto: senza i tempi per stadio non si sa
        # dove va il muro, e un'ottimizzazione diventa un'opinione.
        timings = on_disk.get("timings") or {}
        check(
            "report.timings: ogni stadio e il totale",
            isinstance(timings.get("total"), (int, float))
            and timings["total"] > 0
            and {"triage", "render", "qc", "deliverables"} <= set(timings),
            sorted(timings),
        )

        bridge_path = work / "bridge_report.json"
        bridge_block = report.get("bridge") or {}
        check("bridge_report.json esiste", bridge_path.is_file(), bridge_path)
        check(
            "report.bridge dichiara tagli, gain e referto",
            bridge_block.get("enabled") is True
            and isinstance(bridge_block.get("cuts"), int)
            and isinstance(bridge_block.get("gained_clips"), int)
            and isinstance(bridge_block.get("not_applied"), int)
            and Path(str(bridge_block.get("report") or "")).is_file(),
            bridge_block,
        )

        audio_block = report.get("audio") or {}
        check(
            "F1-T03: snr_db e music_ratio nel referto, con la soglia usata",
            "snr_db" in audio_block
            and "music_ratio" in audio_block
            and audio_block.get("snr_threshold_db")
            == contract.THRESHOLDS["noise_snr_low"]
            and audio_block.get("music_ratio_threshold")
            == contract.THRESHOLDS["triage_music_ratio_low"]
            and (
                audio_block.get("snr_db") is None
                or isinstance(audio_block.get("snr_db"), (int, float))
            )
            and (
                audio_block.get("music_ratio") is None
                or isinstance(audio_block.get("music_ratio"), (int, float))
            ),
            audio_block,
        )

        beds = report.get("music") or []
        check(
            "F1-T04: due letti musicali, ognuno con la sua soglia di ducking",
            len(beds) == 2
            and [bed.get("role") for bed in beds] == ["music_a", "music_b"]
            and len({bed.get("ducking_threshold") for bed in beds}) == 2
            and all(bed.get("sha256") for bed in beds),
            [(bed.get("role"), bed.get("ducking_threshold")) for bed in beds],
        )

        rejected = report.get("rejected") or []
        reasons = {item.get("rejection_reason") for item in rejected}
        check("almeno 2 scarti", len(rejected) >= 2, len(rejected))
        check("scarto DUPLICATE_ASSET", "DUPLICATE_ASSET" in reasons, sorted(reasons))
        check("scarto UNREADABLE", "UNREADABLE" in reasons, sorted(reasons))

        seen = [
            item.get("asset_id") for item in (report.get("selected") or []) + rejected
        ]
        check(
            "ogni asset esattamente una volta",
            sorted(seen) == sorted(before),
            (sorted(seen), sorted(before)),
        )

        qc_block = report.get("qc") or {}
        check("qc.ok", qc_block.get("ok") is True, qc_block.get("failures"))
        measured = (qc_block.get("measured") or {}).get("i")
        check(
            "qc LUFS entro 1.5",
            measured is not None and abs(float(measured) - (-14.0)) <= 1.5,
            measured,
        )

        items = report.get("deliverables") or []
        check("4 deliverable", len(items) == 4, len(items))
        by_kind = {item.get("kind"): item for item in items}
        for kind in ("master", "thumbnail", "contact_sheet"):
            item = by_kind.get(kind) or {}
            target = Path(str(item.get("path") or ""))
            check(
                f"{kind} esiste",
                bool(item.get("available")) and target.is_file(),
                item.get("path"),
            )
        subs = by_kind.get("subtitles") or {}
        check(
            "sottotitoli dichiarati non disponibili",
            subs.get("available") is False
            and subs.get("reason") in ("no speech", "sottotitoli non richiesti"),
            subs,
        )

        check(
            "camera_exemptions e' una lista",
            isinstance(report.get("camera_exemptions"), list),
            report.get("camera_exemptions"),
        )

        notes = report.get("plan_notes") or []
        check(
            "note del piano persistite nel referto",
            isinstance(notes, list) and any("payload v1.5.0" in n for n in notes),
            notes,
        )
        check(
            "musica corta dichiarata nel referto",
            any("più corta della timeline" in n for n in notes),
            notes,
        )

        after = {p.name: _sha256(p) for p in pool.iterdir() if p.is_file()}
        check("sources_unchanged", report.get("sources_unchanged") is True)
        check("sha input invariati", before == after, sorted(before))
        # Solo i file scelti entrano nel master (e nel guardiano): gli asset
        # scoperti e scartati non pagano 100 MB di lettura per un numero che
        # nessun cancello consulta.
        reported = report.get("source_sha") or {}
        check(
            "source_sha == pre-run per i soli file scelti",
            reported == {k: v for k, v in before.items() if k in reported},
            sorted(reported),
        )
        check(
            "un asset scartato non e' hashato",
            set(reported) <= set(before),
            (sorted(set(before) - set(reported))),
        )

        # -- determinism: same command, fresh workdir --------------------- #
        work2 = tmp / "work2"
        run2 = _auto("un reel veloce", pool, tmp / "master2.mp4", work2, "--autonomous")
        check("seconda corsa rc 0", run2.returncode == 0, run2.stderr[-300:])
        plan1 = work / "plan.json"
        plan2 = work2 / "plan.json"
        check(
            "plan.json scritto in entrambe le workdir",
            plan1.is_file() and plan2.is_file(),
            (plan1.is_file(), plan2.is_file()),
        )
        if plan1.is_file() and plan2.is_file():
            check(
                "plan deterministico",
                _normalised_plan(plan1) == _normalised_plan(plan2),
                "to_dict() identico a meno del prefisso workdir",
            )

    elapsed = time.time() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
