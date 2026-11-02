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

  Vedere il lavoro mentre si fa: "mostrami cosa succede", "fammi vedere",
  "segui il lavoro", "guardiamo mentre lavora", "apri il pannello",
  "pannello live", "watch", "voglio vedere il progresso". Con una di queste
  frasi `--watch` e' OBBLIGATORIO e l'indirizzo va aperto PRIMA del render.
license: MIT
compatibility: opencode
metadata:
  version: "1.5.0"
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

**1-bis. Se l'utente ha chiesto di vedere cosa accade**, `--watch` va aggiunto a
`auto` e l'indirizzo stampato va aperto subito nel pannello di anteprima:
`vx auto ... --watch` scrive `Lo guardi qui: http://127.0.0.1:<porta>/` prima del
render, e il server resta vivo dopo la fine, cosi' il pulsante del risultato
funziona anche a lavoro concluso. Senza `--watch` non si apre niente, e non si
promette una vista che non c'e'.

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

**5. Run.** Esegue ingest, piano, sottotitoli, render e QC in un colpo. Dura
minuti: timeout del tool disattivato o background, mai interrotto a metà. Per
una corsa manuale restano `subs`, `render --precision proxy|full`, `qc`, `deliverables`.

```powershell
& $PY $VX run --input "%USERPROFILE%\clip.mp4" --answers "%USERPROFILE%\answers.json"
```

**6. QC e deliverables.** Gate binari sul file renderizzato e quattro artefatti.
`run` li esegue già; i comandi singoli servono quando si riprende un render.

```powershell
& $PY $VX qc --plan "<WORK_DIR>\clip-a1b2c3d4\plan.json" --render "<WORK_DIR>\clip-a1b2c3d4\stage\final_full.mp4"
& $PY $VX deliverables --plan "<WORK_DIR>\clip-a1b2c3d4\plan.json"
```

**Auto.** `vx auto --prompt ... --assets ... --out ...` esegue triage, piano, render e QC in un colpo; `--montage` monta ogni video che passa i gate come `a_roll` in ordine, `--fill crop|fit|blur` sceglie il riempimento (`fit` predefinito, upscale per clip in `auto_report.json`); scrive `plan.json`, `auto_report.json` e `<workdir>/out`; exit 0/2/5/6.

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

## Doppio regime

`decide` sceglie l'iniziativa: 7 parametri espliciti, soglia 0.7. Sotto soglia è
**autonomo** (deduce formato, palette, ritmo, tipografia e motiva in
`creative_rationale`; i fallback sono dichiarati). Sopra è **strict**: esegue e si
ferma sul non quantificato. Il Python segnala `rd_search_required`, mai
`conducted`: quello lo mette l'agente dopo la ricerca, con le fonti. Ref 16.

## Cosa misura

Jitter (optical flow DIS), esposizione (istogrammi luma, clipping), cast colore
(gray-world), freeze, scene cut, noise floor e SNR (p10/p90 del RMS), dead air,
LUFS e true peak, clipping, music ratio, volti/track/speaker (YuNet + IoU + MAR),
WPM. Le soglie vivono in `scripts/vx/contract.py`; la tabella è read-only e
`settings` è l'unico scrittore.

## Trattamenti

Ogni trattamento e' nel codice e le soglie in `scripts/vx/contract.py`; il
dettaglio di cosa fa ciascuno (sottotitoli, font, fade, compress, loudness,
hook, guardia trascrizione) sta in `references/30-trattamenti.md`.

## Deliverable

Esattamente quattro artefatti, con sha256 in `out/deliverables.json`:
`out/master.mp4` sempre; `out/subtitles.srt` solo se esiste parlato;
`out/thumbnail.jpg` e `out/contact_sheet.jpg` sempre. L'export multi-aspect non è
implementato: non esistono varianti per piattaforma. Gate di regressione visiva: `python tests/test_edl3_golden.py` confronta un frame per ogni effetto con `tests/fixtures/edl3_golden/`; `python tests/test_motion_golden.py` ricattura i 54 template della Motion Studio a tre istanti e li confronta byte per byte con `tests/fixtures/motion_golden/`.

## Font e vista

Il font non si eredita da un default: `auto` propone dal contesto, scarica dal
catalogo e aspetta la scelta, dichiarandola nel report. La vista locale ha
selettore dei lavori, fotogrammi, approvazione, player e playhead vero.
Dettaglio: `references/28-font-proposta-e-cancello.md`, `references/29-vedere-il-lavoro.md`.

## Percorsi e sicurezza

Il sorgente non viene MAI modificato: l'ingest scrive lo sha256 in `source.json`
e il QC riverifica `source_sha_unchanged`. Il master è `work/stage/final_full.mp4`,
i deliverable in `work/out/`. Tutto vive in `<WORK_DIR>\<stem>-<sha8>\`
(`proxy`, `analysis`, `subs`, `stage`, `out`, `qc`); un lock per liveness
(heartbeat 30 s) impedisce due sessioni sulla stessa workdir.

## Superficie opzionale, non normativa

`board.py`, `pipeline.py`, `decide.py`, `creative.py`, `motion_browser.py` sono
motore sperimentale di giuria. La Motion Studio (54 template) si usa dall'API:
un EDL 3 dichiara una clip `generator` e il render la compone con l'alfa.

## Riferimenti


- `references/01-ambiente.md` ambiente, dipendenze, GPU; `references/02-metriche-analisi.md` metriche e soglie
- `references/03-matrice-decisionale.md` finding, gate, trattamento; `references/04-specifiche-social.md` safe zone, loudness, hook
- `references/05-auto-framing.md` tracking volti e crop; `references/06-sottotitoli-ass.md` whisper, cue, ASS
- `references/07-render.md` stadi, tagli, loudnorm; `references/08-qc-e-consegna.md` codici QC e deliverable
- `references/09-accelerazione-e-vad.md` hardware e VAD; `references/10-grammatica-del-montaggio.md` grammatica e ritmo
- `references/11-retention-e-piattaforme.md` retention; `references/12-lezioni-dal-campo.md` lezioni misurate
- `references/13-lezioni-impresse.md` le 15 regole e il codice che le impone; `references/14-direzione-visiva-adattiva.md` i sei assi; `references/15-open-montage-rnd.md` studio esterno: cosa si adotta, cosa si lascia, con fonte e gap; `references/16-doppio-regime.md` STRICT/AUTONOMOUS, rationale, gate R&D selettivo
- `references/17-ingest.md` stage 00: VFR/HDR/SAR/rotazione/4K; `references/18-clip-lunghi-checkpoint.md` clip lunghe, chunk e resume; `references/19-edl-multisorgente.md` EDL v2.1, A/B-Roll, source_id; `references/20-render-multisorgente.md` render N sorgenti, Gate 1 per fonte, `vx edl`; `references/21-regia-semantica-keyword.md` B-roll dalle keyword; `references/22-grammatica-e-mixer-audio.md` 30°/180° e mixer A/B; `references/23-triage-e-auto.md` triage, EDL 2.2, master e `vx auto`; `references/24-api-e-mcp.md` API stabile, install editable e server MCP; `references/25-job-engine.md` motore di job asincrono, store su file, claim, reaping, container non costruito; `references/26-edl3-e-ponte.md` specifica EDL 3, ponte analisi-assemblaggio con la Fase A implementata, ciclo critico e backlog; `references/27-studio-higgsfield.md` studio Higgsfield (documentale, non provato)
