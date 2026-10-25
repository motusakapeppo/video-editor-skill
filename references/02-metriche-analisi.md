# 02. Metriche di analisi

Ogni numero della pre-analisi, con unità, soglia reale da
`vx.contract.THRESHOLDS` e modo di leggerlo. Se una metrica non ha soglia, non
genera trattamenti.

## Video

| Campo | Unità | Soglia | Lettura |
|-------|-------|--------|---------|
| `scene_cuts` | secondi | `scene_cut = 0.30` | tagli netti, ordinati, distanziati 0.25 s |
| `freezes` | secondi | `freeze_min = 1.5` | `{start, end, duration}` per span |
| `jitter.score` | % larghezza frame | bande `0.35 / 0.60 / 1.00` | sotto 0.35 stabile, 0.35-0.60 leggero, oltre 0.60 tremolio |
| `jitter.verdict` | testo | dalle bande | `stable`, `slight`, `shaky` |
| `jitter.mean_residual_px` | pixel sorgente | nessuna | residuo medio dopo il passaalto |
| `jitter.samples` | conteggio | nessuna | coppie di frame analizzate |
| `jitter.headroom_pct` | % | `stab_min_headroom_pct = 8.0` | margine per il crop di stabilizzazione |
| `exposure.mean_luma` | 0..255 | `luma_under = 45.0` | sotto 45 `dark`, sopra 210 `bright` |
| `exposure.clip_high_pct` | % pixel | `clip_high_pct = 2.0`, `clip_high_block_pct = 8.0` | oltre 2.0 bruciata, oltre 8.0 `clipped` |
| `exposure.clip_low_pct` | % pixel | nessuna | ombre chiuse a luma <= 5 |
| `exposure.verdict` | testo | dalle soglie | `ok`, `dark`, `bright`, `clipped` |
| `color.cast_rg` | unità 0..255 | `cast_rg = 12.0` | media R meno media G |
| `color.cast_by` | unità 0..255 | `cast_by = 12.0` | media B meno luma Y |
| `color.saturation` | % | nessuna | saturazione media HSV |
| `color.verdict` | testo | dalle soglie | `neutral`, `warm`, `green`, `cool`, `magenta` |
| `motion.per_second` | diff media 0..255 | `_STATIC_DIFF = 1.0` | energia di movimento per secondo |
| `motion.mean` | 0..255 | nessuna | media della serie |
| `motion.static_run_max` | secondi | nessuna | run statico più lungo |
| `motion.cuts_per_10s` | tagli / 10 s | nessuna | densità di montaggio |
| `pacing.mean_shot_len` | secondi | `hook_window = 4.0` | sotto 2.0 `snappy`, sopra 4.0 `slow` |
| `pacing.shot_count` | conteggio | nessuna | numero di inquadrature |
| `pacing.fastest_shot` | secondi | nessuna | inquadratura più corta |
| `pacing.slowest_shot` | secondi | nessuna | inquadratura più lunga |
| `pacing.verdict` | testo | dalle bande | `snappy`, `normal`, `slow` |
| `retention.risk_drop` | lista `{at, reason}` | nessuna | punti di probabile abbandono |
| `retention.hook_candidates` | lista `{at, score, reason}` | massimo 3 | ordinati per score |

Primario dei tagli: `select='gt(scene,0.30)',metadata=print`. Il controllo
`scdet` gira a `threshold=30` (0.30 su scala 100) e aggiunge solo tagli mancati
di oltre 0.5 s: aumenta la recall, non riordina un risultato sicuro.
`freezedetect` gira a `n=-50dB:d=1.5`; un freeze aperto a fine file si chiude
con la durata del container.

Jitter: optical flow denso (DIS, fallback Farneback) a 320 px, path cumulato,
media mobile a 0.5 s sottratta, residuo normalizzato sulla larghezza.
Cavalletto dà 0, mano libera seria supera 1.0. Sotto 8.0 di headroom la
stabilizzazione non ha spazio senza croppare il volto: si veta.

Esposizione: la soglia del bright è speculare, `255 - 45 = 210`. `clipped`
vince su tutto, con l'8% dei pixel a fondo scala nessun `eq` recupera nulla.

Colore: gray-world, oltre 12.0 su un asse il verdetto non è `neutral`. L'asse
con la deviazione maggiore dà il nome. R-G positivo è `warm`, negativo `green`.
B-Y positivo è `cool`, negativo `magenta`.

Ritmo: le bande derivano da `hook_window`, `<= 2.0` è `snappy` (metà finestra),
`>= 4.0` è `slow`. `risk_drop` contiene le inquadrature oltre 4.0 s con moto
sotto la media (`long_shot_low_motion`) e, se la clip è quasi statica, la
finestra 0..3 s (`weak_hook_low_motion`). `hook_candidates` usa
`0.7 * moto_normalizzato + 0.3 * novita + 0.15 * face_span`, con novità 1.0 se
il segmento inizia dopo un taglio.

## Audio

| Campo | Unità | Soglia | Lettura |
|-------|-------|--------|---------|
| `noise_floor_dbfs` | dBFS | nessuna | p10 dell'RMS per frame |
| `speech_level_dbfs` | dBFS | nessuna | p90 dell'RMS per frame |
| `snr_db` | dB | `noise_snr_low = 12.0` | sotto 12.0 il verdetto è `noisy` |
| `dead_air` | secondi | `silence_min = 0.35` | span `{start, end, duration}` |
| soglia rilevazione | dB | `clamp(noise + 8, -45, -25)` | margine `NOISE_MARGIN_DB = 8.0` |
| `silence_pad` | secondi | `0.12` | margine tenuto ai bordi del taglio |
| `dead_air_ratio` | frazione | `0.08` | sopra 8% del totale vale un taglio |
| `dead_air_max_cut` | frazione | `0.35` | mai rimuovere più del 35% |
| `loudness.input_i` | LUFS | `QUIET_LUFS = -24.0` | sotto è `quiet` |
| `loudness.input_tp` | dBTP | target `-1.0` social | true peak misurato |
| `loudness.input_lra` | LU | `lra_wide = 12.0` | range dinamico largo |
| `loudness.input_thresh` | LUFS | nessuna | soglia di gating |
| `loudness.target_offset` | LU | nessuna | offset per il secondo passaggio |
| `clipping_pct` | % | `clip_pct = 0.01` | sopra 0.01% il verdetto è `clipped` |
| `dc_offset` | frazione | nessuna | media del segnale, vicino a 0 è sano |
| `peaks` | lista | `MAX_CLIP_EVENTS = 32` | eventi `{start, end, peak_dbfs}` |
| `music_detected` | booleano | `MUSIC_THRESHOLD = 0.45` | sopra 0.45 è musica |
| `music_ratio` | 0..1 | `0.45` | periodicità 0.55 + energia bassa 0.45 |

Il campo `Noise_floor` di `astats` non si usa: su silenzio digitale dà `-inf`.
I percentili p10 e p90 sono stabili anche con molto silenzio.

`silencedetect` confronta il picco per campione, non l'RMS: uno span significa
"nessun campione sopra soglia", non "era piano". `build_cut_ranges` restringe
ogni span di `pad`, scarta i gap sotto 0.15 s, assorbe le keep-range sotto
0.25 s e, oltre il budget del 35%, tiene i gap più lunghi.

Un campione è clippato a `|x| >= 0.9999`. Il true peak arriva da
`ebur128=peak=true`, indipendente da `loudnorm`. La music detection è
un'euristica dichiarata nel campo `method`, non una certezza: serve a un solo
scopo, se c'è musica il denoise si veta.

| Verdetto audio | Condizione |
|----------------|------------|
| `clipped` | `clipping_pct >= 0.01` |
| `noisy` | `snr_db < 12.0` |
| `quiet` | `input_i < -24.0` |
| `clean` | nessuna delle precedenti |
| `silent` | nessuna traccia audio |

L'ordine è quello del codice: clipping, poi rumore, poi volume.

## Volti

| Campo | Unità | Soglia | Lettura |
|-------|-------|--------|---------|
| `detected_ratio` | 0..1 | nessuna | frame campionati con almeno un volto |
| `max_concurrent` | conteggio | nessuna | massimo volti simultanei |
| `framing` | testo | `FIT_SPAN_RATIO = 0.95` | `center`, `single`, `multi`, `fit` |
| `span_x_ratio` | 0..1 | `0.95` | ampiezza orizzontale dei volti |
| `detector` | testo | nessuna | `yunet`, `mediapipe`, `none` |
| `tracks[].mean_area` | pixel quadri | nessuna | area media del volto |
| `tracks[].duration_s` | secondi | `MIN_TRACK = 0.5` | sotto 0.5 s la track è scartata |
| `tracks[].centrality` | 0..1 | nessuna | 1.0 al centro, 0.0 all'angolo |
| `tracks[].persistence` | 0..1 | nessuna | durata track su durata totale |
| `tracks[].score` | numero | nessuna | area, centralità, persistenza |

Campionamento a `SAMPLE_FPS_FACE = 8.0` fps, confidenza minima 0.6, detection
ridotta a 640 px di larghezza.

## Sottotitoli

| Campo | Unità | Soglia | Lettura |
|-------|-------|--------|---------|
| `wpm` | parole/minuto | `speech_wpm_fast = 190` | sopra 190 il parlato è veloce |
| `words` | lista | nessuna | `{start, end, word, probability}` |
| `segments` | lista | nessuna | segmenti whisper con `avg_logprob` |
| `skipped_reason` | testo | nessuna | perché la trascrizione non è avvenuta |

Limiti a valle: `sub_max_chars_916 = 18`, `sub_max_chars_169 = 42`,
`sub_max_lines = 2`, `sub_max_cps = 25`.

## Come si legge un report

```powershell
$PY = "<VENV_DIR>\Scripts\python.exe"
$env:PYTHONPATH = "<SKILL_DIR>\scripts"
& $PY -m vx.analyze --input "%USERPROFILE%\clip.mp4" --json
& $PY -m vx.audio --input "%USERPROFILE%\clip.mp4" --json --cuts
```

`--cuts` aggiunge `cut_ranges`, le keep-range già calcolate con il budget del
35%. Sono quelle che finiscono in `plan.cuts`.
