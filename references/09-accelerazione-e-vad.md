# 09. Accelerazione hardware e VAD

Due sottosistemi che decidono *dove* gira il lavoro e *dove* è lecito tagliare.
Entrambi seguono la stessa regola: si crede solo a ciò che è stato eseguito.

## Probe: la lista mente, l'encode no

`ffmpeg -hwaccels` e `-encoders` sono pubblicità. Su questa macchina:

| Encoder | Elencato | Test-encode 8 frame | Esito |
|---|---|---|---|
| `h264_nvenc` | sì | 328 ms | **usabile** |
| `hevc_nvenc` | sì | 287 ms | usabile |
| `h264_qsv` | sì | 700 ms | usabile (iGPU Intel) |
| `hevc_qsv` | sì | 832 ms | usabile |
| `h264_amf` | sì | 121 ms, **errore** | **da scartare** |
| `libx264` | sì | 154 ms | usabile |

`h264_amf` è elencato e non parte (`amfrt64.dll`). Per questo `vx.accel.test_encode()`
scrive davvero otto frame e guarda il file: se non esiste o pesa meno di 1 KB, l'encoder
non c'è.

Il risultato del probe è in cache (`%APPDATA%\vx\accel.json`, TTL da
`accel.probe_ttl_hours`, default 24 h). Forzare un nuovo probe:

```powershell
$env:PYTHONPATH = "<SKILL_DIR>\scripts"
& $PY -m vx.accel
```

## Routing per tipo di lavoro

| Contesto | Encoder | Perché |
|---|---|---|
| proxy 640 px | primo hardware verificato | la velocità batte tutto: è materiale di lavoro |
| anteprima (`--precision proxy`) | primo hardware verificato | la guardi adesso, non la consegni |
| master (`--precision full`) | `libx264` (o `libx265`) | controllo su bitrate e qualità |
| richiesta esplicita del job | quello richiesto | `render.encoder` nel piano |

Misure reali sul clip di prova (12 s, 1920×1080):

| Encoder | Tempo | Peso | Nota |
|---|---|---|---|
| `libx264` medium, crf 18 | 6,00 s | 13,1 MB | riferimento qualità |
| `libx264` fast, crf 18 | 5,35 s | 13,2 MB | |
| `h264_nvenc` p5, cq 20 | **2,62 s** | 20,3 MB | **2,3× più veloce, +55% peso** |

Il +55% di peso è il motivo per cui l'hardware **non** va usato sul master: chi
ricodifica (le piattaforme) parte da un file più grosso e più sporco. Sul proxy invece
il peso non conta e i 2,3× si sentono.

Sul proxy a 640 px il guadagno misurato è 9,3× → 15,8× realtime.

## Whisper su GPU

| Configurazione | 232 s di audio, modello `small` |
|---|---|
| CPU int8 | 284 s (0,82× realtime) |
| **CUDA float16** | **82 s (2,8× realtime)** |

Guadagno **3,5×**: su un podcast di un'ora sono 73 → 21 minuti. Per questo la GPU è il
target primario e la CPU il fallback.

Serve il runtime CUDA, che non è in torch ma in due pacchetti pip:

```powershell
& $PY -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Le DLL finiscono in `site-packages\nvidia\<lib>\bin` e vanno registrate con
`os.add_dll_directory` **prima** di importare `faster_whisper`: `vx.accel.register_cuda_dlls()`
lo fa. Senza, l'errore è `cublas64_12.dll is not found`.

Il probe lancia una trascrizione vera in un processo figlio con timeout: se non
completa entro 180 s, la GPU è considerata **non disponibile** (un hang è un guasto,
non un successo lento) e si usa CPU int8 con il motivo scritto nel report.

## Silero VAD

Perché: la stima p10/p90 dell'RMS misura i frame "quieti", e una pausa dentro una
parola, un respiro o una vocale sostenuta sono tutti quieti. Il risultato è un noise
floor sbagliato, quindi un SNR sbagliato, quindi un denoise che può mangiarsi la
fondamentale della voce.

Silero dà una probabilità di parlato per finestra. Da quella maschera si ricavano due
cose:

1. **le regioni di parlato** — l'unico posto dove un taglio non può atterrare;
2. **un noise floor misurato fuori dal parlato**, che è ciò che "rumore di fondo"
   significa davvero.

Stesso clip, due metodi:

| Metodo | Noise floor | Livello parlato | SNR |
|---|---|---|---|
| p10/p90 RMS | stimato sui frame quieti | stimato | incoerente |
| **VAD-masked** | **−36,94 dBFS** | **−21,09 dBFS** | **15,85 dB** |

### Calibrazione del modello (fatta misurando)

Il modello su `master` e quello del tag `v5.1.2` **non sono la stessa build** (stessa
dimensione, hash diverso) e non accettano la stessa finestra:

| Modello | Finestra | Probabilità max | Ratio parlato (target 0,82) |
|---|---|---|---|
| master | 512 | 0,096 | 0,000 |
| master | 256 | 0,899 | 0,123 |
| **v5.1.2** | 512 | **1,000** | **0,841** |
| v5.1.2 | 256 | 0,979 | 0,023 |

Il target viene da `silencedetect`, che è un segnale indipendente (energia, non
contenuto). Adottata la coppia **v5.1.2 + 512 campioni a 16 kHz**.

Il modello è in `assets/models/silero_vad.onnx` (2.327.524 byte) e gira su CPU via
`onnxruntime`: è piccolo abbastanza che mandarlo su GPU costerebbe più di quanto
risparmia.

### Regola dell'inazione

Il detector musicale è ancora un'euristica spettrale. La sua confidenza è **zero**
quando il ratio sta fra 0,2 e 0,8, cioè quando sta indovinando.
Con confidenza sotto `audio.detector_min_confidence` (default 0,6) il piano **vieta
ogni trattamento distruttivo** — denoise e taglio silenzi — con una motivazione
esplicita:

```
denoise      enabled=False  ::  detector musicale incerto: non distruggo il segnale
cut_silence  enabled=False  ::  detector musicale incerto: nessun taglio alla cieca
```

L'incertezza porta all'inazione, non all'azione. Un detector vero (YAMNet o simile)
può essere innestato in `audio.music_detector` senza toccare il piano.

## Parametri correlati

| Chiave | Default | Effetto |
|---|---|---|
| `audio.vad` | `silero` | `none` disattiva il VAD e torna a p10/p90 |
| `audio.vad_threshold` | 0.5 | soglia di probabilità |
| `audio.vad_min_speech` | 0.25 s | regione minima valida |
| `audio.vad_min_silence` | 0.35 s | gap minimo per separare due regioni |
| `audio.detector_min_confidence` | 0.6 | sotto questa soglia: nessuna azione distruttiva |
| `accel.probe_ttl_hours` | 24 | validità della cache del probe |
| `accel.master_encoder` | `libx264` | encoder del master |
| `render.encoder` | — | forzatura per-job |

Esempio: forzare NVENC sul master per un export urgente.

```powershell
& $PY $VX render --plan plan.json --precision full --override render.encoder=h264_nvenc
```