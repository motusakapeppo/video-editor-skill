# 05. Auto-framing

Come un video orizzontale diventa verticale senza tagliare le teste.
Rilevazione volti, tracking, scelta dello speaker, smoothing, espressione di
crop.

## Rilevazione

| Voce | Valore |
|------|--------|
| Detector primario | YuNet, `cv2.FaceDetectorYN` |
| Modello | `assets\models\face_detection_yunet_2023mar.onnx` |
| Confidenza minima | 0.6 |
| NMS | 0.3 |
| Top K | 5000 |
| Larghezza di detection | massimo 640 px |
| Campionamento | 8.0 fps (`SAMPLE_FPS_FACE`) |
| Detector opzionale | MediaPipe, richiede un bundle `.task` |

MediaPipe si attiva solo se il bundle `.task` è presente in `assets\models`. Il
gate è il file, non l'import, perché l'API Tasks non sa caricare l'ONNX di
YuNet e un controllo sul solo import darebbe un falso positivo.

YuNet restituisce 15 float per volto: box, occhio destro, occhio sinistro, naso,
bocca destra, bocca sinistra, score. Le coordinate vengono riportate alla
risoluzione del sorgente moltiplicando per il fattore di scala.

## Tracking

| Costante | Valore | Significato |
|----------|--------|-------------|
| `IOU_MATCH` | 0.25 | stesso volto se IoU >= 0.25 |
| `CENTRE_MATCH_K` | 1.2 | oppure distanza fra centri <= 1.2 * max(larghezza precedente, corrente) |
| `MAX_GAP` | 0.8 s | una track resta aperta al massimo 0.8 s senza detections |
| `MIN_TRACK` | 0.5 s | le track più corte vengono scartate |

L'assegnazione è greedy best-first: coppie ordinate per IoU decrescente, poi
per distanza crescente, ogni track e ogni detection consumata una volta sola.
Lo score di una track combina tre fattori:

```
score = sqrt(mean_area) * (0.5 + 0.5 * centrality) * (0.4 + 0.6 * persistence)
```

`centrality` vale 1.0 al centro del frame e 0.0 all'angolo. `persistence` è la
durata della track divisa per la durata totale.

## Scelta dello speaker

Il proxy del parlato è il MAR, il rapporto di apertura della bocca:

```
MAR = |bocca_sinistra_y - bocca_destra_y| / |bocca_destra_x - bocca_sinistra_x|
```

E' invariante alla scala, quindi si calcola sulla riga del detector senza
riportarla al sorgente.

| Costante | Valore | Significato |
|----------|--------|-------------|
| `SPEAKER_MIN_ACTIVITY` | 0.0004 | varianza di popolazione del MAR sotto soglia: nessuno speaker chiaro |
| `SPEAKER_MARGIN` | 1.6 | lo score migliore deve superare il secondo di 1.6 volte |

Se una delle due condizioni fallisce, `select_speaker_track` restituisce
`None`. Il piano usa allora il centroide del gruppo di volti e lo annota con
`speaker=ambiguous, using group centroid`. Non si inventa un protagonista.

## Smoothing

| Stadio | Parametro | Valore |
|--------|-----------|--------|
| One Euro | `min_cutoff` | 0.6 |
| One Euro | `beta` | 0.02 |
| One Euro | `d_cutoff` | 1.0 |
| Dead zone | `DEAD_ZONE_PX` | 12.0 px |
| Velocità | `MAX_VELOCITY_PX_S` | 260.0 px/s |

One Euro filtra il centro target: poco jitter quando il soggetto è fermo, poca
latenza quando si muove. La dead zone tiene il crop fermo finché il target non
si sposta di 12 px, così i micro-movimenti non producono deriva. Il limite di
260 px/s impedisce le panoramiche a frusta.

**Mai attraverso un taglio.** La serie viene spezzata sui `scene_cuts` e lo
stato dei filtri viene azzerato a ogni segmento: un'interpolazione che scavalca
un taglio produrrebbe una panoramica fantasma fra due inquadrature diverse.

## Modalità

| Modalità | Quando | Cosa fa |
|----------|--------|---------|
| `center` | nessun volto | crop centrale statico |
| `fit` | `span_x_ratio > 0.95` | blur-pad: sfondo sfocato, soggetto intero |
| `lock` | larghezza media volto `> 0.55 * crop_w` | crop fermo, nessuna panoramica |
| `track` | tutti gli altri casi con volto | crop che segue il soggetto |

La classificazione del framing avviene prima, in `vx.faces`: nessuna track dà
`center`, `span_x_ratio > 0.95` dà `fit`, `max_concurrent >= 2` dà `multi`,
altrimenti `single`. `LOCK_FACE_WIDTH_RATIO = 0.55`: se il volto occupa più del
55% della larghezza del crop, muoverlo lo farebbe uscire, quindi si blocca.
`FIT_SPAN_RATIO = 0.95`: se i volti coprono più del 95% della larghezza del
sorgente, un crop verticale li taglierebbe, quindi si passa al blur-pad.

## Geometria

| Costante | Valore | Significato |
|----------|--------|-------------|
| `DEFAULT_EYE_LINE` | 0.38 | gli occhi cadono al 38% dell'altezza del crop |
| `SAFE_LEFT_BIAS` | 0.02 | lieve spostamento a sinistra per la rail destra |
| `MAX_KEYFRAMES` | 48 | keyframe per segmento, per asse |
| `DECIMATE_TOLERANCE_PX` | 1.0 | un keyframe che devia meno di 1 px viene scartato |
| `FIT_BLUR_SIGMA` | 20 | sfocatura dello sfondo in modalità fit |

La linea degli occhi al 38% lascia headroom sopra la testa e spazio per i
sottotitoli sotto. Il bias a sinistra compensa i pulsanti di interazione che sui
social stanno a destra.

## Espressione ffmpeg

ffmpeg valuta `crop=w:h` una volta sola, all'inizio del filtro: non esiste modo
di animare `crop_w` o `crop_h` nel tempo, solo `x` e `y` possono essere
espressioni. La dimensione del crop è quindi fissa per tutta la clip e il
soggetto viene inseguito spostando la finestra. Se si avvicina troppo alla
camera, non si allarga il crop: si passa a `lock`.

L'espressione è lineare a tratti, costruita all'indietro con `if()` annidati:

```
if(lt(t,1.2000),100+12.5000*(t-0.8000),if(lt(t,2.4000),150,180))
```

Le virgole sono escapate come `\,` perché il parser del filtergraph le consuma
prima che il parser dell'espressione le veda: metterla fra apici lascerebbe un
backslash letterale.

Il piano diventa un valore numerico singolo, non un'espressione, quando la
modalità è `fit`, `lock` o `center`, quando c'è meno di un box, quando l'fps
stimato è 0, o quando non ci sono segmenti validi. In quei casi non c'è asse
temporale affidabile e un'espressione con `t` produrrebbe NaN.

## Comandi

```powershell
$PY = "<VENV_DIR>\Scripts\python.exe"
$env:PYTHONPATH = "<SKILL_DIR>\scripts"
& $PY -m vx.autoframe "%USERPROFILE%\clip.mp4" "9:16"
& $PY -m vx.autoframe "%USERPROFILE%\clip.mp4" "9:16" --proxy
& $PY -c "from vx.faces import yunet_available, mediapipe_available; print(yunet_available(), mediapipe_available())"
```

Il secondo comando produce il piano a 640 px, identico in proporzione al full. Il terzo deve stampare `True False`; altrimenti manca l'ONNX o OpenCV.