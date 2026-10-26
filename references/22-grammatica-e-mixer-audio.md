# 22 — Grammatica cinematografica e mixer audio A/B

Due finzioni diventano controlli: la regola dei 30° e quella dei 180° hanno bisogno
dei metadati della macchina da presa, e il J/L cut ha bisogno di un audio che non
sia più incollato al video. Il contratto passa a **EDL v2.1.0** e il render a un
mixer non lineare.

## Camera nella sorgente

`sources[id].camera` è una proprietà della sorgente fisica:

```json
"src_cam_a": {
  "path": "stage_A.mp4",
  "sha256": "<64 hex>",
  "camera": {"angle_deg": 0.0, "focal_length_mm": 35.0}
}
```

- `angle_deg`: 0 = frontale, negativo = sinistra, positivo = destra, rispetto
  alla linea d'azione;
- `focal_length_mm`: focale effettiva;
- default neutro: `0.0` / `35.0`;
- `camera` è il **quarto** campo di `SourceRef`, dopo `duration`: i chiamanti
  posizionali esistenti (`SourceRef(path, sha, durata)`) restano validi.

Validazione: `angle_deg` finito, `focal_length_mm > 0`. `to_dict()` scrive sempre
il blocco `camera`; `from_dict()` rifiuta chiavi sconosciute dentro `camera` con
il percorso (`sources.src_01.camera.bad`).

## Regola dei 30°

Per due clip adiacenti di layer 0 che vengono da **sorgenti diverse**:

- se `|Δangle| < 30°` **e** `|Δfocal| < 20 mm`, il taglio è un jump cut;
- è scusato solo da un B-roll (layer ≥ 1) che copre il taglio, con tolleranza
  ±0,05 s su `dst_in`/`dst_out`.

Stessa sorgente: il controllo non si applica, perché la macchina non è cambiata.

## Regola dei 180°

Se il segno di `angle_deg` si inverte e entrambi gli angoli sono non neutri
(`|angle| > 1°`), l'asse di campo è scavalcato. È permesso solo con un B-roll che
copre il taglio. Una clip neutra intermedia (`0°`) spezza la coppia e rende
legittimo il passaggio: `-45° → 0° → +45°` non è uno scavalcamento.

## Validatore

`grammar.validate_cinematic_rules(plan) -> List[GrammarViolation]`

- `GrammarViolation` porta regola, tempo del taglio, clip e sorgenti coinvolte,
  dettaglio leggibile; `to_dict()` lo serializza;
- `grammar.assert_cinematic_rules(plan)` solleva `GrammarError` con la lista;
- `vx edl validate` e `vx edl render` eseguono il validatore prima di qualsiasi
  stage: una violazione esce 2 con `errors` e `grammar` nel JSON.

## Mixer audio A/B

Il video resta un `concat` di layer 0 (`v=1:a=0`) più gli overlay B-roll. L'audio
è indipendente:

1. ogni clip audio: `atrim=start=src_in:end=src_out,asetpts=PTS-STARTPTS`;
2. posizionamento assoluto: `adelay={dst_in*1000}|{dst_in*1000}`;
3. somma: `amix=inputs=N:dropout_transition=0:normalize=0`.

Overlap, buchi, J/L e tracce multiple sono nativi. Non c'è più il vincolo che
l'audio rispecchi l'A-Roll: il mixer somma ciò che è dichiarato.

## J/L cut

`DialogueShift(at, lead_sec, finding_id, finding_source_id, t0, t1)`

- `at` è un tempo **sorgente** sul dialogo principale, in corrispondenza del
  taglio da spostare;
- `lead_sec > 0` = J cut: l'audio entrante parte prima del taglio video;
  richiede `finding_id = "audio.dialogue_lead"`;
- `lead_sec < 0` = L cut: l'audio uscente continua dopo il taglio video;
  richiede `finding_id = "audio.dialogue_trail"`;
- `0 < |lead_sec| <= 1.5`;
- il clip spostato va su una **traccia audio propria**, così l'overlap è
  legale e il mixer lo somma; il video non cambia;
- `src_in >= 0` e `src_out` entro la durata nota della sorgente;
- il witness usa `finding_source_id` (la fonte del dialogo);
- una seconda modifica sullo stesso clip è rifiutata: il witness non saprebbe
  quale dei due finding rappresentare.

## Limiti dichiarati

- `amix` somma, non applica un inviluppo di crossfade: due dialoghi sovrapposti
  possono alzare il livello. La dissolvenza audio non è ancora nel contratto.
- La camera è per sorgente, non per clip: una stessa sorgente usata con
  inquadrature diverse non è distinguibile.
- I metadati non vengono dedotti dal contenuto: se non dichiari `camera`, due
  sorgenti valgono `0°/35mm` e un taglio senza B-roll viene segnalato.
- Il percorso legacy `cli -> render.render` resta mono-sorgente e non usa il
  mixer; il percorso `vx edl` sì.
- Nessun QC finale su loudness/cue per `render_edl`: Gate 1 e grammatica sono
  l'audit aritmetico e sintattico.

## Test

- `tests/test_grammar_cinematic.py` (16): 30° violato e coperto, focale e
  angolo che scusano, 180° violato, neutra intermedia, stessa sorgente,
  `GrammarError`.
- `tests/test_dialogue_shift.py` (20): J e L, tracce separate, witness,
  lead 0/1.6, finding sbagliato, taglio inesistente, limiti di sorgente,
  due J, e un render J reale con `amix`.
- `tests/test_edl_plan.py` (108): include il round-trip di `CameraSpec`.
- `tests/test_render_multisource.py` (24): `concat v=1:a=0`, `adelay`, `amix`.
- `tests/test_edl_cli.py` (12): include il rifiuto grammaticale con exit 2.
