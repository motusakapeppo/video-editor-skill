"""vx.auto: il cancello dei font aspetta una scelta, ma non all'infinito.

Nessuna rete e nessun render: ``fontproposal.propose`` e' sostituito da una
proposta finta, cosi' si prova solo il comportamento del cancello.

    python tests/test_font_gate.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import auto, cli  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

CALLS = []


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:70} {detail}")


def skip(label: str, detail: str = "") -> None:
    global SKIP
    SKIP += 1
    print(f"{'SKIP':4} {label:70} {detail}")


def fake_propose(workdir, requirement, energy, *, allow_network=False, **kwargs):
    """Una proposta valida senza misurare ne' scaricare niente."""
    CALLS.append(
        {
            "requirement": requirement,
            "energy": energy,
            "allow_network": allow_network,
            "workdir": str(workdir),
        }
    )
    families = (("Inter", 0.91), ("Oswald", 0.72))
    payload = {
        "schema": "vx.fontproposal/1",
        "requirement": str(requirement),
        "pool": "local",
        "chosen": families[0][0],
        "candidates": [
            {
                "family": family,
                "licence": "commercial",
                "source": "bundle",
                "downloaded": False,
                "file": str(Path(workdir) / "fonts" / f"{family}.ttf"),
                "score": score,
                "because": "prova",
            }
            for family, score in families
        ],
    }
    (Path(workdir) / auto.FONT_PROPOSAL_NAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return payload


def write_choice(workdir: Path, family: str) -> None:
    (Path(workdir) / auto.FONT_CHOICE_NAME).write_text(
        json.dumps({"schema": "vx.fontchoice/1", "family": family, "from": "ui"}),
        encoding="utf-8",
    )


def test_existing_choice() -> None:
    print("\n-- scelta gia' presente: nessuna attesa")
    CALLS.clear()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        write_choice(workdir, "Oswald")
        seen = []
        started = time.monotonic()
        section = auto.font_gate(
            workdir,
            requirement="reel aggressivo",
            energy=0.8,
            wait=30.0,
            gate=True,
            propose=fake_propose,
            notify=seen.append,
        )
        elapsed = time.monotonic() - started
        check("usa la famiglia scelta, non il chosen", section["family"] == "Oswald", section["family"])
        check("selected_by == user", section["selected_by"] == "user", section["selected_by"])
        check("nessuna attesa (parete < 1 s)", elapsed < 1.0, f"{elapsed:.3f}s")
        check("waited_sec quasi zero", float(section["waited_sec"]) < 0.5, section["waited_sec"])
        check("propose chiamato una volta", len(CALLS) == 1, len(CALLS))
        check("candidati con famiglia e punteggio", [
            item["family"] for item in section["candidates"]
        ] == ["Inter", "Oswald"] and all("score" in item for item in section["candidates"]))
        check(
            "licenza, fonte e pool dichiarati",
            section["licence"] == "commercial"
            and section["source"] == "bundle"
            and section["downloaded"] is False
            and section["pool"] == "local",
            section,
        )
        check("nessun errore", "error" not in section)
        check(
            "la narrazione dice la famiglia",
            any("Oswald" in line and "scelt" in line for line in seen),
            seen,
        )


def test_choice_arrives() -> None:
    print("\n-- scelta che arriva durante l'attesa")
    CALLS.clear()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        seen = []
        timer = threading.Timer(0.15, write_choice, args=(workdir, "Oswald"))
        timer.start()
        started = time.monotonic()
        section = auto.font_gate(
            workdir,
            requirement="reel aggressivo",
            energy=0.8,
            wait=5.0,
            gate=True,
            view_url="http://127.0.0.1:9999/",
            propose=fake_propose,
            notify=seen.append,
        )
        elapsed = time.monotonic() - started
        timer.join()
        check("la scelta arrivata vince", section["family"] == "Oswald", section["family"])
        check("selected_by == user", section["selected_by"] == "user", section["selected_by"])
        check("ha aspettato il file, non 5 s", 0.5 <= elapsed < 4.0, f"{elapsed:.3f}s")
        check(
            "l'avviso dice dove scegliere",
            any("Scegli il font nella vista: http://127.0.0.1:9999/" in line for line in seen),
            seen,
        )


def test_timeout() -> None:
    print("\n-- allo scadere si usa chosen, e lo si dichiara")
    CALLS.clear()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        seen = []
        started = time.monotonic()
        section = auto.font_gate(
            workdir,
            requirement="reel aggressivo",
            energy=0.8,
            wait=1.0,
            gate=True,
            propose=fake_propose,
            notify=seen.append,
        )
        elapsed = time.monotonic() - started
        check("ha aspettato circa un secondo", elapsed >= 0.9, f"{elapsed:.3f}s")
        check(
            "selected_by == auto_after_timeout",
            section["selected_by"] == "auto_after_timeout",
            section["selected_by"],
        )
        check("usa il chosen della proposta", section["family"] == "Inter", section["family"])
        check("waited_sec dichiarato", float(section["waited_sec"]) >= 0.9, section["waited_sec"])
        check("i candidati restano nel report", len(section["candidates"]) == 2)
        check(
            "la narrazione dice timeout e famiglia",
            any("Inter" in line and "automatico" in line for line in seen),
            seen,
        )


def test_no_gate() -> None:
    print("\n-- --no-font-gate: nessuna attesa")
    CALLS.clear()
    with tempfile.TemporaryDirectory() as tmp:
        started = time.monotonic()
        section = auto.font_gate(
            Path(tmp),
            requirement="reel aggressivo",
            energy=0.8,
            wait=30.0,
            gate=False,
            propose=fake_propose,
        )
        elapsed = time.monotonic() - started
        check("nessuna attesa", elapsed < 0.5, f"{elapsed:.3f}s")
        check("selected_by == auto", section["selected_by"] == "auto", section["selected_by"])
        check("usa il chosen", section["family"] == "Inter", section["family"])


def test_propose_fails() -> None:
    print("\n-- propose solleva: il montaggio continua col preset")
    CALLS.clear()

    def broken(*args, **kwargs):
        raise RuntimeError("rete assente")

    with tempfile.TemporaryDirectory() as tmp:
        started = time.monotonic()
        section = auto.font_gate(
            Path(tmp),
            requirement="reel aggressivo",
            energy=0.8,
            wait=30.0,
            gate=True,
            propose=broken,
        )
        elapsed = time.monotonic() - started
        check("nessuna attesa dopo l'errore", elapsed < 0.5, f"{elapsed:.3f}s")
        check("family vuota: resta il preset", section["family"] == "", section["family"])
        check("selected_by == auto", section["selected_by"] == "auto", section["selected_by"])
        error = str(section.get("error") or "")
        check("font.error valorizzato in italiano", "proposta font non disponibile" in error, error)
        check("nessun candidato inventato", section["candidates"] == [])


def test_instruction_without_server() -> None:
    print("\n-- senza server l'avviso dice la cartella del JSON")
    with tempfile.TemporaryDirectory() as tmp:
        seen = []
        auto.font_gate(
            Path(tmp),
            requirement="reel",
            energy=0.5,
            wait=0.01,
            gate=True,
            propose=fake_propose,
            notify=seen.append,
        )
        check(
            "l'avviso nomina font_choice.json e la workdir",
            any(
                auto.FONT_CHOICE_NAME in line and str(tmp) in line
                for line in seen
            ),
            seen,
        )


def test_context_from_plan() -> None:
    print("\n-- richiesta ed energia dai numeri del piano")

    class Track:
        clips = [object()] * 8

    class Plan:
        duration_sec = 10.0
        aspect_ratio = "9:16"
        video_tracks = [Track()]

    payload = {
        "execution_context": {
            "creative_rationale": {
                "genre_detected": "tiktok_promo",
                "pacing": {"bpm_target": 140, "cut_policy": "beat_synced"},
                "color_palette": {"type": "duotone"},
            }
        }
    }
    requirement = auto.font_requirement("reel palestra", payload, Plan())
    check("la richiesta contiene il prompt", "reel palestra" in requirement, requirement)
    check("la richiesta contiene i bpm", "140 bpm" in requirement, requirement)
    check("la richiesta contiene il look vintage", "vintage" in requirement, requirement)
    energy = auto.font_energy(payload, Plan())
    check("energia 0..1 e alta col ritmo dichiarato", 0.5 < energy <= 1.0, energy)


def test_cli_flags() -> None:
    print("\n-- flag di vx auto")
    parser = cli.build_parser()
    base = ["auto", "--prompt", "p", "--assets", ".", "--out", "o.mp4"]
    plain = parser.parse_args(base)
    check("default: rete decisa piu' tardi (None)", plain.font_network is None, plain.font_network)
    check("default: attesa decisa piu' tardi (None)", plain.font_wait is None, plain.font_wait)
    check("default: cancello acceso", plain.no_font_gate is False)
    waited = parser.parse_args(base + ["--font-wait", "1"])
    check("--font-wait 1 vale 1.0 s", waited.font_wait == 1.0, waited.font_wait)
    enabled = parser.parse_args(base + ["--font-network"])
    check("--font-network accende la rete", enabled.font_network is True, enabled.font_network)
    disabled = parser.parse_args(base + ["--no-font-network"])
    check("--no-font-network la spegne", disabled.font_network is False, disabled.font_network)
    closed = parser.parse_args(base + ["--no-font-gate"])
    check("--no-font-gate chiude il cancello", closed.no_font_gate is True)


def main() -> int:
    test_existing_choice()
    test_choice_arrives()
    test_timeout()
    test_no_gate()
    test_propose_fails()
    test_instruction_without_server()
    test_context_from_plan()
    test_cli_flags()
    print(f"\n{PASS} PASS, {FAIL} FAIL, {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
