"""Test della leggibilita' del titolo: misurata, non sperata.

Copre i quattro difetti del reel consegnato con l'hook illeggibile:

* la banda sotto il testo si misura con la geometria di ``vx.verify``;
* la mitigazione si sceglie per numero (contorno -> ombra -> placca);
* il hook non sta dentro la UI bassa della piattaforma;
* un contrasto sotto 4.5 toglie la consegna, e senza testo non si scrive
  nessun ASS.

    python tests/test_text_legibility.py
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import auto, ffmpeg as ff, subs, verify  # noqa: E402

PASS = 0
FAIL = 0
FONT = ROOT / "assets" / "fonts" / "Antonio-Bold.ttf"


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += 1 if ok else 0
    FAIL += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:")


def _solid(path: Path, colour: str) -> None:
    subprocess.run(
        [
            ff.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={colour}:s=1080x1920:d=1",
            "-frames:v", "1", str(path),
        ],
        check=True,
        capture_output=True,
    )


def _bad_master(path: Path) -> None:
    """Titolo grigio su fondo grigio: il caso che il gate deve bocciare."""
    graph = (
        f"[0:v]drawtext=fontfile='{_filter_path(FONT)}':text='TITOLO':"
        "fontcolor=0x8a8a8a:fontsize=80:x=(w-tw)/2:y=120[v]"
    )
    subprocess.run(
        [
            ff.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0x808080:s=320x180:d=2:r=30",
            "-filter_complex", graph, "-map", "[v]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )


def _style_line(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Style:"):
            return line
    return ""


def main() -> int:
    if not FONT.exists():
        print(f"font di prova assente: {FONT}")
        return 2

    with tempfile.TemporaryDirectory(prefix="vx-legibility-") as tmp:
        work = Path(tmp)
        dark = work / "dark.png"
        light = work / "light.png"
        _solid(dark, "0x101010")
        _solid(light, "0xf2f2f2")

        # 1. La banda si legge come la legge il gate: media e percentili.
        dark_band = subs.band_contrast_file(dark, [0.0, 0.0, 1.0, 1.0])
        light_band = subs.band_contrast_file(light, [0.0, 0.0, 1.0, 1.0])
        check(
            "banda scura: luminanza media ~16",
            abs(dark_band["mean"] - 16.0) <= 2.0,
            str(dark_band),
        )
        check(
            "banda chiara: luminanza media ~242",
            abs(light_band["mean"] - 242.0) <= 3.0,
            str(light_band),
        )
        check(
            "bianco su fondo scuro: contrasto >= 4.5",
            subs._wcag_ratio(255.0, dark_band["mean"]) >= subs.LEGIBILITY_MIN,
            f"{subs._wcag_ratio(255.0, dark_band['mean']):.2f}",
        )

        # 2. Geometria della banda: alto e basso.
        metrics = subs._hook_metrics(
            "GUARDA COSA SUCCEDE", out_w=1080, out_h=1920, font="Antonio", size=None
        )
        top = subs.text_band(
            metrics, out_w=1080, out_h=1920, margin_v=269, alignment=8, outline=9
        )
        bottom = subs.text_band(
            metrics, out_w=1080, out_h=1920, margin_v=40, alignment=2, outline=7
        )
        check(
            "banda del hook in alto parte dal margine",
            abs(top[1] - 269 / 1920) < 0.002 and top[3] >= 0.05,
            str(top),
        )
        check(
            "banda in basso finisce al margine inferiore",
            abs((bottom[1] + bottom[3]) - (1920 - 40) / 1920) < 0.004,
            str(bottom),
        )

        # 3. Zona sicura: il hook non entra nella UI bassa.
        vertical = subs.hook_placement("9:16", 1080, 1920)
        horizontal = subs.hook_placement("16:9", 1920, 1080)
        cta = subs.cta_placement("9:16", 1080, 1920)
        check(
            "hook verticale: terzo alto, margine >= 0.13 H",
            vertical["placement"] == "top_third"
            and vertical["alignment"] == 8
            and vertical["margin_v"] >= round(subs.SAFE_BOTTOM_RATIO * 1920),
            str(vertical),
        )
        check(
            "hook orizzontale: in basso ma sopra la linea sicura",
            horizontal["placement"] == "bottom_safe"
            and horizontal["margin_v"] >= 36,
            str(horizontal),
        )
        check(
            "CTA verticale: sopra la UI (>= 0.13 H e >= inset)",
            cta["placement"] == "lower_safe"
            and cta["margin_v"] >= round(subs.SAFE_BOTTOM_RATIO * 1920)
            and cta["margin_v"] >= 660,
            str(cta),
        )

        # 4. Scelta per misura: fondo gia' buono -> stile di oggi, zero contorni.
        good = subs.choose_hook_legibility(
            dark, "GUARDA COSA SUCCEDE", out_w=1080, out_h=1920,
            font="Antonio", size=106, margin_v=269, alignment=8,
            workdir=work / "good",
        )
        check(
            "fondo gia' leggibile: nessun ritocco, stile di oggi",
            good["chosen"] == "nessun ritocco"
            and good["params"]["outline"] == subs._scaled(4, 1920, 1080)
            and good["measured"] >= subs.LEGIBILITY_MIN,
            f"{good['chosen']} {good['measured']}",
        )
        check(
            "fondo gia' leggibile: la prova conferma il numero",
            good["resolved"] and good["probe_ratio"] >= subs.LEGIBILITY_MIN,
            str(good["probe_ratio"]),
        )

        # 5. Fondo chiaro: il bianco non si legge, serve il contorno.
        outline_choice = subs.choose_hook_legibility(
            light, "GUARDA COSA SUCCEDE", out_w=1080, out_h=1920,
            font="Antonio", size=106, margin_v=269, alignment=8,
            workdir=work / "outline",
        )
        check(
            "fondo chiaro: il bianco misurato e' sotto 4.5",
            outline_choice["measured"] < subs.LEGIBILITY_MIN,
            str(outline_choice["measured"]),
        )
        check(
            "fondo chiaro: scelto il contorno crescente",
            outline_choice["chosen"].startswith("outline")
            and outline_choice["params"]["outline"] > subs._scaled(4, 1920, 1080),
            f"{outline_choice['chosen']} outline={outline_choice['params']['outline']}",
        )
        check(
            "fondo chiaro: il contorno supera 4.5 alla prova",
            outline_choice["resolved"]
            and outline_choice["probe_ratio"] >= subs.LEGIBILITY_MIN,
            str(outline_choice["probe_ratio"]),
        )

        # 6. Titolo enorme: un anello di 6 px non basta, serve la placca.
        plate_choice = subs.choose_hook_legibility(
            light, "GUARDA COSA SUCCEDE", out_w=1080, out_h=1920,
            font="Antonio", size=300, margin_v=269, alignment=8,
            workdir=work / "plate",
        )
        check(
            "titolo enorme: scelta la placca al 65%",
            plate_choice["chosen"].startswith("placca")
            and plate_choice["params"]["border_style"] == 3
            and plate_choice["probe_ratio"] >= subs.LEGIBILITY_MIN,
            f"{plate_choice['chosen']} {plate_choice['probe_ratio']}",
        )
        unmeasured = subs.choose_hook_legibility(
            None, "HOOK", out_w=1080, out_h=1920, font="Antonio",
            size=106, margin_v=269, alignment=8, workdir=work / "none",
        )
        check(
            "fondo non campionabile: placca prudente e nessun numero finto",
            unmeasured["measured"] is None
            and not unmeasured["resolved"]
            and unmeasured["params"]["border_style"] == 3,
            str(unmeasured["chosen"]),
        )

        # 7. Nessuna regressione: l'ASS di default resta quello di oggi.
        default_hook = subs.write_hook_ass(
            work / "hook_default.ass", "guarda questo",
            out_w=1080, out_h=1920, start_s=1.0, duration_s=3.0,
        )
        default_style = _style_line(Path(default_hook))
        check(
            "write_hook_ass 1920 invariato (1,7,4, MarginV 40)",
            ",1,7,4,2,60,60,40,1" in default_style,
            default_style,
        )
        chosen_hook = subs.write_hook_ass(
            work / "hook_chosen.ass", "guarda cosa succede",
            out_w=1080, out_h=1920,
            margin_v=vertical["margin_v"],
            alignment=vertical["alignment"],
            outline=outline_choice["params"]["outline"],
            shadow=outline_choice["params"]["shadow"],
        )
        chosen_style = _style_line(Path(chosen_hook))
        check(
            "write_hook_ass porta contorno e margine scelti",
            ",1,9,4,8,60,60,269,1" in chosen_style,
            chosen_style,
        )

        # 8. La verifica ferma la consegna.
        bad = work / "bad.mp4"
        _bad_master(bad)
        report = {
            "fps": 30.0,
            "timeline_sec": 2.0,
            "text_required": True,
            "texts": ["TITOLO"],
            "contrast_at": 1.0,
            "contrast_region": [0.0, 0.0, 1.0, 0.35],
            "master": str(bad),
        }
        contrast = verify.check_contrast(bad, report)
        blocked = auto.apply_verification(
            dict(report, delivered=True),
            {"ok": contrast["passed"], "checks": [contrast]},
        )
        check(
            "contrasto grigio su grigio: check fallito sotto 4.5",
            not contrast["passed"]
            and contrast["measured"]["ratio"] < subs.LEGIBILITY_MIN,
            str(contrast["measured"]),
        )
        check(
            "contrasto fallito: delivered False e outcome failed",
            blocked["delivered"] is False
            and blocked["outcome"] == "failed"
            and blocked["outcome_suggested"] == "failed",
            str(blocked.get("outcome")),
        )
        check(
            "contrasto fallito: motivo in chiaro, master ancora sul disco",
            any("illeggibile" in str(item) for item in blocked.get("failures") or [])
            and bad.is_file(),
            str(blocked.get("failures")),
        )
        passed_report = dict(report, delivered=True)
        auto.apply_verification(
            passed_report,
            {"ok": True, "checks": [{"id": "contrast", "passed": True, "measured": {"ratio": 12.0}}]},
        )
        check(
            "contrasto passato: la consegna resta",
            passed_report["delivered"] is True,
            str(passed_report.get("delivered")),
        )

        # 9. Senza titolo il contrasto non si applica.
        no_text = verify.check_contrast(
            bad, {"text_required": False, "contrast_at": 1.0}
        )
        check(
            "senza testo: contrasto non applicabile, nessun falso negativo",
            no_text["passed"] is True
            and no_text["measured"].get("applicable") is False,
            no_text["why"],
        )

        # 10. Senza testo non si scrive nessun ASS e il master resta identico.
        notext_work = work / "notext"
        notext_work.mkdir()
        master = notext_work / "master.mp4"
        master.write_bytes(b"master-byte-identico")
        before = hashlib.sha256(master.read_bytes()).hexdigest()
        captions = str(notext_work / "captions.ass")
        section, render_layer = auto._run_typography(
            notext_work,
            object(),
            "reel verticale, ritmo serrato",
            {"family": "", "fonts_dir": ""},
            captions,
        )
        after = hashlib.sha256(master.read_bytes()).hexdigest()
        check(
            "brief senza testo: nessun ASS scritto",
            section.get("applied") is False
            and not list(notext_work.glob("*.ass")),
            str(section.get("reason")),
        )
        check(
            "brief senza testo: master invariato byte per byte (sha)",
            before == after,
            f"{before[:16]}...",
        )
        check(
            "brief senza testo: il livello di render resta quello di prima",
            render_layer == captions,
            str(render_layer),
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
