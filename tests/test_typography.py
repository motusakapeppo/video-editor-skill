"""Typography: the chosen font reaches the burnt-in frame, and its licence travels.

The decisive check is (c): the same 1 s clip is rendered twice, once with libass
pointed at the job fonts dir that holds a serif whose family differs sharply from
the bundled Antonio, once with the bundled dir. If the two frames are identical,
libass substituted the family and the injection failed: that is a FAIL, not a
warning.

    python tests/test_typography.py
"""

from __future__ import annotations

import inspect
import subprocess
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import contract, fontchooser, fontlib, fonts, render, subs, typography  # noqa: E402
from vx.contract import Cue  # noqa: E402
from vx.edl_plan import ClipSegment, EDLPlan, SourceRef, Track  # noqa: E402
from vx.ffmpeg import ffmpeg  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

_SYSTEM_ROOT = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or ""
WINDOWS_FONTS = Path(_SYSTEM_ROOT) / "Fonts" if _SYSTEM_ROOT else Path("")
SERIF_PREFERENCE = (
    "georgia.ttf",
    "times.ttf",
    "constan.ttf",
    "pala.ttf",
    "bookos.ttf",
)
TEXT = "HELLO GEORGIA WORLD"
WIDTH = 320
HEIGHT = 568


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:58} {reason}")


def sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _system_serif() -> tuple[Path, str]:
    """A real system font whose family is not Antonio, read from its name table."""
    if not WINDOWS_FONTS.is_dir():
        return Path(), ""
    candidates: list[Path] = []
    for name in SERIF_PREFERENCE:
        path = WINDOWS_FONTS / name
        if path.is_file():
            candidates.append(path)
    candidates.extend(sorted(WINDOWS_FONTS.glob("*.ttf"))[:400])
    seen: set = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            names = fontchooser.read_names(path)
        except (OSError, contract.VxError):
            continue
        family = str(names.get("family") or "").strip()
        if family and family.lower() != "antonio":
            return path, family
    return Path(), ""


def _record(path: Path, family: str, licence: str = "commercial") -> fontlib.FontRecord:
    return fontlib.FontRecord(
        family=family,
        licence=licence,
        file=str(path.resolve()),
        sha256=fontlib.sha256_file(path),
        styles=["Regular"],
        licence_url="https://example.invalid/licence",
        source_url=path.resolve().as_uri(),
        retrieved_at="2026-10-27T00:00:00+00:00",
    )


def _non_background_ratio(path: Path, background=(12, 12, 12)) -> float:
    from PIL import Image, ImageChops

    with Image.open(path) as image:
        frame = image.convert("RGB")
    diff = ImageChops.difference(frame, Image.new("RGB", frame.size, background))
    total = frame.size[0] * frame.size[1]
    if not total:
        return 0.0
    return (total - diff.convert("L").histogram()[0]) / total


def _png_bytes(path: Path) -> bytes:
    return path.read_bytes()


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


def _make_source(path: Path) -> str:
    proc = subprocess.run(
        [
            ffmpeg(),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x1a1a1a:size={WIDTH}x{HEIGHT}:rate=24:duration=1",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return "" if proc.returncode == 0 and path.is_file() else (proc.stderr or "")[-400:]


def _plan() -> EDLPlan:
    clip = ClipSegment("v0_0", "src", 0.0, 1.0, 0.0, 1.0, 0, "main_talking_head")
    return EDLPlan(
        fps=24.0,
        duration_sec=1.0,
        aspect_ratio="9:16",
        resolution=(WIDTH, HEIGHT),
        sources={"src": SourceRef("src.mp4", "a" * 64, 1.0)},
        video_tracks=(Track(0, (clip,), "a_roll"),),
        audio_tracks=(),
    )


def _cue() -> Cue:
    return Cue(
        start=0.0,
        end=1.0,
        text=TEXT,
        words=[{"start": 0.0, "end": 1.0, "word": TEXT}],
        style="karaoke",
        line_count=1,
    )


def _frame(video: Path, png: Path) -> str:
    proc = subprocess.run(
        [
            ffmpeg(),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            "0.5",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(png),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return "" if proc.returncode == 0 and png.is_file() else (proc.stderr or "")[-400:]


def _mad(a: Path, b: Path) -> float:
    from PIL import Image, ImageChops, ImageStat

    with Image.open(a) as ia, Image.open(b) as ib:
        left = ia.convert("L")
        right = ib.convert("L")
        if left.size != right.size:
            return 999.0
        return float(ImageStat.Stat(ImageChops.difference(left, right)).mean[0])


def _ink_in_band(path: Path, top: int, bottom: int) -> int:
    from PIL import Image, ImageChops

    with Image.open(path) as image:
        frame = image.convert("RGB")
    band = frame.crop((0, max(0, top), frame.size[0], min(frame.size[1], bottom)))
    diff = ImageChops.difference(band, Image.new("RGB", band.size, (12, 12, 12)))
    return band.size[0] * band.size[1] - diff.convert("L").histogram()[0]


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #


def check_preview(root: Path, antonio: Path) -> fontlib.FontRecord:
    print("\n(a) preview: PNG, deterministic, not blank")
    record = _record(antonio, "Antonio")
    first = typography.preview(record, "Antonio 123", root / "preview_a.png")
    second = typography.preview(record, "Antonio 123", root / "preview_b.png")
    check("preview scrive un PNG", first.is_file() and first.stat().st_size > 0, first)
    check(
        "preview due volte byte-identico",
        _png_bytes(first) == _png_bytes(second),
        sha256(first)[:16],
    )
    ratio = _non_background_ratio(first)
    check("preview non e' vuoto (>0.5% pixel)", ratio > 0.005, f"{ratio:.4f}")
    return record


def check_sheet(
    root: Path, antonio: Path, antonio_bold: Path, system: Path, family: str
) -> None:
    print("\n(b) preview_sheet: una riga per record, deterministico")
    records = [
        _record(antonio, "Antonio"),
        _record(system, family),
        _record(antonio_bold, "Antonio"),
    ]
    one = typography.preview_sheet(records[:1], "Sample", root / "sheet_1.png")
    two = typography.preview_sheet(records[:2], "Sample", root / "sheet_2.png")
    three = typography.preview_sheet(records, "Sample", root / "sheet_3.png")
    repeat = typography.preview_sheet(records, "Sample", root / "sheet_3b.png")
    from PIL import Image

    with Image.open(one) as im1, Image.open(two) as im2, Image.open(three) as im3:
        h1, h2, h3 = im1.size[1], im2.size[1], im3.size[1]
    check(
        "altezza lineare nel numero di record",
        h2 > h1 and (h3 - h2) == (h2 - h1),
        f"{h1} -> {h2} -> {h3}",
    )
    check(
        "sheet deterministico",
        _png_bytes(three) == _png_bytes(repeat),
        sha256(three)[:16],
    )
    top = _ink_in_band(three, 0, h3 // 3)
    bottom = _ink_in_band(three, 2 * h3 // 3, h3)
    check("prima e ultima riga hanno inchiostro", top > 0 and bottom > 0, (top, bottom))


def check_injection(
    root: Path, system: Path, family: str, source: Path
) -> tuple[Path, Path]:
    print("\n(c) il font viene davvero usato (MAD tra i due render)")
    store = fontlib.Store(root / "store")
    workdir = root / "job"
    applied = typography.apply(store, _record(system, family), workdir)
    check(
        "apply: fonts_dir costruita",
        Path(applied["fonts_dir"]).is_dir(),
        applied["fonts_dir"],
    )
    check(
        "apply: il font scelto e' nella cartella del lavoro",
        any(
            p.suffix.lower() in (".ttf", ".otf")
            and p.stem.lower().startswith(system.stem.lower())
            for p in Path(applied["fonts_dir"]).iterdir()
        ),
        sorted(p.name for p in Path(applied["fonts_dir"]).iterdir()),
    )
    ass = Path(
        subs.write_ass(
            [_cue()],
            root / "captions.ass",
            aspect="9:16",
            out_w=WIDTH,
            out_h=HEIGHT,
            margin_lr=10,
            font_family=applied["ass_fontname"],
        )
    )
    plan = _plan()
    job_mp4 = root / "render_job.mp4"
    bundled_mp4 = root / "render_bundled.mp4"
    err_job = ""
    err_bundled = ""
    try:
        render.render_edl(
            plan,
            {"src": str(source)},
            job_mp4,
            workdir=root / "work_job",
            has_audio=False,
            subtitles=str(ass),
            fonts_dir=applied["fonts_dir"],
        )
    except render.RenderFailure as exc:
        err_job = str(exc)
    try:
        render.render_edl(
            plan,
            {"src": str(source)},
            bundled_mp4,
            workdir=root / "work_bundled",
            has_audio=False,
            subtitles=str(ass),
            fonts_dir=None,
        )
    except render.RenderFailure as exc:
        err_bundled = str(exc)
    check(
        "render con fonts_dir del lavoro riuscito",
        job_mp4.is_file() and not err_job,
        err_job[-200:],
    )
    check(
        "render con fonts_dir=None riuscito",
        bundled_mp4.is_file() and not err_bundled,
        err_bundled[-200:],
    )
    frame_job = root / "frame_job.png"
    frame_bundled = root / "frame_bundled.png"
    err_f1 = _frame(job_mp4, frame_job)
    err_f2 = _frame(bundled_mp4, frame_bundled)
    check(
        "frame estratti",
        frame_job.is_file() and frame_bundled.is_file(),
        err_f1 or err_f2,
    )
    mad = _mad(frame_job, frame_bundled) if err_f1 == "" and err_f2 == "" else 0.0
    check(
        f"il font e' iniettato: MAD > 2.0 (famiglia {family!r})",
        mad > 2.0,
        f"MAD={mad:.4f}",
    )
    print(f"  MAD misurata: {mad:.4f}")
    return ass, Path(applied["matrix_path"])


def check_ass(ass: Path, family: str) -> None:
    print("\n(d) l'ASS generato porta il Fontname scelto")
    text = ass.read_text(encoding="utf-8")
    check("ASS contiene Vx,<famiglia>", f"Vx,{family}," in text, family)
    check("ASS non contiene il fallback Antonio", "Vx,Antonio," not in text)


def check_matrix(matrix: Path, family: str) -> None:
    print("\n(e) apply scrive LICENSES_MATRIX.md con ogni font e la sua licenza")
    text = matrix.read_text(encoding="utf-8")
    check("matrice scritta", matrix.is_file() and "| Family |" in text, matrix)
    check("matrice nomina il font scelto", family in text, family)
    check("matrice porta la licenza commercial", "commercial" in text)
    check("matrice include il font incluso (Antonio)", "Antonio" in text)
    for column in ("Licence", "Licence URL", "Source", "Retrieved", "SHA256"):
        check(f"matrice: colonna {column}", column in text)


def check_personal(root: Path, system: Path, family: str) -> None:
    print("\n(f) licenza personal: rifiutata, poi ammessa con avviso")
    store = fontlib.Store(root / "store_personal")
    personal = _record(system, family, licence="personal")
    message = ""
    try:
        typography.apply(store, personal, root / "job_personal")
    except contract.VxError as exc:
        message = str(exc)
    except Exception as exc:  # noqa: BLE001 - wrong type is a failure
        message = f"__WRONG_TYPE__{type(exc).__name__}: {exc}"
    check(
        "personal rifiutata con VxError che nomina la licenza",
        message and "__WRONG_TYPE__" not in message and "personal" in message,
        message[-160:],
    )
    unknown = _record(system, family, licence="unknown")
    unknown_message = ""
    try:
        typography.apply(store, unknown, root / "job_unknown")
    except contract.VxError as exc:
        unknown_message = str(exc)
    except Exception as exc:  # noqa: BLE001
        unknown_message = f"__WRONG_TYPE__{type(exc).__name__}: {exc}"
    check(
        "unknown rifiutata con VxError che nomina la licenza",
        unknown_message and "unknown" in unknown_message,
        unknown_message[-160:],
    )
    applied = typography.apply(
        store, personal, root / "job_personal_ok", allow_personal=True
    )
    text = Path(applied["matrix_path"]).read_text(encoding="utf-8")
    check(
        "allow_personal=True accetta e la matrice porta l'avviso",
        "WARNING" in text and "personal" in text,
        applied["licence"],
    )


def check_graph_identity(ass: Path) -> None:
    print("\n(g) fonts_dir=None: grafo byte-identico a oggi")
    plan = _plan()
    today = render.build_edl_filtergraph(
        plan, {"src": 0}, has_audio=False, subtitles=str(ass)
    )
    explicit = render.build_edl_filtergraph(
        plan, {"src": 0}, has_audio=False, subtitles=str(ass), fonts_dir=None
    )
    check("graph identico senza il nuovo keyword", today == explicit)
    check(
        "graph None usa la cartella inclusa",
        "fontsdir=" + fonts.fontsdir_arg() in today,
        fonts.fontsdir_arg(),
    )
    check(
        "render_edl: fonts_dir default None",
        inspect.signature(render.render_edl).parameters["fonts_dir"].default is None,
    )


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="vx-typography-"))
    print(f"workdir: {root}")

    antonio = fonts.font_file("Antonio", "Regular")
    antonio_bold = fonts.font_file("Antonio", "Bold")
    if antonio is None or antonio_bold is None:
        check("font incluso presente", False, fonts.package_font_dir())
        return 1

    system, family = _system_serif()
    if not system.is_file():
        skip("(c) font di sistema", f"nessun .ttf leggibile in {WINDOWS_FONTS}")
        check("font di sistema disponibile", False, WINDOWS_FONTS)
        return 1

    check_preview(root, antonio)
    check_sheet(root, antonio, antonio_bold, system, family)

    source = root / "source.mp4"
    err = _make_source(source)
    check("clip fixture 320x568 1s", source.is_file() and not err, err[-200:])
    if not source.is_file():
        print(f"\n{PASS} PASS / {FAIL} FAIL")
        return 1

    ass, matrix = check_injection(root, system, family, source)
    check_ass(ass, family)
    check_matrix(matrix, family)
    check_personal(root, system, family)
    check_graph_identity(ass)

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
