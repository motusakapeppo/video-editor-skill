"""Benchmark riproducibile della misura di ``vx auto``.

Non e' un test della suite: e' lo strumento che dice, su questa macchina, quanto
costano le quattro scorciatoie e se il totale scende.

    python tests/bench_auto_speed.py                      # 1920x1080, 20 s
    python tests/bench_auto_speed.py --width 3840 --seconds 30 --repeats 2
    python tests/bench_auto_speed.py --whisper             # include 3 file

Misura, sulla stessa sorgente sintetica:
  * tagli+freeze in una passata contro le tre passate di oggi;
  * metriche px a piena risoluzione (cv2) contro proxy 640 decodificata a 320;
  * ``analyze_video`` intero, con e senza proxy;
  * hash di tutto il pool contro hash dei soli scelti.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import analyze, auto, ffmpeg as ff, proxy as proxy_mod  # noqa: E402

MB = 1024.0 * 1024.0


def _time(fn, repeat: int = 1) -> tuple[float, object]:
    runs = []
    value = None
    for _ in range(max(1, repeat)):
        started = time.perf_counter()
        value = fn()
        runs.append(time.perf_counter() - started)
    return statistics.median(runs), value


def build_source(path: Path, width: int, seconds: float) -> None:
    height = int(round(width * 9 / 16 / 2)) * 2
    proc = ff.run_ff(
        [
            "-f", "lavfi", "-i",
            f"testsrc2=s={width}x{height}:r=30:d={seconds}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "23", str(path),
        ],
        loglevel="error",
    )
    if not proc.ok:
        raise RuntimeError(proc.tail())


def bench_whisper(tmp: Path) -> None:
    from vx import subs

    try:
        import faster_whisper  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"whisper                  non disponibile ({exc})")
        return
    wavs = []
    for index in range(3):
        wav = tmp / f"speech_{index}.wav"
        proc = ff.run_ff(
            [
                "-f", "lavfi", "-i",
                "sine=frequency=220:duration=60", "-ac", "1", "-ar", "16000",
                str(wav),
            ],
            loglevel="error",
        )
        if proc.ok:
            wavs.append(wav)
    if not wavs:
        print("whisper                  fixtur3 non costruite")
        return
    subs.reset_whisper_cache()
    started = time.perf_counter()
    for wav in wavs:
        subs.transcribe(str(wav), device="cpu", compute_type="int8")
    cold = time.perf_counter() - started
    started = time.perf_counter()
    for wav in wavs:
        subs.transcribe(str(wav), device="cpu", compute_type="int8")
    warm = time.perf_counter() - started
    subs.reset_whisper_cache()
    started = time.perf_counter()
    for wav in wavs:
        subs.transcribe(str(wav), device="cpu", compute_type="int8")
    rebuilt = time.perf_counter() - started
    subs.reset_whisper_cache()
    print(f"whisper 3 file, caldo    {warm:8.2f} s   (modello riusato)")
    print(f"whisper 3 file, freddo   {cold:8.2f} s   (primo caricamento)")
    print(f"whisper 3 file, ricarica {rebuilt:8.2f} s   (comportamento di oggi)")


def main() -> int:
    parser = argparse.ArgumentParser(description="benchmark della misura di vx auto")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--assets", type=int, default=8, help="file per il test hash")
    parser.add_argument("--asset-mb", type=float, default=8.0)
    parser.add_argument("--whisper", action="store_true")
    args = parser.parse_args()

    base = Path(tempfile.mkdtemp(prefix="vx-bench-auto-"))
    try:
        src = base / "source.mp4"
        build_source(src, args.width, args.seconds)

        one_s, (cuts, freezes) = _time(
            lambda: analyze._scan_cuts_freezes(src), args.repeats
        )
        three_s, _ = _time(
            lambda: (
                analyze.detect_scene_cuts(src),
                analyze.detect_freezes(src),
            ),
            args.repeats,
        )
        full_px_s, _ = _time(lambda: analyze._pixel_metrics(src, 10.0), args.repeats)

        dst = base / "proxy_640.mp4"
        proxy_s, info = _time(
            lambda: proxy_mod.make_proxy(src, dst, width=640), args.repeats
        )
        proxy_path = str(info.get("path") or dst)
        proxy_px_s, _ = _time(
            lambda: analyze._pixel_metrics(
                proxy_path, 10.0, decode_width=320, source_width=args.width
            ),
            args.repeats,
        )

        full_total_s, _ = _time(lambda: analyze.analyze_video(src), args.repeats)
        new_total_s, _ = _time(
            lambda: analyze.analyze_video(src, proxy_path=proxy_path),
            args.repeats,
        )

        pool = []
        for index in range(max(1, args.assets)):
            asset = base / f"asset_{index}.mp4"
            asset.write_bytes(b"\x00" * int(args.asset_mb * MB))
            pool.append(asset)
        all_s, _ = _time(lambda: [auto._sha256(path) for path in pool], args.repeats)
        selected_s, _ = _time(lambda: [auto._sha256(pool[0])], args.repeats)

        print(f"sorgente                 {args.width}x{arg_height(args.width)} {args.seconds:g}s")
        print(f"tagli+freeze 3 passate   {three_s:8.2f} s")
        print(f"tagli+freeze 1 passata   {one_s:8.2f} s   ({(1 - one_s / three_s) * 100:+.0f}%)")
        print(f"tagli/freeze             {len(cuts)} tagli, {len(freezes)} freeze")
        print(f"px cv2 piena risoluzione {full_px_s:8.2f} s")
        print(f"proxy 640                 {proxy_s:8.2f} s")
        print(f"px proxy decod. a 320    {proxy_px_s:8.2f} s")
        print(f"analyze_video oggi       {full_total_s:8.2f} s")
        print(f"analyze_video nuovo      {new_total_s:8.2f} s   (proxy a parte: {proxy_s:.2f} s)")
        print(f"hash pool ({len(pool)})       {all_s:8.2f} s")
        print(f"hash scelti (1)          {selected_s:8.2f} s")
        if args.whisper:
            bench_whisper(base)
        return 0
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def arg_height(width: int) -> int:
    return int(round(width * 9 / 16 / 2)) * 2


if __name__ == "__main__":
    raise SystemExit(main())
