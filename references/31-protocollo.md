# 31 — Protocollo operativo (dettaglio)

Questa è la parte **esecutiva** della skill: comandi, flag, percorsi, eccezioni.
`SKILL.md` dice *quando* e *come giudicare*; qui c'è *come si invoca*. Se le due
cose si contraddicono, vince il codice (`scripts/vx/cli.py`).

## 0. Setup

1. Virtualenv Python 3.10+ con `opencv-python-headless mediapipe moviepy librosa
   soundfile scipy pydub faster-whisper pyyaml imageio-ffmpeg`; ffmpeg 8.x con
   libass, vidstab, loudnorm, silencedetect (es. gyan.dev essentials).
2. `vx.config.json` accanto a `SKILL.md` (gitignored) oppure le variabili
   `VX_WORK`, `VX_VENV`, `VX_FFMPEG`, `VX_FFPROBE`, `VX_WHISPER_CACHE`, `VX_CONFIG`.
   Template: `vx.config.example.json`. GPU opzionale: `accel.py` prova CUDA a ogni
   corsa, Whisper ricade su CPU int8 se il test non passa.
3. `python scripts/vx.py doctor --json` deve dire `"verdict": "ready"` prima di
   ogni lavoro. Ogni soglia è sovrascrivibile con `--override`/`--settings`; il
   valore effettivo finisce nel report.

```powershell
$VENV = $env:VX_VENV            # es. <venv>
$PY   = "$VENV\Scripts\python.exe"
$VX   = "<SKILL_DIR>\scripts\vx.py"
```

## 1. Il comando unico

```powershell
& $PY $VX reel --prompt "<cosa vuole l'utente>" --assets "<cartella>" --out "<file.mp4>" --workdir "<cartella lavoro>"
```

Varianti utili: `--music FILE` (BPM misurato dalla traccia, `--bpm N` vince),
`--recipe NAME` (composizione dichiarativa), `--montage`, `--fill crop|fit|blur`,
`--resume` (riprende da `.vx/run.jsonl`), `--json`.

`reel` è un **orchestratore**: la pipeline è la stessa di `auto` (parità di sha
del master verificata da `tests/test_reel_cli.py`). `auto`, `run`, `edl`, `edl3`
restano disponibili come strumenti di livello più basso.

## 2. La vista

- `vx up` accende il daemon (URL su stdout, resta acceso; `vx down` lo ferma).
- `vx serve DIR` serve un singolo lavoro; `--watch` aggancia la corsa al daemon.
- Il comando OpenCode `/reel` apre la vista **prima** di ogni altra cosa.
- I cancelli (font, fotogrammi, piano) aspettano una risposta **solo se c'è un
  client agganciato** e scadono da soli; a lavoro chiuso non chiedono più niente.
- Se nessuno guarda, la proposta si usa subito e si dichiara `by: "auto"`.

## 3. Ingest e triage (livello basso)

```powershell
& $PY $VX ingest --input "<clip>" [--brief "<brief.json>"]
& $PY $VX ingest --input "<clip>" --autonomous     # non esce mai 3
```

Se il brief non dichiara formato, hook e sottotitoli, `ingest` esce **3** e
stampa tre domande: mostrale e fermati. Con `--autonomous` registra i default
(`triage.status`, `triage.defaults`, provenienza `declared:autonomous`).

Quando **tutte** le clip sono scartate solo per risoluzione
(`RESOLUTION_TOO_LOW`), `auto` si adatta: alza il limite dell'upscale entro il
tetto o abbassa l'uscita, e scrive `triage.adapted` col fattore misurato. Con
motivi misti non si adatta e l'errore li conta.

## 4. Piano e render (livello basso)

```powershell
& $PY $VX plan --report "<workdir>\report.json" --answers "<answers.json>" --out "<workdir>\plan.json"
& $PY $VX run  --input "<clip>" --answers "<answers.json>"
& $PY $VX qc   --plan "<workdir>\plan.json" --render "<workdir>\stage\final_full.mp4"
& $PY $VX deliverables --plan "<workdir>\plan.json"
```

`run` esegue ingest, piano, sottotitoli, render e QC in un colpo: dura minuti,
timeout del tool disattivato o in background, mai interrotto a metà. Per una
corsa manuale restano `subs`, `render --precision proxy|full`, `qc`, `deliverables`.

## 5. Auto

`vx auto --prompt ... --assets ... --out ...` esegue triage, piano, render e QC
in un colpo. `--montage` monta ogni video che passa i gate come `a_roll`;
`--fill crop|fit|blur` sceglie il riempimento (`fit` predefinito); `--music FILE`
monta sul brano; `--recipe NAME` applica una composizione. Scrive `plan.json`,
`auto_report.json` e `<workdir>/out`. Exit 0/2/5/6.

## 6. EDL3 (effetti, transizioni, testo)

```powershell
& $PY $VX edl3 validate "<piano.edl3.json>"
& $PY $VX edl3 render --plan "<piano.edl3.json>" --out "<master.mp4>" --audio mix|drop
& $PY $VX edl3 migrate --plan "<plan.json 2.2>" --out "<piano.edl3.json>"
```

`validate` non renderizza (exit 2 col campo che sbaglia). `migrate` porta un
piano `vx.edl/2.2` di `auto` in `vx.edl3/1.2` **dichiarando le perdite** ed esce 2
se non riesce a migrare senza (piani con layer ≥ 1 restano fuori).

## 7. Ricette

Un file JSON (`vx.recipe/1`) in `recipes/` o in `~/.vx/recipes/`:
`settings` (layer validato contro le chiavi di `settings.DEFAULTS`), `brief`
(frammento libero), `params` (`beat_grid`, `shot_max`, `effects_per_shot`, `font`,
`font_size`, `hook_at`, `cta`, `loudness`), `extends`. Le ricette dell'utente
sovrascrivono le incluse (`shadowed: true`); una inclusa non può estendere una
utente. Una ricetta rotta si scarta e il report dice `recipe.applied=false`.

## 8. Font, fotogrammi, voto

Il font non si eredita da un default: `auto` propone dal contesto, scarica dal
catalogo e aspetta la scelta, dichiarandola nel report. Il cancello dei fotogrammi
mostra solo immagini che esistono davvero; se non se ne estrae nessuna, il cancello
non si apre e il render prosegue dichiarandolo. A lavoro finito la vista chiede un
voto 1–5, facoltativo e non bloccante; il registro si legge **solo su richiesta**
(`vx learn`), mai all'avvio.

## 9. Percorsi e sicurezza

Il sorgente non viene MAI modificato: l'ingest scrive lo sha256 in `source.json`
e il QC riverifica `source_sha_unchanged`. i deliverable stanno in `out/` della
workdir; il master registrato può essere altrove (`--out`). Il lavoro vive in una
cartella propria con un lock di liveness che impedisce due sessioni sulla stessa
workdir. `vx guard <dir>` dice se un lavoro è passato davvero da qui.
