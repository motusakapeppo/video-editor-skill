"""F28-T02 gate -- the Motion Studio resolves fonts through the font system.

    python tests/test_fontresolve.py

No network. The provider is injected everywhere it is asked, so a real lookup
would fail loudly: a socket cannot hide behind a passing check.

What it proves:

a. the six bundled ids resolve to their shipped file, licence OFL-1.1, source
   ``bundled``, no fallback and no provider construction at all;
b. a commercial family from the font system resolves to its file, the record
   names ``commercial`` and the source licence, and ``page_font`` stays a
   bundled id because the face is registered in the page;
c. a personal family is refused with ``FONT_UNLICENSED`` and the declared
   fallback, unless an explicit approval object names it;
d. an unknown licence (and an absent family) gives ``FONT_UNKNOWN`` plus the
   declared fallback; a commercial family with no local file gives
   ``FONT_FALLBACK`` and is never downloaded;
e. the template's own default is the fallback, and a default outside the six
   ids cannot smuggle a face in;
f. the record survives JSON, and ``fallback_record`` never declares a fallback
   for the face that was asked for;
g. the Pillow raster declares the one face it can paint;
h. one real 1-frame Chromium render at 320x568 for each of the three branch
   outcomes: bundled, declared fallback, and a resolved commercial file
   registered in the page. The frame exists and is 320x568 in every case.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import fontresolve, motion, motion_fallback  # noqa: E402

FONTS = ROOT / "assets" / "fonts"
TEMPLATES = ROOT / "assets" / "templates"
SAMPLE = TEMPLATES / "vx-card-quote"
SIZE = (320, 568)
FPS = 0.5

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<66} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [{'SKIP'}] {label:<66} {reason}")


class FakeProvider:
    """A font system made of declared rows; every lookup is recorded."""

    name = "fake"

    def __init__(self, rows: Dict[str, Dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: List[str] = []

    def lookup(self, family: str) -> Optional[Dict[str, Any]]:
        self.calls.append(str(family))
        return self.rows.get(key(family))


def key(text: Any) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


class ExplodingProvider:
    """Any lookup is a failure: the bundled branch must never ask."""

    name = "exploding"

    def lookup(self, family: str) -> Optional[Dict[str, Any]]:
        raise AssertionError(f"provider asked for {family!r} on a bundled family")


class ExplodingCatalogue(ExplodingProvider):
    def __init__(self, **_kwargs: Any) -> None:
        raise AssertionError("a provider was constructed for a bundled family")


def commercial_rows(work: Path) -> Dict[str, Dict[str, Any]]:
    custom = work / "CustomFace-Bold.ttf"
    shutil.copyfile(FONTS / "Antonio-Bold.ttf", custom)
    personal = work / "PersonalFace-Bold.ttf"
    shutil.copyfile(FONTS / "Antonio-Bold.ttf", personal)
    return {
        "customface": {
            "family": "Custom Face",
            "licence": "commercial",
            "licence_detail": "Apache-2.0",
            "path": str(custom),
            "source": "fake",
        },
        "personalface": {
            "family": "Personal Face",
            "licence": "personal",
            "licence_detail": "personal",
            "path": str(personal),
            "source": "fake",
        },
        "nofileface": {
            "family": "No File Face",
            "licence": "commercial",
            "licence_detail": "OFL-1.1",
            "path": "",
            "source": "fake",
        },
        "mysteryface": {
            "family": "Mystery Face",
            "licence": "proprietary",
            "licence_detail": "proprietary",
            "path": str(custom),
            "source": "fake",
        },
        "unknownface": {
            "family": "Unknown Face",
            "licence": "unknown",
            "path": str(custom),
            "source": "fake",
        },
    }


def run_resolver(work: Path) -> None:
    ids = fontresolve.bundled_ids()
    check("six bundled ids come from the bundle", sorted(ids) == sorted(fontresolve.BUNDLED_IDS), sorted(ids))
    check("the refusal code set is the declared three", set(fontresolve.CODES) == {"FONT_UNLICENSED", "FONT_UNKNOWN", "FONT_FALLBACK"}, fontresolve.CODES)

    antonio = fontresolve.resolve("antonio", provider=ExplodingProvider())
    check("a bundled id resolves with licence OFL-1.1", antonio["licence"] == "OFL-1.1", antonio["licence"])
    check("a bundled id reports source 'bundled'", antonio["source"] == "bundled", antonio["source"])
    check("a bundled id is not a fallback", antonio["declared_fallback"] is False and antonio["code"] == "", antonio["code"])
    check("a bundled id points at a real file", Path(str(antonio["path"])).is_file(), antonio["path"])
    check("a bundled id is handed to the page unchanged", antonio["page_font"] == "antonio" and antonio["id"] == "antonio", antonio["page_font"])

    by_name = fontresolve.resolve("Antonio", provider=ExplodingProvider())
    check("the bundled family name resolves without the provider too", by_name["id"] == "antonio", by_name["family"])
    mono = fontresolve.resolve("jetbrainsmono", provider=ExplodingProvider())
    check("every bundled id maps to its own shipped file", mono["id"] == "jetbrainsmono" and "JetBrainsMono" in str(mono["path"]), mono["path"])

    fake = FakeProvider(commercial_rows(work))
    commercial = fontresolve.resolve("Custom Face", provider=fake)
    check("a commercial face resolves", commercial["licence"] == "commercial", commercial["licence"])
    check("the commercial record names the source licence", commercial["licence_detail"] == "Apache-2.0", commercial["licence_detail"])
    check("the commercial record names its source", commercial["source"] == "fake", commercial["source"])
    check("the commercial record has no bundled id", commercial["id"] == "" and commercial["path"] is not None, commercial["id"])
    check("the commercial record is not a fallback", commercial["declared_fallback"] is False and commercial["code"] == "")

    personal = fontresolve.resolve("Personal Face", provider=fake)
    check("a personal face is refused with FONT_UNLICENSED", personal["code"] == "FONT_UNLICENSED", personal["code"])
    check("a personal refusal declares the fallback", personal["declared_fallback"] is True and personal["source"] == "bundled", personal["source"])
    check("the personal refusal falls back to the template default", personal["id"] == "antonio" and personal["family"] == "Antonio", personal["family"])
    check("the personal refusal names the reason", "personal licence" in personal["reason"], personal["reason"])

    approved = fontresolve.resolve(
        "Personal Face", provider=fake, approval={"family": "Personal Face", "by": "operator"}
    )
    check("an approval object unlocks the personal face", approved["approved"] is True and approved["declared_fallback"] is False, approved["code"])
    check("the approved face keeps its personal licence on record", approved["licence"] == "personal", approved["licence"])
    wrong = fontresolve.resolve(
        "Personal Face", provider=fake, approval={"family": "Someone Else", "by": "operator"}
    )
    check("an approval for another family unlocks nothing", wrong["code"] == "FONT_UNLICENSED", wrong["code"])

    unknown = fontresolve.resolve("Mystery Face", provider=fake)
    check("an unknown licence gives FONT_UNKNOWN", unknown["code"] == "FONT_UNKNOWN", unknown["code"])
    check("an unknown licence declares the fallback", unknown["declared_fallback"] is True and unknown["id"] == "antonio", unknown["reason"])
    unknown2 = fontresolve.resolve("Unknown Face", provider=fake)
    check("an explicit 'unknown' licence is discarded the same way", unknown2["code"] == "FONT_UNKNOWN", unknown2["code"])
    absent = fontresolve.resolve("Nowhere", provider=fake)
    check("an absent family gives FONT_UNKNOWN and the fallback", absent["code"] == "FONT_UNKNOWN" and absent["declared_fallback"] is True, absent["reason"])

    nofile = fontresolve.resolve("No File Face", provider=fake)
    check("a commercial face with no local file gives FONT_FALLBACK", nofile["code"] == "FONT_FALLBACK", nofile["code"])
    check("the no-file fallback says nothing is downloaded", "never downloads" in nofile["reason"], nofile["reason"])

    text_default = fontresolve.resolve("Nowhere", provider=fake, template_default="inter")
    check("the template's own default is the fallback", text_default["id"] == "inter" and text_default["family"] == "Inter", text_default["family"])
    broken_default = fontresolve.resolve("Nowhere", provider=fake, template_default="comic")
    check("a default outside the six ids cannot be painted", broken_default["id"] == "antonio", broken_default["id"])

    same = fontresolve.fallback_record("antonio", template_default="antonio")
    check("asking for the fallback face is not a declared fallback", same["declared_fallback"] is False and same["code"] == "", same["code"])
    payload = json.loads(json.dumps(commercial))
    check("the record survives JSON unchanged", payload == commercial, sorted(payload))

    fallback = motion_fallback.capture(SAMPLE, {"font": "jetbrainsmono"}, work / "pillow", fps=FPS, size=SIZE)
    record = fallback.get("font") or {}
    check("the Pillow raster declares the one face it paints", record.get("declared_fallback") is True and record.get("id") == "antonio", record.get("reason"))


def frame_size(path: str) -> tuple:
    from PIL import Image

    with Image.open(path) as image:
        return image.size


def loose_params(real: Any) -> Any:
    def validate(template: Any, params: Any) -> Dict[str, Any]:
        given = dict(params or {})
        wanted = given.pop("font", None)
        base = real(template, given)
        if wanted is not None:
            base["font"] = str(wanted)
        return base

    return validate


def run_render(work: Path, fake: FakeProvider) -> None:
    probe = motion.available()
    if not probe["available"]:
        skip("one real Chromium frame per branch", str(probe.get("reason") or "no browser"))
        return

    real_validate = motion.validate_params
    real_resolve = fontresolve.resolve
    real_catalogue = fontresolve.CatalogueProvider
    motion.validate_params = loose_params(real_validate)
    try:
        fontresolve.CatalogueProvider = ExplodingCatalogue
        try:
            bundled = motion.capture(SAMPLE, {"font": "antonio"}, work / "browser-bundled", fps=FPS, size=SIZE)
        finally:
            fontresolve.CatalogueProvider = real_catalogue
        record = bundled.get("font") or {}
        check("bundled: a real Chromium frame is written", bundled["frame_count"] == 1 and frame_size(bundled["frames"][0]) == SIZE, bundled.get("engine"))
        check("bundled: no provider is constructed during the render", record.get("source") == "bundled", record.get("source"))
        check("bundled: the report carries the OFL-1.1 face", record.get("licence") == "OFL-1.1" and record.get("page_font") == "antonio", record.get("family"))

        fallback = motion.capture(SAMPLE, {"font": "NoSuchFamily"}, work / "browser-fallback", fps=FPS, size=SIZE)
        record = fallback.get("font") or {}
        check("fallback: the frame still renders at 320x568", fallback["frame_count"] == 1 and frame_size(fallback["frames"][0]) == SIZE, fallback.get("engine"))
        check("fallback: the report declares FONT_UNKNOWN", record.get("code") == "FONT_UNKNOWN" and record.get("declared_fallback") is True, record.get("reason"))
        check("fallback: the page is handed the bundled default", record.get("page_font") == "inter" and record.get("family") == "Inter", record.get("page_font"))
        check("fallback: the declared params keep the request", fallback["params"]["font"] == "NoSuchFamily", fallback["params"]["font"])
    finally:
        fontresolve.CatalogueProvider = real_catalogue
        motion.validate_params = real_validate

    def with_provider(family: Any, **kwargs: Any) -> Dict[str, Any]:
        return real_resolve(family, provider=fake, **kwargs)

    motion.validate_params = loose_params(real_validate)
    fontresolve.resolve = with_provider
    try:
        resolved = motion.capture(SAMPLE, {"font": "Custom Face"}, work / "browser-resolved", fps=FPS, size=SIZE)
        record = resolved.get("font") or {}
        check("resolved: the frame renders with the registered face", resolved["frame_count"] == 1 and frame_size(resolved["frames"][0]) == SIZE, resolved.get("engine"))
        check("resolved: the report names the commercial licence", record.get("licence") == "commercial" and record.get("licence_detail") == "Apache-2.0", record.get("licence"))
        check("resolved: the file is registered in the page", str(record.get("registered_family") or "").startswith("VX Resolved "), record.get("registered_family"))
        check("resolved: the page still receives a bundled id", record.get("page_font") == "inter", record.get("page_font"))
        check("resolved: the record points at the resolved file", Path(str(record.get("path"))).is_file(), record.get("path"))
    finally:
        fontresolve.resolve = real_resolve
        motion.validate_params = real_validate


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="vx_fontresolve_"))
    try:
        fake = FakeProvider(commercial_rows(work))
        run_resolver(work)
        run_render(work, fake)
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
        global FAIL
        FAIL += 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
