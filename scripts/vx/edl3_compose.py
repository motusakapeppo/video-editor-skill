"""vx.edl3_compose — PiP, masks, blend modes and chroma key as graph fragments.

Layer 1 overlays in ``vx.edl3_render`` are full-frame rectangles: a clip either
covers the frame or it does not. This module gives the compiler the missing
vocabulary. Every builder is pure: it returns a *fragment*

    {"op": str, "filters": [str, ...], "consumes": ["[label]", ...],
     "produces": "[label]", "notes": {...}}

and never touches a file, never knows a timeline, never guesses a duration. The
compiler splices ``filters`` into its own ``-filter_complex_script`` graph and
rewires labels with :func:`relabel`.

Declared numbers, with the range each one accepts
-------------------------------------------------
================  =============  ===========  =================================
constant          value          range        meaning
================  =============  ===========  =================================
MIN_SCALE         0.05           [0.05, 1.0]  smallest PiP footprint, fraction
MAX_SCALE         1.0            [0.05, 1.0]  largest PiP footprint, fraction
DEFAULT_SCALE     0.35           [0.05, 1.0]  used when ``scale`` is omitted
MIN_BORDER        0              [0, 64]      px, no border
MAX_BORDER        64             [0, 64]      px, thicker is a frame not an edge
MIN_FEATHER       0.0            [0, 100]     px; 0 means a hard step
MAX_FEATHER       100.0          [0, 100]     px; gblur sigma ceiling
MIN_SIMILARITY    0.01           [0.01, 1.0]  colorkey similarity floor
MAX_SIMILARITY    1.0            [0.01, 1.0]  everything becomes transparent
DEFAULT_SIMILARITY 0.1           [0.01, 1.0]  the colorkey default
MIN_BLEND         0.0            [0.0, 1.0]   hard edge on the keyed colour
MAX_BLEND         1.0            [0.0, 1.0]   fully soft edge
DEFAULT_BLEND     0.0            [0.0, 1.0]   hard edge, ffmpeg's own default
ALIGN             2              {2}          PiP sides snap to even pixels:
                                               yuv420p cannot address odd ones
================  =============  ===========  =================================

Geometry is computed, never hard-coded: the PiP width/height come from
``round(frame * scale / ALIGN) * ALIGN`` and the placement from the caller's
``x``/``y``. A PiP that would not fit inside the frame is refused, not clipped
silently.

Label contract (what a caller must have on the graph before splicing)
--------------------------------------------------------------------
* ``pip_fragment``    consumes ``[fg]``            produces ``[pip]``
* ``mask_fragment``   consumes ``[fg]``            produces ``[masked]``
* ``blend_fragment``  consumes ``[base]`` ``[fg]`` produces ``[blend]``
* ``chroma_fragment`` consumes ``[fg]``            produces ``[chroma]``

``[pip]`` is a *full-frame* RGBA layer: the scaled picture sits at (x, y) and
everything around it is ``black@0``. The compiler composites it with
``[base][pip]overlay=0:0:format=auto``; ``format=auto`` negotiates an
alpha-capable format because one input carries alpha. Compositing in the
fragment itself would need the base label, which the contract above does not
give it.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

__all__ = [
    "ComposeError",
    "COMPOSE_OPS",
    "BUILDERS",
    "BLEND_MODES",
    "MASK_SHAPES",
    "CHROMA_COLORS",
    "MIN_SCALE",
    "MAX_SCALE",
    "DEFAULT_SCALE",
    "MIN_BORDER",
    "MAX_BORDER",
    "MIN_FEATHER",
    "MAX_FEATHER",
    "MIN_SIMILARITY",
    "MAX_SIMILARITY",
    "DEFAULT_SIMILARITY",
    "MIN_BLEND",
    "MAX_BLEND",
    "DEFAULT_BLEND",
    "ALIGN",
    "pip_fragment",
    "mask_fragment",
    "blend_fragment",
    "chroma_fragment",
    "validate_op",
    "relabel",
]

# ------------------------------------------------------------- constants

MIN_SCALE = 0.05
MAX_SCALE = 1.0
DEFAULT_SCALE = 0.35

MIN_BORDER = 0
MAX_BORDER = 64

MIN_FEATHER = 0.0
MAX_FEATHER = 100.0

MIN_SIMILARITY = 0.01
MAX_SIMILARITY = 1.0
DEFAULT_SIMILARITY = 0.1

MIN_BLEND = 0.0
MAX_BLEND = 1.0
DEFAULT_BLEND = 0.0

#: PiP sides must be even: yuv420p subsamples chroma 2x2.
ALIGN = 2

#: Declared mask shapes. Anything else is refused by name, not approximated.
MASK_SHAPES = ("rect", "ellipse")

#: Declared blend modes -> the ffmpeg ``blend`` mode that implements them.
#: ``add`` is spelled ``addition`` in ffmpeg; the mapping is the whole point of
#: naming the modes here instead of passing a string through.
BLEND_MODES: Dict[str, str] = {
    "screen": "screen",
    "multiply": "multiply",
    "overlay": "overlay",
    "add": "addition",
    "difference": "difference",
}

#: Named key colours. ``chroma_fragment`` also accepts ``0xRRGGBB``/``#RRGGBB``.
CHROMA_COLORS: Dict[str, str] = {
    "green": "0x00FF00",
    "blue": "0x0000FF",
    "magenta": "0xFF00FF",
    "black": "0x000000",
    "white": "0xFFFFFF",
}

_HEX_COLOR = re.compile(r"^(?:0x|#)?([0-9A-Fa-f]{6})$")
_LABEL_TOKEN = re.compile(r"\[([^\[\]]+)\]")


class ComposeError(Exception):
    """A fragment cannot be built; ``code`` is stable and names the valid values."""

    def __init__(self, code: str, detail: str, where: str = "") -> None:
        self.code = code
        self.detail = detail
        self.where = where
        text = f"{code}: {detail}"
        if where:
            text = f"{text} ({where})"
        super().__init__(text)


# ------------------------------------------------------------- internals


def _num(value: float) -> str:
    """Stable decimal literal for ffmpeg's filter parser."""
    return f"{float(value):.6f}"


def _number(name: str, value: Any, code: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ComposeError(code, f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise ComposeError(code, f"{name} must be finite, got {value!r}")
    return number


def _positive_int(name: str, value: Any, code: str) -> int:
    number = _number(name, value, code)
    if number != int(number) or number <= 0:
        raise ComposeError(code, f"{name} must be a positive integer, got {value!r}")
    return int(number)


def _even(value: float) -> int:
    """Round to the nearest even integer, never below ALIGN."""
    return max(ALIGN, int(round(value / float(ALIGN))) * ALIGN)


def _range(name: str, value: float, low: float, high: float, code: str) -> float:
    if value < low or value > high:
        raise ComposeError(
            code, f"{name} {value:g} outside the declared range [{low:g}, {high:g}]"
        )
    return value


def _fragment(
    op: str,
    filters: List[str],
    consumes: Iterable[str],
    produces: str,
    notes: Mapping[str, Any],
) -> Dict[str, Any]:
    """Assemble the contract dict every builder returns."""
    for chain in filters:
        if not isinstance(chain, str) or not chain.strip():
            raise ComposeError("COMPOSE_FRAGMENT", f"{op}: empty filter chain")
    return {
        "op": op,
        "filters": list(filters),
        "consumes": list(consumes),
        "produces": produces,
        "notes": dict(notes),
    }


# ------------------------------------------------------------------- PiP


def pip_fragment(
    *,
    width: Any,
    height: Any,
    x: Any,
    y: Any,
    scale: Any = DEFAULT_SCALE,
    border: Optional[Any] = None,
) -> Dict[str, Any]:
    """Place ``[fg]`` as a picture-in-picture on a transparent full frame.

    ``width``/``height`` are the frame size in pixels. ``x``/``y`` are the
    top-left corner of the PiP inside that frame, in pixels. ``scale`` is the
    fraction of the frame the PiP occupies; both sides are computed from it and
    snapped to ``ALIGN``. ``border`` is an optional stroke thickness in pixels,
    clamped to the declared ``[MIN_BORDER, MAX_BORDER]`` range and to half the
    shortest PiP side.

    Returns ``[fg] -> [pip]`` where ``[pip]`` is full-frame RGBA: the picture at
    (x, y), ``black@0`` everywhere else. Composite with
    ``overlay=0:0:format=auto``.
    """
    frame_w = _positive_int("width", width, "PIP_GEOMETRY")
    frame_h = _positive_int("height", height, "PIP_GEOMETRY")
    scale_value = _range(
        "scale",
        _number("scale", scale, "PIP_GEOMETRY"),
        MIN_SCALE,
        MAX_SCALE,
        "PIP_GEOMETRY",
    )
    left = _number("x", x, "PIP_GEOMETRY")
    top = _number("y", y, "PIP_GEOMETRY")

    pip_w = _even(frame_w * scale_value)
    pip_h = _even(frame_h * scale_value)
    if pip_w > frame_w or pip_h > frame_h:
        raise ComposeError(
            "PIP_GEOMETRY",
            f"scale {scale_value:g} yields {pip_w}x{pip_h}, larger than the "
            f"{frame_w}x{frame_h} frame",
        )
    if (
        left < 0
        or top < 0
        or left + pip_w > frame_w + 1e-6
        or top + pip_h > frame_h + 1e-6
    ):
        raise ComposeError(
            "PIP_GEOMETRY",
            f"a {pip_w}x{pip_h} PiP at ({left:g}, {top:g}) does not fit inside "
            f"{frame_w}x{frame_h}; x must be in [0, {frame_w - pip_w}] and y in "
            f"[0, {frame_h - pip_h}]",
        )

    thickness = 0
    if border is not None:
        thickness = int(
            _range(
                "border",
                _number("border", border, "PIP_BORDER"),
                float(MIN_BORDER),
                float(MAX_BORDER),
                "PIP_BORDER",
            )
        )
        limit = min(pip_w, pip_h) // 2
        if thickness > limit:
            raise ComposeError(
                "PIP_BORDER",
                f"border {thickness} px is thicker than half the shortest PiP "
                f"side ({limit} px)",
            )

    offset_x = int(round(left))
    offset_y = int(round(top))
    chain = [
        f"[fg]scale={pip_w}:{pip_h}:flags=bicubic",
        "setsar=1",
        "format=rgba",
    ]
    if thickness > 0:
        chain.append(f"drawbox=x=0:y=0:w={pip_w}:h={pip_h}:color=white@1:t={thickness}")
    chain.append(f"pad={frame_w}:{frame_h}:{offset_x}:{offset_y}:color=black@0[pip]")
    return _fragment(
        "pip",
        [",".join(chain)],
        ["[fg]"],
        "[pip]",
        {
            "frame": [frame_w, frame_h],
            "size": [pip_w, pip_h],
            "position": [offset_x, offset_y],
            "scale": scale_value,
            "border": thickness,
            "composite": "[base][pip]overlay=0:0:format=auto",
        },
    )


# ------------------------------------------------------------------ mask


def mask_fragment(
    *,
    shape: str = "rect",
    x: Any,
    y: Any,
    width: Any,
    height: Any,
    feather: Any = MIN_FEATHER,
) -> Dict[str, Any]:
    """Keep ``[fg]`` only inside a rect or ellipse, optionally feathered.

    ``x``/``y``/``width``/``height`` are pixels in the *frame* the fragment is
    applied to; the fragment derives its own frame size from its input, so the
    mask always matches the picture it cuts.

    The mask is a real generated plane: ``geq`` paints it from the frame
    coordinates on a ``gray`` branch of a ``split``, the plane is blurred with
    ``gblur=sigma`` when ``feather > 0``, and ``alphamerge`` stamps it into the
    alpha channel of the untouched RGBA branch. ``feather`` is therefore a
    genuine blur, never an ignored parameter, and a rect edge stops being a
    one-pixel step. A ``gray`` branch is used because ``alphaextract`` cannot
    negotiate a format out of ``geq``'s RGB mode; the luma plane needs no such
    negotiation.

    Returns ``[fg] -> [masked]``.
    """
    if shape not in MASK_SHAPES:
        raise ComposeError(
            "MASK_SHAPE",
            f"shape {shape!r} not in the declared set {list(MASK_SHAPES)}",
        )
    left = _number("x", x, "MASK_GEOMETRY")
    top = _number("y", y, "MASK_GEOMETRY")
    box_w = _number("width", width, "MASK_GEOMETRY")
    box_h = _number("height", height, "MASK_GEOMETRY")
    if left < 0 or top < 0:
        raise ComposeError(
            "MASK_GEOMETRY",
            f"mask origin ({left:g}, {top:g}) must be >= 0; the mask lives in "
            "frame coordinates",
        )
    if box_w <= 0 or box_h <= 0:
        raise ComposeError(
            "MASK_GEOMETRY", f"mask size must be positive, got {box_w:g}x{box_h:g}"
        )
    feather_value = _range(
        "feather",
        _number("feather", feather, "MASK_FEATHER"),
        MIN_FEATHER,
        MAX_FEATHER,
        "MASK_FEATHER",
    )

    right = left + box_w
    bottom = top + box_h
    if shape == "rect":
        expression = (
            f"if(between(X,{_num(left)},{_num(right)})"
            f"*between(Y,{_num(top)},{_num(bottom)}),255,0)"
        )
    else:
        centre_x = left + box_w / 2.0
        centre_y = top + box_h / 2.0
        radius_x = box_w / 2.0
        radius_y = box_h / 2.0
        expression = (
            f"if(lte(pow((X-{_num(centre_x)})/{_num(radius_x)},2)"
            f"+pow((Y-{_num(centre_y)})/{_num(radius_y)},2),1),255,0)"
        )

    mask_step = f"format=gray,geq=lum='{expression}'"
    if feather_value > 0.0:
        mask_step = f"{mask_step},gblur=sigma={_num(feather_value)}"

    filters = [
        "[fg]format=rgba,split=2[mskA][mskB]",
        f"[mskA]{mask_step}[mskMask]",
        "[mskB][mskMask]alphamerge[masked]",
    ]
    return _fragment(
        "mask",
        filters,
        ["[fg]"],
        "[masked]",
        {
            "shape": shape,
            "rect": [left, top, box_w, box_h],
            "feather": feather_value,
            "alpha": "geq luma plane on gray, alphamerge",
            "blur": "gblur" if feather_value > 0.0 else "none",
        },
    )


# ----------------------------------------------------------------- blend


def blend_fragment(mode: Any) -> Dict[str, Any]:
    """Blend ``[base]`` and ``[fg]`` with a declared mode.

    ``screen``, ``multiply``, ``overlay``, ``add`` (ffmpeg's ``addition``) and
    ``difference`` are the declared modes. ``shortest=1`` keeps a finite overlay
    from extending the base timeline.

    Returns ``[base] [fg] -> [blend]``.
    """
    key = str(mode)
    if key not in BLEND_MODES:
        raise ComposeError(
            "BLEND_MODE",
            f"blend mode {key!r} not in the declared set {sorted(BLEND_MODES)}",
        )
    ffmpeg_mode = BLEND_MODES[key]
    return _fragment(
        "blend",
        [f"[base][fg]blend=all_mode={ffmpeg_mode}:shortest=1[blend]"],
        ["[base]", "[fg]"],
        "[blend]",
        {"mode": key, "ffmpeg_mode": ffmpeg_mode},
    )


# ----------------------------------------------------------------- chroma


def chroma_fragment(
    *,
    color: Any = "green",
    similarity: Any = DEFAULT_SIMILARITY,
    blend: Any = DEFAULT_BLEND,
) -> Dict[str, Any]:
    """Key a flat colour out of ``[fg]`` and hand back transparency.

    Accepts a name from :data:`CHROMA_COLORS` or an explicit ``0xRRGGBB`` /
    ``#RRGGBB`` literal. ``similarity`` (``[0.01, 1.0]``) is how far from the
    key colour a pixel may sit and still be removed; ``blend`` (``[0.0, 1.0]``)
    is the width of the soft edge. Both numbers are echoed in the returned dict
    exactly as passed, so a report can state what was keyed.

    ``colorkey`` operates in RGB, so the fragment converts to RGBA first; its
    output carries an alpha plane that ``overlay`` respects.

    Returns ``[fg] -> [chroma]``.
    """
    name = str(color).strip().lower()
    if name in CHROMA_COLORS:
        hex_color = CHROMA_COLORS[name]
    else:
        match = _HEX_COLOR.match(name)
        if match is None:
            raise ComposeError(
                "CHROMA_COLOR",
                f"colour {color!r} is neither a declared name "
                f"{sorted(CHROMA_COLORS)} nor an RRGGBB literal",
            )
        hex_color = "0x" + match.group(1).upper()

    similarity_value = _range(
        "similarity",
        _number("similarity", similarity, "CHROMA_RANGE"),
        MIN_SIMILARITY,
        MAX_SIMILARITY,
        "CHROMA_RANGE",
    )
    blend_value = _range(
        "blend",
        _number("blend", blend, "CHROMA_RANGE"),
        MIN_BLEND,
        MAX_BLEND,
        "CHROMA_RANGE",
    )
    if name not in CHROMA_COLORS:
        name = hex_color
    return _fragment(
        "chroma",
        [
            f"[fg]format=rgba,colorkey={hex_color}:"
            f"{_num(similarity_value)}:{_num(blend_value)}[chroma]"
        ],
        ["[fg]"],
        "[chroma]",
        {
            "color": name,
            "color_hex": hex_color,
            "similarity": similarity_value,
            "blend": blend_value,
            "filter": "colorkey",
        },
    )


# ------------------------------------------------------------ op table


#: ``op`` -> the parameter names a document must declare. The compiler validates
#: a document against this table without calling any builder, so a missing
#: parameter is a schema problem and not an ffmpeg failure much later.
COMPOSE_OPS: Dict[str, Tuple[str, ...]] = {
    "pip": ("width", "height", "x", "y"),
    "mask": ("shape", "x", "y", "width", "height"),
    "blend": ("mode",),
    "chroma": (),
}

#: ``op`` -> the builder that turns parameters into a fragment.
BUILDERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "pip": pip_fragment,
    "mask": mask_fragment,
    "blend": blend_fragment,
    "chroma": chroma_fragment,
}

#: Parameters a builder accepts but does not require, so a document with an
#: unknown key is refused before ffmpeg ever sees the graph.
_OPTIONAL_PARAMS: Dict[str, Tuple[str, ...]] = {
    "pip": ("scale", "border"),
    "mask": ("feather",),
    "blend": (),
    "chroma": ("color", "similarity", "blend"),
}


def validate_op(op: Any, params: Optional[Mapping[str, Any]] = None) -> None:
    """Check an op declaration against :data:`COMPOSE_OPS`; raise or return None.

    Value checks belong to the builders; this only refuses an unknown op, a
    missing required parameter, or an undeclared parameter name.
    """
    key = str(op)
    if key not in COMPOSE_OPS:
        raise ComposeError(
            "COMPOSE_OP",
            f"compose op {key!r} not in the declared set {sorted(COMPOSE_OPS)}",
        )
    supplied = dict(params or {})
    missing = [name for name in COMPOSE_OPS[key] if name not in supplied]
    if missing:
        raise ComposeError(
            "COMPOSE_PARAMS",
            f"op {key!r} requires {list(COMPOSE_OPS[key])}, missing {missing}",
        )
    allowed = set(COMPOSE_OPS[key]) | set(_OPTIONAL_PARAMS[key])
    unknown = sorted(set(supplied) - allowed)
    if unknown:
        raise ComposeError(
            "COMPOSE_PARAMS",
            f"op {key!r} does not declare {unknown}; declared parameters are "
            f"{sorted(allowed)}",
        )


# ------------------------------------------------------------- relabel


def _bare(label: str) -> str:
    return label[1:-1] if label.startswith("[") and label.endswith("]") else label


def relabel(fragment: Mapping[str, Any], mapping: Mapping[str, str]) -> Dict[str, Any]:
    """Return a copy of ``fragment`` with labels renamed by ``mapping``.

    Keys are bare labels (``"fg"``) or bracketed ones (``"[fg]"``). Only whole
    bracketed tokens are rewritten, so ``[mskA]`` is never touched by a mapping
    for ``[msk]``.
    """
    pairs = {_bare(str(key)): _bare(str(value)) for key, value in mapping.items()}

    def rewrite(chain: str) -> str:
        def swap(match: "re.Match[str]") -> str:
            token = match.group(1)
            return f"[{pairs.get(token, token)}]"

        return _LABEL_TOKEN.sub(swap, chain)

    filters = [rewrite(chain) for chain in fragment.get("filters") or []]
    consumes = [rewrite(label) for label in fragment.get("consumes") or []]
    produces = rewrite(str(fragment.get("produces") or ""))
    result = dict(fragment)
    result["filters"] = filters
    result["consumes"] = consumes
    result["produces"] = produces
    return result
