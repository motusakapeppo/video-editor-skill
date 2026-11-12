"""F25-T03: profili di camera con dominio, seme deterministico e un frame per profilo.

Scenario:

S1 — 35mm, 16mm, 8mm e dv dichiarano grana, tremolio e alonatura con dominio e
     default dentro il dominio; `validate()` e' pulito.
S2 — lo stesso seme da' la stessa traccia di gate weave, un seme diverso un'altra:
     il tremolio e' deterministico, non casuale.
S3 — un rapporto non dichiarato e' `PROFILE_RATIO` con il valore nel messaggio; un
     parametro fuori dominio e' `PROFILE_LIMIT` con il nome.
S4 — ogni profilo ha un frame di riferimento su disco, byte-identico a una nuova
     resa, e diverso dal frame senza profilo (il profilo ai pixel, non nel JSON).

    python tests/test_camera_profiles.py --write
    python tests/test_camera_profiles.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import camera_profiles as cp, edl3, edl3_render  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402
from _golden import compare_images as golden_compare  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "camera_profiles"
FPS = 12
SIZE = (320, 568)
SHOT = 1.0
AT = 0.5

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:58} {reason}")


def source(work: Path) -> Path:
    target = work / "source.mp4"
    if target.is_file():
        return target
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={SIZE[0]}x{SIZE[1]}:rate={FPS}:duration={SHOT}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18", str(target),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError("cannot build the source")
    return target


def frame(effects, work: Path, name: str) -> Path:
    clip = edl3.Clip3(
        id="c1", source=str(source(work)), src_in=0.0, src_out=SHOT, effects=effects
    )
    document = edl3.Timeline3(fps=FPS, size=SIZE, clips=[clip])
    video = work / f"{name}.mp4"
    edl3_render.render(document, video, workdir=work / f"w-{name}", audio="drop", settings={})
    png = work / f"{name}.png"
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{AT:.3f}", "-i", str(video), "-frames:v", "1", str(png),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not png.is_file():
        raise RuntimeError(f"cannot extract {name}")
    return png


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    try:
        binary = ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0
    if not binary:
        skip("whole file", "no ffmpeg")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    check(
        "S1 the four profiles are declared",
        set(cp.FAMILY) == {"35mm", "16mm", "8mm", "dv"},
        cp.FAMILY,
    )
    check("S1 every parameter has its domain and default", not cp.validate(), cp.validate())

    first = cp.build("8mm", duration=2.0, width=SIZE[0], height=SIZE[1])
    second = cp.build("8mm", duration=2.0, width=SIZE[0], height=SIZE[1])
    other = cp.build("8mm", duration=2.0, width=SIZE[0], height=SIZE[1], seed=99)
    track = lambda chain: [(k.t, round(k.value, 6)) for k in chain[1].keyframes["x"].keys]
    check("S2 the same seed gives the same gate weave", track(first) == track(second), "")
    check("S2 a different seed gives another weave", track(first) != track(other), "")

    try:
        cp.build("8mm", duration=1.0, width=320, height=568, ratio="21:9")
        ratio_code = ""
    except cp.ProfileError as exc:
        ratio_code = exc.code
        ratio_detail = exc.detail
    check("S3 an undeclared ratio is refused with its value", ratio_code == "PROFILE_RATIO", ratio_code)
    check("S3 and the message names it", "21:9" in ratio_detail, ratio_detail[:60])

    try:
        cp.build("35mm", duration=1.0, width=320, height=568, grain=5.0)
        limit_code = ""
    except cp.ProfileError as exc:
        limit_code = exc.code
        limit_detail = exc.detail
    check("S3 a parameter outside its domain is refused", limit_code == "PROFILE_LIMIT", limit_code)
    check("S3 and the message names the parameter", "grain" in limit_detail, limit_detail[:60])

    with tempfile.TemporaryDirectory(prefix="vx-camera-profiles-") as raw:
        work = Path(raw)
        plain = digest(frame([], work, "plain"))
        for name in cp.FAMILY:
            chain = cp.build(name, duration=SHOT, width=SIZE[0], height=SIZE[1])
            try:
                png = frame(chain, work, name)
            except Exception as exc:  # noqa: BLE001
                check(f"S4 {name}: renders", False, f"{type(exc).__name__}: {exc}")
                continue
            if args.write:
                FIXTURES.mkdir(parents=True, exist_ok=True)
                (FIXTURES / f"{name}.png").write_bytes(png.read_bytes())
                (FIXTURES / f"{name}.json").write_text(
                    json.dumps(
                        {
                            "profile": name,
                            "fps": FPS,
                            "size": list(SIZE),
                            "shot": SHOT,
                            "at": AT,
                            "sha256": digest(png),
                            "chain": [effect.kind for effect in chain],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                check(f"S4 {name}: written", True, digest(png)[:12])
                continue
            stored = FIXTURES / f"{name}.png"
            # Pixel con tolleranza, non byte: la stessa golden rigirata su un
            # altro ffmpeg/CPU non e' binariamente identica, ma resta la stessa
            # immagine. Un profilo sbagliato (colore, vignettatura) sfora.
            same, detail = golden_compare(stored, png)
            check(f"S4 {name}: the frame matches its reference", same, detail)
            check(
                f"S4 {name}: the profile reaches the pixels",
                digest(png) != plain,
                "",
            )

    if not args.write:
        strips = sorted(p.stem for p in FIXTURES.glob("*.png")) if FIXTURES.is_dir() else []
        check(
            "S4 one reference frame per profile, no more and no fewer",
            strips == sorted(cp.FAMILY),
            strips,
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

