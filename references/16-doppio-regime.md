# 16 — Doppio regime: STRICT e AUTONOMOUS

Il motore ha due modi di rispondere allo stesso contratto, scelti dalla specificità
del prompt. Non sono due prodotti: sono due gradi di iniziativa.

## Selettore

`decide.calculate_specificity_score(prompt)` misura 7 parametri espliciti:
geometria/aspect, BPM, colore hex, font, politica di taglio, durata, engine.
`select_mode` restituisce `strict` se il punteggio è **>= 0.7**, altrimenti
`autonomous`.

## STRICT — esegue

- I parametri espliciti sono il contratto; non si inventa nulla.
- Un aggettivo non quantificato ferma il motore con `AmbiguousContractError`:
  `[vague_vintage]`, `[vague_dynamic]`, `[asset_missing]`.
- Il payload dichiara solo ciò che il prompt contiene: se il colore non c'è,
  `color_palette.type = "none"` e il rationale lo dice.
- I gate fisici 1-5 restano invariati.

## AUTONOMOUS — sceglie e motiva

- Non si ferma sugli aggettivi: deduce piattaforma, formato, palette, ritmo e
  tipografia, e li scrive in `execution_context.creative_rationale`.
- Ogni scelta ha una riga di rationale. Il payload è valido solo se `pacing`,
  `color_palette` e `typography` hanno un rationale non vuoto.
- I default sono dichiarati, non misurati: duotone caldo per "vintage", 120 BPM
  per "dinamico", Antonio 72 px. Se un font richiesto non è nel registro, si usa
  Antonio e il rationale lo dichiara.
- I gate fisici 1-5 restano invariati: l'autonomia decide *cosa*, non *come*.

## Gate R&D selettivo

Tre contesti: specifiche piattaforma, reference di stile, sintassi FFmpeg non
usuale. Il modulo Python **non fa rete**. Il payload porta tre campi distinti:

- `rd_search_required: true` quando il target non è nei preset;
- `rd_search_conducted: false` finché l'agente non ha davvero cercato;
- `rd_search_sources: []` finché non ci sono fonti.

L'agente esegue la ricerca, cita le fonti e solo allora porta `conducted` a true.
Un `conducted: true` senza fonti è invalido (`validate_scene_payload`). Questo
evita la dichiarazione falsa che il vecchio nome `rd_search_conducted` produceva
quando il solo flag Python veniva letto come "ricerca fatta".

## Payload

```json
{
  "contract_version": "1.3.0",
  "execution_context": {
    "mode": "autonomous",
    "specificity_score": 0.14,
    "creative_rationale": {
      "genre_detected": "reel_promo",
      "pacing": {"bpm_target": 120, "cut_policy": "beat_synced", "rationale": "..."},
      "color_palette": {"type": "duotone", "shadows": "#1A100C", "highlights": "#FFE8C8", "rationale": "..."},
      "typography": {"font_family": "Antonio", "size_px": 72, "rationale": "..."},
      "rd_search_required": false,
      "rd_search_conducted": false,
      "rd_search_sources": []
    }
  },
  "scene": {"aspect_ratio": "9:16", "resolution": [1080, 1920], "engine": "pillow"}
}
```

## CLI

`python -m vx.decide --prompt "..." [--assets "Antonio,Inter"]`

Exit 0 con payload valido, 3 se STRICT è ambiguo, 2 se il payload non valida.

`python -m vx.cli run --input clip.mp4 --payload scene_payload.json`
esegue il payload: `_ingest` analizza, `payload_to_edit_plan` produce l'EditPlan,
il finding `brief.autonomous` entra nel report e il render esegue. `--prompt`
genera il payload prima, con le stesse regole.

## Engine

Predefinito: **ffmpeg/Pillow**. Chromium resta confinato a CSS Grid,
`backdrop-filter`, layout reflow e blend non separabile su contenuto animato
(ref 15). Un `scene.engine = "chromium"` finisce in `bridge.unsupported`, non
viene finto.

## Cosa non cambia

La legge dell'evidenza e i gate 1-5. L'autonomia non è un permesso per saltare una
misura: è un permesso per scegliere tra misure dichiarate.
