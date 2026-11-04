"""vx.auto + --recipe: la ricetta smette di essere un file e diventa montaggio.

Senza rete e con un piano sintetico, si prova il filo intero:

    recipe.load_safe -> settings layer -> decide(beat_grid) -> testo (hook/CTA)
    -> effetti per clip (vx.efx) -> render graph

    python tests/test_auto_recipe.py

Le asserzioni sono sui numeri e sulle strutture, non sulle opinioni: un
parametro non applicato deve comparire in ``ignored_params`` con un motivo, e
``effects_per_shot: 0`` deve lasciare il grafo byte per byte quello di prima.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, decide, degrade, efx, qc, recipe, render, settings  # noqa: E402
from vx.edl_plan import ClipSegment, EDLPlan, SourceRef, Track  # noqa: E402
from vx.ffmpeg import ffmpeg  # noqa: E402

PASS = 0
FAIL = 0
CLIP_SECONDS = 0.6
CLIPS = 31
FPS = 30.0
FFMPEG = ffmpeg()
FFPROBE = str(Path(FFMPEG).with_name("ffprobe.exe"))


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def build_plan(clips: int = CLIPS, seconds: float = CLIP_SECONDS) -> EDLPlan:
    rows = []
    for index in range(clips):
        rows.append(
            ClipSegment(
                f"v0_{index}",
                "c01",
                index * seconds,
                (index + 1) * seconds,
                index * seconds,
                (index + 1) * seconds,
                0,
                "main_talking_head",
            )
        )
    return EDLPlan(
        fps=FPS,
        duration_sec=clips * seconds,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources={
            "c01": SourceRef(path="synthetic.mp4", sha256="a" * 64, duration=clips * seconds)
        },
        video_tracks=(Track(0, tuple(rows), "a_roll"),),
    )


def audio_plan(clips: int = 3, seconds: float = 0.6) -> EDLPlan:
    """Un piano con dialogo e musica, come quello che ``vx auto`` consegna."""
    video = []
    dialogue = []
    for index in range(clips):
        start = index * seconds
        video.append(
            ClipSegment(f"v0_{index}", "c01", 0.0, seconds, start, start + seconds, 0, "main_talking_head")
        )
        dialogue.append(
            ClipSegment(f"a0_{index}", "c01", 0.0, seconds, start, start + seconds, 0, "dialogue")
        )
    total = clips * seconds
    music = ClipSegment("a1_0", "c01", 0.0, total, 0.0, total, 0, "music", gain_db=-6.0)
    return EDLPlan(
        fps=FPS,
        duration_sec=total,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources={"c01": SourceRef(path="synthetic.mp4", sha256="a" * 64, duration=total)},
        video_tracks=(Track(0, tuple(video), "a_roll"),),
        audio_tracks=(Track(0, tuple(dialogue), "dialogue"), Track(1, (music,), "music")),
    )


def audio_stream_seconds(plan: EDLPlan, source: Path, work: Path) -> float:
    """Render only the audio graph to AAC/mp4 and read the stream's own duration.

    The graph is the one ``render_edl`` builds: the audio part is extracted so
    nothing has to encode 1080x1920 video just to measure the sound.
    """
    work.mkdir(parents=True, exist_ok=True)
    graph = render.build_edl_filtergraph(plan, {"c01": 0}, has_audio=True)
    starts = [index for index in (graph.find("[0:a]"), graph.find("[1:a]")) if index >= 0]
    start = min(starts) if starts else 0
    end = graph.index("[aout]") + len("[aout]")
    graph_path = work / "audio_only.txt"
    graph_path.write_text(graph[start:end], encoding="utf-8")
    out = work / "audio.mp4"
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-filter_complex_script",
            str(graph_path),
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    info = json.loads(
        subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=duration",
                "-of",
                "json",
                str(out),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    return float(info["streams"][0]["duration"])


def graph_sha(plan: EDLPlan) -> str:
    graph = render.build_edl_filtergraph(plan, {"c01": 0}, has_audio=False)
    return hashlib.sha256((" ".join(graph.split()) + "\n").encode()).hexdigest()


def make_av_source(path: Path, seconds: float = 2.0) -> Path:
    """Un sorgente sintetico con video e audio: serve al check del master."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x303030:size=270x480:rate=30:duration={seconds:g}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={seconds:g}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path
def args(**overrides: object) -> argparse.Namespace:
    base = {
        "recipe": None,
        "settings": None,
        "override": [],
        "no_feature": [],
        "prompt": "reel 115 BPM",
        "autonomous": True,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def section_for(name: str, workdir: Path) -> dict:
    return auto.load_recipe(args(recipe=name), workdir)


def write_json(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vx_auto_recipe_"))
    user_dir = tmp / "user_recipes"
    user_dir.mkdir()
    os.environ["VX_RECIPE_DIR"] = str(user_dir)
    settings.apply(settings.build())
    degrade.clear()
    try:
        work = tmp / "work"
        work.mkdir()

        print("\n-- ricetta assente / rotta: degrada e il lavoro prosegue")
        missing = section_for("non-esiste", work)
        check("ricetta inesistente: applied False", missing.get("applied") is False, missing.get("applied"))
        check("ricetta inesistente: problems nel report", bool(missing.get("problems")), missing.get("problems"))
        check("ricetta inesistente: degrado annotato", "recipe" in degrade.names(), degrade.names())
        check(
            "ricetta inesistente: nessun parametro inventato",
            auto._recipe_beat_args(missing) == (None, None)
            and auto._recipe_text_brief(missing) == {},
            auto._recipe_beat_args(missing),
        )
        zero_effects, same_plan = auto._recipe_effects(missing, build_plan(), work)
        check(
            "ricetta inesistente: effetti non applicati, piano intatto",
            zero_effects.get("applied") is False and not hasattr(same_plan, "effects"),
            zero_effects.get("reason"),
        )

        (user_dir / "rotten.json").write_text("{not json", encoding="utf-8")
        rotten = section_for("rotten", work)
        check("ricetta rotta: applied False", rotten.get("applied") is False, rotten.get("applied"))
        check(
            "ricetta rotta: il problema e' nel report",
            bool(rotten.get("problems")) and "rotten" in " ".join(rotten["problems"]),
            rotten.get("problems"),
        )

        print("\n-- ricetta valida: settings nel cascade e parametri dichiarati")
        beat = section_for("beat-cut", work)
        check("beat-cut caricata", beat.get("applied") is True and beat.get("name") == "beat-cut", beat.get("name"))
        check(
            "params espliciti letti dalle sorgenti",
            set(beat.get("explicit") or ()) == set(recipe.PARAMS),
            beat.get("explicit"),
        )
        check(
            "settings della ricetta applicati (effect_budget 4)",
            beat.get("settings_apply", {}).get("applied") is True
            and int(settings.get("render.effect_budget")) == 4,
            settings.get("render.effect_budget"),
        )
        check(
            "layer scritto dove settings.build lo legge",
            (work / auto.RECIPE_SETTINGS_NAME).is_file(),
            work / auto.RECIPE_SETTINGS_NAME,
        )

        print("\n-- beat_grid arriva a decide")
        payload, note = auto._decide_payload(args(prompt="reel tatuaggio 115 BPM"), [], beat, False)
        block = payload.get("beat_grid") or {}
        check("payload con griglia accesa", block.get("mode") == "on" and not note, block)
        check("BPM del prompt nella griglia", float(block.get("bpm") or 0) == 115.0, block.get("bpm"))
        auto._declare_beat_grid(beat, payload, note)
        check(
            "beat_grid dichiarato applicato",
            any(row["name"] == "beat_grid" for row in beat["applied_params"]),
            beat["applied_params"],
        )

        print("\n-- loudness: target vecchio e nuovo")
        loud = auto._recipe_loudness(beat, -14.0, -1.0)
        check(
            "loudness -14 -> -10.5, true peak invariato",
            loud["old_lufs"] == -14.0 and loud["new_lufs"] == -10.5 and loud["true_peak_db"] == -1.0,
            loud,
        )

        print("\n-- testo: la ricetta riempie i vuoti, il prompt vince")
        plan = build_plan(10, 0.6)
        text_brief = auto._recipe_text_brief(beat)
        check(
            "font/corpo/hook/cta dal file",
            text_brief.get("font") == "Antonio"
            and text_brief.get("size") == 106
            and text_brief.get("hook_at") == 0.1
            and text_brief.get("cta") is True,
            text_brief,
        )
        section, _layer = auto._run_typography(
            work / "t1", plan, "hook: CIAO A TUTTI", {"family": "", "fonts_dir": ""}, None, None,
            recipe_brief=text_brief,
        )
        hook = next((item for item in section.get("texts") or () if item["kind"] == "hook"), None)
        check(
            "hook a 0.10s dalla ricetta, corpo 106 nel PixelStyle",
            hook is not None and hook["at"] == 0.1 and section["sources"].get("size") == "recipe",
            hook,
        )
        ass = (work / "t1" / "hook.ass").read_text(encoding="utf-8")
        check("Fontsize 106 scritto nell'ASS", "Antonio,106" in ass or ",106," in ass, ass.splitlines()[3][:60])

        section2, _ = auto._run_typography(
            work / "t2", plan, "hook: CIAO hook at 2.5s", {"family": "", "fonts_dir": ""}, None, None,
            recipe_brief=text_brief,
        )
        hook2 = next((item for item in section2.get("texts") or () if item["kind"] == "hook"), None)
        check(
            "il prompt vince sul secondo dell'hook",
            hook2 is not None and hook2["at"] == 2.5 and section2["sources"].get("hook_at") == "prompt",
            (hook2 or {}).get("at"),
        )

        bare, _ = auto._run_typography(
            work / "t3", plan, "reel 115 BPM", {"family": "", "fonts_dir": ""}, None, None,
            recipe_brief=text_brief,
        )
        check(
            "cta senza frase: nessun ASS inventato",
            bare.get("applied") is False and "frase" in str(bare.get("reason")),
            bare.get("reason"),
        )
        auto._declare_text_params(beat, bare, plan)
        ignored = {row["name"]: row["reason"] for row in beat["ignored_params"]}
        check(
            "cta e hook dichiarati ignorati col motivo",
            "cta" in ignored and "hook_at" in ignored and ignored["cta"],
            ignored,
        )

        print("\n-- effects_per_shot: zero e' zero, uno e' uno per clip")
        base = build_plan()
        base_sha = graph_sha(base)
        zero_section, zero_plan = auto._recipe_effects(
            {"applied": True, "explicit": ["effects_per_shot"], "params": {"effects_per_shot": 0.0}},
            base,
            work / "fx0",
        )
        check(
            "effects_per_shot 0: nessuna spec, nessuna tabella",
            zero_section.get("specs") == 0 and not hasattr(zero_plan, "effects"),
            zero_section,
        )
        check(
            "effects_per_shot 0: grafo byte-identico (sha)",
            graph_sha(zero_plan) == base_sha,
            base_sha[:16],
        )

        one_section, one_plan = auto._recipe_effects(
            {"applied": True, "explicit": ["effects_per_shot"], "params": {"effects_per_shot": 1.0}},
            base,
            work / "fx1",
        )
        table = getattr(one_plan, "effects", {})
        check(
            "effects_per_shot 1: una spec per clip",
            one_section.get("specs") == CLIPS
            and len(table) == CLIPS
            and all(len(specs) == 1 for specs in table.values()),
            (one_section.get("specs"), len(table)),
        )
        check(
            "il grafo cambia quando gli effetti entrano",
            graph_sha(one_plan) != base_sha,
            graph_sha(one_plan)[:16],
        )

        from vx.effects import compose  # noqa: PLC0415

        specs = [specs[0] for specs in table.values()]
        check(
            "ogni spec passa expand e validate",
            all(not compose.validate(compose.expand(spec)) for spec in specs),
            len(specs),
        )
        check(
            "solo nomi esistenti della libreria",
            all(spec.get("id") in compose.HISTORICAL_NAMES for spec in specs),
            sorted({spec.get("id") for spec in specs}),
        )
        budget = float(settings.get("render.effect_budget"))
        check(
            "nessuna spec sopra il budget di render",
            all(compose.cost(spec) <= budget for spec in specs),
            (budget, max(compose.cost(spec) for spec in specs)),
        )

        again = efx.generate(build_plan(), 1.0, salt="beat-cut")
        first = efx.generate(build_plan(), 1.0, salt="beat-cut")
        check(
            "deterministico: due run, stesse spec",
            first["table"] == again["table"] and first["names"] == again["names"],
            first["names"][:4],
        )

        print("\n-- shot_max: dichiarato ignorato, con i numeri del piano")
        shot_section: dict = {
            "applied": True,
            "explicit": ["shot_max"],
            "params": {"shot_max": 0.8},
            "applied_params": [],
            "ignored_params": [],
        }
        auto._declare_text_params(shot_section, {"applied": False, "reason": "n/a"}, base)
        shot_rows = {row["name"]: row["reason"] for row in shot_section["ignored_params"]}
        check(
            "shot_max in ignored_params, mai in applied_params",
            "shot_max" in shot_rows and not shot_section["applied_params"],
            shot_rows.get("shot_max", "")[:70],
        )
        check(
            "shot_max: motivo con la misura vera del piano",
            "0.60s" in shot_rows.get("shot_max", ""),
            shot_rows.get("shot_max", ""),
        )
        reason = auto._shot_max_reason(shot_section, base)
        check(
            "shot_max: il motivo dice che il piano non ha un tetto",
            "non espone un tetto" in reason,
            reason[:70],
        )

        print("\n-- il master: l'audio dura quanto il container")
        source = make_av_source(tmp / "av.mp4", 2.0)
        audio_only = audio_plan(3, 0.6)
        measured = audio_stream_seconds(audio_only, source, work / "master_audio")
        tolerance = float(qc.AUDIO_COVERAGE_TOLERANCE)
        check(
            "audio del master >= container - tolleranza (gate QC)",
            measured >= float(audio_only.duration_sec) - tolerance,
            f"audio={measured:.3f}s video={audio_only.duration_sec:.3f}s "
            f"shortfall={audio_only.duration_sec - measured:.3f}s",
        )

    finally:
        os.environ.pop("VX_RECIPE_DIR", None)
        settings.apply(settings.build())
        degrade.clear()

    print(f"\n{PASS} pass, {FAIL} fail")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
