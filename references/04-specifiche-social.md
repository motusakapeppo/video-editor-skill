# 04. Specifiche social

Le costanti che decidono dove finisce il contenuto e a che volume suona. Tutte
lette da `vx.contract`, nessuna inventata.

## Formati di uscita

| Aspect | Risoluzione full | Proxy 640 | Uso |
|--------|------------------|-----------|-----|
| `9:16` | 1080x1920 | 640x1138 | TikTok, Reels, Shorts |
| `1:1` | 1920x1920 | 640x640 | feed quadrato |
| `4:5` | 1536x1920 | 640x800 | feed verticale Instagram |
| `16:9` | 1920x1080 | 640x360 | YouTube, orizzontale |
| `4:3` | 1920x1440 | 640x480 | archivio |
| `3:4` | 1440x1920 | 640x853 | ritratto |

Il proxy mantiene lo stesso aspect a 640 px di larghezza. Serve per
l'anteprima: costa un quarto del decode e le metriche sono normalizzate sulla
scala, quindi i numeri restano confrontabili.

## Safe zone

Rettangoli in pixel su una tela 1080x1920, nel formato `(x1, x2, y1, y2)`.

| Aspect | x1 | x2 | y1 | y2 |
|--------|----|----|----|----|
| `9:16` | 120 | 888 | 288 | 1248 |
| `1:1` | 60 | 1020 | 120 | 960 |
| `4:5` | 60 | 1020 | 120 | 1200 |
| `16:9` | 48 | 1872 | 36 | 1044 |

Per il verticale la zona sicura combinata è **x 120..888, y 288..1248** su
1080x1920. Fuori da quel rettangolo ci sono l'interfaccia dell'app e i pulsanti
di sistema.

## Inset per piattaforma

Valori `(top, bottom, left, right)` in pixel su 1080x1920, da linee guida
pubblicitarie.

| Piattaforma | Top | Bottom | Left | Right |
|-------------|-----|--------|------|-------|
| `tiktok` | 240 | 660 | 120 | 120 |
| `reels` | 269 | 672 | 65 | 65 |
| `shorts` | 288 | 672 | 48 | 192 |
| `facebook` | 269 | 672 | 65 | 65 |
| `snapchat` | 192 | 672 | 65 | 162 |

L'inset più severo vince sempre. Se un video va su TikTok e su Shorts, il
margine inferiore è il massimo fra 660 e 672, e il margine destro è il massimo
fra 120 e 192.

## Loudness

| Profilo | Integrated | True peak |
|---------|------------|-----------|
| `social` | -14.0 LUFS | -1.0 dBTP |
| `apple` | -16.0 LUFS | -1.0 dBTP |
| `broadcast` | -23.0 LUFS | -2.0 dBTP |

Il profilo predefinito è `social`. Il QC accetta uno scarto di 0.3 LU
(`lufs_delta`) e un true peak fino a 0.1 dB sopra il target
(`true_peak_margin`).

LRA di lavoro: 11.0 LU. Oltre 12.0 LU (`lra_wide`) il range dinamico è troppo
largo per un social e il loudnorm lavora male.

## Posizionamento dei sottotitoli

Il margine inferiore non è un numero fisso: si calcola.

```
margin_v = max(out_h - safe_y2 + 12, inset_bottom * scale + 12)
```

Su 1080x1920 con `9:16`:

| Piattaforma | Calcolo | `margin_v` |
|-------------|---------|------------|
| `tiktok` | max(1920-1248+12, 660+12) | 684 |
| `reels` | max(684, 672+12) | 684 |
| `shorts` | max(684, 672+12) | 684 |

Il margine laterale è 60 px per lato. Il font di base è `0.062 * out_h`, cioè
119 px sul verticale, con outline 7 e shadow 4. In orizzontale il font è
`0.045 * out_h` (49 px su 1080), outline 4, shadow 2, margine verticale 40.

La dimensione viene poi ridotta se la cue più lunga non ci sta:
`size = min(base, (out_w - 80) / (longest * 0.50))`. Il fattore 0.50 è
l'avanzamento misurato di Arial Black maiuscolo, con margine di sicurezza.

## Hook

| Finestra | Valore | Cosa significa |
|----------|--------|----------------|
| `hook_window` | 4.0 s | oltre questa durata un'inquadratura è "lenta" |
| sotto-finestra critica | 0..3.0 s | `0.75 * hook_window`, dove si decide la retention |

Nei primi 3 secondi serve un motivo per restare: movimento, un taglio, un volto
che parla. `retention.hook_candidates` ordina i segmenti della finestra con
`0.7 * moto + 0.3 * novita + 0.15 * face_span` e ne restituisce al massimo 3.

Se `mean_motion < 1.0` (`_STATIC_DIFF`), l'intera clip è quasi ferma e il
toolkit segnala `weak_hook_low_motion` a 0.0 s. In quel caso l'hook va
costruito, non trovato.

## Regole di retention

| Segnale | Soglia | Conseguenza |
|---------|--------|-------------|
| Inquadratura lunga e ferma | `> 4.0 s` e moto sotto la media | `long_shot_low_motion`, candidata al taglio |
| Apertura statica | `mean_motion < 1.0` | `weak_hook_low_motion` a 0.0 s |
| Ritmo lento | `mean_shot_len >= 4.0` | verdetto `slow`, serve un taglio o uno zoom |
| Ritmo serrato | `mean_shot_len <= 2.0` | verdetto `snappy`, non toccare |
| Parlato veloce | `wpm > 190` | cue più corte, altrimenti si sfonda il cps |

## Densità di montaggio

`motion.cuts_per_10s` è il numero di tagli ogni 10 secondi. Non ha una soglia
nel contratto, quindi non genera trattamenti da sola. Serve come contesto: un
valore sotto 1 su un reel di 30 secondi spiega perché il ritmo risulta lento,
ma la decisione arriva da `pacing.verdict`.

## Tabella di scelta rapida

| Richiesta | Aspect | Loudness | Sottotitoli |
|-----------|--------|----------|-------------|
| TikTok organico | `9:16` | `social` | `karaoke` |
| Reels | `9:16` | `social` | `karaoke` |
| Shorts | `9:16` | `social` | `karaoke` |
| Feed Instagram | `4:5` | `social` | `clean` |
| YouTube lungo | `16:9` | `apple` | `clean` |
| Spot broadcast | `16:9` | `broadcast` | `clean` |

Questa tabella è un punto di partenza, non una regola. Il brief dell'utente
vince: se chiede `9:16` con loudness `apple`, si fa `9:16` con `apple`.
