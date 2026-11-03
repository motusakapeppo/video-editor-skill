"""F2-T02 contract: every entity validates, every refusal has a stable code.

No ffmpeg here: the compiler is asked for plans and refusals, never to render.
The canonical-JSON round trip is byte comparison, not dict equality.

    python tests/test_edl3_contract.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402

PASS = 0
FAIL = 0
SELF = str(Path(__file__).resolve())


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


# ------------------------------------------------------------------ helpers


def track(points, interp: str = "linear") -> edl3.KeyframeTrack:
    return edl3.KeyframeTrack(
        [edl3.Keyframe(t=t, value=v, interp=interp) for t, v in points]
    )


def clip(**fields) -> edl3.Clip3:
    base = {"id": "c1", "source": SELF, "src_in": 0.0, "src_out": 2.0}
    base.update(fields)
    return edl3.Clip3(**base)


def timeline(clips=None, **fields) -> edl3.Timeline3:
    base = {
        "fps": 12,
        "size": (320, 568),
        "clips": [clip()] if clips is None else clips,
    }
    base.update(fields)
    return edl3.Timeline3(**base)


def codes(document: edl3.Timeline3) -> list:
    return [problem["code"] for problem in document.validate()]


def canonical(document: edl3.Timeline3) -> str:
    return json.dumps(
        document.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def compile_code(document: edl3.Timeline3, **kwargs) -> str:
    try:
        edl3_render.compile_timeline(document, **kwargs)
    except edl3_render.CompileError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 - wrong error type is a failure
        return f"__WRONG_TYPE__{type(exc).__name__}: {exc}"
    return ""


# ------------------------------------------------------------- full document


def full_document() -> edl3.Timeline3:
    inner = edl3.Timeline3(
        fps=24,
        size=(160, 284),
        clips=[
            edl3.Clip3(
                id="n1",
                source="inner.mp4",
                src_in=0.0,
                src_out=1.0,
                effects=[edl3.Effect("blur", {"sigma": 2.0})],
            )
        ],
    )
    return edl3.Timeline3(
        fps=12,
        size=(320, 568),
        clips=[
            edl3.Clip3(
                id="c1",
                source="a.mp4",
                src_in=0.0,
                src_out=1.0,
                dst_in=0.0,
                dst_out=1.0,
                effects=[
                    edl3.Effect(
                        "transform",
                        {"scale": 1.0, "opacity": 0.8},
                        {"opacity": track([(0.0, 1.0), (1.0, 0.4)], "bezier")},
                    ),
                    edl3.Effect(
                        "crop",
                        {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
                        {"x": track([(0.0, 0.1), (1.0, 0.2)], "hold")},
                    ),
                    edl3.Effect(
                        "color",
                        {"brightness": 0.1, "contrast": 1.1, "saturation": 0.9},
                        {"brightness": track([(0.0, -0.2), (1.0, 0.2)])},
                    ),
                    edl3.Effect("blur", {"sigma": 3.0}),
                    edl3.Effect("chroma_key", {"similarity": 0.2, "blend": 0.1}),
                ],
                transition_in=edl3.Transition("fade_in", 0.4),
                transition_out=edl3.Transition("fade_out", 0.4),
            ),
            edl3.Clip3(
                id="c2",
                sequence="s1",
                src_in=0.0,
                src_out=1.0,
                dst_in=1.0,
                dst_out=2.0,
                transition_in=edl3.Transition("fade_in", 0.5),
            ),
        ],
        sequences={"s1": edl3.NestedSequence("s1", inner)},
        adjustments=[
            edl3.AdjustmentLayer(
                track=1, effects=[edl3.Effect("color", {"saturation": 0.5})]
            )
        ],
        text_layers=[
            edl3.TextLayer(
                text="title",
                style=edl3.TextStyle(
                    family="Antonio",
                    size=48.0,
                    color="#ffcc00",
                    stroke=2.0,
                    position=(0.5, 0.8),
                    align="center",
                ),
                time_in=0.0,
                time_out=1.5,
                keyframes={
                    "opacity": track([(0.0, 0.0), (0.5, 1.0)]),
                    "position_x": track([(0.0, 0.5), (1.5, 0.2)], "bezier"),
                    "position_y": track([(0.0, 0.8), (1.5, 0.5)]),
                },
            )
        ],
        captions=[
            edl3.CaptionTrack(
                [
                    edl3.CaptionCue("first", 0.0, 0.9),
                    edl3.CaptionCue("second", 0.9, 1.8),
                ]
            )
        ],
        audio=[
            edl3.AudioTrack3(
                role="music",
                clips=[
                    edl3.AudioClip3(
                        source="bed.m4a",
                        src_in=0.0,
                        src_out=2.0,
                        dst_in=0.0,
                        dst_out=2.0,
                        gain_db=-6.0,
                    )
                ],
            )
        ],
        ducking=edl3.Ducking3(
            sidechain_role="dialogue",
            target_role="music",
            threshold=0.05,
            ratio=8.0,
            attack_ms=20.0,
            release_ms=250.0,
            makeup_db=0.0,
        ),
    )


def check_registry() -> None:
    print("\n-- registry")
    required = {"transform", "crop", "color", "blur", "chroma_key"}
    check(
        "the registry declares every required kind",
        required <= set(edl3.EFFECT_REGISTRY),
        sorted(edl3.EFFECT_REGISTRY),
    )
    problems = []
    for kind, spec in edl3.EFFECT_REGISTRY.items():
        if not spec.params:
            problems.append(f"{kind}: no params")
        if spec.backend not in edl3.BACKENDS:
            problems.append(f"{kind}: backend {spec.backend!r} not declared")
        if not spec.notes.strip():
            problems.append(f"{kind}: no notes")
        if spec.animatable != bool(spec.animated_params):
            problems.append(f"{kind}: animatable/params disagree")
        if not set(spec.animated_params) <= set(spec.params):
            problems.append(f"{kind}: animated param not in params")
    check(
        "every entry declares params, animatable, backend, notes coherently",
        problems == [],
        problems,
    )
    transform = edl3.EFFECT_REGISTRY["transform"]
    check(
        "transform animates opacity and NOT scale (D012 #2)",
        "opacity" in transform.animated_params
        and "scale" not in transform.animated_params,
        transform.animated_params,
    )
    check(
        "blur is declared static only",
        edl3.EFFECT_REGISTRY["blur"].animatable is False,
        edl3.EFFECT_REGISTRY["blur"].animated_params,
    )
    check(
        "chroma_key is a declared placeholder with the frames backend",
        edl3.EFFECT_REGISTRY["chroma_key"].backend == "frames"
        and not edl3.EFFECT_REGISTRY["chroma_key"].animatable,
        edl3.EFFECT_REGISTRY["chroma_key"].notes[:40],
    )
    renderable = set(edl3.RENDERABLE_EFFECTS)
    declarative = {
        kind
        for kind, spec in edl3.EFFECT_REGISTRY.items()
        if spec.backend == edl3.BACKEND_FILTER
    }
    check(
        "the renderable set is declared, excludes the placeholder and carries "
        "every declarative filter entry",
        {"transform", "crop", "color", "blur"} <= renderable
        and "chroma_key" not in renderable
        and renderable == {"transform", "crop", "color", "blur"} | declarative,
        sorted(edl3.RENDERABLE_EFFECTS),
    )
    check(
        "transition kinds are fade_in / fade_out / crossfade",
        set(edl3.TRANSITION_KINDS) == {"fade_in", "fade_out", "crossfade"},
        sorted(edl3.TRANSITION_KINDS),
    )


def check_entities(document: edl3.Timeline3) -> None:
    print("\n-- validate and round trip")
    check(
        "the full document validates cleanly",
        document.validate() == [],
        document.validate(),
    )
    check(
        "the full document carries every entity",
        bool(document.sequences)
        and bool(document.adjustments)
        and bool(document.text_layers)
        and bool(document.captions)
        and bool(document.audio)
        and document.ducking is not None,
    )

    entities = [
        edl3.Keyframe(0.5, 2.0, "bezier"),
        edl3.Effect(
            "color", {"brightness": 0.2}, {"brightness": track([(0, 0), (1, 1)])}
        ),
        edl3.Transition("fade_out", 0.5),
        document.clips[0],
        edl3.AdjustmentLayer(track=2, effects=[edl3.Effect("blur", {"sigma": 1.0})]),
        edl3.TextStyle(family="Antonio", size=40.0, color="#00ff00", stroke=1.0),
        document.text_layers[0],
        edl3.CaptionCue("hello", 0.0, 1.0),
        document.captions[0],
        edl3.AudioClip3("m.m4a", 0.0, 1.0, 0.0, 1.0, -3.0),
        document.audio[0],
        edl3.Ducking3(ratio=4.0),
        document.sequences["s1"],
        document,
    ]
    failures = []
    for entity in entities:
        name = type(entity).__name__
        kind = getattr(entity, "kind", "")
        try:
            reborn = type(entity).from_dict(entity.to_dict())
        except Exception as exc:  # noqa: BLE001 - a raise is a failure, reported
            failures.append(f"{name}/{kind}: raised {type(exc).__name__}: {exc}")
            continue
        if reborn.to_dict() != entity.to_dict():
            failures.append(f"{name}/{kind}: to_dict differs after a round trip")
    check(
        "every entity round-trips through to_dict/from_dict", failures == [], failures
    )
    keys = track([(0.0, 1.0), (1.0, 0.0)])
    check(
        "a keyframe track round-trips through from_list",
        edl3.KeyframeTrack.from_list(keys.to_dict()).to_dict() == keys.to_dict(),
        keys.to_dict(),
    )

    raw = canonical(document)
    reloaded = edl3.Timeline3.from_dict(json.loads(raw))
    check(
        "canonical JSON round trip is byte-identical",
        canonical(reloaded) == raw,
        f"{len(raw)} bytes",
    )
    check(
        "the round trip keeps every keyframe",
        len(reloaded.text_layers[0].keyframes["position_x"].keys) == 2
        and len(reloaded.clips[0].effects[0].keyframes["opacity"].keys) == 2,
    )
    check(
        "sampling survives the round trip",
        abs(
            reloaded.clips[0].effects[0].sample("opacity", 0.5)
            - document.clips[0].effects[0].sample("opacity", 0.5)
        )
        < 1e-9,
    )


# ------------------------------------------------------------------- codes


def code_cases() -> list:
    inner_ok = edl3.Timeline3(
        fps=12,
        size=(320, 568),
        clips=[edl3.Clip3(id="n", source=SELF, src_in=0, src_out=1)],
    )
    nested_ok = {
        "s1": edl3.NestedSequence(
            "s1",
            edl3.Timeline3(
                fps=12,
                size=(320, 568),
                clips=[edl3.Clip3(id="n", source=SELF, src_in=0, src_out=1)],
            ),
        )
    }
    deep_3 = edl3.Timeline3(
        fps=12,
        size=(320, 568),
        clips=[edl3.Clip3(id="a", sequence="s3", src_in=0, src_out=1)],
        sequences={
            "s3": edl3.NestedSequence(
                "s3",
                edl3.Timeline3(
                    fps=12,
                    size=(320, 568),
                    clips=[edl3.Clip3(id="b", sequence="s4", src_in=0, src_out=1)],
                    sequences={
                        "s4": edl3.NestedSequence(
                            "s4",
                            edl3.Timeline3(
                                fps=12,
                                size=(320, 568),
                                clips=[
                                    edl3.Clip3(
                                        id="c", sequence="s5", src_in=0, src_out=1
                                    )
                                ],
                                sequences={"s5": edl3.NestedSequence("s5", inner_ok)},
                            ),
                        )
                    },
                ),
            )
        },
    )
    return [
        ("SCHEMA_VERSION", timeline(version="vx.edl3/0")),
        ("FPS_INVALID", timeline(fps=0)),
        ("SIZE_INVALID", timeline(size=(0, 0))),
        ("TIMELINE_EMPTY", timeline(clips=[])),
        (
            "DUPLICATE_CLIP_ID",
            timeline(clips=[clip(id="c"), clip(id="c", src_in=1.0, src_out=2.0)]),
        ),
        ("CLIP_RANGE", timeline(clips=[clip(src_in=1.0, src_out=1.0)])),
        (
            "CLIP_SHAPE",
            timeline(
                clips=[clip(sequence="s1")],
                sequences=nested_ok,
            ),
        ),
        (
            "NESTING_UNKNOWN",
            timeline(clips=[clip(sequence="ghost")]),
        ),
        ("NESTING_TOO_DEEP", deep_3),
        (
            "SEQUENCE_SHAPE",
            timeline(sequences={"s1": edl3.NestedSequence("other", inner_ok)}),
        ),
        (
            "EFFECT_UNKNOWN",
            timeline(clips=[clip(effects=[edl3.Effect("wobble")])]),
        ),
        (
            "EFFECT_PARAM",
            timeline(clips=[clip(effects=[edl3.Effect("transform", {"wobble": 1.0})])]),
        ),
        (
            "EFFECT_RANGE",
            timeline(
                clips=[clip(effects=[edl3.Effect("transform", {"opacity": 2.0})])]
            ),
        ),
        (
            "EFFECT_NOT_ANIMATABLE",
            timeline(
                clips=[
                    clip(
                        effects=[
                            edl3.Effect(
                                "blur",
                                {"sigma": 2.0},
                                {"sigma": track([(0, 1), (1, 2)])},
                            )
                        ]
                    )
                ]
            ),
        ),
        (
            "KEYFRAME_PARAM",
            timeline(
                clips=[
                    clip(
                        effects=[
                            edl3.Effect(
                                "transform", {}, {"wobble": track([(0, 1), (1, 2)])}
                            )
                        ]
                    )
                ]
            ),
        ),
        (
            "KEYFRAME_EMPTY",
            timeline(
                clips=[
                    clip(
                        effects=[
                            edl3.Effect(
                                "transform", {}, {"opacity": edl3.KeyframeTrack([])}
                            )
                        ]
                    )
                ]
            ),
        ),
        (
            "KEYFRAME_ORDER",
            timeline(
                clips=[
                    clip(
                        effects=[
                            edl3.Effect(
                                "transform",
                                {},
                                {"opacity": track([(1.0, 1.0), (0.5, 0.0)])},
                            )
                        ]
                    )
                ]
            ),
        ),
        (
            "KEYFRAME_RANGE",
            timeline(
                clips=[
                    clip(
                        effects=[
                            edl3.Effect(
                                "transform",
                                {},
                                {"opacity": track([(0.0, 1.0), (9.0, 0.0)])},
                            )
                        ]
                    )
                ]
            ),
        ),
        (
            "KEYFRAME_INTERP",
            timeline(
                clips=[
                    clip(
                        effects=[
                            edl3.Effect(
                                "transform",
                                {},
                                {"opacity": track([(0.0, 1.0), (1.0, 0.0)], "spin")},
                            )
                        ]
                    )
                ]
            ),
        ),
        (
            "TRANSITION_UNKNOWN",
            timeline(clips=[clip(transition_in=edl3.Transition("wipe", 0.5))]),
        ),
        (
            "TRANSITION_RANGE",
            timeline(clips=[clip(transition_in=edl3.Transition("fade_in", 0.0))]),
        ),
        (
            "TRANSITION_TOO_LONG",
            timeline(clips=[clip(transition_in=edl3.Transition("fade_out", 5.0))]),
        ),
        (
            "ADJUSTMENT_TRACK",
            timeline(
                adjustments=[
                    edl3.AdjustmentLayer(
                        track=0, effects=[edl3.Effect("blur", {"sigma": 1.0})]
                    )
                ]
            ),
        ),
        (
            "TEXT_STYLE",
            timeline(
                text_layers=[
                    edl3.TextLayer(
                        text="t",
                        style=edl3.TextStyle(
                            family="",
                            size=0.0,
                            color="red",
                            stroke=-1.0,
                            position=(2.0, 0.5),
                            align="middle",
                        ),
                        time_in=0.0,
                        time_out=1.0,
                    )
                ]
            ),
        ),
        (
            "TEXT_RANGE",
            timeline(text_layers=[edl3.TextLayer(text="t", time_in=1.0, time_out=1.0)]),
        ),
        (
            "CAPTION_RANGE",
            timeline(captions=[edl3.CaptionTrack([edl3.CaptionCue("", 1.0, 1.0)])]),
        ),
        (
            "CAPTION_OVERLAP",
            timeline(
                captions=[
                    edl3.CaptionTrack(
                        [edl3.CaptionCue("a", 0.0, 1.2), edl3.CaptionCue("b", 1.0, 1.8)]
                    )
                ]
            ),
        ),
        (
            "AUDIO_RANGE",
            timeline(
                audio=[
                    edl3.AudioTrack3(
                        role="music",
                        clips=[edl3.AudioClip3("m", 0.0, 1.0, 0.0, 1.0, 100.0)],
                    )
                ]
            ),
        ),
        (
            "DUCKING_RANGE",
            timeline(ducking=edl3.Ducking3(ratio=50.0)),
        ),
    ]


def check_codes() -> None:
    print("\n-- stable codes")
    missing = []
    for code, document in code_cases():
        found = codes(document)
        if code not in found:
            missing.append(f"{code} -> {found}")
    check("every declared validation code is triggered", missing == [], missing)

    required = {
        "KEYFRAME_ORDER",
        "KEYFRAME_RANGE",
        "CLIP_RANGE",
        "TRANSITION_TOO_LONG",
        "EFFECT_UNKNOWN",
        "EFFECT_NOT_ANIMATABLE",
        "NESTING_TOO_DEEP",
        "TEXT_STYLE",
        "CAPTION_OVERLAP",
    }
    triggered = {code for code, document in code_cases() for code in codes(document)}
    check(
        "the nine codes named by F2-T02 are all exercised",
        required <= triggered,
        sorted(required - triggered),
    )

    deep = next(
        document for code, document in code_cases() if code == "NESTING_TOO_DEEP"
    )
    where = [p["where"] for p in deep.validate() if p["code"] == "NESTING_TOO_DEEP"]
    check(
        "NESTING_TOO_DEEP names the nested path",
        bool(where) and where[0].startswith("sequences."),
        where,
    )
    check(
        "the nesting ceiling is a declared constant",
        edl3.MAX_NESTING_DEPTH == 2,
        edl3.MAX_NESTING_DEPTH,
    )
    broken_inner = timeline(
        clips=[clip(sequence="s1", source="")],
        sequences={
            "s1": edl3.NestedSequence(
                "s1",
                edl3.Timeline3(
                    fps=12,
                    size=(320, 568),
                    clips=[edl3.Clip3(id="n", source=SELF, src_in=1.0, src_out=1.0)],
                ),
            )
        },
    )
    nested_problems = broken_inner.validate()
    check(
        "an invalid inner timeline is reported with a nested path",
        any(
            problem["code"] == "CLIP_RANGE"
            and problem["where"].startswith("sequences.s1.timeline.")
            for problem in nested_problems
        ),
        [problem["where"] for problem in nested_problems],
    )

    scale_doc = timeline(
        clips=[
            clip(
                effects=[
                    edl3.Effect(
                        "transform",
                        {"scale": 1.0},
                        {"scale": track([(0.0, 1.0), (1.0, 1.0)])},
                    )
                ]
            )
        ]
    )
    check(
        "scale keyframes give EFFECT_NOT_ANIMATABLE even when constant",
        codes(scale_doc) == ["EFFECT_NOT_ANIMATABLE"],
        codes(scale_doc),
    )
    check(
        "the compiler refuses EFFECT_NOT_ANIMATABLE as a hard code",
        compile_code(scale_doc) == "EFFECT_NOT_ANIMATABLE",
        compile_code(scale_doc),
    )


# ---------------------------------------------------------------- refusals


def check_compiler_refusals() -> None:
    print("\n-- compiler refusals (no ffmpeg)")
    text_doc = timeline(
        text_layers=[edl3.TextLayer(text="hi", time_in=0.0, time_out=1.0)]
    )
    check(
        "a text layer compiles instead of being refused",
        compile_code(text_doc) == "",
        compile_code(text_doc),
    )
    text_plan = edl3_render.compile_timeline(text_doc)
    check(
        "the text layer is one drawtext over the composition",
        "drawtext=" in text_plan["filter_script"]
        and text_plan["map"].startswith("[tx")
        and len(text_plan["text_layers"]) == 1,
        text_plan["map"],
    )
    unknown_font = timeline(
        text_layers=[
            edl3.TextLayer(
                text="hi",
                style=edl3.TextStyle(family="NoSuchFont-XYZ"),
                time_in=0.0,
                time_out=1.0,
            )
        ]
    )
    check(
        "a text font with no file of the work is FONT_MISSING",
        compile_code(unknown_font) == "FONT_MISSING",
        compile_code(unknown_font),
    )
    caption_doc = timeline(
        captions=[edl3.CaptionTrack([edl3.CaptionCue("hi", 0.0, 1.0)])]
    )
    check(
        "a caption track is refused with UNSUPPORTED_EFFECT",
        compile_code(caption_doc) == "UNSUPPORTED_EFFECT",
        compile_code(caption_doc),
    )
    check(
        "chroma_key is refused with UNSUPPORTED_EFFECT",
        compile_code(
            timeline(
                clips=[clip(effects=[edl3.Effect("chroma_key", {"similarity": 0.2})])]
            )
        )
        == "UNSUPPORTED_EFFECT",
        compile_code(
            timeline(
                clips=[clip(effects=[edl3.Effect("chroma_key", {"similarity": 0.2})])]
            )
        ),
    )

    nested_doc = timeline(
        clips=[clip(sequence="s1", source="")],
        sequences={
            "s1": edl3.NestedSequence(
                "s1",
                edl3.Timeline3(
                    fps=12,
                    size=(320, 568),
                    clips=[edl3.Clip3(id="n", source=SELF, src_in=0.0, src_out=2.0)],
                ),
            )
        },
    )
    check(
        "nested sequences are refused with UNSUPPORTED_SEQUENCE",
        compile_code(nested_doc) == "UNSUPPORTED_SEQUENCE",
        compile_code(nested_doc),
    )
    orphan = timeline(
        clips=[clip(transition_in=edl3.Transition("crossfade", 0.5))]
    )
    check(
        "a crossfade on the first clip is an orphan",
        "TRANSITION_ORPHAN" in {p["code"] for p in orphan.validate()},
        sorted({p["code"] for p in orphan.validate()}),
    )
    too_long = timeline(
        clips=[
            clip(id="a"),
            clip(id="b", transition_in=edl3.Transition("crossfade", 0.6)),
        ]
    )
    check(
        "a dissolve over a quarter of the shorter shot is refused",
        "TRANSITION_TOO_LONG" in {p["code"] for p in too_long.validate()},
        sorted({p["code"] for p in too_long.validate()}),
    )
    audio_doc = timeline(
        audio=[
            edl3.AudioTrack3(
                role="music",
                clips=[edl3.AudioClip3(SELF, 0.0, 2.0, 0.0, 2.0, -6.0)],
            )
        ]
    )
    check(
        "audio is refused with UNSUPPORTED_AUDIO by default",
        compile_code(audio_doc) == "UNSUPPORTED_AUDIO",
        compile_code(audio_doc),
    )
    plan = edl3_render.compile_timeline(audio_doc, audio="drop")
    check(
        "audio='drop' compiles and states the policy",
        plan["audio_policy"] == "drop",
        plan["audio_policy"],
    )
    check(
        "an unknown audio policy is refused",
        compile_code(audio_doc, audio="maybe") == "AUDIO_POLICY",
        compile_code(audio_doc, audio="maybe"),
    )
    missing_doc = timeline(clips=[clip(source="definitely-not-here.mp4")])
    check(
        "a missing source is refused with MISSING_SOURCE",
        compile_code(missing_doc) == "MISSING_SOURCE",
        compile_code(missing_doc),
    )


def main() -> int:
    started = time.monotonic()
    document = full_document()
    check_registry()
    check_entities(document)
    check_codes()
    check_compiler_refusals()

    elapsed = time.monotonic() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL  ({elapsed:.2f}s)")
    check("suite stays under the 15 s ceiling", elapsed < 15.0, f"{elapsed:.2f}s")
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
