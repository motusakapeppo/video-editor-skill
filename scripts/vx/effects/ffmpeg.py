"""vx.effects.ffmpeg — the bridge from a validated compose spec to ffmpeg.

:mod:`vx.effects.compose` describes an effect; :mod:`vx.edl3_render` renders a
document. This module is the missing middle: it turns one *validated* spec into
the filters that execute it, or refuses it with a stable code and an Italian
sentence. It never guesses and it never approximates a curve it cannot write.

Primitive -> filter
-------------------

``transform``
    ``zoompan``. ``scale``/``x``/``y`` animate through ``z``/``x``/``y`` because
    ``zoompan`` evaluates them per output frame (``on``), while ``scale`` and
    ``crop`` evaluate their *size* once at initialisation. A static scale below
    1 cannot be a zoom factor (``z >= 1``), so it becomes ``scale`` + ``pad``.
    ``opacity`` becomes a premultiplied ``geq``: the clip fades against black,
    which is what opacity means for a single stream. ``rotate`` is refused with
    ``PARAM_NOT_RENDERED`` because a rotated frame needs a new canvas, a
    decision this bridge does not invent.

``grade``
    ``eq`` for contrast/saturation/brightness/gamma; ``colorbalance`` for a
    warm/cool/teal-orange look and for temperature; ``hue`` for noir;
    ``colorchannelmixer`` for sepia; a second ``eq`` for bleach/vibrant. An
    animated ``eq`` parameter is flattened to one ``sendcmd`` command per frame
    because ``eq`` has no time variable in its options (the same reason
    :mod:`vx.edl3_render` flattens its own grade).

``blur``
    ``gblur`` for ``gaussian`` (``sigma`` animatable through ``sendcmd``),
    ``boxblur`` for a static ``box``. ``motion`` and ``radial`` are refused with
    ``BLUR_STYLE_NOT_RENDERED``: a direction cannot be honestly expressed by an
    isotropic Gaussian, and inventing one would be a silent approximation.

``blend``
    ``blend`` with the declared mode, against another source label or, when the
    source is ``self``, against a black canvas of the same geometry. ``mix`` is
    the ``all_opacity`` value, animated through ``sendcmd`` when it is a span.

Curves
------

``linear``, ``in``, ``out`` and ``in_out`` are written as the same closed forms
:func:`vx.effects.compose.sample_curve` uses, so the graph samples the declared
maths rather than a lookalike. A ``keys`` track is a piecewise expression: one
polynomial segment per key, exactly as ``sample_curve`` interpolates it. A
``bezier`` curve is **refused** with ``CURVE_NOT_REPRESENTABLE``: inverting the
cubic x(t) for a general t is not an ffmpeg expression, and sampling it into
many linear steps would be an approximation the spec did not declare.

Budget
------

:func:`vx.effects.compose.cost` stays the estimate. A spec above ``budget``
(default :data:`DEFAULT_BUDGET`, 8) is refused with ``BUDGET_EXCEEDED`` and the
two numbers in the sentence; it is never rendered at a lower quality quietly.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .compose import (
    DEFAULT_DURATION,
    Issue,
    SpecError,
    canonical_easing,
    cost,
    expand,
    sample_param,
    validate,
)

#: The four primitives this bridge actually executes.
RENDERABLE_PRIMITIVES: Tuple[str, ...] = ("transform", "grade", "blur", "blend")
#: Declared and validated, but deliberately not executed by this bridge.
DECLARED_ONLY: Tuple[str, ...] = ("mask", "time", "text", "particles")
#: A spec above this cost is refused instead of rendered.
DEFAULT_BUDGET = 8.0
#: ffmpeg blend mode names for the spec's vocabulary.
BLEND_MODES = {
    "normal": "normal",
    "multiply": "multiply",
    "screen": "screen",
    "overlay": "overlay",
    "add": "addition",
    "difference": "difference",
    "soft_light": "softlight",
}


class RenderSpecError(ValueError):
    """A spec the bridge refuses. ``code`` is stable, ``message`` is Italian."""

    def __init__(self, code: str, message: str, where: str = "") -> None:
        self.code = code
        self.where = where
        self.message = message
        super().__init__(f"{code}: {message}" + (f" ({where})" if where else ""))


# --------------------------------------------------------------------------- #
# small formatting helpers
# --------------------------------------------------------------------------- #


def _num(value: float) -> str:
    """A stable decimal literal ffmpeg's expression parser always accepts."""
    return f"{float(value):.6f}"


def _is_pair(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    )


def _pair(value: Any) -> Optional[Tuple[float, float]]:
    return (float(value[0]), float(value[1])) if _is_pair(value) else None


def _animated(value: Any) -> bool:
    pair = _pair(value)
    return pair is not None and abs(pair[1] - pair[0]) > 1e-12


def _scalar(value: Any, default: float = 0.0) -> float:
    pair = _pair(value)
    if pair is not None:
        return pair[0]
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _label(name: str) -> str:
    text = str(name)
    return text if text.startswith("[") and text.endswith("]") else f"[{text}]"


def _clamped(expression: str) -> str:
    return f"clip({expression},0,1)"


# --------------------------------------------------------------------------- #
# curves
# --------------------------------------------------------------------------- #


def _ease_expression(name: str, u: str) -> str:
    """The closed form of one declared easing, or a declared refusal."""
    canonical = canonical_easing(name) or "linear"
    if canonical == "linear":
        return u
    if canonical == "in":
        return f"({u}*{u})"
    if canonical == "out":
        return f"(1-(1-{u})*(1-{u}))"
    if canonical == "in_out":
        return f"({u}*{u}*(3-2*{u}))"
    raise RenderSpecError(
        "CURVE_NOT_REPRESENTABLE",
        "la curva 'bezier' non è rappresentabile come espressione ffmpeg: "
        "invertire x(t) per un t generico non è una formula del linguaggio. "
        "Usa linear, in, out, in_out oppure una curva a chiavi.",
    )


def curve_expression(curve: Optional[Mapping[str, Any]], u_expression: str) -> str:
    """Translate a declared curve into an ffmpeg expression of ``u``.

    ``u_expression`` must already be normalised to ``[0, 1]``; the returned
    expression is the envelope value, from/to included.
    """
    u = _clamped(u_expression)
    if not isinstance(curve, Mapping):
        return u

    easing = curve.get("easing", "linear")
    keys = curve.get("keys")
    if isinstance(keys, (list, tuple)) and keys:
        stops = sorted(
            (
                (
                    float(key.get("t", 0.0)),
                    float(key.get("value", key.get("v", 0.0))),
                    canonical_easing(key.get("easing", "linear")) or "linear",
                )
                for key in keys
            ),
            key=lambda item: item[0],
        )
        raw = _num(stops[-1][1])
        for index in range(len(stops) - 2, -1, -1):
            t0, v0, key_easing = stops[index]
            t1, v1, _ = stops[index + 1]
            span = t1 - t0
            local = (
                "0"
                if span <= 0
                else _clamped(f"(({u})-{_num(t0)})/{_num(span)}")
            )
            segment = f"({_num(v0)}+({_num(v1 - v0)})*{_ease_expression(key_easing, local)})"
            raw = f"if(lt({u},{_num(t1)}),{segment},{raw})"
        # sample_curve returns the keys' own values: from/to are not applied to
        # a piecewise track, and the graph must agree with the declared maths.
        return f"if(lt({u},{_num(stops[0][0])}),{_num(stops[0][1])},{raw})"

    raw = _ease_expression(str(easing), u)
    start = float(curve.get("from", 0.0))
    end = float(curve.get("to", 1.0))
    return f"({_num(start)}+({_num(end - start)})*{raw})"


def _window(primitive: Mapping[str, Any], duration: float, clock: str) -> str:
    """Normalised time inside the primitive's ``at``/``duration`` window."""
    at = float(primitive.get("at", 0.0))
    length = float(primitive.get("duration", max(0.0, duration - at)))
    if length <= 0:
        length = duration if duration > 0 else 1.0
    return _clamped(f"(({clock})-{_num(at)})/{_num(length)}")


def _param_expression(
    primitive: Mapping[str, Any], name: str, u_expression: str
) -> str:
    params = primitive.get("params") or {}
    value = params.get(name)
    pair = _pair(value)
    if pair is None:
        return _num(_scalar(value, 0.0))
    start, end = pair
    if abs(end - start) <= 1e-12:
        return _num(start)
    progress = curve_expression(primitive.get("curve"), u_expression)
    return f"({_num(start)}+({_num(end - start)})*{progress})"


def _sampled_value(
    primitive: Mapping[str, Any], name: str, u: float
) -> float:
    try:
        return float(sample_param(primitive, name, u))
    except SpecError:
        return _scalar((primitive.get("params") or {}).get(name), 0.0)


def _frame_table(
    primitive: Mapping[str, Any],
    names: Sequence[str],
    duration: float,
    fps: float,
) -> List[Tuple[float, Dict[str, float]]]:
    """One entry per output frame: ``(t, {param: value})`` sampled by the curve."""
    at = float(primitive.get("at", 0.0))
    length = float(primitive.get("duration", max(0.0, duration - at)))
    if length <= 0:
        length = duration if duration > 0 else 1.0
    frames = max(1, int(round(max(0.0, duration) * max(1.0, fps))))
    rows: List[Tuple[float, Dict[str, float]]] = []
    for index in range(frames + 1):
        t = index / float(fps)
        if t > duration + 1e-9:
            break
        u = 0.0 if length <= 0 else (t - at) / length
        rows.append((t, {name: _sampled_value(primitive, name, u) for name in names}))
    return rows


def _sendcmd(
    target: str,
    rows: Sequence[Tuple[float, Mapping[str, float]]],
) -> str:
    commands: List[str] = []
    for t, values in rows:
        for name, value in values.items():
            commands.append(f"{_num(t)} {target} {name} {_num(value)}")
    return f"sendcmd=c='{';'.join(commands)}'"


# --------------------------------------------------------------------------- #
# per-primitive filters (single input)
# --------------------------------------------------------------------------- #


def _transform_filters(
    primitive: Mapping[str, Any],
    ordinal: int,
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
) -> List[str]:
    params = primitive.get("params") or {}
    rotate = _scalar(params.get("rotate"), 0.0)
    if abs(rotate) > 1e-9:
        raise RenderSpecError(
            "PARAM_NOT_RENDERED",
            "transform.rotate non è eseguito dal ponte ffmpeg: una rotazione "
            "cambia il canvas e il ponte non inventa la geometria di riempimento.",
            f"primitives[{ordinal}].params.rotate",
        )

    clock = f"(on/{_num(fps)})"
    u = _window(primitive, duration, clock)
    scale_expr = _param_expression(primitive, "scale", u)
    x_expr = _param_expression(primitive, "x", u)
    y_expr = _param_expression(primitive, "y", u)

    raw_scale = params.get("scale", 1.0)
    pair = _pair(raw_scale)
    minimum = min(pair) if pair is not None else _scalar(raw_scale, 1.0)
    animated_scale = _animated(raw_scale)
    start_x = _scalar(params.get("x"), 0.0)
    start_y = _scalar(params.get("y"), 0.0)
    needs_geometry = (
        minimum < 1.0 - 1e-9
        or animated_scale
        or _animated(params.get("x"))
        or _animated(params.get("y"))
        or abs(minimum - 1.0) > 1e-9
        or abs(start_x) > 1e-9
        or abs(start_y) > 1e-9
    )
    filters: List[str] = []

    if not needs_geometry:
        # A transform that only carries opacity must not pay for a scaler: the
        # default scale 1 and pan 0 are already the identity.
        pass
    elif minimum < 1.0 - 1e-9:
        if animated_scale:
            raise RenderSpecError(
                "PARAM_NOT_RENDERED",
                "transform.scale animato sotto 1.0 non è rappresentabile con "
                "zoompan (z >= 1); usa una scala >= 1.0 oppure un valore statico.",
                f"primitives[{ordinal}].params.scale",
            )
        out_w = max(2, int(round(width * minimum / 2.0)) * 2)
        out_h = max(2, int(round(height * minimum / 2.0)) * 2)
        x = int(round(_scalar(params.get("x"), 0.0)))
        y = int(round(_scalar(params.get("y"), 0.0)))
        pad_x = max(0, min(width - out_w, (width - out_w) // 2 + x))
        pad_y = max(0, min(height - out_h, (height - out_h) // 2 + y))
        filters.append(f"scale={out_w}:{out_h}:flags=bicubic")
        filters.append(
            f"pad={width}:{height}:{pad_x}:{pad_y}:color=black"
        )
    else:
        anchor = str(params.get("anchor", "center"))
        if anchor == "top_left":
            base_x, base_y = "0", "0"
        elif anchor == "top_right":
            base_x, base_y = "(iw-iw/zoom)", "0"
        elif anchor == "bottom_left":
            base_x, base_y = "0", "(ih-ih/zoom)"
        elif anchor == "bottom_right":
            base_x, base_y = "(iw-iw/zoom)", "(ih-ih/zoom)"
        else:
            base_x, base_y = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
        px = f"max(0,min(iw-iw/zoom,{base_x}+({x_expr})))"
        py = f"max(0,min(ih-ih/zoom,{base_y}+({y_expr})))"
        filters.append(
            f"zoompan=z='max(1,{scale_expr})':x='{px}':y='{py}':"
            f"d=1:s={int(width)}x{int(height)}:fps={_num(fps)}"
        )

    opacity_raw = params.get("opacity", 1.0)
    opacity_pair = _pair(opacity_raw)
    constant_one = (
        opacity_pair is None and abs(_scalar(opacity_raw, 1.0) - 1.0) <= 1e-12
    ) or (
        opacity_pair is not None
        and abs(opacity_pair[0] - 1.0) <= 1e-12
        and abs(opacity_pair[1] - 1.0) <= 1e-12
    )
    if not constant_one:
        op_clock = "T"
        op_u = _window(primitive, duration, op_clock)
        opacity = _param_expression(primitive, "opacity", op_u)
        filters.append("format=rgba")
        filters.append(
            f"geq=r='r(X,Y)*({opacity})':g='g(X,Y)*({opacity})':"
            f"b='b(X,Y)*({opacity})':a='255'"
        )
    return filters


def _lut_filters(lut: str) -> List[str]:
    table = {
        "warm": ["colorbalance=rs=0.080000:gs=0.020000:bs=-0.080000"],
        "cool": ["colorbalance=rs=-0.080000:gs=0.000000:bs=0.080000"],
        "teal_orange": [
            "colorbalance=rs=0.080000:bs=-0.060000:rh=-0.040000:bh=0.080000"
        ],
        "sepia": [
            "colorchannelmixer="
            ".393000:.769000:.189000:0:"
            ".349000:.686000:.168000:0:"
            ".272000:.534000:.131000:0"
        ],
        "noir": ["hue=s=0"],
        "bleach": ["eq=contrast=1.150000:saturation=0.550000"],
        "vibrant": ["eq=saturation=1.250000"],
    }
    return list(table.get(str(lut), []))


def _grade_filters(
    primitive: Mapping[str, Any],
    ordinal: int,
    *,
    fps: float,
    duration: float,
) -> List[str]:
    params = primitive.get("params") or {}
    filters: List[str] = _lut_filters(str(params.get("lut", "none")))

    temperature = params.get("temperature", 0.0)
    if _animated(temperature):
        raise RenderSpecError(
            "PARAM_NOT_RENDERED",
            "grade.temperature animato non è rappresentabile: colorbalance non "
            "accetta un comando per frame in questo ponte. Usa un valore statico.",
            f"primitives[{ordinal}].params.temperature",
        )
    temp = _scalar(temperature, 0.0)
    if abs(temp) > 1e-9:
        filters.append(
            f"colorbalance=rs={_num(temp * 0.100000)}:bs={_num(-temp * 0.100000)}"
        )

    names = ("brightness", "contrast", "saturation", "gamma")
    animated = [name for name in names if _animated(params.get(name))]
    target = f"eq@fxgrade{ordinal}"
    initial = {name: _scalar(params.get(name), 0.0) for name in names}
    settings = ":".join(f"{name}={_num(value)}" for name, value in initial.items())

    if animated:
        rows = _frame_table(primitive, animated, duration, fps)
        commands = _sendcmd(target, rows)
        filters.append(f"{commands},{target}={settings}")
    else:
        filters.append(f"{target}={settings}")
    return filters


def _blur_filters(
    primitive: Mapping[str, Any],
    ordinal: int,
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
) -> List[str]:
    params = primitive.get("params") or {}
    style = str(params.get("style", "gaussian"))
    if style == "radial":
        raise RenderSpecError(
            "BLUR_STYLE_NOT_RENDERED",
            "blur.style='radial' non è eseguito dal ponte: un gblur isotropo non "
            "è una sfocatura radiale e il ponte non finge che lo sia.",
            f"primitives[{ordinal}].params.style",
        )
    if style == "motion":
        raise RenderSpecError(
            "BLUR_STYLE_NOT_RENDERED",
            "blur.style='motion' non è eseguito dal ponte: gblur/boxblur non "
            "hanno una direzione e angle resterebbe ignorato.",
            f"primitives[{ordinal}].params.style",
        )
    if abs(_scalar(params.get("angle"), 0.0)) > 1e-9:
        raise RenderSpecError(
            "PARAM_NOT_RENDERED",
            "blur.angle è dichiarato solo per la sfocatura direzionale, che il "
            "ponte rifiuta; su gaussian/box non ha un effetto rappresentabile.",
            f"primitives[{ordinal}].params.angle",
        )

    sigma_raw = params.get("sigma", 4.0)
    target = f"gblur@fxblur{ordinal}"
    if style == "box":
        if _animated(sigma_raw):
            raise RenderSpecError(
                "PARAM_NOT_RENDERED",
                "blur.style='box' con sigma animato non è rappresentabile: "
                "boxblur non accetta un comando sigma per frame. Usa gaussian.",
                f"primitives[{ordinal}].params.sigma",
            )
        radius = max(1, int(round(_scalar(sigma_raw, 0.0))))
        radius = min(radius, max(1, min(width, height) // 2 - 1))
        return [f"boxblur=luma_radius={radius}:luma_power=1"]

    if _animated(sigma_raw):
        rows = _frame_table(primitive, ("sigma",), duration, fps)
        commands = _sendcmd(target, rows)
        return [f"{commands},{target}=sigma={_num(_scalar(sigma_raw, 0.0))}"]
    sigma = max(0.0, _scalar(sigma_raw, 0.0))
    if sigma <= 1e-9:
        return []
    return [f"{target}=sigma={_num(sigma)}"]


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #


def _first_issue(issues: Sequence[Issue]) -> RenderSpecError:
    issue = issues[0]
    return RenderSpecError(
        "SPEC_INVALID",
        issue.message,
        issue.where,
    )


def _normalisation(
    width: int, height: int, fps: float, duration: float
) -> str:
    return (
        f"scale={int(width)}:{int(height)}:"
        "force_original_aspect_ratio=increase:flags=bicubic,"
        f"crop={int(width)}:{int(height)},"
        f"fps={_num(fps)},"
        f"tpad=stop_mode=clone:stop_duration={_num(duration)},"
        f"trim=duration={_num(duration)},"
        "setpts=PTS-STARTPTS,setsar=1,format=yuv420p"
    )


def _other_source(
    source_name: str,
    ordinal: int,
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
    extra_sources: Mapping[str, Any],
) -> Tuple[str, bool]:
    """Return ``(label, is_generated)`` for a blend's second input."""
    name = str(source_name or "self")
    if name in extra_sources:
        return _label(extra_sources[name]), False
    if name not in ("self", ""):
        raise RenderSpecError(
            "BLEND_SOURCE_MISSING",
            f"blend.source={name!r} non corrisponde a nessuna sorgente fornita "
            f"(disponibili: {sorted(str(key) for key in extra_sources) or 'nessuna'}).",
            f"primitives[{ordinal}].params.source",
        )
    return f"[fxblack{ordinal}]", True


def plan_spec(
    spec: Any,
    *,
    width: int,
    height: int,
    fps: float,
    duration: Optional[float] = None,
    budget: float = DEFAULT_BUDGET,
    source: str = "[0:v]",
    output: str = "[vout]",
    extra_sources: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Compile one spec into filters, or refuse it with a declared reason.

    ``duration`` defaults to the spec's own. ``extra_sources`` maps a blend's
    ``source`` name to an ffmpeg label (``"[1:v]"``); ``self`` blends against a
    generated black canvas. Returns ``filters`` (the effect chain, embeddable
    with commas when it has no blend), ``chain`` (``None`` when a blend needs a
    multi-input graph), ``filter_complex`` (the complete graph), ``cost`` and
    ``estimate``.
    """
    normalized = expand(spec)

    issues = validate(normalized)
    if issues:
        raise _first_issue(issues)

    spec_duration = float(normalized.get("duration", DEFAULT_DURATION))
    span = float(duration) if duration is not None else spec_duration
    if not math.isfinite(span) or span <= 0:
        raise RenderSpecError(
            "SPEC_INVALID",
            f"la durata di render deve essere positiva (trovata {span!r})",
            "duration",
        )

    estimate = cost(normalized)
    limit = float(budget)
    if estimate > limit + 1e-9:
        raise RenderSpecError(
            "BUDGET_EXCEEDED",
            f"il costo della specifica è {estimate:.3f} ma il budget è {limit:.3f}: "
            "il render rifiuta invece di eseguirla a qualità ridotta.",
            "cost",
        )

    primitives = [
        primitive
        for primitive in normalized.get("primitives") or []
        if primitive.get("enabled", True)
    ]
    for index, primitive in enumerate(primitives):
        kind = primitive.get("kind")
        if kind not in RENDERABLE_PRIMITIVES:
            raise RenderSpecError(
                "PRIMITIVE_NOT_RENDERED",
                f"la primitiva {kind!r} è dichiarata e validata ma il renderer "
                f"ffmpeg non la esegue (eseguibili: {', '.join(RENDERABLE_PRIMITIVES)}).",
                f"primitives[{index}].kind",
            )
        curve = primitive.get("curve")
        if (
            isinstance(curve, Mapping)
            and canonical_easing(curve.get("easing", "linear")) == "bezier"
        ):
            raise RenderSpecError(
                "CURVE_NOT_REPRESENTABLE",
                "la curva 'bezier' non è rappresentabile come espressione ffmpeg: "
                "invertire x(t) per un t generico non è una formula del linguaggio "
                "e campionarla sarebbe un'approssimazione non dichiarata. Usa "
                "linear, in, out, in_out oppure una curva a chiavi.",
                f"primitives[{index}].curve.easing",
            )

    width = int(width)
    height = int(height)
    rate = float(fps)
    extra = dict(extra_sources or {})
    filters: List[str] = []
    graph: List[str] = []
    current = "[fx0]"
    graph.append(f"{_label(source)}{_normalisation(width, height, rate, span)}{current}")
    serial = 0
    has_blend = False

    for index, primitive in enumerate(primitives):
        kind = primitive.get("kind")
        next_label = f"[fx{serial + 1}]"
        if kind == "transform":
            stage = _transform_filters(
                primitive,
                index,
                width=width,
                height=height,
                fps=rate,
                duration=span,
            )
        elif kind == "grade":
            stage = _grade_filters(primitive, index, fps=rate, duration=span)
        elif kind == "blur":
            stage = _blur_filters(
                primitive,
                index,
                width=width,
                height=height,
                fps=rate,
                duration=span,
            )
        elif kind == "blend":
            has_blend = True
            stage = _blend_stage(
                primitive,
                index,
                current=current,
                next_label=next_label,
                width=width,
                height=height,
                fps=rate,
                duration=span,
                extra=extra,
                graph=graph,
            )
            current = next_label
            serial += 1
            continue
        else:  # pragma: no cover - checked above, kept as a guard
            raise RenderSpecError(
                "PRIMITIVE_NOT_RENDERED",
                f"la primitiva {kind!r} non è eseguita dal ponte ffmpeg.",
                f"primitives[{index}].kind",
            )

        if not stage:
            continue
        filters.extend(stage)
        graph.append(f"{current}{','.join(stage)}{next_label}")
        current = next_label
        serial += 1

    graph.append(f"{current}format=yuv420p{_label(output)}")
    return {
        "id": normalized.get("id", "custom"),
        "filters": filters,
        "chain": None if has_blend else ",".join(filters),
        "filter_complex": ";\n".join(graph) + "\n",
        "has_blend": has_blend,
        "cost": estimate,
        "budget": limit,
        "duration": span,
        "size": [width, height],
        "fps": rate,
        "primitives": [
            primitive.get("kind") for primitive in primitives
        ],
        "estimate": {
            "cost": estimate,
            "budget": limit,
            "primitives": len(primitives),
        },
    }


def _blend_stage(
    primitive: Mapping[str, Any],
    ordinal: int,
    *,
    current: str,
    next_label: str,
    width: int,
    height: int,
    fps: float,
    duration: float,
    extra: Mapping[str, Any],
    graph: List[str],
) -> List[str]:
    params = primitive.get("params") or {}
    mode = BLEND_MODES.get(str(params.get("mode", "normal")))
    if mode is None:
        raise RenderSpecError(
            "BLEND_MODE_NOT_RENDERED",
            f"blend.mode={params.get('mode')!r} non ha un corrispondente ffmpeg.",
            f"primitives[{ordinal}].params.mode",
        )
    other, generated = _other_source(
        params.get("source", "self"),
        ordinal,
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        extra_sources=extra,
    )
    if generated:
        graph.append(
            f"color=c=black:s={int(width)}x{int(height)}:r={_num(fps)}:"
            f"d={_num(duration)},format=yuv420p{other}"
        )
    else:
        graph.append(
            f"{other}{_normalisation(width, height, fps, duration)}[fxother{ordinal}]"
        )

    # mix is the blend's all_opacity; opacity scales it further.
    mix_raw = params.get("mix", 1.0)
    opacity = _scalar(params.get("opacity", 1.0), 1.0)
    target = f"blend@fxblend{ordinal}"
    if _animated(mix_raw):
        rows = _frame_table(primitive, ("mix",), duration, fps)
        scaled = [
            (
                t,
                {
                    "all_opacity": max(
                        0.0, min(1.0, values["mix"] * opacity)
                    )
                },
            )
            for t, values in rows
        ]
        commands = _sendcmd(target, scaled)
        graph.append(f"{current}{commands}[fxblendin{ordinal}]")
        current = f"[fxblendin{ordinal}]"
        initial = max(0.0, min(1.0, _scalar(mix_raw, 1.0) * opacity))
        opacity_arg = f":all_opacity={_num(initial)}"
    else:
        opacity_arg = f":all_opacity={_num(max(0.0, min(1.0, _scalar(mix_raw, 1.0) * opacity)))}"

    other_label = other if generated else f"[fxother{ordinal}]"
    graph.append(
        f"{current}{other_label}{target}=all_mode={mode}{opacity_arg}{next_label}"
    )
    return []


__all__ = [
    "BLEND_MODES",
    "DECLARED_ONLY",
    "DEFAULT_BUDGET",
    "RENDERABLE_PRIMITIVES",
    "RenderSpecError",
    "curve_expression",
    "plan_spec",
]
