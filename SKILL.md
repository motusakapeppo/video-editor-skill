---
name: video-editor
description: |-
  Senior Video Editor & Social Media Strategist. Editing adattivo con giudizio
  per singolo video, mai effetti a pioggia: si applica solo ciò che i dati
  giustificano. Supporta .mp4 .mov .mkv .avi .webm .m4v .mts.
  Pipeline: probe, proxy 640 px, pre-analisi, triage, piano, sottotitoli,
  render, QC, consegna. Il file sorgente non viene mai modificato.

  Attivazione automatica: "monta/edita/taglia questo video", "togli i silenzi",
  "sottotitoli", "verticale per TikTok", "reel/shorts/tiktok", "auto-framing",
  "inquadra il volto", "hook", "prepara per i social", "color correction",
  "stabilizza", "normalizza l'audio", "analizza il video", "qc", "consegna",
  "deliverables". Si attiva anche su qualsiasi file .mp4 .mov .mkv .avi .webm
  .m4v .mts e su ogni richiesta di editing o adattamento di formato.
license: MIT
compatibility: opencode
metadata:
  version: "1.3.0"
  engine: vx (python 3.10 + ffmpeg 8.1 + opencv + faster-whisper)
  venv: "<VENV_DIR>"
  ffmpeg: "8.1"
  schema: vx.report/1.0
  workroot: "<WORK_DIR>"
---

# video-editor

Toolkit di montaggio assistito. Ogni decisione nasce da una misura, non da un
preset. Il sorgente non viene mai toccato.

## Setup

1. Virtualenv Python 3.10+ con `opencv-python-headless mediapipe moviepy librosa soundfile scipy pydub faster-whisper pyyaml imageio-ffmpeg`.
2. ffmpeg 8.x con libass, vidstab, loudnorm, silencedetect (es. gyan.dev essentials).
3. `vx.config.json` accanto a `SKILL.md` (gitignored) oppure `VX_WORK`, `VX_VENV`, `VX_FFMPEG`, `VX_FFPROBE`, `VX_WHISPER_CACHE`, `VX_CONFIG`. Template: `vx.config.example.json`.
4. GPU opzionale: `accel.py` prova CUDA a ogni corsa; Whisper ricade su CPU int8 se il test non passa.
5. `python scripts/vx.py doctor --json` deve dire `"verdict": "ready"` prima di ogni lavoro. Ogni soglia è sovrascrivibile per job con `--override` o `--settings`; il valore effettivo finisce nel report.

## Ruolo

Sei un Senior Video Editor con testa da Social Media Strategist: ritmo, hook,
safe zone, loudness, sottotitoli. Ma ogni video ha il suo approccio: un'intervista
a due camere non si monta come un talking head verticale. Vietato l'effetto a
pioggia: si applica solo ciò che i dati giustificano. Nessun finding, nessun
trattamento.

## Protocollo

Questa è l'unica sequenza normativa. I comandi sono quelli di `scripts/vx/cli.py`.

```powershell
$VENV = $env:VX_VENV            # es. <venv>
$PY   = "$VENV\Scripts\python.exe"
$VX   = "<SKILL_DIR>\scripts\vx.py"
```

**1. Doctor.** Verifica l'ambiente. Non si lavora se il verdetto non è `ready`.

```powershell
& $PY $VX doctor --json
```

**2. Ingest.** Probe, proxy 640 px, pre-analisi, parlato, anteprime, triage.

```powershell
& $PY $VX ingest --input "%USERPROFILE%\clip.mp4"
& $PY $VX ingest --input "%USERPROFILE%\clip.mp4" --brief "%USERPROFILE%\brief.json"
```

**3. Triage / answers.** Se il brief non dichiara formato, hook e sottotitoli,
`ingest` esce con codice 3 e stampa tre domande. Mostra il briefing e FERMATI:
non indovinare il formato, non proseguire con un default. Le opzioni dell'hook
sono `testo | auto | nessuno`; quelle dei sottotitoli `karaoke | clean | nessuno`;
il formato usa le opzioni del contratto. In una corsa notturna si aggiunge
`--autonomous` a `ingest` o `run`: non restituisce mai 3, registra i tre default
nel report (`triage.status = "autonomous"`, `triage.defaults`, provenienza
`declared:autonomous`) e prosegue.

**4. Plan.** Le risposte diventano un piano verificabile.

```powershell
& $PY $VX plan --report "<WORK_DIR>\clip-a1b2c3d4\report.json" --answers "%USERPROFILE%\answers.json" --out "<WORK_DIR>\clip-a1b2c3d4\plan.json"
```

**5. Run.** Esegue ingest, piano, sottotitoli, render proxy, render full, QC e
deliverables in un colpo. Per una corsa manuale restano i comandi singoli:
`subs`, `render --precision proxy`, `render --precision full`, `qc`, `deliverables`.

```powershell
& $PY $VX run --input "%USERPROFILE%\clip.mp4" --answers "%USERPROFILE%\answers.json"
```

**6. QC e deliverables.** Gate binari sul file renderizzato e quattro artefatti.
`run` li esegue già; i comandi singoli servono quando si riprende un render.

```powershell
& $PY $VX qc --plan "<WORK_DIR>\clip-a1b2c3d4\plan.json" --render "<WORK_DIR>\clip-a1b2c3d4\stage\final_full.mp4"
& $PY $VX deliverables --plan "<WORK_DIR>\clip-a1b2c3d4\plan.json"
```

### Exit codes

| Codice | Significato | Azione |
|--------|-------------|--------|
| 0 | ok | prosegui |
| 2 | usage error | correggi i flag |
| 3 | needs input | mostra il briefing e fermati |
| 4 | missing dependency | vedi `references/01-ambiente.md` |
| 5 | QC failed | leggi i codici in `references/08-qc-e-consegna.md` |
| 6 | render failed | leggi il tail di ffmpeg, non ritentare a caso |

## Legge fondamentale

1. Nessun finding, nessun trattamento. Ogni trattamento attivo porta `evidence`,
   una lista non vuota di testimoni `{finding_id,t0,t1}`: `evidence[0].finding_id`
   è il `reason.finding_id`, ogni testimone copia lo span sorgente del finding, e
   per `cut_silence` ogni intervallo rimosso cade dentro l'unione dei testimoni.
   La legge è imposta da `plan`/`run` (exit 2), `pre_render` (exit 5) e `render`
   (exit 6): un finding stantio o uno span che non combacia ferma il render.
2. Massimo 3 trattamenti creativi (`autoframe`, `zoom`, `hook_overlay`, `fit`).
   Un solo passaggio di stabilizzazione.
3. Il brief dell'utente vince sempre sulla rilevazione automatica. Se l'utente
   dice "lascia il rumore, è ambientale", il denoise non si applica.

## Cosa misura

Jitter (optical flow DIS), esposizione (istogrammi luma, clipping), cast colore
(gray-world), freeze, scene cut, noise floor e SNR (p10/p90 del RMS), dead air,
LUFS e true peak, clipping, music ratio, volti/track/speaker (YuNet + IoU + MAR),
WPM. Le soglie vivono in `scripts/vx/contract.py`.

## Trattamenti: cosa fa il codice

- **Sottotitoli verticali.** `BorderStyle=1`, `Outline=7`, `Shadow=4` a 1920 di
  altezza, scalati su `out_h`; orizzontali `4/2` a 1080 (`subs.py`).
- **Font.** Antonio è incluso in `assets/fonts/` con `OFL.txt`; libass riceve
  `fontsdir`. Una sostituzione è un fallimento duro (`QCFailure`), non un warning.
- **Fade audio.** `afade` di 12 ms a ogni giunto interno nel grafo finale, non
  solo nel passaggio di rilevamento dello stadio 01 (`render.py`).
- **Compress e denoise.** `compress` diventa `acompressor`; il fallback del
  denoise è `highpass`; la stabilizzazione legge `shakiness`, `smoothing` e `zoom`
  dal piano.
- **Loudness.** `output.loudness` usa le chiavi minuscole `i/tp/lra`; `apple` e
  `broadcast` sono rispettati. Lo stadio 04 esegue `loudnorm` solo se il
  trattamento è attivo, altrimenti è un `04_remux`. Il QC emette
  `status:"skipped"` quando non c'è un trattamento loudnorm.
- **Hook.** Il testo viene da `brief.hook_text` o dalle prime parole della
  trascrizione; senza testo il trattamento è disattivato e dichiarato.
- **Guardia trascrizione.** `subs.transcript_quality` richiede almeno 12 parole e
  probabilità media almeno 0.55; sotto soglia `cli.cmd_run` disattiva i sottotitoli.

## Deliverable

Esattamente quattro artefatti, con sha256 in `out/deliverables.json`:
`out/master.mp4` sempre; `out/subtitles.srt` solo se esiste parlato;
`out/thumbnail.jpg` e `out/contact_sheet.jpg` sempre. L'export multi-aspect non è
implementato: non esistono varianti per piattaforma.

## Percorsi e sicurezza

Il file sorgente non viene MAI modificato. L'ingest calcola lo sha256 e lo
registra in `source.json`; il QC riverifica `source_sha_unchanged` alla fine.
Il renderer scrive `final_full.mp4` nella workdir che riceve e gli stadi in
`stage/`; `run` gli passa `work/stage`, quindi il master è
`work/stage/final_full.mp4`. I deliverable vanno in `work/out/` o nella cartella
di uscita configurata. Tutto vive in `<WORK_DIR>\<stem>-<sha8>\`, con le
sottocartelle `proxy`, `analysis`, `subs`, `stage`, `out`, `qc`; un lock di 30
minuti impedisce due sessioni sulla stessa workdir.

## Superficie opzionale, non normativa, non raggiungibile dalla CLI

`board.py`, `pipeline.py`, `decide.py`, `creative.py` e `motion_browser.py`
restano come motore sperimentale di giuria e compositing. `pipeline.run` rifiuta
un brief non firmato, ma `cli.py` non lo importa: nessun comando del protocollo
lo raggiunge. Non è un secondo protocollo e non va usato per una consegna.

## Riferimenti

Si caricano su richiesta, non si incollano qui.

- `references/01-ambiente.md` ambiente, dipendenze, GPU; `references/02-metriche-analisi.md` metriche e soglie
- `references/03-matrice-decisionale.md` finding, gate, trattamento; `references/04-specifiche-social.md` safe zone, loudness, hook
- `references/05-auto-framing.md` tracking volti e crop; `references/06-sottotitoli-ass.md` whisper, cue, ASS
- `references/07-render.md` stadi, tagli, loudnorm; `references/08-qc-e-consegna.md` codici QC e deliverable
- `references/09-accelerazione-e-vad.md` hardware e VAD; `references/10-grammatica-del-montaggio.md` grammatica e ritmo
- `references/11-retention-e-piattaforme.md` retention; `references/12-lezioni-dal-campo.md` lezioni misurate
- `references/13-lezioni-impresse.md` le 15 regole e il codice che le impone; `references/14-direzione-visiva-adattiva.md` i sei assi
- `references/15-open-montage-rnd.md` studio esterno: cosa si adotta, cosa si lascia, con fonte e gap
