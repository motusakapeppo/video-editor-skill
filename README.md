# video-editor-skill

> Versione: **1.3.0** — protocollo CLI unico, quattro deliverable, nessun export multi-aspect.

> **English TL;DR** — An adaptive video-editing skill for the opencode agent runtime.
> It measures a video before touching it (jitter, exposure, colour cast, noise floor,
> dead air, loudness, faces, speech), then **pauses and asks the operator** three
> questions before applying anything. No finding → no treatment: every enabled
> treatment carries a non-empty `evidence` list of `{finding_id,t0,t1}` witnesses
> that copy the finding's source-time span, and `cut_silence` must keep every
> removed interval inside the union of its witnesses. The source file is never
> modified.

Skill di montaggio assistito per opencode. Non è un preset: è una pipeline che misura,
decide, chiede conferma, e solo allora tocca il video.

```
ingest ──▶ proxy 640px ──▶ pre-analisi ──▶ TRIAGE (exit 3, ti ferma)
                                              │
                        ┌─────────────────────┘
                        ▼
             plan ──▶ sottotitoli ──▶ render proxy ──▶ render full ──▶ QC ──▶ consegna
```

## La legge che lo rende adattivo

**Nessun finding, nessun trattamento.** Ogni effetto attivo porta `evidence`, una
lista non vuota di testimoni `{finding_id,t0,t1}`: il primo testimone è il
`reason.finding_id`, ognuno copia lo span sorgente del finding, e per `cut_silence`
ogni intervallo rimosso cade dentro l'unione dei testimoni. Un finding stantio o
uno span che non combacia ferma il piano (exit 2), il pre-render (exit 5) e il
render (exit 6). Niente hook, niente zoom, niente sottotitoli, niente auto-framing
se i dati o il brief dell'utente non li chiedono. Massimo tre trattamenti creativi
per video; un solo passaggio di stabilizzazione; il brief vince sempre sulla
rilevazione automatica.

Se il brief non dichiara formato, hook e sottotitoli, il comando `ingest` **esce con
codice 3** e stampa un briefing con i numeri reali del tuo materiale e tre domande.
Non indovina il formato.

## Direzione visiva

La direzione visiva è decisa dalla misura, non dal gusto: palette, grade,
animazione, densità dei tagli, inquadratura e tipografia hanno un driver misurato
e una provenienza, e una confidenza fra 0.2 e 0.8 produce inazione. Il brief è
`vx.visual/1.0`; due progetti si confrontano con `distinct_surface` e
`hook_distinct`, che certificano solo la divergenza di superficie e dell'hook,
non la differenza di montaggio.

## Requisiti

| Componente | Versione | Note |
|---|---|---|
| Python | 3.10+ | dentro un virtualenv dedicato |
| ffmpeg | 8.x | build con `libass`, `vidstab`, `loudnorm`, `silencedetect` (es. gyan.dev essentials) |
| opencode | — | la skill si attiva da sola su file video e richieste di editing |

Dipendenze Python:

```powershell
python -m venv <venv>
<venv>\Scripts\python.exe -m pip install opencv-python-headless mediapipe moviepy `
  librosa soundfile scipy pydub faster-whisper pyyaml imageio-ffmpeg
```

## Setup

1. Copia `vx.config.example.json` in `vx.config.json` (è in `.gitignore`, resta tuo) e
   punta `work_root` e `venv` alle tue cartelle:

```json
{
  "work_root": "<work_root>",
  "venv": "<venv>",
  "proxy_width": 640
}
```

In alternativa, variabili d'ambiente: `VX_WORK`, `VX_VENV`, `VX_FFMPEG`, `VX_FFPROBE`,
`VX_WHISPER_CACHE`, `VX_CONFIG`.

2. Verifica l'ambiente. **Non si lavora se questo non dice `ready`:**

```powershell
<venv>\Scripts\python.exe scripts\vx.py doctor --json
```

Il modello per il face tracking (`assets/models/face_detection_yunet_2023mar.onnx`) è
incluso. I modelli Whisper vengono scaricati da HuggingFace al primo uso.

## Uso

```powershell
$PY = "<venv>\Scripts\python.exe"
$VX = ".\scripts\vx.py"

# 1. Fase 4 + 5: proxy, pre-analisi, triage. Esce 3 e ti fa le 3 domande.
& $PY $VX ingest --input "<clip.mp4>"

# 2. Fase 6 + 7: piano, sottotitoli, render anteprima, render finale, QC.
& $PY $VX run --input "<clip.mp4>" --answers "<answers.json>"
```

Ogni comando accetta `--json` (un solo oggetto JSON su stdout, log su stderr).
Exit code: `0` ok · `2` uso errato · `3` serve l'utente · `4` dipendenza mancante ·
`5` QC fallito · `6` render fallito.

### Comandi

| Comando | Cosa fa |
|---|---|
| `doctor` | scansione ambiente e verdetto `ready/degraded/broken` |
| `probe` | sonda tecnica del file, sola lettura |
| `proxy` | proxy a bassa risoluzione + anteprime + contact sheet |
| `analyze` | pre-analisi video/audio/volti, non modifica nulla |
| `subs` | trascrizione → ASS karaoke / SRT |
| `triage` | briefing interattivo, esce 3 |
| `plan` | matrice decisionale → `plan.json` verificabile |
| `render` | render da piano, `--precision proxy` o `full` |
| `qc` | gate binari sul file renderizzato |
| `ingest` | protocollo completo di analisi + triage |
| `run` | pipeline end-to-end |
| `deliverables` | matrice di consegna |

## Cosa misura

Stabilità (optical flow), esposizione (istogrammi luma, clipping), dominante colore
(gray-world), freeze, scene cut, noise floor e SNR (p10/p90 del RMS per frame),
dead air, LUFS e true peak, clipping, music ratio, volti/track/speaker (YuNet + IoU +
mouth-aspect-ratio), WPM. Le soglie vivono in un solo posto: `scripts/vx/contract.py`.

## Struttura

```
SKILL.md                  ruolo, protocollo, trigger di auto-invocazione
references/01..15.md      playbook: ambiente, metriche, matrice, social, framing, subs, render, QC,
                          accelerazione, grammatica, retention, lezioni, direzione visiva, R&D esterna
scripts/vx.py             launcher
scripts/vx/
  contract.py             interfaccia congelata: schemi, soglie, validatori
  ffmpeg.py probe.py      esecuzione sicura (mai shell), sonda tecnica
  winpath.py config.py    quoting Windows, configurazione portabile
  doctor.py proxy.py      ambiente, flusso proxy
  analyze.py audio.py faces.py autoframe.py subs.py
  plan.py triage.py       decisione e pausa interattiva
  render.py qc.py report.py cli.py
examples/                 brief/answers di esempio + test end-to-end
assets/models/            modello YuNet
```

Il lavoro vive in `<work_root>\<stem>-<sha8>\`; il file sorgente non viene mai
scritto, e il QC ricontrolla lo `sha256` prima di dichiarare finito un render.

## Test

```powershell
<venv>\Scripts\python.exe examples\e2e_check.py <video.mp4>
```

Verifica il gate di triage (exit 3), la purezza del `--json`, la citazione del finding
da parte di ogni trattamento attivo, i tagli dentro la durata, il tetto dei trattamenti
creativi. La legge dei testimoni (`evidence`, span copiato, unione per `cut_silence`)
è coperta da `tests/test_evidence_law.py` e `tests/test_evidence_gate.py`.

## Licenza

MIT — vedi `LICENSE`. Provenienza del modello YuNet (opencv_zoo) e dei progetti
da cui sono adattate le euristiche di framing e cutting: `NOTICE.md`.
