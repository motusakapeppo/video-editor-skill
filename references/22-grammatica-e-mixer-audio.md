# 22 — Grammatica cinematografica e mixer audio A/B

Due finzioni diventano controlli: la regola dei 30° e quella dei 180° hanno bisogno
dei metadati della macchina da presa, e il J/L cut ha bisogno di un audio che non
sia più incollato al video. Il contratto passa a **EDL v2.2.0** (gain per clip,
ducking dichiarato, camera opzionale) e il render a un mixer non lineare.

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
- **nessun default**: `CameraSpec()` vale `(None, None)`. `None` significa "non
  dichiarato", mai `0°/35mm`. Un blocco `camera` vuoto (`{}`) è la sorgente con
  entrambi i campi non dichiarati;
- `camera` è il **quarto** campo di `SourceRef`, dopo `duration`: i chiamanti
  posizionali esistenti (`SourceRef(path, sha, durata)`) restano validi.

Validazione: se dichiarati, `angle_deg` è finito e `focal_length_mm > 0`.
`to_dict()` scrive solo i campi dichiarati; `from_dict()` rifiuta chiavi
sconosciute dentro `camera` con il percorso (`sources.src_01.camera.bad`).

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

## Camera non dichiarata: esenzione visibile

Un controllo non può essere aggirato in silenzio. Se manca il metadato che serve:

- la regola dei 30° è saltata quando **una delle due** sorgenti non ha
  `angle_deg` **o** `focal_length_mm`;
- la regola dei 180° è saltata quando **una delle due** sorgenti non ha
  `angle_deg`.

`grammar.camera_exemptions(plan) -> List[Dict[str, Any]]` restituisce una voce
per ogni taglio saltato, in ordine deterministico per tempo del taglio e poi per
id:

```json
{"cut_time_sec": 5.0, "clip_from_id": "c1", "clip_to_id": "c2",
 "missing": ["angle_deg", "focal_length_mm"], "rule": "both"}
```

`rule` è `"30deg"`, `"180deg"` o `"both"`; `missing` elenca i campi assenti.
L'esenzione è una **omissione dichiarata**, non una violazione: `ok` resta
`true` se non ci sono violazioni. `vx edl validate --json` espone sempre
`camera_exemptions` (lista vuota quando tutte le camere sono dichiarate).

## Validatore

`grammar.validate_cinematic_rules(plan) -> List[GrammarViolation]`

- `GrammarViolation` porta regola, tempo del taglio, clip e sorgenti coinvolte,
  dettaglio leggibile; `to_dict()` lo serializza;
- `grammar.assert_cinematic_rules(plan, *, require_camera=False)` solleva
  `GrammarError` con la lista delle violazioni; con `require_camera=True`
  solleva anche quando esiste almeno un'esenzione (regola
  `CAMERA_UNDECLARED`). Il default `False` mantiene i chiamanti esistenti;
- `vx edl validate` e `vx edl render` eseguono il validatore prima di qualsiasi
  stage: una violazione esce 2 con `errors`, `grammar` e `camera_exemptions` nel
  JSON.

## Gain per clip e ducking dichiarato

`ClipSegment.gain_db` è l'ultimo campo del dataclass (dopo `transition_out`),
default `0.0`, finito e in `[-60, +24]`. È serializzato solo quando diverso da
zero. Una traccia audio con un solo clip resta in forma piatta **solo** se il
gain è zero: con un gain diverso la traccia passa a `{"clips": [...]}` per non
perdere il valore.

`EDLPlan.ducking` è un `DuckingSpec` opzionale, dopo `notes`:

```json
{"sidechain_role": "dialogue", "target_role": "music", "threshold": 0.05,
 "ratio": 8.0, "attack_ms": 20.0, "release_ms": 250.0, "makeup_db": 0.0}
```

Intervalli (ffmpeg 8.1): `threshold` 0.000976563..1, `ratio` 1..20,
`attack_ms` 0.01..2000, `release_ms` 0.01..9000, `makeup_db` 0..36 (il `makeup`
lineare di ffmpeg ha minimo 1: un valore negativo in dB non è esprimibile).
`sidechain_role` e `target_role` devono essere diversi ed esistere entrambi tra
i `role` delle tracce audio: un ducking senza musica o senza dialogo è un no-op
silenzioso e viene rifiutato. `ducking` è emesso solo quando non è `None`.

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
  possono alzare il livello. `ducking` è nel contratto ma il renderer non emette
  ancora `sidechaincompress`.
- La camera è per sorgente, non per clip: una stessa sorgente usata con
  inquadrature diverse non è distinguibile.
- I metadati non vengono dedotti dal contenuto: se non dichiari `camera`, le
  regole 30°/180° sono **saltate** e l'omissione compare in `camera_exemptions`;
  non viene più assunto `0°/35mm`.
- Il percorso legacy `cli -> render.render` resta mono-sorgente e non usa il
  mixer; il percorso `vx edl` sì.
- Nessun QC finale su loudness/cue per `render_edl`: Gate 1 e grammatica sono
  l'audit aritmetico e sintattico.

## Test

- `tests/test_grammar_cinematic.py` (29): 30° violato e coperto, focale e
  angolo che scusano, 180° violato, neutra intermedia, stessa sorgente,
  camera ignota e parziale con esenzione, `require_camera`, `GrammarError`.
- `tests/test_dialogue_shift.py` (20): J e L, tracce separate, witness,
  lead 0/1.6, finding sbagliato, taglio inesistente, limiti di sorgente,
  due J, e un render J reale con `amix`.
- `tests/test_edl_plan.py` (138): include il round-trip di `CameraSpec`
  opzionale, `gain_db` e `DuckingSpec`.
- `tests/test_render_multisource.py` (24): `concat v=1:a=0`, `adelay`, `amix`.
- `tests/test_edl_cli.py` (15): include il rifiuto grammaticale con exit 2 e
  `camera_exemptions` nel JSON di validate.
