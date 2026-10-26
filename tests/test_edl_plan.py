"""EDL v2.0 multi-source contract: model, validation, builder, real round-trip.

python tests/test_edl_plan.py

Red contract for Phase 3.1. ``vx.edl_plan`` and ``cutplan.build_edl_plan`` do
not exist yet: every missing API is reported as a FAIL line, never as a
traceback. Model cases run before builder cases, and the builder is imported
lazily so a missing builder cannot hide a model regression.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from vx import edl_plan as model  # noqa: E402

    MODEL_ERROR = ""
except Exception as exc:  # noqa: BLE001 - the module is the thing under test
    model = None  # type: ignore[assignment]
    MODEL_ERROR = f"{type(exc).__name__}: {exc}"

# fmt: off

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:56} {reason}")


def run_case(label: str, fn: Callable[[], None]) -> None:
    before = PASS + FAIL
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - a red contract must not traceback
        check(label, False, f"{type(exc).__name__}: {exc}")
    if PASS + FAIL == before:
        check(label, False, "no assertion executed")


def _ready(label: str) -> bool:
    if model is None:
        check(label, False, f"vx.edl_plan unavailable: {MODEL_ERROR}")
        return False
    return True


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


def _src(path="a.mp4", sha=None, duration=20.0):
    return model.SourceRef(path=path, sha256=sha or "a" * 64, duration=duration)


def _c(cid, si=0.0, so=10.0, di=0.0, do=10.0, *, source_id="src_01", layer=0, role="main_talking_head", tin=None, tout=None):
    return model.ClipSegment(clip_id=cid, source_id=source_id, src_in=si, src_out=so, dst_in=di, dst_out=do, layer=layer, role=role, transition_in=tin, transition_out=tout)


def _b(cid, si, so, di, do):
    return _c(cid, si, so, di, do, layer=1, role="broll_overlay")


def _t(tid, clips, role="main"):
    return model.Track(track_id=tid, clips=tuple(clips), role=role)


def _p(**kw):
    base = dict(contract_version="2.0.0", fps=25.0, duration_sec=20.0, aspect_ratio="9:16", resolution=(1080, 1920),
                sources={"src_01": _src(), "src_02": _src("b.mp4", "b" * 64)},
                video_tracks=(_t(0, [_c("c1"), _c("c2", 10, 20, 10, 20)]),),
                audio_tracks=(_t(0, [_c("a1", 0, 20, 0, 20, role="dialogue")], "dialogue"),), notes=())
    base.update(kw)
    return model.EDLPlan(**base)


def _v(*clips, **kw):
    return _p(video_tracks=(_t(0, list(clips)),), audio_tracks=(), **kw)


def _r(label, p):
    errs = p.validate()
    check(label, bool(errs), errs)


def _a(label, p):
    errs = p.validate()
    check(label, errs == [], errs)


def _raises(label, fn, exc_type):
    try:
        fn()
    except exc_type:
        check(label, True)
    except Exception as exc:  # noqa: BLE001
        check(label, False, f"{type(exc).__name__}: {exc}")
    else:
        check(label, False, "no error")


def _set(d, path, value=1):
    node = d
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


# --------------------------------------------------------------------------- #
# model cases
# --------------------------------------------------------------------------- #


def case_m1() -> None:
    if not _ready("M1 import / re-export identity"): return
    from vx import edl as edl_mod
    check("M1 schema", model.EDL_SCHEMA == "vx.edl/2.0", model.EDL_SCHEMA)
    check("M1 version", model.EDL_CONTRACT_VERSION == "2.0.0")
    check("M1 re-export", getattr(edl_mod, "EDLPlan", None) is model.EDLPlan)
    err = model.EDLValidationError(["boom"])
    check("M1 errors", err.errors == ["boom"], err.errors)


def case_m2() -> None:
    if not _ready("M2 minimal valid plan"): return
    p = _p()
    d = p.to_dict()
    keys = {"contract_version", "timeline", "sources", "tracks"}
    tkeys = {"fps", "duration_sec", "aspect_ratio", "resolution"}
    check("M2 validate", p.validate() == [], p.validate())
    check("M2 top keys", set(d) == keys, sorted(d))
    check("M2 timeline keys", set(d["timeline"]) == tkeys, sorted(d["timeline"]))
    check("M2 resolution", d["timeline"]["resolution"] == [1080, 1920])
    check("M2 video clips", isinstance(d["tracks"]["video"][0].get("clips"), list))


def case_m3() -> None:
    if not _ready("M3 contract_version"): return
    _r("M3 wrong version", _p(contract_version="1.0.0"))


def case_m4() -> None:
    if not _ready("M4 timeline scalars"): return
    bad = [("fps0", dict(fps=0.0)), ("fps<0", dict(fps=-25.0)), ("dur0", dict(duration_sec=0.0)),
           ("dur<0", dict(duration_sec=-1.0)), ("res1", dict(resolution=(1080,))),
           ("res3", dict(resolution=(1080, 1920, 1))), ("resstr", dict(resolution=("1080", 1920))),
           ("resoddw", dict(resolution=(1081, 1920))), ("resoddh", dict(resolution=(1080, 1921))),
           ("aspect", dict(aspect_ratio="9x16")), ("aspect0a", dict(aspect_ratio="0:16")),
           ("aspect0b", dict(aspect_ratio="9:0"))]
    for label, kw in bad:
        _r(f"M4 {label}", _p(**kw))


def case_m5() -> None:
    if not _ready("M5 sources"): return
    bad = [("empty", dict(sources={})), ("badkey", dict(sources={"bad key": _src()})),
           ("nopath", dict(sources={"src_01": _src(path="")})),
           ("shortsha", dict(sources={"src_01": _src(sha="abc")})),
           ("uppersha", dict(sources={"src_01": _src(sha="A" * 64)})),
           ("nonhex", dict(sources={"src_01": _src(sha="g" * 64)})),
           ("dur0", dict(sources={"src_01": _src(duration=0.0)})),
           ("dur<0", dict(sources={"src_01": _src(duration=-1.0)}))]
    for label, kw in bad:
        _r(f"M5 {label}", _p(**kw))


def case_m6() -> None:
    if not _ready("M6 unknown keys"): return

    def reject(label, path, expected, base=None):
        d = (base if base is not None else _p()).to_dict()
        _set(d, path)
        try:
            model.EDLPlan.from_dict(d)
        except model.EDLValidationError as exc:
            msg = " | ".join(str(e) for e in exc.errors)
            check(label, expected in msg, msg)
        except Exception as exc:  # noqa: BLE001
            check(label, False, f"{type(exc).__name__}: {exc}")
        else:
            check(label, False, "accepted")

    reject("M6 top", ["bad"], "bad")
    reject("M6 timeline", ["timeline", "bad"], "timeline.bad")
    reject("M6 source", ["sources", "src_01", "bad"], "sources.src_01.bad")
    reject("M6 vtrack", ["tracks", "video", 0, "bad"], "tracks.video[0].bad")
    reject("M6 clip", ["tracks", "video", 0, "clips", 0, "bad"], "tracks.video[0].clips[0].bad")
    reject("M6 atrack", ["tracks", "audio", 0, "bad"], "tracks.audio[0].bad")
    base = _p(video_tracks=(_t(0, [_c("c1", tin=model.Transition("fade", 0.3)), _c("c2", 10, 20, 10, 20)]),))
    reject("M6 trans", ["tracks", "video", 0, "clips", 0, "transition_in", "bad"], "tracks.video[0].clips[0].transition_in.bad", base)


def case_m7() -> None:
    if not _ready("M7 ids"): return
    _r("M7 dup clip", _v(_c("c1"), _c("c1", 10, 20, 10, 20)))
    _r("M7 dup track", _p(video_tracks=(_t(0, [_c("c1")]), _t(0, [_b("b1", 2, 5, 2, 5)])), audio_tracks=()))
    _a("M7 audio reuse", _p())


def case_m8() -> None:
    if not _ready("M8 source references"): return
    _r("M8 empty source", _v(_c("c1", source_id="")))
    _r("M8 unknown source", _v(_c("c1", source_id="src_99")))


def case_m9() -> None:
    if not _ready("M9 clip arithmetic"): return
    _r("M9 src<=in", _v(_c("c1", 5, 5, 0, 0)))
    _r("M9 dst<=in", _v(_c("c1", 0, 10, 5, 5)))
    _r("M9 >1 frame", _v(_c("c1", 0, 10, 0, 10.05)))
    _a("M9 =1 frame", _v(_c("c1", 0, 10, 0, 10.04)))


def case_m10() -> None:
    if not _ready("M10 timeline bounds"): return
    _r("M10 dst_in<0", _v(_c("c1", 0, 10, -1, 9)))
    _r("M10 dst_out>dur", _v(_c("c1", 0, 21, 0, 21)))


def case_m11() -> None:
    if not _ready("M11 layers"): return
    _r("M11 layer<0", _v(_c("c1", layer=-1)))
    _r("M11 broll layer0", _v(_c("c1", layer=0, role="broll_overlay")))


def case_m12() -> None:
    if not _ready("M12 roles"): return
    _r("M12 clip role", _v(_c("c1", role="")))
    _r("M12 track role", _p(video_tracks=(_t(0, [_c("c1")], role=""),), audio_tracks=()))


def case_m13() -> None:
    if not _ready("M13 transitions"): return
    _r("M13 negative", _v(_c("c1", tin=model.Transition("fade", -0.1))))
    _r("M13 >clip", _v(_c("c1", 0, 1, 0, 1, tin=model.Transition("fade", 2.0))))
    _r("M13 >1.5", _v(_c("c1", tin=model.Transition("fade", 1.6))))
    _r("M13 empty type", _v(_c("c1", tin=model.Transition("", 0.3))))
    _a("M13 0.3", _v(_c("c1", tin=model.Transition("fade", 0.3))))


def case_m14() -> None:
    if not _ready("M14 overlap"): return
    _r("M14 same layer", _v(_c("c1"), _b("b1", 2, 5, 2, 5), _b("b2", 4, 7, 4, 7)))
    _a("M14 cross layer", _v(_c("c1"), _b("b1", 2, 5, 2, 5)))


def case_m15() -> None:
    if not _ready("M15 coverage"): return
    _r("M15 gap", _v(_c("c1", 0, 10, 0, 10), _c("c2", 12, 20, 12, 20)))
    _a("M15 hold 45/20", _v(_c("c1", 0, 20, 0, 20), duration_sec=45.0))
    _a("M15 broll 22", _v(_c("c1", 0, 20, 0, 20), _b("b1", 18, 22, 18, 22), duration_sec=45.0))
    _r("M15 broll hole", _v(_c("c1", 0, 20, 0, 20), _b("b1", 30, 32, 30, 32), duration_sec=45.0))


def case_m16() -> None:
    if not _ready("M16 J/L cuts"): return
    video = _t(0, [_c("c1", 0, 10, 0, 10), _c("c2", 10, 20, 10, 20)])

    def dialogue(b):
        return _t(0, [_c("a1", 0, b, 0, b, role="dialogue"), _c("a2", b, 20, b, 20, role="dialogue")], "dialogue")

    _a("M16 cut 1.4", _p(video_tracks=(video,), audio_tracks=(dialogue(11.4),)))
    _r("M16 cut 1.6", _p(video_tracks=(video,), audio_tracks=(dialogue(11.6),)))
    music = _t(0, [_c("m1", 0, 11.6, 0, 11.6, role="music"), _c("m2", 11.6, 20, 11.6, 20, role="music")], "music")
    _a("M16 music ignored", _p(video_tracks=(video,), audio_tracks=(music,)))
    long_clip = _t(0, [_c("a1", 0, 20, 0, 20, role="dialogue")], "dialogue")
    _a("M16 long dialogue", _p(video_tracks=(video,), audio_tracks=(long_clip,)))


def case_m17() -> None:
    if not _ready("M17 audio shorthand"): return
    p = _p()
    d = p.to_dict()
    a = d["tracks"]["audio"][0]
    req = {"track_id", "role", "source_id", "src_in", "src_out", "dst_in", "dst_out"}
    check("M17 flat", req <= set(a) and "clips" not in a, sorted(a))
    check("M17 deterministic", p.to_dict() == d)
    back = model.EDLPlan.from_dict(d)
    check("M17 one clip", len(back.audio_tracks[0].clips) == 1)
    check("M17 round-trip", back == p and back.to_dict() == d)
    mixed = json.loads(json.dumps(d))
    a = mixed["tracks"]["audio"][0]
    a["clips"] = [{"clip_id": "a1", "source_id": a["source_id"], "src_in": a["src_in"], "src_out": a["src_out"],
                   "dst_in": a["dst_in"], "dst_out": a["dst_out"], "layer": 0, "role": "dialogue"}]
    try:
        model.EDLPlan.from_dict(mixed)
    except model.EDLValidationError:
        check("M17 mixed", True)
    except Exception as exc:  # noqa: BLE001
        check("M17 mixed", False, f"{type(exc).__name__}: {exc}")
    else:
        check("M17 mixed", False, "accepted")


def case_m18() -> None:
    if not _ready("M18 immutability"): return
    p = _p()
    _raises("M18 frozen fps", lambda: setattr(p, "fps", 30.0), FrozenInstanceError)
    _raises("M18 sources", lambda: p.sources.__setitem__("src_99", _src()), TypeError)
    check("M18 clips tuple", isinstance(p.video_tracks[0].clips, tuple))
    check("M18 tracks tuple", isinstance(p.video_tracks, tuple))


def case_m19() -> None:
    if not _ready("M19 JSON on disk"): return
    p = _p()
    d = p.to_dict()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "plan.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(d, handle)
        with open(path, encoding="utf-8") as handle:
            back = model.EDLPlan.from_dict(json.load(handle))
        _a("M19 reload valid", back)
        check("M19 idempotent", back.to_dict() == d)


# --------------------------------------------------------------------------- #
# builder cases
# --------------------------------------------------------------------------- #


def _builder(label):
    try:
        from vx import cutplan
    except Exception as exc:  # noqa: BLE001
        check(label, False, f"vx.cutplan import failed: {type(exc).__name__}: {exc}")
        return None
    missing = [n for n in ("build_edl_plan", "BrollCandidate", "BuiltEdl") if not hasattr(cutplan, n)]
    if missing:
        check(label, False, f"vx.cutplan missing: {', '.join(missing)}")
        return None
    return cutplan


def _shot(label, si, so, ti, to, fps=25.0):
    from vx.edl import EdlShot
    return EdlShot(label=label, src_in=si, src_out=so, duration=so - si, t_in=ti, t_out=to,
                   frames=int(round((to - ti) * fps)), cut_on="bar", contrast=1.5)


def _edl(shots, fps=25.0):
    from vx.edl import Edl
    return Edl(fps=fps, shots=list(shots), total=shots[-1].t_out, frames=sum(s.frames for s in shots),
               rhythmic_shots=len(shots), inaction_shots=0)


def _sources():
    return {"src_01": _src("main.mp4", "a" * 64, 20.0), "src_02": _src("broll.mp4", "b" * 64, 20.0)}


def _cand(cp, **kw):
    base = dict(broll_source_id="src_02", src_in=0.0, src_out=2.0, at=3.0, finding_id="f1",
                finding_source_id="src_01", t0=2.0, t1=4.0)
    base.update(kw)
    return cp.BrollCandidate(**base)


def _build(cp, edl, broll, duration, sources=None):
    return cp.build_edl_plan(edl, source_id="src_01", sources=sources or _sources(), broll=broll, fps=25.0, duration=duration)


def case_m20() -> None:
    if not _ready("M20 builder happy path"): return
    cp = _builder("M20 builder happy path")
    if cp is None: return
    edl = _edl([_shot("main", 0, 10, 0, 10), _shot("main", 10, 20, 10, 20)])
    built = _build(cp, edl, [_cand(cp)], 20.0)
    plan = built.plan
    check("M20 two tracks", len(plan.video_tracks) == 2)
    aroll, broll = plan.video_tracks[0], plan.video_tracks[1]
    check("M20 aroll layer0", all(c.layer == 0 for c in aroll.clips))
    check("M20 broll track", broll.track_id != aroll.track_id)
    check("M20 broll layer1", all(c.layer == 1 for c in broll.clips))
    check("M20 aroll no layer1", all(c.layer == 0 for c in aroll.clips))
    audio = plan.audio_tracks[0] if plan.audio_tracks else None
    check("M20 audio mirror", audio is not None and audio.role == "dialogue" and len(audio.clips) == len(aroll.clips)
          and all(c.source_id == "src_01" for c in audio.clips))
    check("M20 audio times", audio is not None and [(c.dst_in, c.dst_out) for c in audio.clips]
          == [(c.dst_in, c.dst_out) for c in aroll.clips])
    check("M20 valid", plan.validate() == [], plan.validate())
    bc = broll.clips[0]
    check("M20 broll source", bc.source_id == "src_02")
    check("M20 broll role", bc.role == "broll_overlay")
    check("M20 witness key", bc.clip_id in built.witnesses)
    check("M20 witness source", built.witness(bc.clip_id)["source_id"] == "src_01")


def case_m21() -> None:
    if not _ready("M21 builder clock mapping"): return
    cp = _builder("M21 builder clock mapping")
    if cp is None: return
    edl = _edl([_shot("main", 10, 20, 0, 10), _shot("main", 2, 12, 10, 20)])
    built = _build(cp, edl, [_cand(cp, src_out=10.0, t0=2.5, t1=3.5)], 25.0)
    bc = next(c for t in built.plan.video_tracks for c in t.clips if c.layer == 1)
    check("M21 dst_in 11", abs(bc.dst_in - 11.0) < 1e-6, bc.dst_in)
    check("M21 dst_out 21", abs(bc.dst_out - 21.0) < 1e-6, bc.dst_out)


def case_m22() -> None:
    if not _ready("M22 builder rejects"): return
    cp = _builder("M22 builder rejects")
    if cp is None: return
    edl = _edl([_shot("main", 0, 10, 0, 10), _shot("main", 10, 20, 10, 20)])

    def expect(label, broll):
        try:
            _build(cp, edl, broll, 20.0)
        except model.EDLValidationError as exc:
            check(label, True, str(exc.errors)[:100])
        except Exception as exc:  # noqa: BLE001
            check(label, False, f"{type(exc).__name__}: {exc}")
        else:
            check(label, False, "no error")

    expect("M22 source mismatch", [_cand(cp, finding_source_id="src_02")])
    expect("M22 outside shots", [_cand(cp, at=50.0, t0=49.0, t1=51.0)])
    expect("M22 beyond duration", [_cand(cp, at=19.5, t0=19.0, t1=20.0, src_out=10.0)])
    expect("M22 overlap", [_cand(cp), _cand(cp)])
    expect("M22 unknown source", [_cand(cp, broll_source_id="src_99")])


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def case_m23() -> None:
    if not _ready("M23 real clip round-trip"): return
    cp = _builder("M23 real clip round-trip")
    if cp is None: return
    ffmpeg = os.environ.get("VX_FFMPEG") or ""
    if not ffmpeg or not Path(ffmpeg).is_file():
        ffmpeg = shutil.which("ffmpeg") or ""
    if not ffmpeg:
        skip("M23 real clip round-trip", "ffmpeg not found")
        return
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        clips = {}
        for name, color in (("a.mp4", "black"), ("b.mp4", "white")):
            target = tmp_path / name
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=25:d=2",
                   "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", str(target)]
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
            if proc.returncode != 0:
                skip("M23 real clip round-trip", "ffmpeg failed")
                return
            clips[name] = target
        sources = {"src_01": model.SourceRef(path=str(clips["a.mp4"]), sha256=_sha256(clips["a.mp4"]), duration=2.0),
                   "src_02": model.SourceRef(path=str(clips["b.mp4"]), sha256=_sha256(clips["b.mp4"]), duration=2.0)}
        edl = _edl([_shot("main", 0, 1, 0, 1), _shot("main", 1, 2, 1, 2)])
        built = _build(cp, edl, [_cand(cp, src_out=0.5, at=0.5, t0=0.4, t1=0.6)], 2.0, sources)
        plan_path = tmp_path / "plan.json"
        with open(plan_path, "w", encoding="utf-8") as handle:
            json.dump(built.plan.to_dict(), handle)
        with open(plan_path, encoding="utf-8") as handle:
            back = model.EDLPlan.from_dict(json.load(handle))
        _a("M23 reload valid", back)
        base = [c for t in back.video_tracks for c in t.clips if c.layer == 0]
        check("M23 base source", bool(base) and all(c.source_id == "src_01" for c in base))
        broll = [c for t in back.video_tracks for c in t.clips if c.layer == 1]
        check("M23 one broll", len(broll) == 1)
        if broll:
            check("M23 witness source", built.witness(broll[0].clip_id)["source_id"] == "src_01")


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #


def main() -> int:
    cases = [("M1 import / re-export identity", case_m1), ("M2 minimal valid plan", case_m2),
             ("M3 contract_version", case_m3), ("M4 timeline scalars", case_m4), ("M5 sources", case_m5),
             ("M6 unknown keys", case_m6), ("M7 ids", case_m7), ("M8 source references", case_m8),
             ("M9 clip arithmetic", case_m9), ("M10 timeline bounds", case_m10), ("M11 layers", case_m11),
             ("M12 roles", case_m12), ("M13 transitions", case_m13), ("M14 overlap", case_m14),
             ("M15 coverage", case_m15), ("M16 J/L cuts", case_m16), ("M17 audio shorthand", case_m17),
             ("M18 immutability", case_m18), ("M19 JSON on disk", case_m19), ("M20 builder happy path", case_m20),
             ("M21 builder clock mapping", case_m21), ("M22 builder rejects", case_m22),
             ("M23 real clip round-trip", case_m23)]
    for label, fn in cases:
        print(f"\n{label}")
        run_case(label, fn)
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


# fmt: on

if __name__ == "__main__":
    raise SystemExit(main())
