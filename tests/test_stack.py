"""Split screen e confronto: due flussi affiancati da ffmpeg, non dal browser.

    python tests/test_stack.py

Il confine dichiarato del 7 novembre: la grafica (testo, sottopancia, card) la fa
Chromium; la composizione di flussi video la fa ffmpeg. Qui si prova la seconda,
con due clip vere generate sul momento.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import stack  # noqa: E402

PASS = 0
FAIL = 0
FFMPEG = "ffmpeg"


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def clip(path: Path, source: str, seconds: float = 1.0) -> None:
    subprocess.run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"{source}=size=480x640:rate=25:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=False,
        capture_output=True,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-stack-") as tmp:
        work = Path(tmp)
        left, right = work / "a.mp4", work / "b.mp4"
        clip(left, "testsrc")
        clip(right, "smptebars")
        if not (left.is_file() and right.is_file()):
            print("SKIP: ffmpeg non ha prodotto le clip di prova")
            return 0

        info = stack.stack(left, right, work / "out.mp4")
        check("S1 il file esiste", Path(info["path"]).is_file(), info["path"])
        check("S1 e' 1080x1920", (info["width"], info["height"]) == (1080, 1920), (info["width"], info["height"]))
        check("S1 dura quanto la piu' corta", abs(info["duration_sec"] - 1.0) < 0.2, info["duration_sec"])
        check("S1 motore e filtro dichiarati", info["engine"] == "ffmpeg" and "hstack" in info["filter"], info["filter"])
        check("S1 l'audio e' dichiarato assente", "nessuno" in info["audio"], info["audio"])

        short = stack.stack(left, right, work / "short.mp4", duration=0.5)
        check("S2 la durata chiesta vince", abs(short["duration_sec"] - 0.5) < 0.2, short["duration_sec"])
        check("S2 e resta dichiarata", short["requested_sec"] == 0.5, short["requested_sec"])

        odd = stack.stack(left, right, work / "odd.mp4", width=1081)
        check("S3 la larghezza dispari diventa pari", odd["width"] == 1080, odd["width"])

        raised = None
        try:
            stack.stack(work / "non-esiste.mp4", right, work / "x.mp4")
        except ValueError as exc:
            raised = str(exc)
        check("S4 una sorgente mancante e' un errore chiaro", raised is not None and "inesistente" in raised, raised)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
