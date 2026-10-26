# 19 — EDL multi-sorgente v2.1

Il motore mono-sorgente (`Edl`, un clip → una timeline) non basta più quando il
montaggio accosta A-Roll e B-Roll da file diversi. Questo modulo aggiunge il
contratto **EDL v2.1.0** (v2.0 + metadati camera) senza toccare `Edl`: il modello
vive in `scripts/vx/edl_plan.py`, `edl.py` lo ri-esporta in fondo al file.

## Forma JSON

```json
{
  "contract_version": "2.1.0",
  "timeline": {"fps": 25.0, "duration_sec": 45.0, "aspect_ratio": "9:16", "resolution": [1080, 1920]},
  "sources": {"src_01": {"path": "stage/main.mp4", "sha256": "<64 hex>", "duration": 45.0,
                          "camera": {"angle_deg": 0.0, "focal_length_mm": 35.0}}},
  "tracks": {
    "video": [
      {"track_id": 0, "role": "main", "clips": [
        {"clip_id": "c1", "source_id": "src_01", "src_in": 0.0, "src_out": 10.0,
         "dst_in": 0.0, "dst_out": 10.0, "layer": 0, "role": "main_talking_head"}
      ]},
      {"track_id": 1, "role": "broll", "clips": [
        {"clip_id": "b1", "source_id": "src_02", "src_in": 0.0, "src_out": 2.0,
         "dst_in": 3.0, "dst_out": 5.0, "layer": 1, "role": "broll_overlay",
         "transition_in": {"type": "fade", "duration_sec": 0.3}}
      ]}
    ],
    "audio": [
      {"track_id": 0, "role": "dialogue", "source_id": "src_01",
       "src_in": 0.0, "src_out": 10.0, "dst_in": 0.0, "dst_out": 10.0}
    ]
  }
}
```

Regole di serializzazione:

- le tracce video portano sempre `clips`;
- una traccia audio con **un solo** clip è la forma piatta qui sopra (senza
  `clip_id`, `layer`, `clips`); con più clip passa a `clips`. Un oggetto che
  contiene sia i campi diretti sia `clips` viene rifiutato: niente doppia
  grammatica silenziosa;
- `resolution` è una coppia di interi pari positivi; `aspect_ratio` è `W:H`;
- `sha256` è esattamente 64 caratteri esadecimali minuscoli;
- `clip_id` è un'etichetta, non contenuto: la forma piatta dell'audio non può
  portarlo, quindi l'uguaglianza del segmento ignora `clip_id`. L'unicità è
  verificata esplicitamente da `validate()`, non dall'uguaglianza.

## Invarianti di validazione

`EDLPlan.validate()` restituisce una lista di errori con il percorso
(`tracks.video[0].clips[1].source_id`); `from_dict()` li raccoglie e solleva
`EDLValidationError`. Chiavi sconosciute a ogni livello sono rifiutate con il
percorso esatto.

- `contract_version` esattamente `2.1.0`; `fps > 0`; `duration_sec > 0`.
- `sources` non vuota; chiavi `[A-Za-z0-9_.-]+`; path non vuoto; sha a 64 hex.
- ogni `camera`, se presente, ha `angle_deg` finito e `focal_length_mm > 0`;
  il default è `0°/35mm`.
- almeno una traccia video, ogni traccia con almeno un clip; `clip_id` unico
  globalmente; `track_id` unico dentro video e dentro audio (video e audio
  possono riusare lo stesso id).
- ogni `source_id` esiste in `sources`; ogni ruolo (clip e traccia) non vuoto.
- `src_out > src_in`, `dst_out > dst_in`; la durata destino non differisce da
  quella sorgente per più di un frame (`1/fps`).
- `dst_in >= 0`, `dst_out <= duration_sec`; `layer >= 0`; un clip
  `broll_overlay` deve stare su `layer >= 1`.
- transizioni: tipo non vuoto, durata tra 0 e `min(durata clip, 1.5)`.
- nessuna sovrapposizione tra clip della **stessa traccia e stesso layer**;
  la sovrapposizione tra layer diversi è il senso del B-roll.
- i clip di layer 0 partono da 0 e sono contigui; l'unione di tutti i clip
  video copre `[0, content_end]`. `duration_sec` può essere più lunga
  (coda/hold): 45 s di timeline con clip fino a 20 s è valido.
- J/L cut: per ogni traccia audio con `role == "dialogue"`, ogni confine
  interno `(0, duration_sec)` deve distare al massimo 1,5 s da un confine video
  di layer 0. Le tracce `music`/`sfx` sono ignorate; un unico clip audio che
  attraversa più tagli video, senza confini interni, è valido.

## Legge dell'evidenza multi-sorgente

Il contratto passa a **v1.4.0** (`contract.py`, `SKILL.md`, `README.md`). La
firma diventa:

```python
validate_evidence(plan, findings, duration=None, *, sources=None)
```

- `sources=None`: resta la legge v1. Un testimone senza `source_id` è valido;
  se `source_id` c'è, deve essere una stringa non vuota.
- `sources` dichiarata: **ogni** testimone di un trattamento attivo deve portare
  un `source_id` non vuoto che sia una chiave di `sources`. Se il finding
  dichiara a sua volta `source_id`, i due devono coincidere.
- `validate_plan` passa `plan["sources"]` o `report["sources"]`; senza mappa
  resta v1.
- `plan.py` e `decide.py` aggiungono `source_id` **solo** quando la sorgente lo
  dichiara. I piani single-source mantengono il dizionario v1 identico, e i due
  test a dizionario esatto continuano a passare.

La soglia non è aggirabile: chi dichiara una mappa multi-sorgente non può
lasciare un testimone senza fonte.

## Costruzione A-Roll + B-Roll

`cutplan.build_edl_plan(edl, *, source_id, sources, broll=(), dialogue_shifts=(),
fps=None, duration=None, aspect_ratio="9:16", resolution=None) -> BuiltEdl`

- ogni `EdlShot` diventa un clip di layer 0 con `role="main_talking_head"`;
- la traccia audio `dialogue` parte come specchio degli stessi clip; un
  `DialogueShift` (v2.1) sposta un confine di al massimo 1,5 s e mette quel clip
  su una traccia propria, così l'overlap è legale e il mixer lo somma;
- ogni `BrollCandidate` diventa un clip di **una traccia video separata**
  (`track_id=1`, `layer=1`), con fade in/out. Metterlo nella traccia A-Roll
  violerebbe l'invariante di non sovrapposizione;
- `BrollCandidate.at` è un tempo **sorgente** sul dialogo principale. Il builder
  trova lo shot che lo contiene e mappa
  `dst_in = shot.t_in + (at - shot.src_in)`;
- il finding deve stare dentro lo stesso shot (`t0 >= src_in`,
  `t1 <= src_out`), altrimenti la giustificazione è ambigua e il builder rifiuta;
- il testimone usa `finding_source_id` (la sorgente del dialogo), mai la
  sorgente del B-roll: il timestamp è stato misurato lì;
- `BuiltEdl` restituisce anche `witnesses: clip_id -> testimone`, così la prova
  non sparisce dopo la costruzione;
- rifiuta con `EDLValidationError`: finding su un'altra sorgente, finding fuori
  da ogni shot, B-roll oltre la durata, B-roll sovrapposti, sorgente B-roll non
  dichiarata, `src_out` oltre la durata nota della sorgente.

## Cosa non è ancora

- Il percorso legacy (`cli -> render.render`) resta mono-sorgente; il percorso
  EDL usa `render.render_edl`, descritto in
  `references/20-render-multisorgente.md`.
- `amix` somma le tracce senza un inviluppo di crossfade: l'overlap J/L può
  alzare il livello. La dissolvenza audio non è ancora nel contratto.
- La camera è per sorgente, non per clip: la stessa sorgente usata con
  inquadrature diverse non è distinguibile.
- `notes` non è serializzato: il formato del contratto non lo prevede.

## Test

`tests/test_edl_plan.py`: 108 assert. Modello (forma JSON, chiavi sconosciute,
sorgenti, camera, id, aritmetica dei tempi, layer, transizioni, copertura, J/L,
shorthand audio, immutabilità, round-trip su disco) e builder (due tracce,
mappatura dell'orologio, witness, cinque rifiuti, round-trip su due clip reali
generate con ffmpeg).

`tests/test_evidence_sources.py`: 14 assert sulla legge v1.4.0 e sulla
compatibilità v1.

Regressioni: `test_cutplan`, `test_animplan`, `test_gradeplan`,
`test_evidence_law`, `test_payload_bridge`, `test_payload_cli`.
