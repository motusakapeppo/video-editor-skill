"""vx.scenes — le composizioni che meritano un motore grafico, dichiarate prima.

Un browser sa comporre ciò che un grafo di filtri non sa dire: due riquadri, un
confronto prima/dopo, un lower third, testo dentro una forma. Ma il browser costa
50-200 ms per fotogramma, quindi non si usa sempre: si usa **quando la
composizione lo chiede**, e lo si dichiara.

Qui c'è la prima metà, quella che non costa nulla:

- :func:`wants` legge la richiesta e dice quale scena serve (o nessuna), con le
  parole che l'hanno deciso;
- :func:`build_html` scrive il documento della scena: HTML e CSS inline, niente
  JavaScript, niente rete, font di sistema — quindi lo stesso input produce lo
  stesso documento, byte per byte;
- il documento è la cosa che il motore (Chromium o Pillow) dovrà rasterizzare, e
  che la vista potrà mostrare appena il pezzo esiste.

Nessuna scena si inventa: se la richiesta non nomina una composizione, `wants`
restituisce ``None`` e il lavoro resta sulla pipeline normale.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Mapping, Optional

__all__ = ["SCENES", "wants", "build_html", "document_sha256", "declare"]

#: Le composizioni dichiarate, con le parole che le chiedono (italiano e inglese).
SCENES: Dict[str, Dict[str, Any]] = {
    "split_screen": {
        "words": ("split screen", "splitscreen", "due riquadri", "affiancato",
                  "side by side", "metà e metà", "meta e meta"),
        "why": "due sorgenti affiancate: layout, non un filtro",
    },
    "compare": {
        "words": ("prima e dopo", "prima/dopo", "confronto", "before after",
                  "before/after", "paragone"),
        "why": "confronto prima/dopo con etichette: layout e testo insieme",
    },
    "lower_third": {
        "words": ("lower third", "sottopancia", "nome e ruolo", "titolo in basso"),
        "why": "sottopancia con nome e ruolo: tipografia impaginata",
    },
}


def wants(request: Any) -> Optional[Dict[str, Any]]:
    """Quale scena chiede la richiesta, con le parole che l'hanno decisa.

    Una sola scena per lavoro: la prima che compare nell'ordine dichiarato, così
    la scelta è riproducibile. Nessuna parola, nessuna scena.
    """
    text = str(request or "").lower()
    for scene_id, spec in SCENES.items():
        hits = [word for word in spec["words"] if word in text]
        if hits:
            return {
                "id": scene_id,
                "signals": hits,
                "why": spec["why"],
                "engine": "chromium",
            }
    return None


def _escape(text: Any) -> str:
    raw = str(text or "")
    return (
        raw.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_html(scene: Mapping[str, Any], *, width: int = 1080, height: int = 1920) -> str:
    """Il documento della scena, deterministico e senza risorse esterne.

    ``scene`` porta ``id`` e, secondo la scena, ``left``/``right`` (etichette) o
    ``name``/``role``. Font di sistema, nessun orologio, nessun random: due
    chiamate con lo stesso input danno la stessa stringa.
    """
    scene_id = str(scene.get("id") or "")
    if scene_id not in SCENES:
        raise ValueError(f"scena sconosciuta: {scene_id!r}")
    left = _escape(scene.get("left") or "PRIMA")
    right = _escape(scene.get("right") or "DOPO")
    name = _escape(scene.get("name") or "")
    role = _escape(scene.get("role") or "")
    style = (
        "html,body{margin:0;padding:0;background:#000;}"
        f".stage{{width:{int(width)}px;height:{int(height)}px;position:relative;"
        "font-family:'Segoe UI',system-ui,-apple-system,Arial,sans-serif;color:#fff;}"
        ".pane{position:absolute;top:0;height:100%;width:50%;}"
        ".pane.left{left:0;}.pane.right{right:0;}"
        ".seam{position:absolute;left:50%;top:0;width:6px;height:100%;"
        "transform:translateX(-3px);background:#fff;opacity:.9;}"
        ".tag{position:absolute;bottom:120px;left:50%;transform:translateX(-50%);"
        "font-size:44px;font-weight:700;letter-spacing:.06em;"
        "background:rgba(0,0,0,.55);padding:10px 22px;border-radius:8px;}"
        ".third{position:absolute;left:60px;bottom:180px;max-width:60%;padding:18px 26px;"
        "background:rgba(0,0,0,.62);border-left:6px solid #fff;}"
        ".name{font-size:46px;font-weight:700;line-height:1.1;}"
        ".role{font-size:30px;opacity:.85;margin-top:6px;}"
    )
    if scene_id in ("split_screen", "compare"):
        body = (
            '<div class="pane left"></div><div class="pane right"></div>'
            '<div class="seam"></div>'
            f'<div class="tag">{left}</div>'
        )
        if scene_id == "compare":
            body += f'<div class="tag" style="left:75%">' + right + "</div>"
    else:
        body = (
            '<div class="third">'
            f'<div class="name">{name}</div><div class="role">{role}</div>'
            "</div>"
        )
    return (
        "<!doctype html><html lang=\"it\"><head><meta charset=\"utf-8\">"
        f"<style>{style}</style></head><body><div class=\"stage\">{body}"
        "</div></body></html>"
    )


def document_sha256(html: str) -> str:
    """L'identità del documento: se cambia, cambia la scena (e il golden)."""
    return hashlib.sha256(str(html).encode("utf-8")).hexdigest()


def declare(request: Any) -> List[Dict[str, Any]]:
    """Cosa dichiarare nel report: la scena chiesta, il motore, l'identità.

    Non renderizza niente: dice cosa servirà, così la vista può mostrarlo prima
    che il pezzo esista e il contratto sa cosa aspettarsi.
    """
    wanted = wants(request)
    if wanted is None:
        return []
    html = build_html(wanted)
    return [
        {
            "id": wanted["id"],
            "engine": wanted["engine"],
            "why": wanted["why"],
            "signals": wanted["signals"],
            "document_sha256": document_sha256(html),
            "status": "declared",
        }
    ]
