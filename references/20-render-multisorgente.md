# 20 — Render multi-sorgente ed esecuzione EDL

La 3.1 ha reso l'EDL v2.0 un contratto verificabile. La 3.2 lo esegue: ogni clip
sa da quale sorgente viene, ogni gate misura la sorgente giusta, e il comando
ffmpeg non contiene più un indice cablato.

## Ingest orchestrator

`ingest.ingest_sources(sources_map, stage_dir) -> Dict[str, IngestResult]`

- probe e stage 00 indipendenti per ogni `source_id` (VFR, HDR, SAR, rotazione,
  guardia 4K);
- il nome dello stage è `stage_00_ingest_<source_id>_<stem>.mp4`: due file con lo
  stesso stem non si sovrascrivono;
- una mappa vuota o un `source_id` vuoto è un errore, non un silenzio;
- il risultato conserva `source_path`, `staged_path` e i flag di normalizzazione
  per ogni sorgente.

Non c'è cache di staging: una riesecuzione rifà lo stage. È una scelta
conservativa, non una svista.

## Input mapping

`render.edl_input_map(staged_sources)` assegna `0..N-1` nell'ordine dichiarato.
`render.build_edl_command` emette un `-i` per sorgente e passa il grafo con
`-filter_complex_script` (mai `-vf` inline). `render.render_edl` scrive il grafo
su disco, esegue ffmpeg e verifica che l'output esista e non sia vuoto.

## Grafo

`render.build_edl_filtergraph(plan, source_to_index, has_audio=None)`

- ogni clip A-Roll: `[idx:v]trim=start=src_in:end=src_out,setpts=PTS-STARTPTS,
  scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2,
  setsar=1,format=yuv420p`;
- l'audio che rispecchia l'A-Roll entra nello stesso
  `concat=n=N:v=1:a=1`: il suono resta agganciato al quadro, e Gate 2 trova il
  concat che si aspetta;
- ogni B-roll: stesso trim e normalizzazione, fade in/out dalle transizioni,
  `setpts=PTS-STARTPTS+dst_in/TB`, poi
  `overlay=enable='between(t,dst_in,dst_out)':eof_action=pass`;
- uscite stabili `[vout]` e, se c'è audio, `[aout]`;
- nessun `[0:v]`/`[0:a]` cablato: gli indici vengono solo da `source_to_index`.

Il grafo è una riga sola, scritta su file. Il B-roll è un overlay a pieno frame:
l'EDL v2.0 non porta ancora metadati di crop o autoframe.

## Gate 1 multi-sorgente

`gates.validate_gate1_durations(plan, media_info_map)`

- per ogni clip video e audio legge la durata della **sua** sorgente, da
  `format.duration` o da un dizionario piatto (`duration`/`analysis_duration`);
- fallisce se la sorgente manca dalla mappa, se `src_out` supera la sua durata,
  se `src_in < 0`, se `src_out <= src_in`, o se il clip esce dalla timeline;
- il messaggio nomina `clip_id` e `source_id`: un `src_out` valido su A non
  nasconde uno sforamento su B;
- solleva `contract.QCFailure`, quindi la CLI esce 5 con il payload dei check.

## CLI

```text
vx edl validate --plan edl.json [--json]
vx edl render --plan edl.json --source src_A=A.mp4 --source src_B=B.mp4 \
              --workdir work --out final.mp4 [--crf 17] [--preset fast] [--json]
```

- `validate` carica, valida e riporta contratto, sorgenti, tracce, durata e
  risoluzione;
- `render` esige una `--source` per ogni chiave dichiarata nell'EDL; una
  mancante o una sconosciuta esce 2;
- dopo lo stage applica Gate 1 e renderizza; uno sforamento esce 5, un errore
  ffmpeg esce 6;
- il report JSON elenca output, percorsi stage e flag di ogni sorgente.

## Limiti dichiarati

- Il `concat v=1:a=1` unico richiede che i clip audio rispecchino l'A-Roll per
  numero e tempi. Un J/L cut più profondo richiede un concat audio separato:
  non è ancora implementato.
- Il B-roll è a pieno frame, scalato e paddato alla risoluzione della timeline;
  non c'è ancora una geometria di crop per sorgente.
- Il percorso legacy `cli -> render.render` resta mono-sorgente. Il percorso
  EDL è `cli edl -> render.render_edl`.
- Non c'è QC finale sul file `render_edl`: Gate 1 è l'audit aritmetico, non il
  controllo di loudness, cue e durata del legacy.
- Le transizioni sono solo sul B-roll; i tagli A-Roll restano netti.

## Test

`tests/test_ingest_multisource.py` (14): clean/VFR/HDR, mappa per `source_id`,
stage con nome legato all'id, collisione di stem, input invalido.

`tests/test_render_multisource.py` (21): `[0:v]` per A, `[1:v]` per B, `[0:a]` e
`[1:a]`, concat A/V, overlay a `dst_in`, due `-i`, e un render reale di 2 s con
B-roll.

`tests/test_gate1_multisource.py` (11): mappa valida, sforamento su B, durata
piatta, sorgente mancante, clip audio.

`tests/test_edl_cli.py` (10): validate, render reale, sorgente mancante (exit 2),
sforamento (exit 5) con `source_id` nel payload.
