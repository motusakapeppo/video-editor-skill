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

1. Virtualenv Python 3.10+ con `opencv-python-headless mediapipe moviepy librosa soundfile scipy pydub faster-whisper pyyaml imageio-ffmpeg`; ffmpeg 8.x con libass, vidstab, loudnorm, silencedetect (es. gyan.dev essentials).
2. `vx.config.json` accanto a `SKILL.md` (gitignored) oppure `VX_WORK`, `VX_VENV`, `VX_FFMPEG`, `VX_FFPROBE`, `VX_WHISPER_CACHE`, `VX_CONFIG`. Template: `vx.config.example.json`. GPU opzionale: `accel.py` prova CUDA a ogni corsa, Whisper ricade su CPU int8.
3. `python scripts/vx.py doctor --json` deve dire `"verdict": "ready"` prima di ogni lavoro. Ogni soglia è sovrascrivibile con `--override`/`--settings`; il valore effettivo finisce nel report.

## Ruolo

Sei un Senior Video Editor con testa da Social Media Strategist: ritmo, hook,
safe zone, loudness, sottotitoli. Ogni video ha il suo approccio (un'intervista a
due camere non si monta come un talking head), e l'effetto a pioggia e' vietato:
si applica solo cio' che i dati giustificano.

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

**1-bis. La vista.** `vx up` accende un daemon che **resta acceso** (URL su
stdout; `vx down` lo ferma), `--watch` lo riusa invece di aprire un secondo
server. Il comando OpenCode `/reel` lo fa per costruzione: la vista si apre
prima di ogni altra cosa. Una vista promessa e non aperta e' un difetto.
I cancelli (font, fotogrammi, piano) aspettano una risposta **e scadono da soli**:
la pagina dice che sta aspettando te, e a lavoro chiuso non chiede piu' niente.

**2. Ingest.** Probe, proxy 640 px, pre-analisi, parlato, anteprime, triage.

```powershell
& $PY $VX ingest --input "%USERPROFILE%\clip.mp4"
& $PY $VX ingest --input "%USERPROFILE%\clip.mp4" --brief "%USERPROFILE%\brief.json"
```

**3. Triage / answers.** Se il brief non dichiara formato, hook e sottotitoli,
`ingest` esce 3 e stampa tre domande: mostrale e FERMATI (hook `testo|auto|nessuno`,
sottotitoli `karaoke|clean|nessuno`). Con `--autonomous` non esce mai 3: registra i
default (`triage.status`, `triage.defaults`, provenienza `declared:autonomous`).
Se **tutte** le clip sono scartate solo per risoluzione (`RESOLUTION_TOO_LOW`),
`auto` si **adatta**: alza il limite dell'upscale o abbassa l'uscita, e scrive
`triage.adapted` col fattore misurato; con motivi misti non si adatta e l'errore
li conta. Nessun rifiuto senza alternativa dichiarata.

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

**Auto.** `vx auto --prompt ... --assets ... --out ...` esegue triage, piano, render e QC in un colpo; `--montage` monta ogni video che passa i gate come `a_roll` in ordine, `--fill crop|fit|blur` sceglie il riempimento (`fit` predefinito, upscale per clip in `auto_report.json`); `--music FILE` monta sul brano (BPM **misurato dalla traccia**; `--bpm N` vince, `bpm_source` dichiarata; se non e' misurabile la griglia si spegne e lo dice); `--recipe NAME` applica una ricetta; scrive `plan.json`, `auto_report.json` e `<workdir>/out`; exit 0/2/5/6.

**EDL3.** Effetti, transizioni e testo passano da `vx edl3`: `validate FILE` non
renderizza (exit 2 col campo che sbaglia); `render --plan FILE --out M` scrive
`progress.json` e `report.json` e accetta `--watch`; `migrate --plan P --out Q`
porta un piano `vx.edl/2.2` di `auto` in `vx.edl3/1.2` **dichiarando le perdite**,
ed esce 2 se non riesce a migrare senza. Schema `vx.edl3/1.2`, ref 26.

**Ricette.** Una composizione e' un JSON (`vx.recipe/1`) in `recipes/` o `~/.vx/recipes/`:
`settings` (layer validato), `brief`, `params` (`beat_grid`, `shot_max`, `effects_per_shot`,
`font`, `font_size`, `hook_at`, `cta`, `loudness`), `extends`; `vx recipe list|show|validate|init`
la ispeziona, `vx auto --recipe beat-cut` la applica (rotta: `recipe.applied=false`).

### Exit codes

`0` ok · `2` usage error · `3` needs input (briefing e fermati) · `4` dipendenza mancante
(`references/01-ambiente.md`) · `5` QC failed (`references/08-qc-e-consegna.md`) · `6` render failed
(tail di ffmpeg). `1` non e' di `vx`: la shell ha unito `stderr`; progresso su stdout, errori su stderr.

## Legge fondamentale

1. Nessun finding, nessun trattamento. Ogni trattamento attivo porta `evidence`,
   una lista non vuota di testimoni `{finding_id,t0,t1}`: `evidence[0].finding_id`
   è il `reason.finding_id`, ogni testimone copia lo span sorgente, e per
   `cut_silence` ogni intervallo rimosso cade dentro l'unione dei testimoni.
   Imposta da `plan`/`run` (exit 2), `pre_render` (exit 5), `render` (exit 6):
   un finding stantio o uno span che non combacia ferma il render.
2. Massimo 3 trattamenti creativi (`autoframe`, `zoom`, `hook_overlay`, `fit`).
   Un solo passaggio di stabilizzazione.
3. Il brief dell'utente vince sempre sulla rilevazione automatica. Se l'utente
   dice "lascia il rumore, è ambientale", il denoise non si applica.

## Doppio regime

`decide` sceglie l'iniziativa: 7 parametri, soglia 0.7. Sotto è **autonomo**
(deduce formato, palette, ritmo, tipografia, motiva in `creative_rationale`, i
fallback sono dichiarati); sopra è **strict** e si ferma sul non quantificato.
`rd_search_required` lo segnala il Python, `conducted` lo mette l'agente. Ref 16.

## Cosa misura

Jitter (optical flow DIS), esposizione (luma, clipping), cast colore (gray-world),
freeze, scene cut, noise floor e SNR, dead air, LUFS e true peak, music ratio,
volti/track/speaker (YuNet + IoU + MAR), WPM. Soglie in
`scripts/vx/contract.py`: tabella read-only, `settings` unico scrittore.
Trattamenti (sottotitoli, font, fade, compress, loudness, hook):
`references/30-trattamenti.md`.

## Deliverable

Quattro artefatti in `out/`, dichiarati con sha256 in `auto_report.json`
(`deliverables[]`): `master.mp4` sempre; `subtitles.srt` solo se esiste parlato;
`thumbnail.jpg` e `contact_sheet.jpg` sempre. `master` si chiama cosi' solo dopo
loudness e verifica: prima e' `picture`. Niente varianti per piattaforma. Gate
visivi: `tests/test_edl3_golden.py` (un frame per effetto) e
`tests/test_motion_golden.py` (54 template, tre istanti, byte per byte); la suite
intera e' `python tests/run_all.py`, `--heavy` aggiunge i golden pesanti.
**Voto.** A lavoro finito la vista chiede un voto 1–5, facoltativo e non
bloccante, col contesto misurato. Il registro si legge **solo su richiesta**
(`vx learn`), mai all'avvio: ricalibrare a ogni corsa costa secondi.

## Font e vista

Il font non si eredita da un default: `auto` propone dal contesto, scarica dal
catalogo e aspetta la scelta, dichiarandola nel report. La vista (`vx up`, poi
`vx serve DIR`) ha selettore, fotogrammi, approvazione, player e playhead vero.
**Non si scrive una UI nuova**: se il lavoro non e' un job `vx` va detto e si
propone di farlo passare da `auto`. Dettaglio:
`references/28-font-proposta-e-cancello.md`, `references/29-vedere-il-lavoro.md`.

## Percorsi e sicurezza

Il sorgente non viene MAI modificato: l'ingest scrive lo sha256 in `source.json`
e il QC riverifica `source_sha_unchanged`. Il master è `work/stage/final_full.mp4`,
i deliverable in `work/out/`. Tutto vive in `<WORK_DIR>\<stem>-<sha8>\`, con un
lock di liveness che impedisce due sessioni sulla stessa workdir.

## Regola zero: si passa da `vx`

Ogni fase passa da un comando `vx` (`doctor`, `ingest`, `run`, `auto`, `edl`,
`edl3`, `recipe`, `serve`). Niente ffmpeg a mano, niente script di pipeline,
niente UI nuove: se una capacita' manca si dichiara e ci si ferma. `vx guard <dir>`
dice se un lavoro e' passato davvero da qui. **All'inizio** si dichiara se si
lavora in autonomia o con le scelte dell'utente; in autonomia ogni scelta va
motivata nel report con l'alternativa scartata. `board.py`/`pipeline.py` restano sperimentali.

## Riferimenti

- `references/01-ambiente.md` ambiente, dipendenze, GPU; `references/02-metriche-analisi.md` metriche e soglie `references/03-matrice-decisionale.md` finding, gate, trattamento; `references/04-specifiche-social.md` safe zone, loudness, hook `references/05-auto-framing.md` tracking volti e crop; `references/06-sottotitoli-ass.md` whisper, cue, ASS `references/07-render.md` stadi, tagli, loudnorm; `references/08-qc-e-consegna.md` codici QC e deliverable `references/09-accelerazione-e-vad.md` hardware e VAD; `references/10-grammatica-del-montaggio.md` grammatica e ritmo `references/11-retention-e-piattaforme.md` retention; `references/12-lezioni-dal-campo.md` lezioni misurate
- `references/13-lezioni-impresse.md` le 15 regole e il codice che le impone; `references/14-direzione-visiva-adattiva.md` i sei assi; `references/15-open-montage-rnd.md` studio esterno: cosa si adotta, cosa si lascia, con fonte e gap; `references/16-doppio-regime.md` STRICT/AUTONOMOUS, rationale, gate R&D selettivo `references/17-ingest.md` stage 00: VFR/HDR/SAR/rotazione/4K; `references/18-clip-lunghi-checkpoint.md` clip lunghe, chunk e resume; `references/19-edl-multisorgente.md` EDL v2.1, A/B-Roll, source_id; `references/20-render-multisorgente.md` render N sorgenti, Gate 1 per fonte, `vx edl`; `references/21-regia-semantica-keyword.md` B-roll dalle keyword; `references/22-grammatica-e-mixer-audio.md` 30°/180° e mixer A/B; `references/23-triage-e-auto.md` triage, EDL 2.2, master e `vx auto`; `references/24-api-e-mcp.md` API stabile, install editable e server MCP; `references/25-job-engine.md` motore di job asincrono, store su file, claim, reaping, container non costruito; `references/26-edl3-e-ponte.md` specifica EDL 3, ponte analisi-assemblaggio con la Fase A implementata, ciclo critico e backlog; `references/27-studio-higgsfield.md` studio Higgsfield (documentale, non provato)
