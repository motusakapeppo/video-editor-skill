# 08. QC e consegna

I gate binari che decidono se un render esce o resta dentro. Ogni gate ha un
codice, un valore, un limite. `pass` è vero solo se la lista dei fallimenti è
vuota.

## Codici QC

| Codice | Limite | Cosa misura |
|--------|--------|-------------|
| `has_video` | vero | esiste una traccia video con dimensioni valide |
| `has_audio` | atteso | la traccia audio c'è se il sorgente ne aveva una |
| `aspect_mismatch` | uguaglianza esatta | larghezza e altezza coincidono con `output_size(aspect, precision)` |
| `duration_drift_ms` | 40.0 ms | scarto fra durata reale e durata attesa dai tagli |
| `frame_count_delta` | 1 frame | scarto fra frame decodificati e `expected_frames` |
| `lufs_delta` | 0.3 LU | scarto fra integrated misurato e target |
| `true_peak` | target + 0.1 dB | true peak misurato contro il target |
| `av_start_ms` | 20.0 ms | skew fra `start_time` del video e dell'audio |
| `black_frames_edges` | 2 | frame neri all'inizio o alla fine |
| `font_fallback_count` | 0 | sostituzioni di font nei sottotitoli |
| `cue_cps_max` | 25 | caratteri al secondo della cue più veloce |
| `cue_lines_max` | 2 | righe della cue più alta |
| `crop_in_bounds` | vero | il rettangolo di crop sta dentro il sorgente |
| `source_sha_unchanged` | vero | lo sha256 del sorgente non è cambiato |
| `no_nan_timestamps` | vero | nessun PTS o DTS `nan` nei pacchetti |
| `faststart` | vero | l'atomo `moov` precede `mdat` |

## Dettagli dei gate

**`duration_drift_ms`.** La durata attesa è `expected_frames / fps` quando ci
sono tagli, altrimenti la durata del sorgente. 40 ms sono circa un frame a 25
fps: oltre, il taglio è sbagliato.

**`frame_count_delta`.** Il conteggio viene fatto con `-count_frames`, non
letto dall'header. Un mp4 può dichiarare un `nb_frames` che non corrisponde ai
frame realmente decodificabili.

**`lufs_delta` e `true_peak`.** La misura usa `loudnorm=print_format=json` sul
file finale. Se non c'è audio, entrambi i gate passano con valore `None`: non
si può fallire un gate che non si applica.

**`av_start_ms`.** Uno skew di 20 ms è il limite oltre il quale un labiale
comincia a sembrare fuori sincrono. Nasce quasi sempre da un `atrim` senza
`asetpts`.

**`black_frames_edges`.** `blackdetect` gira con `d=0.05` e `pix_th=0.10`. Uno
span conta come "al bordo" se inizia entro 0.15 s dall'inizio o finisce entro
0.15 s dalla fine. Due bordi neri sono tollerati, tre no.

**`font_fallback_count`.** Brucia un frame e legge le righe `fontselect:`.
Conta anche i glifi mancanti (`Glyph 0x... not found`). Limite zero: un font
sostituito è un difetto visibile.

**`crop_in_bounds`.** Verifica che `x >= 0`, `y >= 0`, `x + w <= src_w`,
`y + h <= src_h`. Un crop fuori bordo produce una banda nera che nessun altro
gate vede.

**`source_sha_unchanged`.** Confronta sha256, dimensione e mtime con il
fingerprint registrato all'ingest. È il gate che garantisce che il materiale
originale sia intatto.

**`no_nan_timestamps`.** Legge `pts_time` e `dts_time` di ogni pacchetto video.
Un `nan` significa che un filtro ha prodotto timestamp non finiti, e il file
non sarà riproducibile ovunque.

**`faststart`.** Legge il primo MiB e confronta la posizione di `moov` e
`mdat`. Se `moov` manca del tutto, il file è rotto.

## Come si legge un QC fallito

```powershell
$VENV = $env:VX_VENV            # es. C:\tools\video_env
$PY   = "$VENV\Scripts\python.exe"
$VX   = "<SKILL_DIR>\scripts\vx.py"
& $PY $VX qc --plan "<WORK_DIR>\clip-a1b2c3d4\plan.json" --render "<WORK_DIR>\clip-a1b2c3d4\out\final_full.mp4"
```

Exit code 5 significa QC fallito. Il JSON contiene `failures`, la lista dei
codici, e `checks`, con `value`, `limit` e `detail` per ognuno.

| Codice fallito | Prima cosa da guardare |
|----------------|------------------------|
| `duration_drift_ms` | i tagli nel piano, non l'encoder |
| `frame_count_delta` | lo snap alla griglia frame, `fps` del media |
| `lufs_delta` | il secondo passaggio loudnorm è stato eseguito |
| `true_peak` | il target scelto, `LRA` troppo largo |
| `av_start_ms` | `asetpts=PTS-STARTPTS` mancante |
| `black_frames_edges` | un taglio che parte prima del primo frame utile |
| `font_fallback_count` | il font non è installato |
| `cue_cps_max` | il budget caratteri è troppo alto per il parlato |
| `crop_in_bounds` | il piano di crop, non il render |
| `source_sha_unchanged` | qualcosa ha scritto sul sorgente, fermarsi |
| `no_nan_timestamps` | un'espressione di crop con `t` senza fps |
| `faststart` | il muxer non ha ricevuto `+faststart` |
| `aspect_mismatch` | `output.aspect` e `precision` nel piano |

## Matrice dei deliverable

| Deliverable | Formato | Contenuto |
|-------------|---------|-----------|
| Master | mp4, full, `crf 18` | il render approvato, loudness a target |
| Tagli per piattaforma | mp4 per aspect | stesso master, ricropato per `9:16`, `1:1`, `4:5`, `16:9` |
| Sottotitoli | srt | testo con timestamp, senza tag karaoke |
| Thumbnail | jpg o png | frame scelto, alla risoluzione della piattaforma |
| Contact sheet | jpg o png | griglia di frame a intervalli regolari |

Il master è uno solo. I tagli per piattaforma derivano dal master, non dal
sorgente: così quello che è stato approvato in anteprima è esattamente quello
che esce. L'SRT accompagna sempre il master, anche quando i sottotitoli sono
bruciati nel video: serve per la correzione e per le piattaforme che li vogliono
separati.

## Checklist prima della consegna

| # | Controllo | Come |
|---|-----------|------|
| 1 | QC verde | exit code 0, `failures` vuota |
| 2 | Sorgente intatto | `source_sha_unchanged` a vero |
| 3 | Loudness a target | `lufs_delta` entro 0.3 LU |
| 4 | True peak sotto target | `true_peak` a vero |
| 5 | Nessun frame nero ai bordi | `black_frames_edges` <= 2 |
| 6 | Nessun font sostituito | `font_fallback_count` a 0 |
| 7 | Cue leggibili | `cue_cps_max` <= 25, `cue_lines_max` <= 2 |
| 8 | Crop dentro il sorgente | `crop_in_bounds` a vero |
| 9 | Faststart presente | `faststart` a vero |
| 10 | Formato corretto | `aspect_mismatch` a vero |
| 11 | SRT presente | file `.srt` nella cartella `out` |
| 12 | Nome file parlante | `<stem>-<sha8>-<aspect>-<profilo>.mp4` |

Se uno dei primi dieci fallisce, non si consegna. Non esiste "quasi verde".

## Cosa non è un gate

| Cosa | Perché non è un gate |
|------|----------------------|
| Bellezza del montaggio | non è binaria |
| Scelta dell'hook | è una decisione editoriale |
| Presenza di musica | è una scelta del brief |
| Durata "giusta" | dipende dalla piattaforma e dal contenuto |
| Numero di tagli | non esiste un numero corretto |

I gate coprono la correttezza tecnica. Il giudizio editoriale resta umano, e il
toolkit non finge di sostituirlo.

## Pulizia

La workdir resta in `<WORK_DIR>\<stem>-<sha8>\` con `proxy`, `analysis`,
`subs`, `stage`, `out`, `qc`. Il lock `.lock` va rimosso a fine sessione, o
scade da solo dopo 30 minuti. Non si cancella la workdir finché il QC non è
verde: `stage/03_graph.txt` e `transforms.trf` sono l'unica traccia del lavoro.
