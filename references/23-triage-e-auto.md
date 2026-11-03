# 23 — Triage degli asset e comando auto

La Fase 4 non parte più da un solo file: parte da una cartella eterogenea di
video, immagini e musica. Prima del piano, un cancello misura ciò che può e
decide cosa merita la timeline. Due regole dominano il modulo: l'omissione è
dichiarata, e sotto incertezza non si rifiuta. Il contratto resta `1.5.0`
(`vx.report/1.0`); l'EDL passa a **2.2.0** e il master ha un QC di loudness
proprio.

## Cosa decide il triage

`asset_triage.triage_assets(assets, target_resolution, ...)` classifica ogni
asset in `video`, `image` o `audio`, misura ciò che è economico e disponibile,
e rifiuta solo quando la misura è inequivocabile. Il risultato ha cinque
blocchi:

```json
{
  "selected": [{"asset_id": "a.mp4", "kind": "video", "selected": true,
                "role": "a_roll", "target_duration_sec": 12.0,
                "treatment": "none", "ducking_db": 0.0,
                "rejection_reason": null, "details": {}, "confidence": 1.0}],
  "rejected": [{"asset_id": "b.mp4", "kind": "video", "selected": false,
                "role": "b_roll", "rejection_reason": "TOO_SHORT",
                "details": {"effective_duration_sec": 0.5, "threshold": 1.0},
                "confidence": 1.0}],
  "not_evaluated": ["semantic_anchor"],
  "thresholds": {"triage_max_upscale": 1.5},
  "role_source": "heuristic"
}
```

`payload_block()` è la forma esterna che un payload di scena può portare:
`selected_assets`, `rejected_assets`, `not_evaluated`, `thresholds`,
`role_source`. Ogni asset scartato compare in `rejected` con un motivo del
vocabolario chiuso e `details` non vuoto; un asset che non arriva in timeline
non sparisce. Un asset selezionato porta `role`, `target_duration_sec`,
`treatment` e, solo se non è zero, `ducking_db`.

I ruoli si assegnano dal manifest quando c'è, altrimenti per euristica: l'audio
è `music`; il primo video con traccia audio è `a_roll`; tutto il resto è
`b_roll`. Il cap dei B-roll è 3, quello della musica è 1. I B-roll si ordinano
prima per ancoraggio dichiarato, poi per risoluzione decrescente, poi per
`asset_id`; la musica per `asset_id`.

## Vocabolario chiuso dei rifiuti

`REJECTIONS` è un `frozenset`: un motivo fuori elenco è un payload invalido.

```
UNREADABLE  TOO_SHORT  RESOLUTION_TOO_LOW  DUPLICATE_ASSET
AUDIO_TOO_NOISY  VIDEO_TOO_BLURRY  VIDEO_TOO_DARK  MUSIC_INCOHERENT
NO_SEMANTIC_ANCHOR  REDUNDANT_BROLL  REDUNDANT_MUSIC
```

- `UNREADABLE`: probe fallito o tipo non riconosciuto.
- `TOO_SHORT`: durata effettiva sotto `triage_min_duration_sec` (1.0 s); per un'immagine vale `triage_image_duration_sec` (3.0 s).
- `RESOLUTION_TOO_LOW`: upscale lineare oltre `triage_max_upscale` (1.5).
- `DUPLICATE_ASSET`: stesso `size` e stesso `sha256` di un asset già visto.
- `AUDIO_TOO_NOISY`: `snr_db` sotto `noise_snr_low` (12.0) con confidenza > 0.8.
- `VIDEO_TOO_BLURRY`: `sharpness` sotto `triage_sharpness_low` (50.0) con confidenza > 0.8.
- `VIDEO_TOO_DARK`: `mean_luma` sotto `luma_under` (45.0) con confidenza > 0.8.
- `MUSIC_INCOHERENT`: `music_ratio` sotto `triage_music_ratio_low` (0.5) con confidenza > 0.8.
- `NO_SEMANTIC_ANCHOR`: B-roll senza `at_sec` quando `require_anchor=True`.
- `REDUNDANT_BROLL`: B-roll oltre il cap di 3.
- `REDUNDANT_MUSIC`: musica oltre il cap di 1.

## Regola geometrica

Per video e immagini con dimensioni note:

```
upscale = max(target_w / src_w, target_h / src_h)
```

Il confronto è stretto: `upscale > 1.5` rifiuta. Un valore di 1.5 passa, un
valore di 1.5001 rifiuta. Il rifiuto porta `upscale`, `max_upscale`, `src_w`,
`src_h`, `target_w`, `target_h` in `details`, così il numero è verificabile.
Un asset senza dimensioni note non entra in questo controllo.

## Inazione sotto incertezza

La confidenza non è un punteggio assoluto: è la distanza relativa dalla soglia.

```
confidence = min(1.0, abs(value - threshold) / max(0.01, abs(threshold)))
```

Il rifiuto scatta solo con `value < threshold` **e** `confidence > 0.8`. Una
confidenza in `[0.2, 0.8]` non rifiuta mai: l'asset viene selezionato e marcato
`details.uncertain = true`, con `details.signal = "ambiguous"`. Se tutte le
confidenze sono sotto 0.2 il segnale è `"none"`; se nessuna cade in `[0.2, 0.8]`
e almeno una è sopra 0.8, è `"ok"`. Se nessuna metrica è stata misurata,
`details.signal` è `"none"` e non si inventa una confidenza.

## not_evaluated

Una metrica non misurata non diventa zero: il suo nome finisce in
`not_evaluated`. I nomi sono `video_sharpness`, `video_exposure`,
`audio_noise`, `music_incoherent` e `semantic_anchor`. Una misura fallita è
assenza, non un valore: la metrica non viene usata per rifiutare e il suo nome
finisce in `not_evaluated`. Il percorso automatico misura nitidezza ed
esposizione, ma non calcola `snr_db` né `music_ratio`: senza una misura
iniettata, `audio_noise` e `music_incoherent` restano in `not_evaluated` e
nessun asset viene scartato per rumore o per incoerenza musicale.

## Duplicati

La deduplica è economica e onesta: prima si raggruppa per `size`, poi si calcola
`sha256` **solo** dentro i gruppi con almeno due file. Il primo del gruppo
resta; i successivi diventano `DUPLICATE_ASSET` con `duplicate_of`, `method: "sha256"` e `size`. Non c'è un confronto percettivo: due file diversi con lo stesso contenuto visivo non sono duplicati.

## Immagini e audio: staging media-agnostico

Un'immagine non entra nel grafo come immagine. `ingest.normalize_image` la
staged come un MP4 con `scale_fit_crop`, `ken_burns_zoom_in` (o `_out`), fps e
durata `triage_image_duration_sec` (3.0 s). Il grafo di render vede solo clip
video, quindi non ha un ramo "immagine". Un'immagine non ha audio; un file
musicale non ha video. Il triage lo sa e non chiede all'uno ciò che l'altro non
può dare.

## EDL 2.2

`edl_plan.EDL_CONTRACT_VERSION` è `"2.2.0"` e `EDL_SCHEMA` è `"vx.edl/2.2"`;
`contract.VERSION` resta `"1.5.0"`. Tre aggiunte:

- `ClipSegment.gain_db`: ultimo campo, default `0.0`, finito e in `[-60, +24]`; serializzato solo se diverso da zero. Una traccia audio con un solo clip resta piatta solo con gain zero; con un gain diverso passa a `{"clips": [...]}`.
- `EDLPlan.ducking`: un `DuckingSpec` opzionale dopo `notes`, con default `sidechain_role="dialogue"`, `target_role="music"`, `threshold=0.05`, `ratio=8.0`, `attack_ms=20.0`, `release_ms=250.0`, `makeup_db=0.0`. I ruoli devono essere diversi ed esistere tra le tracce audio; un ducking senza dialogo o senza musica è un no-op silenzioso e viene rifiutato.
- `SourceRef.camera`: un `CameraSpec` opzionale, quarto campo dopo `duration`; `CameraSpec()` vale `(None, None)`, e `None` significa "non dichiarato", mai `0°/35mm`.

## Regole 30°/180° e camera ignota

Per due clip adiacenti di layer 0 che vengono da sorgenti diverse:

- **30°**: se `|Δangle| < 30°` **e** `|Δfocal| < 20 mm`, il taglio è un jump
  cut. È scusato solo da un B-roll (layer ≥ 1) che copre il taglio, con
  tolleranza ±0.05 s su `dst_in`/`dst_out`. Stessa sorgente: il controllo non
  si applica.
- **180°**: se il segno di `angle_deg` si inverte e entrambi gli angoli sono
  non neutri (`|angle| > 1°`), l'asse di campo è scavalcato. È permesso solo
  con un B-roll che copre il taglio. Una clip neutra intermedia (`0°`) spezza
  la coppia.

Se il metadato manca, il controllo non passa in silenzio:

- la regola dei 30° è saltata quando **una delle due** sorgenti non ha
  `angle_deg` **o** `focal_length_mm`;
- la regola dei 180° è saltata quando **una delle due** sorgenti non ha
  `angle_deg`.

`grammar.camera_exemptions(plan)` restituisce una voce per ogni taglio saltato,
in ordine deterministico per tempo del taglio e poi per id:

```json
{"cut_time_sec": 5.0, "clip_from_id": "c1", "clip_to_id": "c2",
 "missing": ["angle_deg", "focal_length_mm"], "rule": "both"}
```

`rule` è `"30deg"`, `"180deg"` o `"both"`; `missing` elenca i campi assenti.
`grammar.assert_cinematic_rules(plan, require_camera=True)` solleva
`GrammarError` con `CAMERA_UNDECLARED` quando esiste almeno un'esenzione. Il
default `False` mantiene i chiamanti esistenti.

## Mixer del render

Il video resta un `concat` di layer 0 più gli overlay B-roll. L'audio è
indipendente e si divide in due bus:

1. **Dialogo**: ogni clip fa `atrim`, `asetpts=PTS-STARTPTS`, `volume` se
   `gain_db != 0`, `afade` in/out di `dialogue_fade_ms` (12 ms, ridotto a metà
   durata se il clip è più corto), poi `adelay` a `dst_in`.
2. **Musica**: stessa catena senza fade; il gain è statico, da `gain_db`.
3. **Bus**: `anull` per un solo clip, altrimenti
   `amix=inputs=N:dropout_transition=0:normalize=0`; poi
   `apad=whole_dur=<durata>,atrim=start=0:end=<durata>` e
   `aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo`.

Con `ducking`, il bus dialogo viene diviso con `asplit=2`: una copia va al mix,
l'altra è la chiave di `sidechaincompress` sul bus musica. `makeup` è lineare,
`max(1.0, 10^(makeup_db/20))`, perché ffmpeg non esprime un makeup negativo in
dB. `apad` + `atrim` servono perché `sidechaincompress` non ha `eof_action`:
entrambi gli ingressi devono finire insieme, alla durata della timeline.

Il master loudness è un passaggio a due tempi: si renderizza il file
pre-loudness, si misura con `loudnorm=print_format=json`, poi si applica il
secondo passaggio con i valori misurati. In quel passaggio `-c:v copy` è legale
solo perché non taglia: il video è già finale.

## QC del master

`qc.audit_master(path, target_lufs=..., target_tp=..., tolerance_lu=1.5)`
misura il file consegnato, non la dichiarazione. I check sono `master_exists`,
`master_audio`, `master_duration`, `master_lufs`, `master_true_peak`. La
tolleranza deve essere `> 0` e `<= 3.0` LUFS: 3.0 è il tetto di consegna, non un
parametro allargabile. Il true peak deve stare sotto `target_tp + 0.1` dBTP.
Un fallimento solleva `QCFailure`, che la CLI mappa su exit 5.

## vx auto

`vx auto` esegue triage, piano, render e QC in un colpo:

```
vx auto --prompt "<brief>" --assets "<cartella>" --out "<cartella>"
        [--workdir "<cartella>"] [--fps <n>] [--manifest "<file>"]
        [--autonomous] [--crf <n>] [--preset <nome>] [--json]
```

Exit code: `0` ok, `2` uso o validazione, `5` QC o Gate 1, `6` render. Scrive
`plan.json`, `auto_report.json` e `<workdir>/out`. La workdir è protetta da un
lock per liveness: il file `.lock` porta PID, token, creazione del processo e
heartbeat; un owner vivo tiene il lock, un owner morto viene reclamato. Il
heartbeat è di 30 s.

Per i test di recupero, `VX_CRASH_AFTER=<stadio>[:<n>]` fa morire il processo
(`os._exit(70)`, nessun cleanup) subito dopo l'n-esimo confine di quello stadio
in `events.jsonl`; senza la variabile il controllo non fa nulla.

## Limiti dichiarati

- Nessun rilevamento di stesso soggetto o near-duplicate: servirebbe la visione.
  La deduplica è solo byte-identica (`size` + `sha256`).
- Una musica più corta della timeline viene collocata per la sua durata reale e
  dichiarata nel report; non viene loopata.
- I B-roll che si sovrapporrebbero sul layer 1 vengono rifiutati con
  `VxError`, non eliminati in silenzio.
- `auto` non esegue whisper: non ci sono ancoraggi da trascrizione e i B-roll
  sono distribuiti uniformemente (`duration * (index + 1) / (n_broll + 1)`).
- `auto` non ha metadati di camera: ogni taglio è un'esenzione e il report lo
  dice. Con `require_camera=True` lo stesso piano sarebbe un `GrammarError`.

## Test

- `tests/test_probe_native.py` (23): sonda nativa per video, immagini e audio.
- `tests/test_asset_metrics.py` (7): nitidezza laplaciana ed esposizione limitata.
- `tests/test_asset_triage.py` (38): rifiuti, cap, duplicati, incertezza, payload.
- `tests/test_ingest_native.py` (27): staging di immagini e audio.
- `tests/test_qc_master.py` (13): gate di loudness sul master.
- `tests/test_render_audio_buses.py` (25): bus dialogo e musica, sidechain.
- `tests/test_edl_loudness.py` (19): loudness del master EDL.
- `tests/test_payload_edl_bridge.py` (29): payload di triage verso EDL.
