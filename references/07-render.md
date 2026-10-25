# 07. Render

L'ordine degli stadi, i tagli frame-accurate, la loudness a due passaggi. Ogni
scelta qui nasce da una misura, non da una copia.

## Ordine degli stadi

| Stadio | File | Cosa fa |
|--------|------|---------|
| 01 | `stage/01_trim.mp4` | trim/atrim + concat, solo se serve la stabilizzazione |
| 02 | `transforms.trf` | `vidstabdetect`, solo se `stabilize` è attivo |
| 03 | `stage/03_video.mp4` | grafo completo, video e audio |
| 04 | `final_full.mp4` | secondo passaggio `loudnorm`, `+faststart` |

Lo stadio 01 esiste solo perché `vidstabdetect` deve vedere il video già tagliato: senza stabilizzazione si salta e si va diretti al grafo completo.

## Ordine della catena video

Fisso, non negoziabile: `trim`/`atrim`, `concat`, `vidstabtransform`,
`crop`+`scale`, `zoom` (`scale`+`crop`), `eq`, `subtitles`,
`format=yuv420p`+`setsar=1`.

Stabilizzare dopo il crop non funziona: il `.trf` è calcolato sul video
tagliato, e il crop virtuale ha bisogno delle coordinate del sorgente. I
sottotitoli vanno dopo il colore, altrimenti l'`eq` li tinge.
## Perché `-c copy` è vietato sui tagli

`-c copy` con `-ss` cerca il keyframe più vicino e parte da lì.

| Problema | Effetto |
|----------|---------|
| Pre-roll | il taglio parte dal keyframe, non dal tempo richiesto |
| PTS negativi | il primo pacchetto ha timestamp negativo |
| Frame in eccesso | es. 105 frame per una richiesta di 2 secondi |

Un taglio con `-c copy` non è un taglio, è un'approssimazione che sfasa l'audio e allunga il video: ogni taglio passa per `trim`/`atrim` con ri-encoding.

## Trim e concat esatti

```
[0:v]trim=start=S:end=E,setpts=PTS-STARTPTS,setsar=1,format=yuv420p[vN]
[0:a]atrim=start=S:end=E,asetpts=PTS-STARTPTS[aN]
[v0][a0][v1][a1]concat=n=2:v=1:a=1[vc][ac]
```

| Dettaglio | Perché |
|-----------|--------|
| `setpts=PTS-STARTPTS` | senza, il secondo segmento eredita il timestamp del primo e concat lo scarta |
| `setsar=1` prima di concat | concat pretende SAR identico su tutti gli ingressi |
| `format=yuv420p` prima di concat | concat pretende pixel format identico |

I tempi vengono agganciati alla griglia dei frame con `round(t * fps) / fps`:
senza lo snap, `trim` produce un frame in più o in meno. `expected_frames` è
`round(sum(out - in) * fps)`; il QC accetta uno scarto di 1 frame
(`frame_count_delta`).

## Fade ai giunti

| Costante | Valore | Effetto |
|----------|--------|---------|
| `FADE_MS` | 12 | `afade` in ingresso e in uscita su ogni segmento |
| `acrossfade` | vietato | accorcia il totale della propria durata, circa 10 ms |

`afade` non cambia la durata: il conteggio dei frame resta esatto. `acrossfade`
sovrappone i segmenti e accorcia il risultato, rompendo la parità fra
`expected_frames` e il file prodotto. Il fade di 12 ms è sotto la soglia di
percezione e toglie il click al giunto.

## Stabilizzazione

| Passaggio | Filtro | Parametri |
|-----------|--------|-----------|
| detect | `vidstabdetect` | `shakiness=5:accuracy=9` |
| transform | `vidstabtransform` | `smoothing=10:zoom=0:optzoom=1` |

Il file `.trf` viene referenziato con il **solo nome**, e il processo ffmpeg
gira con `cwd` uguale alla workdir: un path assoluto Windows dentro il
filtergraph rompe il parsing, i due punti vengono letti come separatore.
`zoom=0` con `optzoom=1` lascia che sia vidstab a decidere il crop minimo. Il
margine è un gate: sotto `stab_min_headroom_pct = 8.0` non parte.

## Loudness a due passaggi

| Passaggio | Comando | Cosa fa |
|-----------|---------|---------|
| 1 | `loudnorm=print_format=json` su `stage/03_video.mp4` | misura, non tocca il file |
| 2 | `loudnorm=I=...:TP=...:LRA=...:measured_I=...:linear=true` | applica la correzione |

Il secondo passaggio riceve `measured_I`, `measured_TP`, `measured_LRA`,
`measured_thresh` e `offset`. Con `linear=true` applica un guadagno lineare
invece di comprimere, e il risultato resta prevedibile. Se la misura è
inutilizzabile (`nan`, stringa vuota, `None`), il codice ricade sulla modalità
dinamica: è la rete di sicurezza, non la scelta preferita.

Atteso: il secondo passaggio porta l'integrato a **-14.0 LUFS**, scarto entro 0.3 LU, quindi `lufs_delta` passa.

Il video non viene riencodato in questo stadio: `-c:v copy`. L'audio sì, in AAC
192k a 48 kHz. Il `+faststart` sposta l'atomo `moov` prima di `mdat`, così la
piattaforma parte senza scaricare tutto il file.

## Parità proxy / full

Proxy e full condividono lo stesso grafo. Ogni parametro numerico è o una
frazione o derivato dalla dimensione di uscita.

| Parametro | Proxy | Full |
|-----------|-------|------|
| larghezza | 640 | 1080 (9:16) |
| `crf` | 23 | 18 |
| `preset` | `veryfast` | `medium` |
| crop | identico in pixel sorgente | identico in pixel sorgente |
| zoom | frazione dell'uscita | stessa frazione |
| eq | senza unità | senza unità |
| loudnorm | stesso target | stesso target |

Il crop è in pixel del sorgente, quindi non cambia fra proxy e full: solo il
target di scala cambia. I conteggi frame coincidono entro 1, e un piano
approvato in proxy si rirenderizza in full senza sorprese.

## Script del filtergraph ed encoding

Il grafo viene scritto in `stage/03_graph.txt` e passato con
`-filter_complex_script`: il limite di 8191 caratteri della command line Windows
è reale, e un grafo con dieci segmenti lo supera. Il file è una riga sola con
newline finale, spazi multipli collassati.

| Precisione | Video | Audio |
|------------|-------|-------|
| proxy | `libx264`, `crf 23`, `veryfast`, `yuv420p` | `aac 192k`, 48 kHz |
| full | `libx264`, `crf 18`, `medium`, `yuv420p` | `aac 192k`, 48 kHz |

`yuv420p` è obbligatorio: è l'unico pixel format che tutte le piattaforme
accettano senza transcodifica. Nessun `zoompan`: è lento e avanza a scatti
visibili, perché lavora su una griglia interna che non coincide con il frame
rate di uscita. Lo zoom statico si fa con `scale` a dimensione maggiore seguito
da `crop` centrato.

## Comandi

```powershell
$VENV = $env:VX_VENV            # es. C:\tools\video_env
$PY   = "$VENV\Scripts\python.exe"
$VX   = "<SKILL_DIR>\scripts\vx.py"
& $PY $VX render --plan "<WORK_DIR>\clip-a1b2c3d4\plan.json" --precision proxy
& $PY $VX render --plan "<WORK_DIR>\clip-a1b2c3d4\plan.json" --precision full
```

Il render restituisce `path`, `frames`, `expected_frames`, `loudness`, `target`,
`stages`, `graph` e `script`. Se `frames` e `expected_frames` differiscono di
più di 1, il problema è nei tagli, non nell'encoder.
