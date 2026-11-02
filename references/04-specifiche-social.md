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

Una corsa produce **un solo** aspect: quello scelto nel brief. Non esistono
export multi-aspect né tagli per piattaforma.

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

## Posizionamento e stile dei sottotitoli

Il margine inferiore non è un numero fisso: si calcola dalla safe zone, con
l'aria dichiarata dallo stile.

```
margin_v = (ref_h - safe_y2) * out_h / ref_h + aria
```

`ref_h` è la tela su cui è scritta la safe zone (1920 per il verticale, 1080
per il 16:9); `aria` è `safe_air_ratio * out_h`. I margini laterali sono
simmetrici e vengono da `x1` della stessa tabella: il blocco resta centrato,
che è l'ipotesi con cui `vx.layout.text_box` lo misura.

Su 1080x1920 con `9:16`:

| Piattaforma | Calcolo | `margin_v` |
|-------------|---------|------------|
| `tiktok` | max(1920-1248, 660) + 12 | 684 |
| `reels` | max(672, 672) + 12 | 684 |
| `shorts` | max(672, 672) + 12 | 684 |

Tre stili dichiarati in `vx.subs.SUBTITLE_STYLES`. L'aspect sceglie il default:
`reel` sul verticale, `wide` sul 16:9/4:3. `interview` è opt-in: un documentario
non è un reel.

| Stile | Default per | Famiglia | Corpo (tetto) | Contorno/ombra | Margine V | Colore |
|-------|-------------|----------|---------------|----------------|-----------|--------|
| `reel` | 9:16, 4:5, 1:1 | Inter | 0.064 × H (123 px a 1920) | 7/4 a 1920 | safe + 12 px | bianco; il karaoke passa a #FFC24B |
| `wide` | 16:9, 4:3 | Inter | 0.046 × H (50 px a 1080) | 4/2 a 1080 | safe + 4 px | bianco |
| `interview` | — (opt-in) | Playfair Display | 0.040 × H (77 px a 1920) | placca 23 px, nessun contorno | safe + 48 px | #F5EFE6 su nero 65% |

La dimensione viene poi ridotta se la riga più lunga non ci sta:
`size = min(base, larghezza_utile / (caratteri × avanzamento))`, e la larghezza
vince sempre sul pavimento (`min_px` alza il tetto, non autorizza a sfondare).
L'avanzamento è misurato sui file, non supposto: Antonio 0.371 misto / 0.416
maiuscolo, Inter 0.451-0.472 / 0.573-0.613, Playfair 0.433-0.458 / 0.553-0.598.
I valori dichiarati (0.50 misto e 0.62 maiuscolo per Inter) sono il massimo
misurato più il 6-9%.

Il vecchio 0.34 era un limite ottimistico: con 18 caratteri autorizzava 134 px
su una riga che ne occupava 934, e una riga da 30 caratteri usciva dalla tela.
Il contorno 7/4 a 1920 resta quello di questo documento: 0.36% e 0.21%
dell'altezza, cioè 7.5% e 4.3% del corpo di un sottotitolo da 93 px.

Perché Inter e non Antonio per il testo: Antonio è un condensed da cartello,
24% più stretto e senza una larghezza da lettura; in maiuscolo perde le
differenze di lettera. Inter ha x-height alta e un 400 reale; il flag `Bold` di
libass lo ispessisce del 21% misurato (16181 vs 13353 px d'inchiostro, bbox
+1 px) senza deformarlo. Playfair resta il volto editoriale del documentario,
protetto da una placca al 65%: su fondo bianco il testo #F5EFE6 conserva 6.1:1.
L'apostrofo dritto del trascritto diventa `’` (U+2019) nel testo inciso e nel
SRT: un carattere, nessuna parola cambiata.

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
