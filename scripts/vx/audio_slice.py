"""vx.audio_slice -- la fetta continua di un brano, e il suo punto di giunzione.

Il caso che ha motivato questo modulo: un loop Instagram di 50,88 s costruito su
una fetta continua del mix (``7,92`` -> ``58,80``). La fetta era giusta, ma il
salto fra ultimo e primo campione era **4,2 volte** il movimento normale del
segnale: un clic a ogni giro, che nessun flash copre. Sei millisecondi di
dissolvenza ai due bordi, lontani da qualsiasi sillaba, lo hanno portato a zero.

Qui la fetta e la sua verifica sono un comando solo, perche' separarle e' il modo
in cui il difetto torna: chi taglia non misura, chi misura non ha tagliato.

La fetta e' **campione-accurata**: ``atrim`` taglia sul tempo decodificato, non
sul contenitore. Il punto di giunzione si misura leggendo il WAV scritto, non
stimandolo dalla sorgente.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any, Dict

#: Oltre questo rapporto fra il salto di giunzione e il movimento normale del
#: segnale, il giro si sente. Il caso ha misurato 4,2x prima della correzione e
#: 0,0 dopo: 1,0 e' il confine dove il salto non e' piu' distinguibile da un
#: campione qualsiasi.
SEAM_RATIO_LIMIT = 1.0

#: Il percentile usato come "movimento normale": il 95esimo, cosi' un transiente
#: isolato non alza la soglia e un salto vero non si nasconde.
STEP_PERCENTILE = 95.0


def _mono_samples(raw: bytes, channels: int):
    """I campioni per fotogramma, mediati sui canali (mono equivalente)."""
    import numpy as np  # noqa: PLC0415 - solo per la misura del punto di giunzione

    data = np.frombuffer(raw, dtype="<i2")
    if data.size == 0:
        return np.zeros(0, dtype=np.float32)
    usable = (data.size // channels) * channels
    frames = data[:usable].reshape(-1, channels).astype(np.float32) / 32768.0
    return frames.mean(axis=1)


def _percentile(values, q: float) -> float:
    import numpy as np  # noqa: PLC0415

    if getattr(values, "size", 0) == 0:
        return 0.0
    return float(np.percentile(values, q))


def seam(path: str | Path) -> Dict[str, Any]:
    """Misura il punto di giunzione di un file audio: ultimo campione contro primo.

    ``ratio`` e' il salto diviso il movimento normale del segnale (95esimo
    percentile delle differenze fra campioni vicini). Sotto :data:`SEAM_RATIO_LIMIT`
    il giro non si sente; sopra, si sente.
    """
    import numpy as np  # noqa: PLC0415

    source = Path(path)
    with wave.open(str(source), "rb") as handle:
        channels = int(handle.getnchannels())
        rate = int(handle.getframerate())
        width = int(handle.getsampwidth())
        frames = int(handle.getnframes())
        raw = handle.readframes(frames)
    if width != 2:
        raise ValueError(f"{source.name}: serve PCM 16 bit, trovato {width * 8} bit")
    samples = _mono_samples(raw, channels)
    if samples.size < 3:
        raise ValueError(f"{source.name}: troppo corto per misurare la giunzione")
    steps = np.abs(np.diff(samples))
    typical = _percentile(steps, STEP_PERCENTILE)
    jump = abs(float(samples[0]) - float(samples[-1]))
    ratio = jump / typical if typical > 0 else 0.0
    return {
        "first": round(float(samples[0]), 6),
        "last": round(float(samples[-1]), 6),
        "jump": round(jump, 6),
        "typical_step": round(typical, 6),
        "ratio": round(ratio, 3),
        "continuous": ratio <= SEAM_RATIO_LIMIT,
        "samples": int(samples.size),
        "sample_rate": rate,
        "channels": channels,
        "duration": round(samples.size / float(rate), 6) if rate else 0.0,
    }


def slice_audio(
    source: str | Path,
    start: float,
    end: float,
    destination: str | Path,
    *,
    fade_in: float = 0.0,
    fade_out: float = 0.0,
) -> Dict[str, Any]:
    """Taglia ``[start, end)`` in un WAV PCM campione-accurato, con le dissolvenze.

    Le dissolvenze si applicano **dopo** il taglio e prima di ogni altra cosa:
    se stanno all'inizio della catena, ``atrim`` puo' spostarle. Il report dice
    sempre le dissolvenze richieste e il punto di giunzione misurato, cosi' chi
    legge sa se il clic e' stato tolto o solo sperato.
    """
    from . import ffmpeg

    origin = Path(source)
    if not origin.is_file():
        raise ValueError(f"sorgente assente: {origin}")
    duration = float(end) - float(start)
    if duration <= 0:
        raise ValueError(f"la fetta dura {duration:.3f} s: 'end' deve venire dopo 'start'")
    if fade_in < 0 or fade_out < 0:
        raise ValueError("le dissolvenze non possono essere negative")
    if fade_in + fade_out > duration:
        raise ValueError(
            f"dissolvenze {fade_in:g}+{fade_out:g} s su una fetta di {duration:g} s"
        )

    filters = [f"atrim=start={float(start):.6f}:end={float(end):.6f}", "asetpts=PTS-STARTPTS"]
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={float(fade_in):.6f}")
    if fade_out > 0:
        filters.append(
            f"afade=t=out:st={max(0.0, duration - float(fade_out)):.6f}:d={float(fade_out):.6f}"
        )

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = ffmpeg.run_ff(
        [
            "-i",
            str(origin),
            "-map",
            "0:a:0",
            "-af",
            ",".join(filters),
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        loglevel="error",
    )
    if not proc.ok or not target.is_file():
        raise RuntimeError(f"taglio fallito: {proc.tail(4) or 'nessun file scritto'}")

    measured = seam(target)
    return {
        "ok": True,
        "source": str(origin.resolve()),
        "out": str(target.resolve()),
        "start": round(float(start), 6),
        "end": round(float(end), 6),
        "duration": round(duration, 6),
        "fade_in": round(float(fade_in), 6),
        "fade_out": round(float(fade_out), 6),
        "seam": measured,
    }
