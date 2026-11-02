# video-editor-skill

> Versione: **1.5.0** — protocollo CLI unico, quattro deliverable, nessun export multi-aspect.

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
| opencode | 2.x | la skill si attiva da sola su file video e richieste di editing |

Dipendenze Python:

```powershell
python -m venv <venv>
<venv>\Scripts\python.exe -m pip install opencv-python-headless mediapipe moviepy `
  librosa soundfile scipy pydub faster-whisper pyyaml imageio-ffmpeg
```

## Installazione

La skill vive in una cartella il cui nome è il suo ID:

```text
~/.config/opencode/skills/video-editor/
├── SKILL.md
├── scripts/
├── references/
└── assets/
```

OpenCode 2 la scopre lì e, per compatibilità, anche nella vecchia cartella
`~/.config/opencode/skill/`. La si sposta intera, non solo `SKILL.md`: i percorsi
scritti dentro la skill sono relativi alla cartella che contiene `SKILL.md`.

L'unico modo supportato è l'install editable dalla radice del repo:

```powershell
<venv>\Scripts\python.exe -m pip install -e .
vx --version
vx doctor
```

`vx --version` stampa `vx 1.5.0`; `vx doctor` deve dire `ready`. L'install crea
anche `vx-mcp`, il server MCP su stdio. Un'install non editable non è
supportata: `SKILL_ROOT` e `assets/fonts/` devono restare nel repo.

Se il repo viene spostato, l'install editable va rifatta dalla nuova radice:
l'entry point contiene il percorso assoluto del pacchetto. Il server MCP si
registra in OpenCode 2 con un comando, senza toccare il resto della
configurazione:

```powershell
opencode mcp add video-editor --global --env PYTHONIOENCODING=utf-8 -- "<venv>\Scripts\vx-mcp.exe"
opencode mcp list
```

`mcp list` deve mostrare `video-editor  connected`. La skill resta usabile anche
senza MCP: la CLI è il protocollo normativo, l'MCP è la sua variante per agenti.
Dettagli, forma JSON e differenze V1/V2 in `references/24-api-e-mcp.md`.

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
| `auto` | prompt + cartella asset → triage, piano, render, QC; `--montage` monta più A-roll, `--fill crop\|fit\|blur` sceglie il riempimento; scrive `plan.json`, `auto_report.json` e `out/`; exit 0/2/5/6 |
| `deliverables` | matrice di consegna |
| `vx-mcp` | server MCP su stdio: quattro tool, JSON-RPC 2.0 per riga |

## Job asincroni

Un render lungo non deve vivere dentro il timeout di un tool MCP. Il motore
asincrono separa la richiesta dall'esecuzione:

- `vx_job_submit` (`prompt`, `assets`, `out` obbligatori; `workdir`, `fps`,
  `manifest`, `autonomous`, `crf`, `preset` opzionali) scrive un record `queued`
  e torna subito con l'id del job.
- `vx_job_status` (`job_id`, 32 esadecimali minuscoli) restituisce il record:
  stato, percentuale, stage, risultato o errore.

Lo stato vive su disco in `<work_root>/jobs/<job_id>/job.json`, scritto in modo
atomico. Il worker è staccato e sopravvive all'uscita del server MCP, quindi una
sessione nuova può leggere un job che non ha creato. Non c'è cancellazione: un
job avviato arriva a `completed` o `failed`. `vx_auto` resta per i job brevi.
Dettagli e limiti in `references/25-job-engine.md`.

## Ponte analisi-montaggio

La Fase A collega le misure al montaggio. `vx auto --montage` monta ogni video con
audio che supera i cancelli come `a_roll`, in ordine di scoperta; `--fill crop|fit|blur`
sceglie il riempimento, con `fit` ancora predefinito e byte-identico al
comportamento precedente. Il fattore di upscale è dichiarato per clip in
`auto_report.json` sotto `fill[]`; il riempimento resta un parametro di render, non
un campo dell'EDL, che rimane 2.2.0.

Il ponte misura il gain per clip sui soli tratti conservati (`audio.loudness_spans`),
limita il gain a [-60,+24] e dichiara `shortfall_lu` o `measurement_invalid` quando
il numero non è affidabile. I tagli nascono dagli span VAD e `dead_air`, e un
intervallo rimosso fuori dall'unione dei silenzi misurati è un errore duro. I
sottotitoli vengono rimappati sulla timeline tagliata, spezzati sui tagli e bruciati
sulla tela con `margin_v = round(out_h*0.08)` clampato a [24, out_h/4]. Il reframe
usa `autoframe.build_crop_plan` e fa pan, non zoom. Denoise `afftdn` e
stabilizzazione `deshake` in una passata si attivano solo col proprio finding,
mentre il percorso EditPlan usa vidstab in due passate. Lo score dei B-roll è la
quota della finestra coperta dalle parole trascritte per la loro confidenza media
pesata sul tempo coperto: è un proxy di copertura temporale, non una similarità
semantica. La deduplica è un
dHash a 192 bit su tre frame con soglia 0.06; il loop della musica usa `acrossfade`
con copie limitate a 256. Limiti dichiarati: su alcune clip del caso studio il VAD
non era disponibile, quindi non c'è silenzio misurato e nulla viene tagliato.

## Cosa misura

Stabilità (optical flow), esposizione (istogrammi luma, clipping), dominante colore
(gray-world), freeze, scene cut, noise floor e SNR (p10/p90 del RMS per frame),
dead air, LUFS e true peak, clipping, music ratio, volti/track/speaker (YuNet + IoU +
mouth-aspect-ratio), WPM. Le soglie vivono in un solo posto: `scripts/vx/contract.py`.

## Struttura

```
SKILL.md                  ruolo, protocollo, trigger di auto-invocazione
references/01..26.md      playbook: ambiente, metriche, matrice, social, framing, subs, render, QC,
                          accelerazione, grammatica, retention, lezioni, direzione visiva, R&D esterna, EDL,
                          render multi-sorgente, regia semantica, grammatica e mixer audio, triage e auto, API e MCP,
                          motore di job asincrono, EDL 3 e ponte con la Fase A implementata
scripts/vx.py             launcher
scripts/vx/
  contract.py             interfaccia congelata: schemi, soglie, validatori
  ffmpeg.py probe.py      esecuzione sicura (mai shell), sonda tecnica
  winpath.py config.py    quoting Windows, configurazione portabile
  doctor.py proxy.py      ambiente, flusso proxy
  analyze.py audio.py faces.py autoframe.py subs.py
  plan.py triage.py       decisione e pausa interattiva
  render.py qc.py report.py cli.py
  api.py mcp_server.py    superficie stabile e server MCP su stdio
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
