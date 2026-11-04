"""La durata chiesta e' un numero di frame, e la consegna dice la verita'.

D-28:

* la timeline si aggancia ai frame interi (N * fps arrotondato) e la coda
  (l'ultima battuta) si accorcia o si allunga per arrivarci, dichiarata in
  ``report["timeline"]["tail"]``;
* se il materiale non arriva a N si dichiara il massimo raggiungibile e la
  verifica della durata fallisce: non si finge una timeline che non c'e';
* ``verification.ok == false`` implica ``delivered: false``, ``outcome:
  "failed"`` e l'elenco dei controlli falliti con la tolleranza usata;
* ``outcome`` e' sempre valorizzato: ``delivered`` / ``failed`` / ``incomplete``;
* nessuna regressione quando la durata e' gia' esatta: piano identico (sha).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, contract, edl_plan, verify  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def payload(seconds: float, clips: int, target: float, *, beat: bool) -> dict:
    selected = [
        {
            "asset_id": f"a{index}.mp4",
            "role": "a_roll",
            "target_duration_sec": target,
        }
        for index in range(clips)
    ]
    data = {
        "contract_version": contract.VERSION,
        "scene": {
            "aspect_ratio": "9:16",
            "resolution": [1080, 1920],
            "engine": "pillow",
            "duration_sec": seconds,
        },
        "asset_triage": {"selected_assets": selected, "rejected_assets": []},
    }
    if beat:
        data["beat_grid"] = {
            "mode": "on",
            "enabled": True,
            "bpm": 120.0,
            "source": "prompt",
            "subdivision": 1,
        }
    return data


def plan_for(
    seconds: float,
    *,
    fps: float,
    clips: int = 2,
    media: float = 60.0,
    target: float = 60.0,
    beat: bool = False,
):
    sources = {
        f"a{index}.mp4": edl_plan.SourceRef(
            path=f"a{index}.mp4", sha256="0" * 64, duration=media
        )
        for index in range(clips)
    }
    media_info = {f"a{index}.mp4": {"duration": media} for index in range(clips)}
    return contract.payload_to_edl_plan(
        payload(seconds, clips, target, beat=beat),
        sources,
        media_info,
        fps=fps,
    )


def digest(plan) -> str:
    return hashlib.sha256(
        json.dumps(plan.to_dict(), sort_keys=True).encode("utf-8")
    ).hexdigest()


def main() -> int:
    print("\n1. la durata del piano cade su frame interi (griglia di battuta)")
    p25 = plan_for(15, fps=25.0, beat=True)
    check("15 s a 25 fps: durata 15.000", abs(p25.duration_sec - 15.0) < 1e-6, p25.duration_sec)
    check("15 s a 25 fps: 375 frame", auto.frame_count(p25.duration_sec, 25.0) == 375, p25.duration_sec * 25)
    p30 = plan_for(15, fps=30.0, beat=True)
    check("15 s a 30 fps: durata 15.000", abs(p30.duration_sec - 15.0) < 1e-6, p30.duration_sec)
    check("15 s a 30 fps: 450 frame", auto.frame_count(p30.duration_sec, 30.0) == 450, p30.duration_sec * 30)
    p_dec = plan_for(10.01, fps=30.0, beat=True)
    check(
        "10.01 s a 30 fps scende al frame 300 (10.000 s)",
        abs(p_dec.duration_sec - 10.0) < 1e-6
        and auto.frame_count(p_dec.duration_sec, 30.0) == 300,
        p_dec.duration_sec,
    )

    print("\n2. coda: si estende/accorcia l'ultima battuta, mai oltre il materiale")
    grown = plan_for(20, fps=25.0, clips=2, target=5.0)
    check(
        "richiesta 20 s con target 5+5: la coda porta la timeline a 20.000",
        abs(grown.duration_sec - 20.0) < 1e-6,
        grown.duration_sec,
    )
    short = plan_for(60, fps=25.0, clips=2, media=10.0, target=60.0, beat=True)
    check(
        "richiesta 60 s su materiale da 20 s: massimo dichiarato 20.000",
        abs(short.duration_sec - 20.0) < 1e-6,
        short.duration_sec,
    )
    check(
        "il massimo raggiungibile e' un numero intero di frame",
        auto.frame_count(short.duration_sec, 25.0) == 500,
        short.duration_sec * 25,
    )

    print("\n3. nessuna regressione quando la durata e' gia' esatta")
    exact = plan_for(120, fps=30.0)
    free = plan_for(0, fps=30.0)
    check(
        "richiesta 120 s == piano senza richiesta: sha del piano identico",
        digest(exact) == digest(free),
        digest(exact)[:12],
    )

    print("\n4. timeline.tail nel report")
    reached = auto.timeline_declaration(15.0, p25.duration_sec, 25.0, material_sec=15.232)
    check(
        "timeline.tail porta requested/actual/frames",
        isinstance(reached, dict)
        and {"requested", "actual", "frames"} <= set(reached["tail"]),
        reached,
    )
    check(
        "timeline.tail dichiara 375 frame e la coda raggiunta",
        reached["tail"]["frames"] == 375 and reached["tail"]["reachable"] is True,
        reached["tail"],
    )
    missed = auto.timeline_declaration(60.0, short.duration_sec, 25.0, material_sec=20.0)
    check(
        "quando il materiale non basta il tail dice il massimo raggiungibile",
        missed["tail"]["reachable"] is False
        and "massimo raggiungibile" in missed["tail"]["note"],
        missed["tail"]["note"],
    )

    print("\n5. la tolleranza e' un frame, a 25 e a 30 fps")
    real_duration = verify._duration_of
    try:
        verify._duration_of = lambda master: 15.039
        ok25 = verify.check_duration("x", {"duration_requested_sec": 15.0, "fps": 25.0})
        verify._duration_of = lambda master: 15.08
        bad25 = verify.check_duration("x", {"duration_requested_sec": 15.0, "fps": 25.0})
        verify._duration_of = lambda master: 15.033
        ok30 = verify.check_duration("x", {"duration_requested_sec": 15.0, "fps": 30.0})
    finally:
        verify._duration_of = real_duration
    check(
        "25 fps: 39 ms di scarto passa (un frame = 40 ms)",
        ok25["passed"] is True and abs(ok25["measured"]["tolerance_sec"] - 0.04) < 1e-6,
        ok25["measured"],
    )
    check(
        "25 fps: 80 ms di scarto fallisce",
        bad25["passed"] is False,
        bad25["measured"],
    )
    check(
        "30 fps: 33 ms di scarto passa (un frame = 33.3 ms)",
        ok30["passed"] is True
        and abs(ok30["measured"]["tolerance_sec"] - 1.0 / 30.0) < 1e-4,
        ok30["measured"],
    )

    print("\n6. verification.ok == false ferma la consegna e dichiara perche'")
    failed_check = {
        "id": "duration",
        "passed": False,
        "measured": {"duration_sec": 15.1, "delta_sec": 0.1},
        "expected": {"requested_sec": 15.0, "tolerance_frames": 1.0, "fps": 25.0},
        "why": "durata 15.100s fuori di 0.100s da 15.000s",
    }
    report = {"ok": True, "delivered": True, "master": "x"}
    auto.apply_verification(report, {"ok": False, "checks": [failed_check]})
    check("delivered diventa false", report["delivered"] is False, report.get("delivered"))
    check("outcome diventa failed", report.get("outcome") == "failed", report.get("outcome"))
    check(
        "il motivo nomina il check e la tolleranza usata",
        any(
            "duration" in str(item) and "tolleranza" in str(item)
            for item in report.get("failures") or []
        ),
        report.get("failures"),
    )
    check(
        "verification porta l'elenco dei controlli bloccanti",
        report["verification"].get("blocking") == ["duration"],
        report["verification"].get("blocking"),
    )
    passed = {"ok": True, "delivered": True, "master": "x"}
    auto.apply_verification(
        passed,
        {"ok": True, "checks": [{"id": "duration", "passed": True}]},
    )
    check(
        "verdetto verde: delivered resta true e l'esito e' delivered",
        passed["delivered"] is True and auto.final_outcome(passed) == "delivered",
        auto.final_outcome(passed),
    )

    print("\n7. outcome sempre valorizzato nei tre casi")
    check(
        "consegna fermata -> failed",
        auto.final_outcome({"ok": True, "delivered": False}) == "failed",
        auto.final_outcome({"ok": True, "delivered": False}),
    )
    check(
        "lavoro interrotto prima della decisione -> incomplete",
        auto.final_outcome({"ok": True}) == "incomplete",
        auto.final_outcome({"ok": True}),
    )
    check(
        "una verifica rossa non lascia outcome delivered",
        auto.final_outcome(
            {"ok": True, "delivered": True, "verification": {"ok": False}}
        )
        == "failed",
        "",
    )

    print("\n8. il consultivo non blocca, e il report lo dice")
    advisory = {"ok": False, "checks": [{"id": "weak_open", "passed": False, "advisory": True}]}
    soft = {"ok": True, "delivered": True, "master": "x"}
    auto.apply_verification(soft, advisory)
    check(
        "weak_open consultivo: la consegna resta",
        soft["delivered"] is True and auto.final_outcome(soft) == "delivered",
        soft["verification"].get("advisory"),
    )
    check(
        "weak_open consultivo: verification.ok riallineato ai bloccanti",
        soft["verification"].get("ok") is True,
        soft["verification"].get("ok"),
    )
    guard_report = {"ok": True, "master": "x"}
    auto.apply_guard(
        guard_report,
        {"ok": False, "skipped": False, "violations": [{"code": "G2"}]},
    )
    check(
        "guardia rossa -> consegna fermata e outcome failed",
        guard_report["delivered"] is False
        and auto.final_outcome(guard_report) == "failed",
        guard_report.get("outcome"),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
