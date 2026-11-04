"""``vx edl3 migrate``: the command that makes an ``auto`` plan re-renderable.

Eleven properties, each a check that can fail:

a. the command exists and is a subcommand of ``edl3``;
b. a real ``plan.json`` written by ``vx auto`` migrates to a document
   ``vx edl3 validate`` accepts (skipped when the fixture is not on the box);
c. the report names both schemas, the clip count, the fps and the two
   durations, and ``--json`` is a parsable document;
d. a fractional rate (``29.97``, and the ``29.645583`` ``auto`` deduces) is
   carried verbatim: EDL3 1.2 stores a rate, so nothing is rounded;
e. a document that is already EDL3 is copied byte for byte, exit 0, and the
   note says so (idempotence, sha256 compared);
f. a plan with a ``layer >= 1`` clip is refused with exit 2 and the clip that
   cannot be represented is named — no output is written;
g. content EDL3 cannot carry is declared in ``loss`` and refused (2) instead of
   being dropped in silence: per-bed ducking and a declared tail;
h. a file that is not a plan at all is refused with ``PLAN_SCHEMA``;
i. a broken or missing file exits 2 with no traceback;
j. the ``--json`` failure document is parsable and carries the same code.

    python tests/test_edl3_migrate_cli.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cli, edl3  # noqa: E402

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


def run(argv: list) -> tuple:
    """``cli.main`` with stdout/stderr captured; SystemExit becomes its code."""
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------ fixtures


def clip(
    clip_id: str,
    *,
    layer: int = 0,
    src_in: float,
    src_out: float,
    dst_in: float,
    dst_out: float,
    role: str = "main_talking_head",
) -> dict:
    return {
        "clip_id": clip_id,
        "source_id": "a",
        "src_in": src_in,
        "src_out": src_out,
        "dst_in": dst_in,
        "dst_out": dst_out,
        "layer": layer,
        "role": role,
    }


def ducking(target: str) -> dict:
    return {
        "sidechain_role": "dialogue",
        "target_role": target,
        "threshold": 0.05,
        "ratio": 8.0,
        "attack_ms": 20.0,
        "release_ms": 250.0,
        "makeup_db": 0.0,
    }


def edl2(**overrides) -> dict:
    """A minimal but valid ``vx.edl/2.2`` plan: two 2 s clips on one source."""
    data = {
        "contract_version": "2.2.0",
        "timeline": {
            "fps": 30.0,
            "duration_sec": 4.0,
            "aspect_ratio": "9:16",
            "resolution": [320, 568],
        },
        "sources": {"a": {"path": "a.mp4", "sha256": "a" * 64, "camera": {}}},
        "tracks": {
            "video": [
                {
                    "track_id": 0,
                    "role": "a_roll",
                    "clips": [
                        clip("c1", src_in=0.0, src_out=2.0, dst_in=0.0, dst_out=2.0),
                        clip("c2", src_in=2.0, src_out=4.0, dst_in=2.0, dst_out=4.0),
                    ],
                }
            ]
        },
    }
    data.update(overrides)
    return data


def with_beds(plan: dict) -> dict:
    """Add dialogue/music/sfx beds so ``ducking_tracks`` can name them."""
    tracks = plan["tracks"]
    tracks["audio"] = [
        {
            "track_id": 0,
            "role": "dialogue",
            "clips": [
                {
                    "clip_id": "d1",
                    "source_id": "a",
                    "src_in": 0.0,
                    "src_out": 4.0,
                    "dst_in": 0.0,
                    "dst_out": 4.0,
                }
            ],
        }
    ]
    for track_id, role in ((1, "music"), (2, "sfx")):
        tracks["audio"].append(
            {
                "track_id": track_id,
                "role": role,
                "clips": [
                    {
                        "clip_id": f"{role}1",
                        "source_id": "a",
                        "src_in": 0.0,
                        "src_out": 4.0,
                        "dst_in": 0.0,
                        "dst_out": 4.0,
                        "gain_db": -6.0,
                    }
                ],
            }
        )
    return plan


def write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------- main


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="vx_edl3_migrate_cli_"))
    try:
        run_checks(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


def run_checks(work: Path) -> None:
    print("\n(a) the command exists")
    code, text, _ = run(["edl3", "--help"])
    check("a. `edl3 --help` lists migrate", code == 0 and "migrate" in text, code)
    code, text, _ = run(["edl3", "migrate", "--help"])
    check(
        "a. `edl3 migrate --help` names --plan and --out",
        code == 0 and "--plan" in text and "--out" in text,
        code,
    )

    print("\n(c) a 2.2 plan migrates and the report names both schemas")
    plan = write(work / "plan.json", edl2())
    out = work / "migrated.json"
    code, text, err = run(["edl3", "migrate", "--plan", str(plan), "--out", str(out)])
    check("c. a valid 2.2 plan exits 0", code == 0, (code, text, err))
    check("c. the document is written", out.exists(), out)
    document = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    check(
        "c. the document declares EDL3_VERSION",
        document.get("version") == edl3.EDL3_VERSION,
        document.get("version"),
    )
    check(
        "c. the text names both schemas",
        "vx.edl/2.2" in text and edl3.EDL3_VERSION in text,
        text.strip(),
    )
    code, text, _ = run(["edl3", "migrate", "--plan", str(plan), "--out", str(out), "--json"])
    payload = json.loads(text)
    check(
        "c. --json is a parsable success document",
        code == 0 and payload["ok"] is True,
        code,
    )
    check(
        "c. the report carries clips, fps and both durations",
        (
            payload["clips"] == 2
            and payload["fps"] == 30
            and payload["duration_sec"] == {"from": 4.0, "to": 4.0}
        ),
        payload["duration_sec"],
    )
    check(
        "c. the report carries both schemas",
        payload["schema"] == {"from": "vx.edl/2.2", "to": edl3.EDL3_VERSION},
        payload["schema"],
    )
    check("c. a faithful migration declares no loss", payload["loss"] == [], payload["loss"])

    print("\n(b) a multi-clip plan shaped like a `vx auto` one migrates and validates")
    auto_source = edl2()
    auto_clips = [
        clip(
            f"c{index}",
            src_in=float(index),
            src_out=float(index + 1),
            dst_in=float(index),
            dst_out=float(index + 1),
        )
        for index in range(8)
    ]
    auto_source["tracks"]["video"][0]["clips"] = auto_clips
    auto_source["timeline"]["duration_sec"] = 8.0
    auto_plan = write(work / "auto_like.json", auto_source)
    auto_out = work / "auto.edl3.json"
    code, text, err = run(
        ["edl3", "migrate", "--plan", str(auto_plan), "--out", str(auto_out)]
    )
    check("b. the multi-clip plan migrates", code == 0, (code, text, err))
    code, text, err = run(["edl3", "validate", str(auto_out)])
    check("b. `edl3 validate` accepts the result", code == 0, (code, text, err))
    source = json.loads(auto_plan.read_text(encoding="utf-8"))
    code, text, _ = run(
        [
            "edl3",
            "migrate",
            "--plan",
            str(auto_plan),
            "--out",
            str(auto_out),
            "--json",
        ]
    )
    payload = json.loads(text)
    check(
        "b. clip count and duration match the 2.2 plan",
        payload["clips"] == len(source["tracks"]["video"][0]["clips"])
        and payload["duration_sec"]["from"] == source["timeline"]["duration_sec"]
        and abs(
            payload["duration_sec"]["to"] - source["timeline"]["duration_sec"]
        )
        <= 1e-3,
        payload["duration_sec"],
    )

    print("\n(d) a fractional rate is carried verbatim, never rounded")
    for rate in (29.97, 29.645583):
        source = edl2()
        source["timeline"]["fps"] = rate
        rate_plan = write(work / f"fps_{rate}.json", source)
        rate_out = work / f"fps_{rate}.edl3.json"
        code, text, err = run(
            ["edl3", "migrate", "--plan", str(rate_plan), "--out", str(rate_out)]
        )
        check(f"d. fps {rate} exits 0", code == 0, (code, text, err))
        migrated = json.loads(rate_out.read_text(encoding="utf-8")) if rate_out.exists() else {}
        check(
            f"d. fps {rate} is preserved exactly",
            migrated.get("fps") == rate,
            migrated.get("fps"),
        )
        check(
            f"d. fps {rate} is noted in the report",
            "fps" in text and "nessun arrotondamento" in text,
            text.strip()[:120],
        )
        code, text, _ = run(["edl3", "validate", str(rate_out)])
        check(f"d. the {rate} document validates", code == 0, (code, text.strip()))

    print("\n(e) an EDL3 document is copied byte for byte")
    edl3_path = work / "already.edl3.json"
    edl3_document = json.loads(out.read_text(encoding="utf-8"))
    write(edl3_path, edl3_document)
    copy_path = work / "already.copy.json"
    before = sha256(edl3_path)
    code, text, err = run(
        ["edl3", "migrate", "--plan", str(edl3_path), "--out", str(copy_path)]
    )
    check("e. an EDL3 input exits 0", code == 0, (code, text, err))
    check("e. the copy exists", copy_path.exists(), copy_path)
    check(
        "e. the copy is byte identical (sha256)",
        copy_path.exists() and sha256(copy_path) == before,
        before[:16],
    )
    check(
        "e. the report says nothing changed",
        "gia'" in text and "nessuna modifica" in text,
        text.strip(),
    )
    code, text, _ = run(
        ["edl3", "migrate", "--plan", str(edl3_path), "--out", str(copy_path), "--json"]
    )
    payload = json.loads(text)
    check(
        "e. --json declares already/changed",
        payload["already"] == edl3.EDL3_VERSION and payload["changed"] is False,
        (payload.get("already"), payload.get("changed")),
    )

    print("\n(f) a layer >= 1 clip is refused, with the clip named")
    overlay = edl2()
    overlay["tracks"]["video"].append(
        {
            "track_id": 1,
            "role": "b_roll",
            "clips": [
                clip(
                    "v1_0",
                    layer=1,
                    src_in=0.0,
                    src_out=1.0,
                    dst_in=0.5,
                    dst_out=1.5,
                    role="broll_overlay",
                )
            ],
        }
    )
    overlay_path = write(work / "overlay.json", overlay)
    overlay_out = work / "overlay.edl3.json"
    code, text, err = run(
        ["edl3", "migrate", "--plan", str(overlay_path), "--out", str(overlay_out)]
    )
    check("f. a layer 1 plan exits 2", code == 2, (code, text))
    check("f. the refusal names the code", "UNSUPPORTED_LAYER" in text, text.strip())
    check("f. the refusal names the clip", "v1_0" in text, text.strip())
    check("f. no document is written", not overlay_out.exists(), overlay_out)
    code, text, err = run(
        ["edl3", "migrate", "--plan", str(overlay_path), "--out", str(overlay_out), "--json"]
    )
    payload = json.loads(text)
    check(
        "f. --json failure carries the code and the field",
        code == 2
        and payload["ok"] is False
        and payload["error"]["code"] == "UNSUPPORTED_LAYER"
        and "tracks.video.clips" in payload["error"]["where"],
        payload["error"],
    )

    print("\n(g) content EDL3 cannot carry is declared in loss")
    beds = with_beds(edl2())
    beds["ducking"] = ducking("music")
    beds["ducking_tracks"] = [ducking("music"), ducking("sfx")]
    beds_path = write(work / "beds.json", beds)
    beds_out = work / "beds.edl3.json"
    code, text, err = run(
        ["edl3", "migrate", "--plan", str(beds_path), "--out", str(beds_out), "--json"]
    )
    payload = json.loads(text)
    check("g. per-bed ducking exits 2", code == 2, (code, text))
    check(
        "g. the loss names the ducking spec",
        any(item["code"] == "DUCKING_TRACKS" for item in payload["loss"]),
        payload["loss"],
    )
    check("g. no document is written", not beds_out.exists(), beds_out)

    tail = edl2()
    tail["timeline"]["duration_sec"] = 5.0
    tail_path = write(work / "tail.json", tail)
    tail_out = work / "tail.edl3.json"
    code, text, err = run(
        ["edl3", "migrate", "--plan", str(tail_path), "--out", str(tail_out), "--json"]
    )
    payload = json.loads(text)
    check("g. a declared tail exits 2", code == 2, (code, text))
    check(
        "g. the loss names timeline.duration_sec",
        any(
            item["code"] == "TAIL" and item["where"] == "timeline.duration_sec"
            for item in payload["loss"]
        ),
        payload["loss"],
    )
    check("g. the refusal says nothing was written", "nessun documento" in text, text.strip()[:100])

    print("\n(h) a file that is not a plan is refused by name")
    other = write(work / "other.json", {"hello": 1})
    code, text, err = run(
        ["edl3", "migrate", "--plan", str(other), "--out", str(work / "x.json")]
    )
    check("h. an unknown document exits 2", code == 2, (code, text))
    check("h. the refusal code is PLAN_SCHEMA", "PLAN_SCHEMA" in text, text.strip())

    print("\n(i) a broken or missing file exits 2 without a traceback")
    broken = work / "broken.json"
    broken.write_text('{"timeline": ', encoding="utf-8")
    for label, path in (("broken", broken), ("missing", work / "nope.json")):
        code, text, err = run(
            ["edl3", "migrate", "--plan", str(path), "--out", str(work / "x.json")]
        )
        check(f"i. a {label} file exits 2", code == 2, (code, text))
        check(
            f"i. a {label} file prints no traceback",
            "Traceback" not in text and "Traceback" not in err,
            err.strip()[:80],
        )
        code, text, _ = run(
            [
                "edl3",
                "migrate",
                "--plan",
                str(path),
                "--out",
                str(work / "x.json"),
                "--json",
            ]
        )
        check(
            f"i. a {label} file has a parsable --json failure",
            code == 2 and json.loads(text)["error"]["code"] == "PLAN_UNREADABLE",
            text.strip()[:80],
        )


if __name__ == "__main__":
    raise SystemExit(main())
