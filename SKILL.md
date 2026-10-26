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
- `references/13-lezioni-impresse.md` le 15 regole del progetto e il codice che le impone
- `references/14-direzione-visiva-adattiva.md` i sei assi, i quattro cancelli, le fragilità dichiarate


---

## Stato dell'architettura (26/10/2026)

### Il flusso obbligatorio

```
1. prefight        board.prefight(richiesta, materiale) -> domande all'utente
                   si chiede SOLO cio' che cambia l'output e non si misura
2. giuria          tipografo | colorista | animatore | direttore
                   domini esclusivi, proposta con misura citata, direttore firma
3. brief firmato   board.merge(verdict)  -> rifiuta un brief senza firma
4. pipeline        pipeline.run(..., verdict=...)  -> RIFIUTA senza brief firmato
5. cancelli        Gate 1 aritmetica, 2 grafo, 3 file, 4 layout, 5 colore
```

Un agente che ha un dubbio **non indovina**: restituisce `questions` nella proposta.
`convene()` le raccoglie, il brief resta aperto, il render non parte.

### Chi fa cosa

| Motore | Cosa possiede | Perche' |
|---|---|---|
| FFmpeg | tagli, transizioni, **colore** | esatto in YUV intero; il grade e' certificato li' |
| Compositor (Pillow) | testo, grafica, animazione | spazio e tempo; 42 ms/frame |
| Chromium | solo effetti che Pillow non sa fare | 2,7x piu' lento, si accende da solo via `board.required_engine()` |

### Moduli nati in questa fase

`layout` (safe area misurate) · `gates` (1-5) · `motion` (compositing) ·
`motion_browser` (engine alternativo) · `grade` (nodo cromatico parametrico, LUT
bit-exact contro `vf_curves.c`) · `creative` (analisi materiale -> parametri) ·
`board` (giuria, domini, verifica) · `pipeline` (catena che si compone) ·
`decide` (autonomia vs domande)

La tornata visiva (W0–W2.5) aggiunge un secondo strato, con un solo vocabolario:

| Modulo | Cosa possiede | Proprietà |
|---|---|---|
| `registry` | `ANIMATIONS`, `THRESHOLDS`, `AXES`, `SCHEMA`, `APPROVED_DELIVERY_SHA256` | l'unico posto dove vivono nomi, motori e soglie; tutto il resto deriva |
| `brief` | `new_brief`, `add_reason`, `add_executed`, `validate`, `to_json` | costruisce e valida il documento `vx.visual/1.0`; non decide |
| `prove` | `check_ledger`, `distinct_surface`, `hook_distinct`, `coverage_gate`, `docs_match`, `reproduce_sha256`, `renderer_delta` | il verificatore indipendente: può dire no, e non importa logica di decisione |
| `palette` | `decide(measurements)` | famiglia e quattro colori dalla massa hue misurata, contrasto WCAG calcolato |
| `gradeplan` | `decide(measurements, palette)`, `chain_for_shot(node)` | nodo cromatico dalla luma e dal cast; frammento ffmpeg o stringa vuota |
| `animplan` | `transient(path, words)`, `decide(ratio)`, `chain_for_shot(choice, shot)` | transient ratio e i tre effetti single-stream, massimo 2 |
| `cutplan` | `snap(edl, vad_gaps, onsets)` | sposta i confini su un onset dentro un silenzio VAD, frame esatti |
| `screen` | `decide(path, at, src_w, src_h)` | inquadratura dall'offset del soggetto, chiamando `aesthetics.place` una volta |
| `docs` | `render(brief)`, `match(brief, text)`, `hash_ledger(brief)` | rende il brief in Markdown e delega il cancello a `prove.docs_match` |

### Debiti aperti, con il fix esatto

1. **luma per frame e peggior patch locale** — `creative.analyse` misura il
   fotogramma intero e ne fa la media; la fascia del testo e' eterogenea (misurato:
   media 0.245, p05 0.161, p95 0.447). Il colore dell'inchiostro va scelto sulla
   patch peggiore sotto i glifi, per ogni cue. **Questo errore ha gia' prodotto
   cinque decisioni sbagliate.**
2. **peso del font** — tre proxy sbagliati provati, tre fallimenti documentati:
   la larghezza della "I" premia i corsivi (Kunstler Script); il filtro per nome
   contiene "black" premia il gotico (ITC Blackadder, reso in un testo illeggibile);
   il massimo stem premia i serif (Garamond). **L'unica misura che ha funzionato e'
   la copertura d'inchiostro su una stringa** (Showcard Gothic 0.681, Impact 0.655,
   Gill Sans Ultra Bold 0.654): va calcolata dentro `_measure_font`, non dedotta dal
   nome ne' dal glifo "I".
3. **effetti solo-Chromium — chiuso il 26/10/2026.** I cinque effetti fantasma
   sono stati rimossi dal vocabolario: `registry.ANIMATIONS` contiene solo
   `none`, `fade`, `wipe`, `punch`, tutti ffmpeg single-stream. `motion_browser.py`
   resta in quarantena: conservato, non importato. Non c'è più un effetto
   dichiarato e non eseguito.

### Test che esistono

- `tests/run_corpus.py` — 9 clip reali e sintetiche, 53 assert (formati, colore, geometria)
- `tests/run_color.py` — calibrazione del nodo cromatico sul corpus, 36 assert
- `tests/test_decisions.py` — 15 assert sul perimetro decisionale
- `tests/test_gradeplan.py` — 35 assert sul nodo cromatico e la sua catena
- `tests/test_cutplan.py` — 15 assert sullo snap dentro il silenzio VAD
- `tests/test_animplan.py` — 49 assert su transient ratio, tre effetti, frame e geometria
- `tests/test_palette.py` — 32 assert su famiglia, colori e contrasto WCAG
- `tests/test_screen.py` — 23 assert su offset, centro e inazione
- `tests/test_ledger.py` — 8 assert sul ledger (`applied:true` => filtro eseguito)
- `tests/test_distinct.py` — 13 assert su `distinct_surface`, `hook_distinct`, `coverage_gate`
- `tests/test_docs.py` — 5 assert su `docs_match`
- `tests/run_visual_corpus.py` — 16 assert: due progetti, due render, tutti i cancelli
- `tests/stock_corpus/real/LICENSES.md` — provenienza del materiale reale

Totale: 104 assert di base + 196 nuovi = 300.

### Regola che vale sopra le altre

Quando un agente sbaglia, si guarda **prima il contesto che ha ricevuto, poi il suo
giudizio**. Se l'errore si ripete, il contesto si corregge nel codice, non nella chat.

### Portabilita' (regola, non aspirazione)

Nessun percorso, nome di font, fps o risoluzione di questa macchina o di questo
progetto e' scritto nel codice:

- cartelle font: `creative._font_dirs()` le scopre in base al sistema operativo
  (Windows, macOS, Linux) piu' `assets/fonts` del progetto;
- font di default: `pipeline._font_path()` sceglie dal pool misurato, filtrando i nomi
  che dichiarano peso (black/heavy/ultra/bold/demi/condensed) ed escludendo i corsivi;
- fps: `pipeline.run` legge quello del materiale con `motion.expected_frames`;
- canvas: viene dal materiale quando la sorgente e' gia' nel formato di consegna,
  altrimenti dalla piattaforma dedotta dalla richiesta.

Se una costante di questo elenco ricompare nel codice, e' una regressione.

### Come si pongono le domande

Le domande di `board.prefight()` e quelle restituite dagli agenti NON si scrivono come
testo nel messaggio: si pongono con lo **strumento `question` di opencode**, in caselle
a scelta multipla, una voce per domanda, con le opzioni gia' pronte.

Regole:
- ogni domanda ha un `header` breve e 2-4 opzioni concrete, ognuna con la conseguenza
  dichiarata (es. "massima -> 4 trattamenti, palette 4 colori");
- la prima opzione e' quella consigliata, con "(Recommended)" nel testo;
- niente "Altro" fra le opzioni: lo strumento aggiunge da solo la voce libera;
- se le domande sono piu' di una, si pongono tutte nello stesso blocco;
- la risposta dell'utente entra nel contesto degli agenti come punto di partenza.

Una domanda posta come prosa e' una domanda che l'utente salta.

---

## Direzione visiva adattiva (26/10/2026)

La direzione visiva non è un preset: ogni asse ha un driver misurato e una
provenienza. Se la confidenza cade fra 0.2 e 0.8, la scelta è inazione. Il
dettaglio completo, con le fragilità dichiarate, sta in
`references/14-direzione-visiva-adattiva.md`.

### I sei assi

| Asse | Driver | Soglie | n | Provenienza |
|---|---|---|---|---|
| palette | `hue_mass_top3[0].mass` | p33 0.1817 / p67 0.5012 | 9 | corpus |
| grade | `luma_p50` + cast | p33 0.2914 / p67 0.5028; cast 0.08 | 9 | corpus + `declared:p95_cast` |
| animation | `transient_ratio` | p33 3.4758 / p67 4.9944 | 3 di 9 | corpus, direzione asserita |
| cut_density | `onsets_per_s` | p33 2.9887 / p67 4.9227 | 9 | corpus |
| composition | `subject_offset` | 0.05 = 1/20 larghezza | 5 per clip | `declared:1/20_width` |
| typography | `ink_coverage_pct` | 0.45 / 0.94 | delivery | `measured:delivery` |

### Comandi

```powershell
& $PY $VX visual --source <clip> --measurements <json> --out <brief.json>
& $PY $VX render --plan <plan.json> --visual-brief <brief.json>
```

`vx visual` costruisce il brief `vx.visual/1.0`; `vx render --visual-brief` lo
esegue, inserendo le catene di grade e animazione nel grafo.

### I quattro cancelli

| Cancello | Cosa prova | Cosa NON prova |
|---|---|---|
| `distinct_surface` | che due brief divergono su almeno tre famiglie di superficie (colore, ritmo, geometria, tipografia) | la differenza di montaggio (rinviata) e la distinzione su materiale arbitrario futuro |
| `hook_distinct` | che i primi 3 secondi hanno tipo di hook e animazione diversi | che l'hook trattenga |
| `coverage_gate` | che l'inchiostro stia fra 0.30% e 2.00% su 12 frame | che il testo sia leggibile o corretto; `overlay == ink` perché le decorazioni sono fuori scope |
| `docs_match` | che il testo contenga l'hash del ledger, l'hash del documento, gli effetti eseguiti e le tre frasi | che la prosa sia completa o utile |

Il ledger è il presupposto: `check_ledger` rifiuta un brief in cui `applied:true`
non punti a un filtro reale (`executed.name != "none"`, filtro non vuoto, `rc 0`,
`frames > 0`). **Regola: `applied:true` => filtro eseguito.** Nessun effetto
dichiarato e non eseguito.

### Le tre frasi che devono restare

1. La direzione ratio↑→animazione più dura è asserita, non misurata; solo i punti di taglio sono empirici.
2. Il gate `distinct_surface` certifica solo la divergenza di superficie fra due brief; non certifica la differenza di montaggio e non pretende di certificare la distinzione su materiale arbitrario futuro.
3. Riduzione di scope rispetto all'ODG: le decorazioni sono fuori da questa tornata per motivo di budget, non per motivo di gate; il gate non le richiede e la loro assenza non è un fallimento.

### Riduzione di scope

Le decorazioni sono fuori da questa tornata per motivo di **budget**, non per
motivo di gate: il gate non le richiede e la loro assenza non è un fallimento.
La priorità della prossima tornata è la **scelta e l'ordine degli spezzoni**, non
un'altra decorazione: `skeleton:"deferred"` lo dichiara nel brief.
