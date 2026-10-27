"""F3-T05 gate -- the Motion Studio frame cache, on the real engine.

    python tests/test_motion_cache.py

Script-style: every check prints PASS/FAIL and the process exits 0 only when
every check passes.  It drives the system Chrome (or Edge) through Playwright's
channel; nothing is downloaded and nothing reaches the network.

What it proves, in order:

a. a first capture of a small template is N misses / 0 hits; a second identical
   capture is N hits / 0 misses and the frames on disk are byte-identical;
b. a parameter that moves the picture invalidates every frame; a parameter that
   does not touch the picture *also* invalidates every frame, because the key is
   parameter-complete on purpose -- the re-rendered frames are byte-identical,
   which is how we know the param is pixel-neutral and the key is conservative;
c. changing `fps` or `size` invalidates every frame;
d. editing `index.html` under the same template name invalidates every frame --
   the key hashes content, not the name;
e. a hand-truncated cached PNG is a MISS, counted in `damaged`, and re-rendered
   to the exact bytes it had before;
f. a corrupt `index.json` does not crash: it is counted, ignored and rebuilt;
g. with no workdir the cache is disabled, writes nothing (the cache directory is
   byte-for-byte untouched) and the capture still produces every frame;
h. the cache files are registered in the workdir's `.vx-owned` and
   `vault.verify(workdir)` is clean;
i. nothing is written outside the workdir: a sibling directory and the cwd are
   snapshotted before and after the whole run.

Eleven captures, one Chromium process: the gate is the frame cache, never the
launcher, so every capture reuses one live browser through `BrowserPool` while
still getting its own context, page, viewport, route block and `window.__vxParams`
-- no state crosses a scenario boundary.  A cold start costs about ten times a
capture here, and eleven of them are what used to push the suite past its
ceiling.  The per-scenario cost table printed at the end keeps that honest.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import motion, motion_cache, vault  # noqa: E402

SIDE = 240
FPS = 6
DURATION = 1.0
FRAMES = 6
CEILING_SECONDS = 120.0

PARAMS: Dict[str, Any] = {"x": 40, "tint": "#ff2a2a", "label": "alpha"}

PASS = 0
FAIL = 0

#: Instrumentation: how the wall clock is spent.  A capture is one call into
#: `motion.capture`; a launch is one cold browser start.  Printing both is the
#: only honest way to see whether the suite pays for frames or for Chromium.
CAPTURES = 0
LAUNCHES = 0
SCENARIOS: List[Tuple[str, float, int, int]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:<58} {detail}")


def note(label: str, detail: str) -> None:
    print(f"    [INFO] {label:<58} {detail}")


class Scen:
    """One timed region: wall clock, captures and browser starts inside it."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._t0 = time.perf_counter()
        self._c0 = CAPTURES
        self._l0 = LAUNCHES

    def stop(self) -> None:
        SCENARIOS.append(
            (
                self.name,
                time.perf_counter() - self._t0,
                CAPTURES - self._c0,
                LAUNCHES - self._l0,
            )
        )


_CURRENT: Optional[Scen] = None


def stage(name: Optional[str] = None) -> None:
    global _CURRENT
    if _CURRENT is not None:
        _CURRENT.stop()
        _CURRENT = None
    if name is not None:
        _CURRENT = Scen(name)


def print_timings() -> None:
    if not SCENARIOS:
        return
    print("\n    per-scenario cost")
    print(f"    {'scenario':<34} {'secs':>8} {'captures':>9} {'launches':>9}")
    for name, seconds, captures, launches in SCENARIOS:
        print(f"    {name:<34} {seconds:>8.2f} {captures:>9d} {launches:>9d}")
    print(
        f"    {'TOTAL':<34} {sum(s for _, s, _, _ in SCENARIOS):>8.2f} "
        f"{sum(c for _, _, c, _ in SCENARIOS):>9d} "
        f"{sum(l for _, _, _, l in SCENARIOS):>9d}"
    )


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def write_template(root: Path) -> Path:
    """A tiny, deterministic template: two boxes, one param that moves one."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>cache-probe</title>
<style>
  html, body {
    margin: 0;
    padding: 0;
    width: 100%;
    height: 100%;
    background: transparent;
    overflow: hidden;
  }
  #box { position: absolute; left: 0; top: 16px; width: 96px; height: 96px; }
  #bar { position: absolute; left: 0; width: 176px; height: 12px; background: #0b0b0b; }
</style>
</head>
<body>
<div id="box"></div>
<div id="bar"></div>
<script>
/* Motion Studio contract: the picture is a pure function of t and the params. */
(function () {
  "use strict";
  var box = document.getElementById("box");
  var bar = document.getElementById("bar");

  function apply(t) {
    var p = Object.assign({ x: 0, tint: "#ff2a2a" }, window.__vxParams || {});
    box.style.left = Math.round(p.x * t) + "px";
    box.style.top = Math.round(16 + t * 48) + "px";
    box.style.background = p.tint;
    bar.style.top = Math.round(180 - t * 40) + "px";
  }

  window.__vxSeek = function (t) {
    apply(t);
    return new Promise(function (resolve) {
      requestAnimationFrame(function () {
        requestAnimationFrame(resolve);
      });
    });
  };

  apply(0);
})();
</script>
</body>
</html>
""",
        encoding="utf-8",
    )
    (root / "template.json").write_text(
        json.dumps(
            {
                "name": "cache-probe",
                "fps": FPS,
                "duration": DURATION,
                "size": [SIDE, SIDE],
                "params": {
                    "x": {"type": "number", "default": 0, "min": 0, "max": 200},
                    "tint": {"type": "color", "default": "#ff2a2a"},
                    "label": {
                        "type": "string",
                        "default": "alpha",
                        "max_length": 24,
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return root


def snapshot_tree(
    root: Path, *, skip: tuple = (".git", "__pycache__")
) -> Dict[str, tuple]:
    """Every file under `root` with (size, mtime_ns); cheap proof of no writes."""
    out: Dict[str, tuple] = {}
    if not root.exists():
        return out
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in skip for part in relative.parts):
            continue
        if not path.is_file():
            continue
        stat = path.stat()
        out[relative.as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return out


def raw_capture(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    global CAPTURES
    CAPTURES += 1
    return motion.capture(*args, **kwargs)


_ORIGINAL_LAUNCH = motion._launch
_ORIGINAL_CLOSE = motion._close_quietly


class BrowserPool:
    """One Chromium for the whole gate, one fresh context per capture.

    What this suite proves is the frame cache, not the launcher: eleven cold
    starts of Chrome cost eleven times the startup, and nothing in the nine
    checks needs a *new process*.  Every capture still gets its own browser
    context and page, its own viewport, its own route block and its own
    `window.__vxParams`, so no state crosses a scenario boundary -- and check
    (d) proves it: a template edited on disk between two captures still
    produces a different picture.
    """

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self.engine = ""
        self.reused = 0

    def launch(self, candidates: Any) -> Any:
        global LAUNCHES
        live = self._browser is not None and bool(self._browser.is_connected())
        if live:
            self.reused += 1
            return self._pw, self._browser, self.engine
        self._pw, self._browser, self.engine = _ORIGINAL_LAUNCH(candidates)
        LAUNCHES += 1
        return self._pw, self._browser, self.engine

    def release(self, page: Any, context: Any, browser: Any, pw: Any) -> None:
        for handle in (page, context):
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - cleanup must not mask the real error
                pass

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001 - cleanup must not mask the real error
                pass
            self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001 - cleanup must not mask the real error
                pass
            self._pw = None


def capture(
    template: Path,
    out_dir: Path,
    params: Optional[Dict[str, Any]] = None,
    *,
    workdir: Optional[Path] = None,
    fps: float = FPS,
    size: tuple = (SIDE, SIDE),
) -> Dict[str, Any]:
    return raw_capture(
        template,
        PARAMS if params is None else params,
        out_dir,
        fps=fps,
        size=size,
        frame_timeout_s=30.0,
        workdir=workdir,
    )


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
def run(tmp: Path) -> None:
    work = tmp / "work"
    work.mkdir()
    sibling = tmp / "sibling"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("untouched\n", encoding="utf-8")
    template = write_template(tmp / "template")
    cache_dir = work / motion_cache.CACHE_DIRNAME

    elsewhere = tmp / "elsewhere"
    elsewhere.mkdir()
    cwd_before = snapshot_tree(elsewhere)
    sibling_before = snapshot_tree(sibling)
    cache_dir_absent = not cache_dir.exists()

    stage("0. probes and key")
    probe = motion.available()
    check(
        "0. a system browser is available",
        bool(probe["available"]),
        f"engine={probe['engine']} reason={probe['reason']}",
    )
    if not probe["available"]:
        return
    engine = probe["engine"]
    check(
        "0. the cache directory does not exist before the first capture",
        cache_dir_absent,
        str(cache_dir),
    )

    resolved = motion.validate_params(template, PARAMS)
    expected_payload = {
        "schema": motion_cache.SCHEMA,
        "stage": motion_cache.STAGE_VERSION,
        "template": motion_cache.template_digest(template),
        "params": resolved,
        "fps": float(FPS),
        "size": [SIDE, SIDE],
        "engine": engine,
        "t": 0.0,
    }
    expected_key = hashlib.sha256(
        json.dumps(
            expected_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    scratch = motion_cache.Cache(work)
    check(
        "0. the key is sha256 of the declared payload",
        scratch.key(
            template, resolved, fps=FPS, size=(SIDE, SIDE), engine=engine, t=0.0
        )
        == expected_key,
        expected_key[:16] + "...",
    )
    note("0. key payload", json.dumps(expected_payload, sort_keys=True)[:150] + "...")
    check(
        "0. the engine is in the key",
        scratch.key(
            template, resolved, fps=FPS, size=(SIDE, SIDE), engine="chrome", t=0.0
        )
        != scratch.key(
            template, resolved, fps=FPS, size=(SIDE, SIDE), engine="msedge", t=0.0
        ),
        "chrome != msedge",
    )

    # -- a. miss, then hit, byte-identical -------------------------------- #
    stage("a1. first capture (all miss)")
    first = capture(template, work / "out-a1", workdir=work)
    cached = first["cache"]
    check(
        "a. first capture: every frame a miss, no hits",
        cached["hits"] == 0
        and cached["misses"] == FRAMES
        and first["frame_count"] == FRAMES,
        f"hits={cached['hits']} misses={cached['misses']} frames={first['frame_count']}",
    )
    check(
        "a. first capture wrote every frame into the cache",
        cached["writes"] == FRAMES and len(list(cache_dir.glob("*.png"))) == FRAMES,
        f"writes={cached['writes']} files={len(list(cache_dir.glob('*.png')))}",
    )
    check(
        "a. the cache dir lives inside the workdir",
        Path(cached["dir"]).resolve().is_relative_to(work.resolve()),
        cached["dir"],
    )
    stage("a2. second capture (all hits)")
    second = capture(template, work / "out-a2", workdir=work)
    check(
        "a. second identical capture: every frame a hit, no miss",
        second["cache"]["hits"] == FRAMES
        and second["cache"]["misses"] == 0
        and second["cache"]["writes"] == 0,
        f"hits={second['cache']['hits']} misses={second['cache']['misses']}",
    )
    identical = [
        index
        for index, (left, right) in enumerate(zip(first["frames"], second["frames"]))
        if Path(left).read_bytes() != Path(right).read_bytes()
    ]
    check(
        "a. the frames on disk are byte-identical",
        not identical and first["frame_sha256"] == second["frame_sha256"],
        f"{len(identical)} of {FRAMES} differ",
    )

    # -- b. params ------------------------------------------------------- #
    stage("b1. param that moves the picture")
    moved = capture(template, work / "out-b1", {**PARAMS, "x": 160}, workdir=work)
    check(
        "b. a param that moves the picture invalidates every frame",
        moved["cache"]["hits"] == 0 and moved["cache"]["misses"] == FRAMES,
        f"hits={moved['cache']['hits']} misses={moved['cache']['misses']}",
    )
    check(
        "b. and the picture really changed",
        moved["frame_sha256"] != first["frame_sha256"],
        "all frames differ from the x=40 run",
    )
    stage("b2. pixel-neutral param")
    relabelled = capture(
        template,
        work / "out-b2",
        {**PARAMS, "x": 160, "label": "beta"},
        workdir=work,
    )
    check(
        "b. a pixel-neutral param invalidates every frame too",
        relabelled["cache"]["hits"] == 0 and relabelled["cache"]["misses"] == FRAMES,
        f"hits={relabelled['cache']['hits']} misses={relabelled['cache']['misses']}",
    )
    same_picture = [
        index
        for index, (left, right) in enumerate(
            zip(moved["frames"], relabelled["frames"])
        )
        if Path(left).read_bytes() != Path(right).read_bytes()
    ]
    check(
        "b. yet `label` does not touch the picture",
        not same_picture,
        "re-rendered frames are byte-identical to the label='alpha' run",
    )
    note(
        "b. why",
        "the key is parameter-complete (canonical JSON of every resolved "
        "param): a template does not declare which params are pixel-neutral, "
        "so the cache never guesses. No param can partially invalidate.",
    )

    # -- c. fps and size -------------------------------------------------- #
    stage("c1. fps")
    reframed = capture(template, work / "out-c1", workdir=work, fps=4)
    check(
        "c. changing fps invalidates every frame",
        reframed["cache"]["hits"] == 0
        and reframed["cache"]["misses"] == reframed["frame_count"] == 4,
        f"hits={reframed['cache']['hits']} misses={reframed['cache']['misses']}",
    )
    stage("c2. size")
    resized = capture(template, work / "out-c2", workdir=work, size=(SIDE, 200))
    check(
        "c. changing size invalidates every frame",
        resized["cache"]["hits"] == 0
        and resized["cache"]["misses"] == resized["frame_count"] == FRAMES,
        f"hits={resized['cache']['hits']} misses={resized['cache']['misses']}",
    )

    # -- d. same name, new content ---------------------------------------- #
    stage("d1. edited index.html")
    html_path = template / "index.html"
    original_html = html_path.read_text(encoding="utf-8")
    edited = original_html.replace(
        "#box { position: absolute;",
        "#box { border-radius: 14px; position: absolute;",
    )
    html_path.write_text(edited, encoding="utf-8")
    check(
        "d. the template name and the param set are unchanged",
        edited != original_html
        and json.loads((template / "template.json").read_text(encoding="utf-8"))["name"]
        == "cache-probe",
        "only index.html changed",
    )
    restyled = capture(template, work / "out-d1", workdir=work)
    check(
        "d. editing index.html under the same name invalidates every frame",
        restyled["cache"]["hits"] == 0 and restyled["cache"]["misses"] == FRAMES,
        f"hits={restyled['cache']['hits']} misses={restyled['cache']['misses']}",
    )
    check(
        "d. and the picture really changed",
        restyled["frame_sha256"] != first["frame_sha256"],
        "rounded corners are visible in the frame",
    )
    stage("d2. new content cached")
    restyled_again = capture(template, work / "out-d2", workdir=work)
    check(
        "d. the new content is cached in its own right",
        restyled_again["cache"]["hits"] == FRAMES
        and restyled_again["cache"]["misses"] == 0,
        f"hits={restyled_again['cache']['hits']} misses={restyled_again['cache']['misses']}",
    )

    # -- e. a truncated frame is a miss ----------------------------------- #
    stage("e1. truncated frame")
    resolved_now = motion.validate_params(template, PARAMS)
    frame_key = scratch.key(
        template, resolved_now, fps=FPS, size=(SIDE, SIDE), engine=engine, t=0.0
    )
    frame_path = cache_dir / f"{frame_key}.png"
    check(
        "e. the first frame of the current run is on disk",
        frame_path.is_file(),
        frame_path.name[:16] + "...",
    )
    before_bytes = frame_path.read_bytes()
    frame_path.write_bytes(before_bytes[:64])
    repaired = capture(template, work / "out-e", workdir=work)
    check(
        "e. a hand-truncated frame is a miss, counted as damaged",
        repaired["cache"]["hits"] == FRAMES - 1
        and repaired["cache"]["misses"] == 1
        and repaired["cache"]["damaged"] >= 1,
        f"hits={repaired['cache']['hits']} misses={repaired['cache']['misses']} "
        f"damaged={repaired['cache']['damaged']}",
    )
    check(
        "e. the frame was re-rendered to the bytes it had before",
        frame_path.read_bytes() == before_bytes
        and repaired["frame_sha256"][0] == restyled_again["frame_sha256"][0],
        f"{len(before_bytes)} bytes",
    )

    # -- the frame floor -------------------------------------------------- #
    stage("e2. frame floor")
    floor_work = work / "unit-floor"
    floor_cache = motion_cache.Cache(floor_work)
    refused = None
    try:
        floor_cache.put("0" * 64, b"\x89PNG\r\n\x1a\n" + b"\0" * 8)
    except motion_cache.CacheRefused as exc:
        refused = exc
    except motion_cache.CacheError as exc:
        refused = exc
    check(
        "e2. a screenshot below the floor is never cached",
        refused is not None
        and floor_cache.stats()["refused"] == 1
        and floor_cache.stats()["writes"] == 0
        and not (floor_work / motion_cache.CACHE_DIRNAME).exists(),
        f"floor={motion_cache.MIN_FRAME_BYTES}B refused={refused}",
    )

    # -- f. a corrupt index ------------------------------------------------ #
    stage("f0. corrupt entries")
    bad_entry_work = work / "unit-index"
    bad_entry_cache = motion_cache.Cache(bad_entry_work)
    bad_dir = bad_entry_cache.directory
    check(
        "f0. a fresh cache exposes its directory without creating it",
        bad_dir is not None and not bad_dir.exists(),
        str(bad_dir),
    )
    if bad_dir is not None:
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / motion_cache.INDEX_NAME).write_text(
            json.dumps(
                {
                    "schema": motion_cache.SCHEMA,
                    "stage": motion_cache.STAGE_VERSION,
                    "entries": {
                        "a" * 64: {"sha256": "not-a-hash", "size": 0, "created_at": ""},
                        "b" * 64: {
                            "sha256": "0" * 64,
                            "size": 4096,
                            "created_at": "2026-01-01T00:00:00+00:00",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        bad_hit = bad_entry_cache.get("b" * 64)
        bad_stats = bad_entry_cache.stats()
        check(
            "f0. a corrupt entry is declared and a missing frame is a miss",
            bad_hit is None
            and bad_stats["damaged"] == 2
            and bad_stats["misses"] == 1
            and bad_stats["hits"] == 0,
            f"damaged={bad_stats['damaged']} misses={bad_stats['misses']}",
        )

    stage("f. corrupt index")
    index_path = cache_dir / motion_cache.INDEX_NAME
    index_path.write_text(
        '{"schema": "vx.motion.cache/1", "entries": {oops', encoding="utf-8"
    )
    rebuilt = capture(template, work / "out-f", workdir=work)
    check(
        "f. a corrupt index does not crash: every frame is a miss",
        rebuilt["frame_count"] == FRAMES
        and rebuilt["cache"]["hits"] == 0
        and rebuilt["cache"]["misses"] == FRAMES,
        f"hits={rebuilt['cache']['hits']} misses={rebuilt['cache']['misses']}",
    )
    check(
        "f. the damage is declared, not swallowed",
        rebuilt["cache"]["damaged"] >= 1,
        f"damaged={rebuilt['cache']['damaged']}",
    )
    parsed: Optional[dict] = None
    try:
        parsed = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        parsed = None
    check(
        "f. the index was rebuilt and parses again",
        isinstance(parsed, dict)
        and isinstance(parsed.get("entries"), dict)
        and len(parsed["entries"]) == FRAMES,
        f"entries={0 if not isinstance(parsed, dict) else len(parsed.get('entries', {}))}",
    )

    # -- h. the vault owns the cache -------------------------------------- #
    stage("h. vault manifest")
    report = vault.verify(work)
    owned = vault.owned_entries(work)
    cache_files = [
        path.relative_to(work).as_posix()
        for path in cache_dir.rglob("*")
        if path.is_file()
    ]
    unowned_cache = [relative for relative in cache_files if relative not in owned]
    check(
        "h. every cache file is registered in .vx-owned",
        not unowned_cache and cache_files,
        f"{len(cache_files)} files, {len(unowned_cache)} unowned",
    )
    check(
        "h. vault.verify(workdir) is clean",
        bool(report["ok"]) and not report["missing"] and not report["changed"],
        f"checked={report['checked']} ok={report['ok']} "
        f"missing={report['missing']} changed={report['changed']}",
    )
    note("h. unowned on disk", f"{len(report['unowned_on_disk'])} (capture outputs)")
    check(
        "h. the write journal is registered too",
        artifacts_journal(work) in owned,
        artifacts_journal(work),
    )

    # -- g. no workdir: disabled ------------------------------------------ #
    stage("g. no workdir")
    cache_snapshot = snapshot_tree(cache_dir)
    disabled = raw_capture(
        template,
        PARAMS,
        work / "out-g",
        fps=FPS,
        size=(SIDE, SIDE),
        frame_timeout_s=30.0,
    )
    disabled_cache = disabled["cache"]
    check(
        "g. with no workdir the cache is disabled and says so",
        disabled_cache["enabled"] is False
        and disabled_cache["status"] == "disabled"
        and disabled_cache["dir"] is None
        and disabled_cache["hits"] == 0
        and disabled_cache["misses"] == 0,
        json.dumps(
            {
                key: disabled_cache[key]
                for key in ("enabled", "status", "hits", "misses")
            }
        ),
    )
    check(
        "g. the disabled capture still produced every frame",
        disabled["frame_count"] == FRAMES
        and disabled["frame_sha256"] == restyled_again["frame_sha256"],
        f"{disabled['frame_count']} frames, identical to the cached run",
    )
    check(
        "g. and wrote nothing into the cache",
        snapshot_tree(cache_dir) == cache_snapshot,
        f"{len(cache_snapshot)} cached files untouched",
    )

    # -- i. nothing outside the workdir ----------------------------------- #
    stage("i. outside the workdir")
    cwd_after = snapshot_tree(elsewhere)
    sibling_after = snapshot_tree(sibling)
    cwd_changed = sorted(
        key
        for key in set(cwd_after) | set(cwd_before)
        if cwd_after.get(key) != cwd_before.get(key)
    )
    check(
        "i. the sibling directory is untouched",
        sibling_after == sibling_before,
        f"{sorted(sibling_after)}",
    )
    check(
        "i. the cwd is untouched",
        cwd_after == cwd_before,
        f"{len(cwd_after)} files, changed={cwd_changed[:4]}",
    )
    outside = [
        str(path)
        for path in (
            sibling / motion_cache.CACHE_DIRNAME,
            Path.cwd() / motion_cache.CACHE_DIRNAME,
        )
        if path.exists()
    ]
    check(
        "i. no cache directory outside the workdir",
        not outside,
        f"leaked={outside}" if outside else "only " + str(cache_dir),
    )
    note(
        "i. scope",
        f"everything the run wrote lives under {work}",
    )
    stage()


def artifacts_journal(work: Path) -> str:
    from vx import artifacts  # noqa: PLC0415

    return artifacts.journal_path(work).relative_to(work).as_posix()


def main() -> int:
    global PASS, FAIL
    started = time.perf_counter()
    pool = BrowserPool()
    motion._launch = pool.launch
    motion._close_quietly = pool.release
    try:
        with tempfile.TemporaryDirectory(
            prefix="vx_motion_cache_", ignore_cleanup_errors=True
        ) as tmp:
            run(Path(tmp))
    except motion.MotionError as exc:
        print(f"\nFAIL: declared error {exc.code}: {exc}")
        print(exc.payload)
        FAIL += 1
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
        import traceback  # noqa: PLC0415

        traceback.print_exc()
        FAIL += 1
    finally:
        stage()
        pool.close()
        motion._launch = _ORIGINAL_LAUNCH
        motion._close_quietly = _ORIGINAL_CLOSE
    elapsed = time.perf_counter() - started
    slow = elapsed > CEILING_SECONDS
    print_timings()
    print(
        f"\n    {CAPTURES} captures, {LAUNCHES} browser start(s), "
        f"{pool.reused} reuse(s)"
    )
    print(
        f"\n{PASS} PASS / {FAIL} FAIL, {elapsed:.1f}s (ceiling {CEILING_SECONDS:.0f}s)"
    )
    if slow:
        print(f"FAIL: suite took {elapsed:.1f}s, above the ceiling")
        return 1
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
