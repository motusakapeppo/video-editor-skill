"""Test dell'hook ASS e della soglia di qualita' della trascrizione.

Prima della modifica `write_hook_ass` e `transcript_quality` non esistevano,
quindi il test falliva gia' all'import.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import subs  # noqa: E402

OUT = ROOT / "tests" / "_out" / "hook_ass"

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:52} {detail}")


def words(count: int, probability: float) -> list[dict]:
    return [
        {
            "start": i * 0.2,
            "end": i * 0.2 + 0.2,
            "word": f"w{i}",
            "probability": probability,
        }
        for i in range(count)
    ]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    path = subs.write_hook_ass(
        OUT / "hook.ass",
        "guarda questo",
        out_w=1080,
        out_h=1920,
        start_s=1.0,
        duration_s=3.0,
    )
    text = Path(path).read_text(encoding="utf-8")
    dialogues = [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]
    check("exactly one Dialogue line", len(dialogues) == 1, str(len(dialogues)))

    if dialogues:
        fields = dialogues[0].split(",", 9)
        check(
            "event starts at start_s",
            fields[1] == subs._ass_time(1.0),
            fields[1],
        )
        check(
            "event ends at start_s + duration_s",
            fields[2] == subs._ass_time(4.0),
            fields[2],
        )
        check("hook text is uppercased", "GUARDA QUESTO" in fields[9], fields[9])
        check("hook has no karaoke tag", "\\k" not in fields[9], fields[9])

    style = next((ln for ln in text.splitlines() if ln.startswith("Style:")), "")
    check("hook 1920 uses scaled horizontal outline/shadow", ",1,7,4," in style, style)

    small = subs.write_hook_ass(
        OUT / "hook_1080.ass", "hook", out_w=1920, out_h=1080, start_s=0.0
    )
    small_style = next(
        (
            ln
            for ln in Path(small).read_text(encoding="utf-8").splitlines()
            if ln.startswith("Style:")
        ),
        "",
    )
    check("hook 1080 uses 4/2 outline/shadow", ",1,4,2," in small_style, small_style)

    eleven = subs.transcript_quality(words(11, 0.9))
    check("11 words -> ok False", eleven["ok"] is False, eleven["reason"])
    check(
        "11 words -> word_count 11",
        eleven["word_count"] == 11,
        str(eleven["word_count"]),
    )

    twelve = subs.transcript_quality(words(12, 0.6))
    check("12 words @ 0.6 -> ok True", twelve["ok"] is True, twelve["reason"])
    check(
        "12 words -> mean_probability 0.6",
        abs(twelve["mean_probability"] - 0.6) < 1e-9,
        str(twelve["mean_probability"]),
    )

    forty = subs.transcript_quality(words(40, 0.4))
    check("40 words @ 0.4 -> ok False", forty["ok"] is False, forty["reason"])

    missing = subs.transcript_quality(
        [{"start": 0.0, "end": 0.2, "word": f"w{i}"} for i in range(12)]
    )
    check("missing probabilities count as 0", missing["ok"] is False, missing["reason"])

    print(f"\n{PASS} pass, {FAIL} fail")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
