"""Il gate trattamento->filtro non deve passare con un token che c'e' sempre.

python tests/test_treatment_ledger.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, render  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def treatment(name: str, **params: object) -> dict:
    return {"name": name, "enabled": True, "params": params}


def media() -> dict:
    return {
        "path": "x.mp4",
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "has_audio": True,
        "duration": 6.0,
    }


def plan(treatments: list, crop: dict | None = None) -> contract.EditPlan:
    built = contract.EditPlan(
        output={"aspect": "9:16", "width": 1080, "height": 1920},
        treatments=treatments,
        cuts=[{"in": 0.0, "out": 3.0}],
        render={"precision": "full"},
    )
    if crop is not None:
        built.render["crop"] = crop
    return built


def payload(built: contract.EditPlan, script: str, **kwargs: object) -> dict | None:
    try:
        render.verify_treatments(built, [script], **kwargs)
    except render.RenderFailure as exc:
        first = exc.args[0] if exc.args else None
        return first if isinstance(first, dict) else {"type": type(exc).__name__}
    return None


def main() -> int:
    crop = {"mode": "lock", "crop_w": 606, "crop_h": 1080, "boxes": [{"x": 0, "y": 0}]}
    framed = plan([treatment("autoframe")], crop=crop)
    exact = render._crop_scale(framed, media(), crop, "full").split(",")[0]
    check(
        "autoframe: il frammento esatto passa",
        payload(framed, exact, media=media(), crop_plan=crop) is None,
        exact,
    )
    wrong = payload(
        framed, "crop=2:2:0:0,scale=1080:1920", media=media(), crop_plan=crop
    )
    check("autoframe: un crop diverso non basta", wrong is not None, wrong)
    check(
        "autoframe: treatment nel payload",
        (wrong or {}).get("treatment") == "autoframe",
    )
    missing = payload(framed, exact, media=media(), crop_plan=None)
    check("autoframe: senza crop plan fallisce", missing is not None, missing)

    fit = plan([treatment("fit")])
    scale_only = payload(fit, "scale=1080:1920", media=media(), crop_plan=None)
    check("fit: 'scale' da solo non basta", scale_only is not None, scale_only)
    fit_ok = payload(fit, "gblur=sigma=24", media=media(), crop_plan={"mode": "fit"})
    check("fit: gblur prova il ramo fit", fit_ok is None)

    zoom = plan([treatment("zoom", amount=0.06)])
    zoom_fragment = (render._zoom_chain(zoom, "full") or "").split(",")[0]
    zoom_missing = payload(zoom, "scale=1080:1920", media=media(), crop_plan=None)
    check("zoom: assente -> fallisce", zoom_missing is not None, zoom_missing)
    check(
        "zoom: frammento esatto passa",
        payload(zoom, zoom_fragment, media=media(), crop_plan=None) is None,
        zoom_fragment,
    )

    tight = plan([treatment("compress", ratio=2.0)])
    check(
        "compress: token specifico",
        payload(tight, "acompressor=ratio=2", media=media(), crop_plan=None) is None,
    )
    no_comp = payload(tight, "aresample=48000", media=media(), crop_plan=None)
    check("compress: senza acompressor fallisce", no_comp is not None, no_comp)

    off = plan([{"name": "autoframe", "enabled": False, "params": {}}])
    check(
        "trattamento spento non richiede nulla",
        payload(off, "scale=1:1", media=media(), crop_plan=None) is None,
    )

    ghost = plan([treatment("ghost_filter")])
    ghost_payload = payload(ghost, "scale=1:1", media=media(), crop_plan=None)
    check(
        "trattamento sconosciuto: il ledger fallisce",
        ghost_payload is not None
        and (ghost_payload or {}).get("missing")
        == "no ledger entry for this treatment",
        ghost_payload,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
