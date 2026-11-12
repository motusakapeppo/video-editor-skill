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
opencode mcp add video-editor --global `
  --env PYTHONIOENCODING=utf-8 `
  --env PYTHONPATH="<repo>\scripts" `
  -- "<venv>\Scripts\python.exe" -m vx.mcp_server
opencode mcp list
```

`mcp list` deve mostrare `video-editor  connected`. La forma `python -m
vx.mcp_server` è quella provata: l'entry point `vx-mcp` risponde a mano ma
avviato dal servizio desktop usciva con 255. In un progetto con un proprio
`.opencode/opencode.json` la voce va scritta lì, senza `--global`. La skill resta
usabile anche senza MCP: la CLI è il protocollo normativo, l'MCP è la sua
variante per agenti. Dettagli, forma JSON e differenze V1/V2 in
`references/24-api-e-mcp.md`.

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

## Cosa la vista mostra, e cosa resta nel report

**La vista non si chiede: parte da sola e sta dentro OpenCode.** `vx reel`,
`vx auto` e il tool MCP `vx_auto` chiamano `auto.autostart_view` →
`cli.ensure_view`: il daemon si accende (o si riusa, se già vivo) **prima** che
il girato venga toccato, e la pagina si mostra nel browser interno di OpenCode
(`browser.tabs.open`), non in una finestra separata. Il browser di sistema resta
un'azione dentro la pagina («Apri fuori»). `vx up` accende a mano, `vx down`
ferma; `--no-view` o `VX_NO_VIEW=1` è l'unico modo per non accenderla (CI,
macchine senza browser). Dopo l'apertura il lavoro aspetta qualche secondo il
primo giro di `/api/state`: senza quell'attesa i cancelli userebbero i default
mentre l'utente sta ancora caricando la pagina.

Il primo passo non è un dettaglio tecnico: **chi decide**. `--present` (o «voglio
scegliere io» nella richiesta, o la risposta nella vista) tiene aperti i cancelli
su formato, hook, sottotitoli, font e fotogrammi; `--auto-choices` (o «fai tutto
tu») non chiede niente, decide con le misure e dichiara ogni scelta in
`report["presence"]` e `report["questions"]`.

### Le risposte hanno una conseguenza

| risposta | cosa cambia davvero |
|---|---|
| `presence` = automatico | le tre domande successive non vengono poste: `asked: false` e il perché nel report |
| `target_format` | il **canvas**: `decide` nasce con quell'aspetto, quindi triage, EDL, render e sottotitoli sono coerenti. In «fai tutto tu» senza piattaforma nel prompt, l'aspetto segue il materiale misurato (`3 asset su 3 orizzontali` → 16:9) |
| `hook` = `nessuno` | la card dell'apertura non si scrive, e non viene dichiarata come mancanza |
| `hook` = `testo` | la card si forza, con le parole del brief o la prima riga dei sottotitoli — mai una frase inventata; se non c'è testo, lo dice |
| `subtitles` | acceso/spento **e** stile `karaoke`/`clean`, passato al ponte |
| `font` | `font_choice.json` vince: il master usa quella famiglia |
| fotogrammi | l'approvazione decide la consegna |

Se una risposta non ha prodotto niente, il report lo dice (`applied: false` con
il motivo): non esiste una risposta "registrata" che sembri applicata.

### La pagina si guarda mentre lavora

`progress.json` (e la pagina) si pubblicano a ogni stadio **anche con `--json`**:
prima l'intero pannello era dentro `if not json`, quindi un lavoro lanciato per
leggere il report non aggiornava niente e la vista mostrava solo il resoconto
finale. Il contratto (`.vx/run.jsonl`) si legge riga per riga; il journal cresce
a ogni fase. L'autostart vale per **ogni** comando che fa un lavoro (`auto`,
`reel`, `scene`, `render`, `subs`, `edl3`, `ingest`), non solo per i due
principali, e la pagina va tenuta in **una scheda sola** (`browser.tabs.list` →
naviga quella, non aprirne un'altra).

### Velocità del motore motion (Chromium)

I fotogrammi del layer grafico si leggono dal **canvas** (`toDataURL('image/png')`),
non dal viewport. Misurato su un master vero (1080x1920, 30 fps, 72 frame):
**136-157 → 86-94 ms per frame** (1,5-1,6x). La cattura da sola scende da 82 a
34 ms (2,4x), ma decode, composizione ed encoding restano e diluiscono il
guadagno. Qualità: si legge il pixel che il canvas ha già, senza ricomposizione;
la differenza è 88 pixel su 2.073.600 a 1/255, sotto la tolleranza dei golden
(8/255, 0,02%) — e `test_motion_golden` resta verde.

Provata e **rifiutata** la cattura in parallelo (2 browser, 1,31x): due istanze
di Chrome rasterizzano il testo in modo leggermente diverso — 0,7% dei pixel,
delta fino a 43/255. Velocità contro qualità, e la qualità non si tocca.

La pagina non è un riassunto a sentimento: legge `/api/state` e disegna quello che
il lavoro ha misurato. Accanto alla SCALETTA (hook, clip, b-roll, movimenti) e al
CONTRATTO (gli otto stadi con valore e soglia), ci sono due blocchi: **FUORI**,
che dichiara quello che è stato scartato, e **MISURE**, che mostra quello che è
stato misurato e deciso.

| voce (`out_of_frame`) | cosa dice |
|---|---|
| `warnings[]` | gli avvisi del giro, uno per riga |
| `ignored[]` | file nella cartella che non sono media, con l'estensione |
| `rejected[]` | asset scartati dal triage, col motivo e i numeri del motivo |
| `not_applied{}` | b-roll e sottotitoli non applicati, per motivo, con secondi e parole |
| `tail` · `shortfall` | coda della timeline e secondi mancanti rispetto alla richiesta |
| `excluded[]` | clip escluse dal piano, con il perché |
| `gaps[]` | sezioni rimaste senza materiale |
| `cuts` · `cuts_removed_sec` | quanti tagli di silenzio e quanti secondi hanno tolto |
| `resources` | modo di calcolo, worker, thread ffmpeg, whisper, GPU, sovrapposizione |

`measures` porta il resto del report senza inventare niente: `verification`
(ogni controllo con misurato e atteso), `policy`, `guard`, `beat`, `variety`,
`coherence`, `laws`, `audio`, `opening`, `triage`, `estimate`, `measured`,
`scenes`, `features`, `assets`, `timings`, `deliverable`. Le liste si fermano a
60 voci e le stringhe a 400 caratteri — è l'unico taglio, e non tocca i numeri.

Uno stadio saltato (per `--no-bridge`, cache o guardia non richiesta) è uno stadio
**saltato**, con il suo motivo: il log lo scriveva e la pagina lo chiamava
"mancante". Le tre domande della vista dichiarano se la risposta è stata applicata:
i sottotitoli hanno un interruttore, formato e hook no, e il report lo scrive in
`questions[].applied` e `questions[].why` invece di lasciar credere di essere stati
ascoltati.

Il resto — tempi per stadio, risorse, punteggi dei b-roll, ogni singolo
`not_applied`, ogni intervallo rimosso — resta in `auto_report.json` e
`bridge_report.json`: la pagina è la superficie, il report è il registro.

## Integrazione continua (CI)

Ogni push lancia `.github/workflows/ci.yml`: clone pulito, **ffmpeg 8.1.2
fissato**, `vx doctor`, poi la suite intera. Il **gate è Windows** (py3.10 e
py3.12): è la piattaforma supportata. Linux gira come job **informativo**
(`continue-on-error`): le golden sono catturate su Windows e l'ffmpeg di apt è
un'altra serie, quindi lì si vede se qualcosa peggiora, senza bloccare. macOS è
fuori scope.

Perché la versione è fissata: ffmpeg 9 ha rimosso `-filter_complex_script`,
l'opzione con cui si passano i grafi lunghi (8191 caratteri su Windows). Con
ffmpeg 9 ogni render EDL muore con `Unrecognized option`. `vx doctor` lo
dichiara (`ffmpeg.filter_complex_script: false` → verdetto `broken`), così
l'aggiornamento non arriva a sorpresa.

Come si legge: repo → **Actions** → il run → il job → lo step `Suite`. Un giro
verde significa 246 file e ~7000 controlli eseguiti su una macchina che non è
la tua.

Le golden si confrontano per pixel (`tests/_golden.py`: delta ≤ 8/255 e ≤ 0,02%
di pixel; in alternativa dichiarata `PSNR ≥ 25 dB`). Byte-identico vale solo
sulla macchina che le ha catturate; altrove sarebbe un test che fallisce per
l'ambiente, non per il codice.

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

## Dove riprendere

Il lavoro aperto vive in due posti, e nessuno dei due si ricorda a memoria:

```powershell
<venv>\Scripts\python.exe scripts\vx_tasks.py sync      # ledger -> board
<venv>\Scripts\python.exe scripts\vx_tasks.py next      # il primo task lavorabile
<venv>\Scripts\python.exe scripts\vx_tasks.py list --open
<venv>\Scripts\python.exe scripts\vx_tasks.py start F16-T02
<venv>\Scripts\python.exe scripts\vx_tasks.py done F16-T02 --evidence "..." --commit abc1234
```

`PLAN/TASKS.json` e' la copia di lavoro di `PLAN/MASTER_LEDGER.md`: una riga per
task, con stato, criteri di accettazione, prova e commit. Non si scrive a mano —
`sync` la rilegge dal ledger — e `check` fallisce se un task chiuso non porta la
sua prova, se uno stato non esiste o se ci sono piu' di due task in volo.

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
