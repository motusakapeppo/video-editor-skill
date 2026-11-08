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
- La pagina legge il **contratto** (`.vx/run.jsonl`) e non solo il progresso:
  sezione `CONTRATTO` con gli otto stadi (`firmato` / `fallito` / `mancante`),
  valore misurato e soglia; timbro `NON PUBBLICABILE` col nome vero del file
  quando uno stadio non firma; per lo stadio rosso la frase e le due scelte
  possibili. Con log corrotto dichiara la riga e nessuno stadio è verde.
  Il blocco viaggia in `/api/state` (`run = {stages, complete, failed, log,
  corrupt, signed, missing, tail_truncated, master}`) con cache su mtime.
- La sezione **SCALETTA** mostra le tre leve della composizione con **proposta
  e fatto separati**: il numero grande è quello che il piano ha montato, la riga
  piccola dice da dove si veniva (o che il punto di presa non è servito), e le
  leve non applicate stanno sotto `NON COMPOSTE` col loro motivo. Arriva da
  `/api/state` (`composition`, `sections`), cioè dal report: niente endpoint
  nuovi. Se il blocco manca, la sezione non compare.

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

La durata chiesta è quella del **master**, non del piano: il ponte taglia i
silenzi e poi riempie la coda — prima col materiale contiguo, poi con i tratti di
parlato che seguono, saltando la pausa — finché il piano non torna alla durata
chiesta. Ogni pezzo aggiunto è parlato misurato (nessun morto rientra).

Con i sottotitoli, una sorgente entro **60 s** si trascrive tutta
(`CAPTION_FULL_READ_SEC`): la coda che il ponte aggiunge sta oltre il piano, e
senza il testo resterebbe muta. Oltre i 60 s resta la finestra del piano e il
report dichiara `captions_tail_uncovered` coi secondi che stanno oltre l'ultima
parola letta — un pezzo senza sottotitoli dichiarato, non un buco muto.

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
workdir; il master registrato può essere altrove (`--out`). `vx guard <dir>` dice
se un lavoro è passato davvero da qui.

**Il lease è per cartella, non per macchina** (`<workdir>/.vx/lock`):
`{pid, host, token, created, heartbeat_epoch, stage}`. Un thread dedicato rinnova
il battito ogni 60 s, fuori dal loop di lavoro: un render che blocca non ferma il
cuore. Il lease è vivo se il battito ha meno di 300 s; **scade solo su morte
reale** (battito vecchio *e* pid sparito, o pid riciclato, o host diverso col
battito fermo), e quando viene recuperato lo dichiara (`stale_recovered` con pid
ed età del battito). Chi arriva secondo esce `2` e dice chi tiene la cartella, da
quanto e a che stadio — senza toccare pannello, sessione o run log. Due cartelle
diverse hanno due lease diversi: due lavori legittimi girano in parallelo. Il
vecchio `<workdir>/.lock` resta leggibile, non più scritto.

## 11. Composizioni complesse (Chromium)

Quando la richiesta chiede una composizione che un grafo di filtri non sa dire
(split screen, confronto prima/dopo, sottopancia), la scena si **dichiara** e il
motore grafico la costruisce:

```powershell
& $PY vx.py scene --request "sottopancia con nome e ruolo" --workdir "<dir>" --frames 12
```

- `scenes.wants` riconosce la composizione e cita le parole che l'hanno decisa;
- `render_frames` rasterizza il documento con il **Chrome/Edge di sistema**
  (Playwright) e a ogni fotogramma avanza `--vx-progress` da 0 a 1 — la scena si
  vede costruire;
- ogni fotogramma pubblicato finisce in `<workdir>/live/chromium/` con indice,
  tempo, byte e sha256 (`livepub`: campionamento, finestra anello, manifesto
  atomico);
- la vista lo mostra: `GET /api/scene` e `GET /scene/chromium/frame_000003.jpg`.
  Solo i nomi dichiarati sono serviti (`frame_\d{6}\.jpg`, `manifest.json`).

Confine dichiarato: il browser fa **grafica sopra il video** (testo impaginato,
sottopancia, card, maschere). La composizione di **flussi video** (split screen
con due sorgenti, picture-in-picture, griglie) la fa ffmpeg:

```powershell
& $PY vx.py stack --left "<a.mp4>" --right "<b.mp4>" --out "<split.mp4>" [--duration 10]
```

Ogni lato è ritagliato al centro e scalato all'altezza d'uscita, poi affiancato
con `hstack`; la durata è la più corta delle due (o quella chiesta) e l'audio è
dichiarato assente. Se Playwright o Chrome mancano, `render_frames` restituisce
`engine: "absent"` col motivo.



## 12. Composizione della scaletta

La scaletta compila le sezioni e, con le stesse misure, **compone** il montaggio.
Tre leve, tutte numeriche, nessun LLM nel percorso caldo:

| leva | misura | effetto |
|---|---|---|
| durata del `hook` | nitidezza dell'apertura contro la soglia del triage, BPM misurato, corsa utile dell'apertura, quota della durata chiesta | il primo pezzo dell'a_roll si spezza in gancio + corpo (`v0_…_0`, `v0_…_1`), con i frame che tornano |
| numero di clip in timeline | conseguenza delle due leve e delle corse utili del probe | contato **sui due piani** (baseline senza composizione, piano composto): `applied.shots_before/after`. Le *inquadrature* di `coherence` sono un'altra misura (tagli visivi), non vanno confuse |
| ordine dei b-roll | nitidezza misurata di ogni b-roll | l'ordine entra anche negli slot `duration*(i+1)/(n+1)` |

Regole:

- la proposta è **pura e deterministica** (`sections.compose(facts)`): stesso
  input, stesso JSON, niente orologio né random;
- se manca la misura la leva **declina** col motivo e il piano resta quello di
  prima (nessun surrogato, nessuna competenza spenta);
- con la griglia BPM il gancio **è** il primo shot: la composizione dichiara il
  passo della griglia (`60/BPM/subdivision`) e il builder non aggiunge tagli;
- se il picco di nitidezza lascia poco materiale, il punto di presa si sposta
  **indietro** nella stessa corsa, su un campione sopra la soglia preferita già
  misurata; quando il piano deve usare la corsa intera lo spostamento non viene
  applicato e il report lo dice (`hook_take_honored: false`);
- se il triage non è affidabile (girato scuro), l'apertura si misura col
  profilo per-frame e la sua soglia preferita: il report dice quale misura ha
  deciso (`opening.measure`);
- i b-roll entrano sul **taglio più vicino** allo slot uniforme entro
  `composition_broll_snap_sec` (0,75 s), mai sopra il b-roll precedente e mai
  rubando lo spazio dello slot successivo; senza tagli vicini restano dove sono;
- un b-roll che coprirebbe **poco parlato** (score sotto `broll_min_score`) non
  si scarta subito: il ponte prova a spostarlo su un inizio di parola entro
  `bridge_broll_semantic_window_sec` (2,5 s) e tiene la posizione col punteggio
  migliore; se nessuna migliora, resta dov'è e si scarta come prima. Ogni
  spostamento è dichiarato (`broll_shifted_to_speech`, da/a, score);
- quando la richiesta chiede una CTA o una testimonianza, gli ultimi
  `composition_tail_sec` (1,5 s) restano **liberi da b-roll**;
- `report["composition"]` porta `proposed`, `rules`, `declined` e `applied`:
  la proposta e il fatto misurato stanno separati, e `survived` è ricalcolato sul
  piano finale dopo `plan_choice` e `creative`;
- le soglie stanno in `contract.THRESHOLDS` (`composition_hook_*`), come ogni
  altro limite: si sovrascrivono con `settings`, non si riscrivono nei moduli.

## 13. Limiti dichiarati

Un limite scritto è un limite che non sorprende. Questi sono i confini veri di
oggi, non promesse future:

- **Windows prima di tutto.** Il percorso Windows è provato (rilascio del lease
  con `msvcrt`, apertura cartella con Explorer); il ramo macOS/Linux esiste nel
  codice (`fcntl`, `open -R`, `xdg-open`) ma **non è stato eseguito**: su quelle
  piattaforme va riprovato prima di fidarsi.
- **Un lavoro per cartella.** Il lease impedisce due scrittori sulla stessa
  workdir; lavori diversi su cartelle diverse girano in parallelo. Non c'è un
  coordinamento globale, e non serve: ogni lavoro scrive solo i suoi file.
- **Nessuna variante per piattaforma.** Un master, un formato. Le versioni
  9:16/1:1/16:9 si fanno come lavori separati, non come allegati.
- **Una macchina.** Niente render distribuito: la stima parla del tempo di
  questa macchina a macchina libera, e lo dichiara in `assumptions`.
- **La vista è locale.** Il daemon ascolta su `127.0.0.1`; non c'è accesso
  remoto, e il percorso del file non arriva mai dal client.
- **Rete solo dove è dichiarata.** Whisper usa la cache del modello, i font si
  scaricano dal catalogo solo con `allow_network`; senza rete il lavoro degrada
  e lo dice (`degraded`), non si blocca.
- **Le prove pesanti non sono prove sotto carico.** `tests/run_all.py --heavy`
  è l'unico modo di eseguirle; i test di crash misurano la velocità della
  macchina e sotto carico danno falsi negativi, dichiarati come tali.


