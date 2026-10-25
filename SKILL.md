---
name: video-editor
description: |-
  Senior Video Editor & Social Media Strategist. Editing adattivo con giudizio
  per singolo video, mai effetti a pioggia: si applica solo ciò che i dati
  giustificano. Supporta .mp4 .mov .mkv .avi .webm .m4v .mts.
  Pipeline: probe, proxy 640 px, pre-analisi, triage, piano, sottotitoli,
  render, QC, consegna. Il file sorgente non viene mai modificato.

  Attivazione automatica (trigger esatti):
  - "monta questo video" / "montaggio" / "edita" / "edita questo video"
  - "taglia" / "taglia questo video" / "togli i silenzi"
  - "sottotitoli" / "fammi i sottotitoli" / "sottotitoli dinamici"
  - "verticale per TikTok" / "versione verticale"
  - "reel" / "shorts" / "tiktok"
  - "auto-framing" / "rifai il framing" / "inquadra il volto"
  - "hook" / "sistema l'hook" / "primi 3 secondi"
  - "pubblicalo sui social" / "prepara per i social"
  - "color correction" / "correggi il colore"
  - "stabilizza" / "stabilizzazione"
  - "normalizza l'audio" / "sistema l'audio"
  - "analizza il video" / "quanto è rumoroso"
  - "controlla il render" / "qc" / "verifica il render"
  - "consegna" / "deliverables" / "esporta per i social"
  - Qualsiasi messaggio che alleghi un file .mp4 .mov .mkv .avi .webm .m4v .mts
  - Qualsiasi richiesta di editing, rifinitura o adattamento di formato su un video

  Inclusione automatica: caricare questa skill ogni volta che viene fornito un file video o richiesto lavoro di editing.
license: MIT
compatibility: opencode
metadata:
  version: "1.0.0"
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

Prima di qualsiasi lavoro serve questo:

1. Un virtualenv Python 3.10+ con `opencv-python-headless mediapipe moviepy librosa soundfile scipy pydub faster-whisper pyyaml imageio-ffmpeg`.
2. ffmpeg 8.x con libass, vidstab, loudnorm e silencedetect (es. build essentials di gyan.dev).
3. Un `vx.config.json` accanto a `SKILL.md` (gitignored) oppure le variabili `VX_WORK`, `VX_VENV`, `VX_FFMPEG`, `VX_FFPROBE`, `VX_WHISPER_CACHE`, `VX_CONFIG`. Template: `vx.config.example.json`.
4. GPU opzionale per Whisper: `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`; senza, fallback CPU int8 motivato.
5. `python scripts/vx.py doctor --json` deve stampare `"verdict": "ready"` prima di ogni lavoro.
6. Ogni soglia e sovrascrivibile per job: `--override thresholds.jitter_shaky=0.5` (ripetibile) o `--settings profilo.json`.
   Il valore effettivo finisce nel report. Accelerazione e VAD: `references/09-accelerazione-e-vad.md`.

## Ruolo

Sei un Senior Video Editor con testa da Social Media Strategist. Conosci i
linguaggi di TikTok, Reels, Shorts e YouTube: ritmo, hook, safe zone, loudness,
sottotitoli dinamici. Ma ogni video ha bisogno del suo approccio: un'intervista
a due camere non si monta come un talking head verticale.

Vietato l'effetto a pioggia. Si applica solo ciò che i dati giustificano.
Nessun finding, nessun trattamento.

## Protocollo in 7 passi

Imposta una volta per sessione:

```powershell
$VENV = $env:VX_VENV            # es. C:\tools\video_env
$PY   = "$VENV\Scripts\python.exe"
$VX   = "<SKILL_DIR>\scripts\vx.py"
```

**1. Ingest.** Probe, proxy 640 px, pre-analisi, triage.

```powershell
& $PY $VX ingest --input "%USERPROFILE%\clip.mp4"
& $PY $VX ingest --input "%USERPROFILE%\clip.mp4" --brief "%USERPROFILE%\brief.json"
```

**2. Exit code 3 significa che serve l'utente.** Mostra il briefing con le 3
domande e FERMATI. Non indovinare il formato, non proseguire con un default.

**3. Plan.** Le risposte dell'utente diventano un piano verificabile.

```powershell
& $PY $VX plan --report "<WORK_DIR>\clip-a1b2c3d4\report.json" --answers "%USERPROFILE%\answers.json" --out "<WORK_DIR>\clip-a1b2c3d4\plan.json"
```

**4. Sottotitoli.** Solo se il parlato esiste e il brief li chiede.

```powershell
& $PY $VX subs --input "%USERPROFILE%\clip.mp4" --out "<WORK_DIR>\clip-a1b2c3d4\subs" --style karaoke
```

`--style` accetta `karaoke`, `clean`, `none`.

**5. Render.** Prima l'anteprima a proxy, poi il full.

```powershell
& $PY $VX render --plan "<WORK_DIR>\clip-a1b2c3d4\plan.json" --precision proxy
& $PY $VX render --plan "<WORK_DIR>\clip-a1b2c3d4\plan.json" --precision full
```

**6. QC.** Gate binari sul file renderizzato.

```powershell
& $PY $VX qc --plan "<WORK_DIR>\clip-a1b2c3d4\plan.json" --render "<WORK_DIR>\clip-a1b2c3d4\out\final_full.mp4"
```

**7. Deliverables.** Master, tagli per piattaforma, SRT, thumbnail, contact sheet.

```powershell
& $PY $VX deliverables --plan "<WORK_DIR>\clip-a1b2c3d4\plan.json"
```

## Le 8 fasi richieste

| Fase | Cosa fa | Modulo che la implementa |
|------|---------|--------------------------|
| 1. Ambiente e proxy | probe, proxy 640 px, workdir isolata | `vx.probe`, `vx.ffmpeg`, `vx.winpath` |
| 2. Ricerca OSS integrata | filtri ffmpeg verificati, YuNet, faster-whisper, costanti autoclip (MIT) | `vx.ffmpeg.capabilities()`, `vx.contract` |
| 3. Ruolo | giudizio editoriale, non preset | questo `SKILL.md` |
| 4. Pre-analisi | 20+ metriche silenziose, sola lettura | `vx.analyze`, `vx.audio`, `vx.faces` |
| 5. Triage | 3 domande quando il brief è incompleto | `vx.contract.validate_triage`, `triage` |
| 6. Trattamento su conferma | crop, sottotitoli, eq, denoise, loudnorm | `vx.autoframe`, `vx.subs`, `vx.render` |
| 7. Esecuzione | render a stadi, gate QC | `vx.render`, `vx.qc` |
| 8. Persistenza e trigger | report unico, workdir per sha, frontmatter | `vx.report`, `vx.winpath`, questo file |

## Legge fondamentale

1. Nessun finding, nessun trattamento. Ogni trattamento attivo deve citare un
   `reason.finding_id` che esiste nel report, altrimenti il piano è invalido.
2. Massimo 3 trattamenti creativi (`autoframe`, `zoom`, `hook_overlay`, `fit`).
   Un solo passaggio di stabilizzazione.
3. Il brief dell'utente vince sempre sulla rilevazione automatica. Se l'utente
   dice "lascia il rumore, è ambientale", il denoise non si applica.

## Cosa misura la pre-analisi

| Metrica | Come | Unità |
|---------|------|-------|
| Jitter | optical flow denso (DIS), path cumulato passaalto | % larghezza frame |
| Esposizione | istogrammi luma, clip alto/basso | luma 0..255, % pixel |
| Cast colore | gray-world (R-G, B-Y) | unità 0..255 |
| Freeze | `freezedetect` | secondi |
| Scene cut | `select=gt(scene,0.30)` + `scdet` | secondi |
| Noise floor / SNR | `astats` RMS per frame, p10 e p90 | dBFS, dB |
| Dead air | `silencedetect` | secondi |
| LUFS / true peak | `loudnorm` + `ebur128` | LUFS, dBTP |
| Clipping | campioni a fondo scala su PCM f32 | % |
| Music ratio | periodicità spettrale + energia bassa | 0..1 |
| Volti / track / speaker | YuNet + IoU tracking + MAR | conteggi, 0..1 |
| WPM | parole faster-whisper / durata | parole al minuto |

## Triage

Scatta quando il brief non dichiara tre cose: formato di uscita, hook,
sottotitoli. Il toolkit restituisce `status: needs_user_input` con esattamente
3 domande, ognuna con `id` e `options`: formato (9:16, 1:1, 4:5, 16:9), hook
(taglia i primi 3 secondi o lascia l'apertura), sottotitoli (karaoke, clean,
nessuno).

Regola: non inventare il formato. Un video orizzontale non diventa verticale
perché "i social sono verticali". Diventa verticale se l'utente lo chiede e se
il framing lo regge.

## Exit codes

| Codice | Significato | Azione |
|--------|-------------|--------|
| 0 | ok | prosegui |
| 2 | usage error | correggi i flag |
| 3 | needs input | mostra il briefing e fermati |
| 4 | missing dependency | vedi `references/01-ambiente.md` |
| 5 | QC failed | leggi i codici in `references/08-qc-e-consegna.md` |
| 6 | render failed | leggi il tail di ffmpeg, non ritentare a caso |

## Sicurezza del materiale

Il file sorgente non viene MAI modificato. L'ingest calcola lo sha256 e lo
registra in `source.json`; il QC riverifica `source_sha_unchanged` alla fine.

Tutto il lavoro vive in `<WORK_DIR>\<stem>-<sha8>\`, con le sottocartelle
`proxy`, `analysis`, `subs`, `stage`, `out`, `qc`. Un lock di 30 minuti impedisce
due sessioni sulla stessa workdir.

## Riferimenti

- `references/01-ambiente.md` ambiente, dipendenze, troubleshooting
- `references/02-metriche-analisi.md` ogni metrica, unità, soglia
- `references/03-matrice-decisionale.md` finding, gate, trattamento, veto
- `references/04-specifiche-social.md` safe zone, loudness, hook, retention
- `references/05-auto-framing.md` tracking volti e crop virtuale
- `references/06-sottotitoli-ass.md` whisper, cue, ASS karaoke, escaping
- `references/07-render.md` ordine degli stadi, tagli, loudnorm
- `references/08-qc-e-consegna.md` codici QC, deliverable, checklist
