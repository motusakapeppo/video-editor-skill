# 01. Ambiente

Tutto quello che serve per far girare il toolkit su una macchina Windows nuova.
Nessuna installazione automatica: se manca qualcosa, si installa a mano.

## Interprete

| Voce | Valore |
|------|--------|
| Python | `<VENV_DIR>\Scripts\python.exe` |
| Versione | 3.10 o superiore, 64 bit |
| Codice | `<SKILL_DIR>\scripts\vx\` |
| Entry point CLI | `<SKILL_DIR>\scripts\vx.py` |

Mai il Python di sistema. Il venv contiene le versioni pinnate e le wheel
precompilate per Windows.

## ffmpeg

| Voce | Valore |
|------|--------|
| Percorso | `%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe` |
| Versione | 8.1-essentials_build-www.gyan.dev |
| Build | gcc 15.2.0, MSYS2 |
| Ricerca | variabile `VX_FFMPEG`, poi `PATH`, poi `%LOCALAPPDATA%\Microsoft\WinGet\Links` |

`vx.ffmpeg` non usa mai la shell: ogni comando è una lista di argomenti. Se
`ffmpeg` non viene trovato, l'errore è `MissingDependency` con exit code 4.

## Capacità richieste

Il toolkit richiede questi filtri. `vx.ffmpeg.capabilities()` li ricontrolla a
runtime e `require()` blocca il trattamento se uno manca.

| Filtro | Uso |
|--------|-----|
| `vidstabdetect`, `vidstabtransform` | stabilizzazione a due passaggi |
| `silencedetect` | dead air (richiede `loglevel=info`) |
| `loudnorm`, `ebur128` | loudness e true peak |
| `astats` | noise floor e speech level |
| `scdet`, `freezedetect` | scene cut di controllo, freeze |
| `subtitles`, `ass` | burn-in sottotitoli |
| `concat`, `trim`, `atrim` | tagli frame-accurate |
| `afade`, `acrossfade` | fade ai giunti |
| `crop`, `scale`, `gblur`, `overlay` | crop virtuale e fit con blur |
| `afftdn`, `eq`, `acompressor`, `highpass` | pulizia audio e colore |
| `blackdetect`, `volumedetect` | gate QC |
| `tpad`, `apad`, `anullsrc` | riempimento |

Encoder: `libx264`, `aac`. Muxer: mp4 con `+faststart`.

## Pacchetti nel venv

| Pacchetto | Versione | Serve a |
|-----------|----------|---------|
| `numpy` | 2.2.6 | tutto il calcolo numerico |
| `opencv-contrib-python` | 5.0.0.93 | `FaceDetectorYN`, optical flow DIS |
| `opencv-python-headless` | 5.0.0.93 | fallback headless |
| `faster-whisper` | 1.2.1 | trascrizione con word timestamps |
| `ctranslate2` | 4.8.3 | backend di faster-whisper |
| `librosa` | 0.11.0 | music detection (con fallback numpy) |
| `mediapipe` | 1.1.1 | detector opzionale, non attivo |
| `onnxruntime` | 1.23.2 | runtime ONNX |
| `scipy` | 1.15.3 | supporto numerico |
| `soundfile` | 0.14.0 | I/O audio |
| `av` | 17.1.0 | demuxing |
| `PyYAML` | 6.0.3 | parsing frontmatter |

## Modelli

| Modello | Percorso | Stato |
|---------|----------|-------|
| YuNet face detection | `assets\models\face_detection_yunet_2023mar.onnx` | incluso nel repo, 232589 byte |
| MediaPipe face landmarker | `assets\models\face_landmarker.task` | opzionale, se assente MediaPipe non si attiva |
| faster-whisper small | `<WHISPER_CACHE>\models--Systran--faster-whisper-small` | scaricato al primo uso |
| faster-whisper medium | `<WHISPER_CACHE>\models--Systran--faster-whisper-medium` | scaricato al primo uso |
| faster-whisper large-v3 | `<WHISPER_CACHE>\models--Systran--faster-whisper-large-v3` | scaricato al primo uso |

`mediapipe_available()` restituisce False quando il bundle `.task` non esiste.
Il controllo sul file è il gate reale: l'API Tasks non sa caricare l'ONNX di
YuNet, quindi un semplice `import mediapipe` mentirebbe.

## GPU

Il toolkit non usa la GPU, anche quando è presente. `vx.subs.transcribe` è
fissato a `device="cpu"`, `compute_type="int8"`, e OpenCV lavora su CPU. È
deliberato: la parità proxy/full e la riproducibilità contano più della
velocità.

## Reinstallare il venv

```powershell
$PY = "<VENV_DIR>\Scripts\python.exe"
& $PY -m pip install --upgrade pip
& $PY -m pip install opencv-python-headless mediapipe moviepy librosa soundfile scipy pydub faster-whisper pyyaml imageio-ffmpeg
```

`numpy`, `ctranslate2`, `onnxruntime` e `av` arrivano come dipendenze. Per il
tracking dei volti serve `opencv-contrib-python`, che porta `cv2.FaceDetectorYN`.

## Verifica dell'ambiente: `vx.doctor`

Il modo canonico di sapere se la macchina può eseguire la pipeline. Scansione in
sola lettura, non installa nulla.

```powershell
$PY = "<VENV_DIR>\Scripts\python.exe"
$env:PYTHONPATH = "<SKILL_DIR>\scripts"
& $PY -m vx.doctor
& $PY -m vx.doctor --json
```

| Verdetto | Significato | Exit |
|----------|-------------|------|
| `ready` | ffmpeg, libx264, ass, cv2 e faster_whisper presenti | 0 |
| `degraded` | manca qualcosa di opzionale, elencato in `warnings` | 0 |
| `broken` | manca ffmpeg o libx264, non si può encodare nulla | 4 |

Controlla 25 capacità ffmpeg, 10 pacchetti Python, il modello YuNet, `nvidia-smi`,
il runtime CUDA, la cache whisper e la workroot. `--json` dà tutto. Il verdetto
atteso è `ready`.

## Proxy

`vx.proxy` produce il proxy di analisi a 640 px, i frame di anteprima e la contact
sheet. Usa `crf 28`, `veryfast`, `rc-lookahead=0:bframes=0`, scala bicubica, audio
AAC 96k, e confronta il fingerprint prima e dopo: `source_unchanged` è misurato.

```powershell
$PY = "<VENV_DIR>\Scripts\python.exe"
$env:PYTHONPATH = "<SKILL_DIR>\scripts"
& $PY -m vx.proxy make --input "%USERPROFILE%\clip.mp4" --json
& $PY -m vx.proxy thumbs --input "%USERPROFILE%\clip.mp4" --out-dir "<WORK_DIR>\clip-a1b2c3d4\out"
```

I frame di anteprima sono uniformi, mai negli ultimi 0.2 s, con un frame a 1.0 s quando la clip supera i 2 s.

## Troubleshooting

| Sintomo | Causa | Rimedio |
|---------|-------|---------|
| `ffmpeg not found` | non è nel PATH | `winget install Gyan.FFmpeg`, oppure imposta `VX_FFMPEG` |
| `OpenCV (cv2) is not installed` | venv sbagliato | reinstalla `opencv-contrib-python` nel venv |
| `YuNet model missing` | asset cancellato | ripristina `assets\models\face_detection_yunet_2023mar.onnx` |
| `faster-whisper unavailable` | pacchetto assente | `pip install faster-whisper` |
| `workdir busy (lock age Ns)` | sessione precedente interrotta | cancella `<WORK_DIR>\<stem>-<sha8>\.lock` |
| Font sostituito in silenzio | Arial Black assente | installa il font, poi `verify_fonts` |

## Note su PowerShell 5.1

Non esiste `&&`: per concatenare usa `;`, per concatenare solo in caso di
successo usa `if ($?) { ... }`. I path con spazi vanno fra doppi apici.
