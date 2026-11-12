"""La scaletta composta arriva dal server vero e si vede in pagina.

    python tests/test_scaletta_view.py

V1  `/api/state` porta `composition`, `sections` e `movements` letti dal report;
V2  la pagina rende la sezione SCALETTA coi numeri del **fatto** (2,40 s, prima
    4,65 s, 3 → 4) e coi declini;
V3  la riga CLIP cita i movimenti del corpo;
V4  un lavoro senza `composition` non mostra la sezione (niente guscio vuoto).

Il server è quello vero (`serve.Server`). Il browser è Chromium via Playwright:
se manca, il test lo dichiara e salta solo la parte visiva, mai le asserzioni
sul payload.
"""

from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import serve  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=15) as response:
        return response.read()


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:60} {why}")


def composition() -> dict:
    return {
        "schema": "vx.composition/1",
        "proposed": {
            "hook_seconds": 2.4,
            "hook_asset": "clip.mp4",
            "broll_order": ["b_sharp.png", "a_soft.png"],
            "broll_placement": "nearest_cut",
            "tail_seconds": 1.5,
        },
        "rules": {"hook": {"why": "apertura nitida"}, "broll": {"why": "nitidezza"}},
        "declined": [
            {"id": "broll", "why": "una sola nitidezza misurata su due"},
        ],
        "applied": {
            "hook_seconds": 2.4,
            "hook_baseline_seconds": 4.65,
            "hook_applied": True,
            "hook_asset": "clip.mp4",
            "hook_src_in": 0.6,
            "hook_take_moved": True,
            "hook_take_honored": False,
            "shots_before": 3,
            "shots_after": 4,
            "delta": 1,
            "broll_slots_before": [2.0, 4.0],
            "broll_slots_after": [1.5, 3.0],
            "broll_order_before": ["a_soft.png", "b_sharp.png"],
            "broll_order_after": ["b_sharp.png", "a_soft.png"],
            "broll_order_changed": True,
            "broll_moved": True,
            "broll_moved_count": 2,
            "broll_placement": "nearest_cut",
            "tail_sec": 1.5,
            "broll_last_out": 4.5,
            "broll_respects_tail": True,
            "survived": True,
            "shots_final": 4,
        },
        "survival": {"hook_seconds": 2.4, "expected": 2.4, "preserved": True, "shots_final": 4},
        "why": "hook e b-roll",
    }


def movements() -> list:
    return [
        {"from": 0.0, "to": 4.95, "shots": 3, "seconds": 4.95, "density": 0.6061},
        {"from": 4.95, "to": 12.009, "shots": 1, "seconds": 7.059, "density": 0.1417},
    ]


def report_block() -> dict:
    return {
        "ok": True,
        "outcome": "delivered",
        "delivered": True,
        "timeline_sec": 12.009,
        "master": "master.mp4",
        "composition": composition(),
        "movements": movements(),
        "presence": {
            "id": "presence",
            "question": "Vuoi scegliere tu, o faccio tutto io?",
            "value": "automatico",
            "source": "prompt",
            "answered_by": "prompt",
            "asked": False,
            "waited_sec": 0.0,
            "why": "detto nella richiesta («fai tutto tu»)",
        },
        # Quello che il lavoro ha scartato: il report lo porta e la pagina deve
        # mostrarlo nel blocco FUORI, non lasciarlo nel file.
        "warnings": ["coda non raggiunta: 0,4 s"],
        "assets": {
            "total": 4,
            "selected": 2,
            "rejected": 1,
            "ignored": [
                {"asset_id": "notes.txt", "reason": "unknown_extension", "suffix": ".txt"}
            ],
        },
        "rejected": [
            {
                "asset_id": "buio.mp4",
                "rejection_reason": "TOO_DARK",
                "details": {"luma": 0.03},
            }
        ],
        "bridge": {
            "enabled": True,
            "cuts": 2,
            "cuts_removed_sec": 1.4,
            "not_applied": 2,
            "not_applied_reasons": {
                "broll_below_threshold": {"count": 2, "seconds": 0.0, "words": 0}
            },
        },
        "timeline": {"tail": {"requested": 12.0, "actual": 11.6, "reachable": False}},
        "plan": {"excluded": [{"clip_id": "c9", "why": "sotto la soglia di nitidezza"}]},
        "resources": {"mode": "balanced", "workers": 4, "ffmpeg_threads": 8},
        # Le misure e i controlli: la pagina li mostra in MISURE.
        "verification": {
            "ok": True,
            "checks": [
                {
                    "id": "duration",
                    "ok": True,
                    "measured": {"value": 12.0},
                    "expected": 12.0,
                    "why": "tolleranza un frame",
                }
            ],
        },
        "guard": {"ok": True, "violations": [], "warnings": ["upscale 1.4x"]},
        "beat": {"bpm": 120.0, "clips": 4, "cuts": 3, "density_per_sec": 0.25},
        "timings": {"triage": 1.2, "render": 18.4},
        "sections": {
            "ok": True,
            "total": 6,
            "filled": 4,
            "sections": [
                {"id": "hook", "state": "filled", "capability": "decide",
                 "binding": {"seconds": 2.4}, "why": "apertura scelta per nitidezza 82.2",
                 "composed": {"seconds": 2.4, "baseline_seconds": 4.65}},
                {"id": "beat", "state": "filled", "capability": "coherence",
                 "binding": {"shots": 4, "seconds": 12.009, "movements": movements()},
                 "why": "4 inquadrature; 2 movimenti"},
                {"id": "payoff", "state": "absent", "capability": "bridge",
                 "binding": None, "why": "ponte senza tagli"},
                {"id": "cta", "state": "filled", "capability": "typography",
                 "binding": {"texts": 1}, "why": "una chiusura"},
                {"id": "proof", "state": "absent", "capability": "faces",
                 "binding": None, "why": "nessun volto"},
                {"id": "broll", "state": "filled", "capability": "decide",
                 "binding": {"assets": 2}, "why": "due b-roll"},
            ],
            "gaps": [],
        },
    }


def write_job(root: Path, report: dict | None) -> Path:
    job = root / "lavoro"
    job.mkdir(parents=True, exist_ok=True)
    (job / "progress.json").write_text(
        json.dumps(
            {
                "prompt": "un reel di 12 secondi",
                "clips": 2,
                "percent": 100.0,
                "current": "done",
                "note": "",
                "phases": [],
                "journal": [],
            }
        ),
        encoding="utf-8",
    )
    (job / "master.mp4").write_bytes(b"\x00" * 64)
    if report is not None:
        (job / "auto_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
    return job


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-scaletta-") as raw:
        root = Path(raw)
        job = write_job(root, report_block())
        server = serve.Server(job).start()
        try:
            state = json.loads(get(server.url + "api/state").decode("utf-8"))
            check(
                "V1 /api/state porta composition",
                isinstance(state.get("composition"), dict)
                and state["composition"].get("schema") == "vx.composition/1",
                (state.get("composition") or {}).get("schema"),
            )
            check(
                "V1 e sections",
                isinstance(state.get("sections"), dict)
                and len(state["sections"].get("sections") or []) == 6,
                len((state.get("sections") or {}).get("sections") or []),
            )
            check(
                "V1 e movements",
                state.get("movements") == movements(),
                state.get("movements"),
            )
            check(
                "V1 e quello che è rimasto fuori",
                (state.get("out_of_frame") or {}).get("empty") is False
                and (state.get("out_of_frame") or {}).get("cuts") == 2,
                (state.get("out_of_frame") or {}).get("empty"),
            )
            check(
                "V1 e le misure",
                (state.get("measures") or {}).get("empty") is False
                and (state.get("measures") or {}).get("beat", {}).get("bpm") == 120.0,
                sorted(state.get("measures") or {}),
            )

            try:
                from playwright.sync_api import sync_playwright
            except Exception as exc:  # noqa: BLE001 - senza browser si dichiara
                skip("V2/V3 la pagina", f"playwright non disponibile: {exc}")
            else:
                try:
                    browser = None
                    with sync_playwright() as pw:
                        for launcher in ("chrome", None):
                            try:
                                browser = (
                                    pw.chromium.launch(channel=launcher, headless=True)
                                    if launcher
                                    else pw.chromium.launch(headless=True)
                                )
                                break
                            except Exception:  # noqa: BLE001
                                continue
                        if browser is None:
                            skip("V2/V3 la pagina", "nessun Chromium/Chrome avviabile")
                        else:
                            page = browser.new_page(viewport={"width": 1280, "height": 1400})
                            page.goto(server.url, wait_until="networkidle")
                            page.wait_for_timeout(1200)
                            section = page.locator("#scaletta").first
                            check("V2 la sezione SCALETTA è visibile", section.is_visible())
                            text = section.inner_text() if section.count() else ""
                            check(
                                "V2 mostra il fatto: 2,40 s e prima 4,65 s",
                                "2,40" in text and "4,65" in text,
                                text[:120],
                            )
                            check(
                                "V2 e il conto delle clip 3 → 4",
                                "3" in text and "4" in text,
                                text[:80],
                            )
                            check(
                                "V3 la riga CLIP cita i movimenti",
                                "moviment" in text.lower(),
                                text[:160],
                            )
                            check(
                                "V2 i declini non si nascondono",
                                "NON COMPOSTE" in text.upper() and "nitidezza" in text,
                                text[-160:],
                            )
                            fuori = page.locator("#fuori").first
                            check("V5 il blocco FUORI è in pagina", fuori.count() > 0 and fuori.is_visible())
                            fuori_text = fuori.inner_text() if fuori.count() else ""
                            check(
                                "V5 nomina i file ignorati e gli asset scartati",
                                "notes.txt" in fuori_text
                                and "buio.mp4" in fuori_text
                                and "TOO_DARK" in fuori_text,
                                fuori_text[:200],
                            )
                            check(
                                "V5 dice i tagli e i secondi tolti",
                                "1,4" in fuori_text or "1.4" in fuori_text,
                                fuori_text[:200],
                            )
                            misure = page.locator("#misure").first
                            check(
                                "V6 il blocco MISURE è in pagina",
                                misure.count() > 0 and misure.is_visible(),
                            )
                            misure_text = misure.inner_text() if misure.count() else ""
                            check(
                                "V6 mostra la verifica con misurato e atteso",
                                "duration" in misure_text
                                and "12" in misure_text
                                and ("atteso" in misure_text.lower() or "attesa" in misure_text.lower()),
                                misure_text[:200],
                            )
                            check(
                                "V6 e il battito con i suoi numeri",
                                "120" in misure_text and "bpm" in misure_text.lower(),
                                misure_text[:200],
                            )
                            riga = page.locator("#presenceLine").first
                            check(
                                "V7 la modalità è in pagina, con la sua fonte",
                                riga.count() > 0
                                and riga.is_visible()
                                and "fai tutto tu" in riga.inner_text()
                                and "prompt" in riga.inner_text(),
                                riga.inner_text() if riga.count() else "",
                            )
                            browser.close()
                except Exception as exc:  # noqa: BLE001 - il browser non è il soggetto
                    check("V2/V3 la pagina si apre", False, f"{type(exc).__name__}: {exc}")

            empty_job = write_job(root / "vuoto", None)
            empty_server = serve.Server(empty_job).start()
            try:
                empty_state = json.loads(
                    get(empty_server.url + "api/state").decode("utf-8")
                )
                check(
                    "V4 senza report: nessuna composizione nel payload",
                    empty_state.get("composition") is None
                    and empty_state.get("movements") is None,
                    (empty_state.get("composition"), empty_state.get("movements")),
                )
                check(
                    "V4 senza report: niente da mettere in FUORI",
                    (empty_state.get("out_of_frame") or {}).get("empty") is True,
                    empty_state.get("out_of_frame"),
                )
            finally:
                empty_server.stop()
        finally:
            server.stop()

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
