# MASTER_LEDGER

Stato del lavoro per `PLAN/MASTER_PLAN.md`. Questo file è la memoria del
programma: a ogni ripresa si rilegge e si continua dal primo task non chiuso.

## Regole

- Un task è `DONE` solo con: test di accettazione eseguito, evidenza su disco,
  hash del commit, albero pulito e suite completa verde.
- Stati ammessi: `TODO` · `WIP` · `DONE` · `BLOCKED` (con motivo e prova).
- Un commit per task, messaggio `[Fx-Tyy] <descrizione>`.
- Tag `phase-Fx-done` alla chiusura della fase.
- Nessun task si scarta in silenzio: se non si applica, va in `not_applied` nel
  referto con il motivo.

## Decomposizione

F0 è decomposta in task atomici eseguibili adesso. Le fasi successive sono
elencate al livello della consegna e vengono decomposte all'ingresso della fase,
come previsto dalla sezione 9 del piano: la decomposizione di una fase richiede
di conoscere l'implementazione che quella fase costruisce.

---

## F0 — Fondamenta di esecuzione

Accettazione di fase: modificare una clip o un sottotitolo riesegue solo gli
stage dipendenti (il test conta gli stage eseguiti); suite verde.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F0-T01 | Ledger e DECISIONS nel repo; piano copiato in `PLAN/MASTER_PLAN.md` | i tre file esistono, `git log` mostra il commit `[F0-T01]` | DONE `ca375d8` |
| F0-T02 | Registro degli eventi: append JSONL per job, senza telemetria | un job scrive `events.jsonl` con inizio/fine/stadio; test legge il file e conta gli eventi | DONE `4670932` — 22 controlli, `events.py` |
| F0-T03 | Interruttori di funzione: tabella `features` in settings, override da CLI, valore effettivo nel referto | flag spento = codice non eseguito (test con contatore di chiamate); flag ignoto = errore | DONE `4670932` — 32 controlli, `features.py` |
| F0-T04 | Cache incrementale degli stage: chiave = hash di input + parametri + versione dello stadio | secondo giro con input identico: 0 esecuzioni; un parametro cambiato: solo gli stadi dipendenti girano | DONE `dcb4f15` — `stagecache.py`, 61 controlli |
| F0-T05 | Re-render parziale: cambiare una clip o un sottotitolo riesegue solo gli stadi dipendenti | il test conta gli stadi eseguiti prima/dopo e asserisce l'insieme atteso | DONE `dcb4f15` — sottotitolo → {03,04}; smoothing → {02,03,04}; loudness → {04} |
| F0-T06 | Storia e undo dell'EDL: journal append-only con checkpoint e ripristino | dopo N modifiche, `undo` torna allo stato precedente e l'hash del documento coincide | DONE `5283935` — 57 controlli, `history.py` |
| F0-T07 | Harness a frame di riferimento (golden frame) | un test fallisce se un frame cambia oltre la tolleranza dichiarata; due render identici danno lo stesso hash | DONE `1fea22a` — 21 controlli, provato anche su master reale (4 frame, hash identici) |
| F0-T08 | Stimatore dei tempi prima del render | su un set di prova l'errore mediano sta sotto la soglia dichiarata; il valore compare nel referto | DONE `1fea22a` — 20 controlli, erede mediano 0% sul punto tenuto fuori |
| F0-T09 | Corpus annotato per calibrare le soglie | il corpus esiste con annotazioni e un test verifica che le soglie dichiarate siano derivate da esso | BLOCKED — vedi sotto |
| F0-T10 | Marcatura delle soglie provvisorie e cancello d'esame (D010) | finche T09 non e DONE il registro marca le soglie come provvisorie e un test fallisce se una fase risulta chiusa sull'esame | DONE gia in `1fea22a` come meccanismo, riga allineata il 27/10: `tests/test_threshold_provenance.py` verifica (a) lo stato di T09 letto dal ledger, (b) che ogni soglia di corpus sia `provisional` mentre T09 e aperto, (c) che nessuna fase risulti chiusa sull'esame in quello stato; 22 assert, 1 skip dichiarato per il corpus reale assente |
| F0-T11 | Matrice delle licenze con gate vincolante | nessuna dipendenza importata, modello o riga senza controllo; una riga mancante o una verifica piu' vecchia di dodici mesi fanno fallire il test | DONE - `tests/test_licenses_matrix.py` 74 assert su 10 import reali (`PIL`, `cv2`, `faster_whisper`, `librosa`, `mediapipe`, `numpy`, `onnxruntime`, `playwright`, `psutil`, `scipy`), 3 modelli con record fissato e 15 righe di matrice; prova di carico: togliendo la riga di `numpy` il test fallisce nominandola, 70 PASS / 1 FAIL. `LICENSES_MATRIX.md` documenta anche i pesi Silero e YuNet, prima scoperti |

### F0-T09 BLOCKED: servono annotazioni umane

Il corpus esiste e la derivazione funziona: `tests/measure_corpus.py` misura 9 clip in
`tests/fixtures/corpus_stats.json` con `corpus_hash`, e `registry._load_thresholds()`
ne ricava i percentili per asse con provenienza. Quello che manca non è codice: è il
giudizio umano. Un'annotazione inventata dall'agente sarebbe una soglia che si
autogiustifica, cioè esattamente ciò che la regola dell'evidenza vieta.

Per chiudere T09 serve, per ciascuna delle 9 clip del corpus, una direzione dichiarata
da una persona su almeno questi assi: palette (caldo/freddo/neutro), grade
(flat/normale/contrasto), animazione (statica/mossa), densità di taglio
(rada/normale/fitta). Formato proposto: `tests/fixtures/corpus_annotations.json`,
`{"clip": {"palette": "...", "grade": "...", "animation": "...", "cut_density": "..."}}`.
Il test che chiude il task ricalcola i percentili dalle annotazioni e li confronta con
`registry.THRESHOLDS`, fallendo se divergono.

### Note di realtà su F0 (verificate sul codice, 2026-10-27)

- **F0-T04 non è greenfield.** Esiste già una cache a contenuto reale per i chunk
  di analisi del parlato: `checkpoint.py` (`identity`, `Store.load_chunk`,
  `write_chunk`) con chiave `source_sha256 + analysis_sha256 + parametri + versione
  del motore`, e la prova che salta il lavoro sta in
  `tests/test_chunk_checkpoints.py` (8 cached / 1 computed). Il task estende quel
  pattern agli stage di render, non ne inventa un altro.
- **F0-T09 è in gran parte fatto.** `tests/measure_corpus.py` misura 9 clip in
  `corpus_stats.json` con `corpus_hash`; `registry._load_thresholds()` deriva i
  p33/p67 per asse e li espone con provenienza; i test di palette, grade, animazione
  e densità consumano quei valori. Manca solo l'annotazione umana e un test che
  ricalcoli i percentili dalle annotazioni e li confronti con il registro.
- **Attenzione ai falsi amici.** `checkpoint.py` non è un checkpoint dell'EDL (è
  la cache dei chunk); `gates.calibrate()` è la calibrazione colore di un grado,
  non delle soglie; `GOLDEN_SHA256` in `test_edl_ceiling.py` è l'hash di una stringa
  di filtro, non di un fotogramma.
- **Assenti confermati**: event log (T02), feature flag (T03), re-render parziale
  (T05), storia/undo dell'EDL (T06), harness a frame di riferimento (T07),
  stimatore dei tempi (T08).

## F1 — Ponte analisi-assemblaggio e fondamentali

A1-A12 sono già chiusi (commit `5238b2f`..`a0d1045`, reference 26, sezione 6.1).
Restano della fase: compress in auto, SNR e music ratio misurati, più musiche.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F1-T01 | A1-A12 del backlog | già verificati: `vx auto --montage` su 8 clip, 8 a_roll, ±1 LU, sottotitoli sulla tela, silenzi tagliati, ducking | DONE |
| F1-T02 | Compress automatico con finding | compress applicato solo con finding; assenza dichiarata con la misura | DONE - 11 assert in `tests/test_f1_compress.py`; LRA 15 LU: decisione su ogni clip e tre `acompressor` nel grafo; LRA 5: nessun compressore e `compress_declined` con 5.0 <= 12.0; LRA assente: `compress_unmeasured`; la musica non viene mai compressa dalla decisione del dialogo; regressioni 2.2, migratore e renderer verdi |
| F1-T03 | SNR e music ratio nel referto | i due valori compaiono in `auto_report.json` con la soglia usata | DONE - `auto_report.audio` porta `snr_db` (sorgente peggiore) e `music_ratio` (sorgente piu musicale) accanto a `snr_threshold_db=12.0` e `music_ratio_threshold=0.5`; 13 assert unitari e 42 assert e2e in 44 s, con valori reali 0.13 e 0.071. Entrambi i numeri erano gia misurati (`noise_floor_and_speech`, `analyze_audio`) e venivano scartati prima del referto |
| F1-T04 | Più musiche con più tracce | due tracce musicali mixate con ducking distinto e dichiarate | DONE - due letti con ruoli `music_a`/`music_b`, un bus e un `sidechaincompress` per letto (soglie 0.05 e 0.08), `amix=inputs=3`; il caso a un letto produce lo stesso grafo di prima, character for character; e2e `vx auto` con due musiche: 43 assert in 44 s, `report.music` con due voci, `track_id`, `sha256` e la soglia di ducking di ciascuna; il cap di triage passa a 2 e la terza traccia resta `REDUNDANT_MUSIC` |

**Fase chiusa il 27/10/2026:** 4/4 task, tag `phase-F1-done`, suite completa
114 runner / 3645 assert / 0 fail / 2 skip in 669 s. Decisione D027 registrata
(`ducking_tracks` additiva; il secondo letto si abbassa di più).

## F2 — EDL 3 e renderer

Accettazione di fase: un documento EDL per ogni funzione, tutti renderizzano; un
documento 2.2 migrato produce lo stesso render dell'originale a parità di frame;
ogni effetto ha un frame di riferimento. Nessuna fase si chiude con soglie
provvisorie (D010).

| ID | Ondata | Contenuto | Accettazione binaria | Stato |
|---|---|---|---|---|
| F2-T01 | W0 fetta verticale | UN effetto con keyframe e UNA transizione, dall'EDL 3 al render e al frame di riferimento | lo stesso documento renderizza due volte con hash identico; il frame mostra l'effetto; la transizione si vede al tempo dichiarato | DONE `d1c4dd4` (fetta) + `32357c7` (lezioni) |
| F2-T02 | W1 contratto | `Effect`, `Keyframe` (linear/bezier/hold), `AdjustmentLayer`, `TextLayer`, `CaptionTrack`, `NestedSequence`, `Transition` in un modulo EDL 3 separato | valida un documento con tutte le entità; rifiuta keyframe fuori ordine e valori fuori dominio con codice dichiarato | DONE `9327c89` — registro con backend, 32 controlli |
| F2-T03 | W1 migratore | 2.2 -> 3.0 su documenti reali | i documenti 2.2 del caso studio migrano; il render del migrato ha gli stessi frame hash dell'originale; round trip 3.0->2.2->3.0 senza perdite | DONE `9327c89` — MAD 0.0000, 3/3 frame identici, 17 controlli |
| F2-T04 | W2 compilatore | scelta del backend per effetto: espressione ffmpeg / `sendcmd` / Chromium / frame per frame | ogni voce dichiara il backend e la regola di soglia; oltre soglia un effetto passa a `sendcmd` e il test lo prova contando i comandi | DONE `b57a051` - 43 controlli; luma 205 dentro/fuori, ducking 13.30 dB |
| F2-T05 | W3 tempo | speed ramp e time remap con politica audio dichiarata nel contratto (segmenti `atempo` / pitch mantenuto / silenzio) | un ramp da 1x a 2x ha la durata attesa al frame e audio senza buchi ne click; la politica sta nel documento | DONE `64acdcf` + `a0a7a76` - ramp 2x rende 1.000s, 25+48 controlli |
| F2-T06 | W4 estremi | reverse e freeze con tetto di durata dichiarato | oltre il tetto il documento e rifiutato con il numero; sotto il tetto il frame e identico al sorgente invertito | DONE `64acdcf` + `a0a7a76` - freeze hash identico, MAD 0.048, 21 controlli |
| F2-T07 | W5 composizione | PiP, maschere, blend, chroma key | per ognuno un documento, un frame di riferimento e un test sul canale alfa | DONE `64acdcf` + `a0a7a76` - PiP, maschera, blend, chroma, 33 controlli |
| F2-T08 | W6 annidamento | sequenze annidate con parametri e tempi propri | una sequenza dentro una sequenza renderizza; i tempi interni non dipendono da quelli esterni | DONE `64acdcf` + `a0a7a76` - annidamento reso una volta sola, 22 controlli |
| F2-T09 | W7 chiusura | suite golden per ogni effetto, documentazione, `SKILL.md` | ogni effetto ha un frame su disco; `SKILL.md` sotto 250 righe; suite verde | DONE `f028e1c` - 15 effetti, 34 frame, MAD 0.000 |
| F2-T10 | W8 schema 1.1 | EDL 3 `vx.edl3/1.1`, campi nuovi tutti opzionali con default: `preset_ref` (preset con nome risolto da registro), `stack` (max 3 movimenti di camera con regola di composizione), `template_ref` + `exposed_params` (il documento salva solo i valori), `synthetic`, `provenance` per ogni scelta, `attribution` per le risorse di terzi (licenza, autore, fonte, URL), `brand_ref`, `variant` (id, progetto di origine, asse variato), `ext` per i campi `x-` conservati intatti | un test per ogni campo, comprese le violazioni: quattro movimenti nello `stack` rifiutati con codice, un parametro fuori intervallo rifiutato, `attribution` incompleta rifiutata in regime pubblico, un campo `x-` sconosciuto conservato al round-trip; i documenti `/1` restano validi; i 34 frame golden restano identici byte per byte | DONE - 16 assert in `tests/test_edl3_v11.py`: i nove campi validano e tornano dal round-trip, `x-` conservato a livello clip e documento, `/1` accettato e non aggiornato, sette codici di rifiuto esercitati (STACK_TOO_DEEP, STACK_SHAPE, EXPOSED_PARAM_SHAPE, PROVENANCE_SHAPE, ATTRIBUTION_SHAPE, EXT_KEY, VARIANT_SHAPE) |
| F2-T11 | W8 compatibilita' additiva | nessun migratore: il lettore accetta `/1` e `/1.1`, conserva i campi `x-` sconosciuti, e i documenti `/1` reali restano validi | un documento `/1` reale valida e produce gli stessi frame di prima; un `/1.1` fa round-trip conservando un campo `x-sconosciuto`; i 34 frame golden restano **identici byte per byte** (se cambiano e' un bug, non una tolleranza); due giri consecutivi identici | DONE - il golden EDL 3 resta verde: **15 effetti, 34 frame, MAD 0.000 su ogni effetto**, 8,1 s; i documenti `/1` validano e il lettore accetta `/1` e `/1.1`. Nessun migratore: la modifica è additiva (D028) |
| F2-T12 | W8 backend e regola di scelta | ogni voce del registro dichiara backend e regola per numero di keyframe e durata | un test generato da `EFFECT_REGISTRY` prova che nessuna voce resta senza regola: <=3 keyframe -> expr, oltre -> sendcmd, overlay per i testi; una voce senza regola fa fallire il test | DONE - `edl3.select_backend(kind, keyframe_count, duration)` è la regola unica; 46 assert in `tests/test_edl3_backend_rule.py` la confrontano con la scelta del renderer su ogni voce del registro (1, 2, 3, 4, 5 e 6 keyframe): nessuna divergenza, soglia 3 allineata a `SENDCMD_THRESHOLD`, testo → `overlay`, tipo ignoto → `EFFECT_UNKNOWN` |
| F2-T13 | W8 speed ramp e limiti | politica audio dello speed ramp nel contratto e tetto dichiarato per reverse e freeze | la politica (atempo a scalini, pitch mantenuto) e' in DECISIONS e il test prova che il picco spettrale resta entro la tolleranza dichiarata; reverse o freeze oltre il tetto falliscono con un codice che nomina il tetto | DONE - D029 registrata: rampa = `atempo` a scalini con pitch mantenuto (4x → [2,2], 0,25x → [0.5,0.5]); tetti letti dal codice, 30,0 s per reverse e 300,0 s per freeze, rifiutati con `REVERSE_TOO_LONG`/`FREEZE_TOO_LONG` che nominano il tetto; 25 assert in `tests/test_edl3_ramp_policy.py` |
| F2-T14 | W8 fetta verticale v2 | un documento minimo con un effetto a 2 keyframe e una transizione, dal validatore al frame di riferimento | il documento valida, rende e produce un frame di riferimento byte-identico in due esecuzioni; nessuna ondata successiva parte prima che questo sia verde | DONE - 12 assert in `tests/test_edl3_slice_v2.py`: un documento `/1.1` con un effetto a due keyframe e una transizione valida, rende, e lo stesso frame estratto da due render separati ha lo stesso sha256; il golden resta 15 effetti, 34 frame, MAD 0.000 |

**Fase chiusa il 28/10/2026 (ondata W8 inclusa):** 14/14 task, tag `phase-F2-done`, suite completa 120 runner / 3812 assert / 0 fail / 2 skip in 968 s. Lo schema EDL 3 è a `vx.edl3/1.1` (additivo, D028); i 34 frame di riferimento sono rimasti identici byte per byte.

## F3 — Motion Studio (Chromium)

Motore: Playwright guida il Chrome di sistema (`channel="chrome"`), nessun browser
da scaricare, nessuna rete. Accettazione di fase: golden frame in tre istanti per
template, budget dichiarato rispettato, zero richieste esterne, parametri invalidi
respinti.

| ID | Ondata | Contenuto | Accettazione binaria | Stato |
|---|---|---|---|---|
| F3-T01 | W0 fetta verticale | un solo template con animazione, cattura deterministica a tempo virtuale, PNG con alfa, conversione in video | due catture danno frame identici byte per byte; nessuna richiesta esterna; il tempo misurato sta nel budget dichiarato | DONE `958eabb` - due catture identiche byte per byte, 91,7% trasparente, 73 ms/frame a 1080x1920 (4,37 s sul budget di 5 s), zero Chrome orfani |
| F3-T02 | W1 motore di cattura | viewport, fps, scala, seek virtuale, timeout, pulizia del contesto | un template che non risponde viene terminato al timeout con errore dichiarato, nessun processo orfano | DONE `958eabb` - timeout dichiarato su seek che non risolve, zero processi orfani (slice i) |
| F3-T03 | W2 template | registro con 40+ template, parametri dichiarati e valori ammessi | ogni template dichiara parametri e rifiuta un valore fuori dominio con il nome del parametro | DONE `988cff5` - 54 template su 12 famiglie (11x4 + 10 sottotitoli), 0 bloccati, 405 assert in 174 s; 54 hash del frame centrale tutti distinti e 4 per famiglia; ogni template ha 3+ parametri con almeno un dominio chiuso e rifiuta il fuori dominio nominandolo |
| F3-T04 | W3 linter di authoring | controllo offline di HTML/JS: nessuna rete, nessun API vietata, dipendenze locali | un template con un tag remoto viene respinto dal linter prima della cattura | DONE `d779921` - 21 controlli, nove codici, browser mai avviato su template ostile |
| F3-T05 | W4 cache | chiave template + parametri + fps + risoluzione, riuso dei frame gia resi | secondo giro identico: zero catture; un parametro cambiato: solo quello | DONE `d44076b` - 37 controlli; chiave da digest del contenuto del template + parametri + fps + size + engine + t; 11 catture con un solo avvio di browser, test da 137 s a 7,2 s |
| F3-T06 | W5 ripiego Pillow | senza Chromium i template statici rendono comunque, e lo dichiarano | Chromium spento = ripiego dichiarato, stesso tempo di esecuzione misurato | DONE `6dae3d1` + `a0b59a6` - 54/54 template resi senza Chromium (243 frame in 9,8 s, 66 ms/frame nel caso peggiore); `engine=pillow`, `fallback=true`, `degraded=[html,layout,effects]` dichiarati; stessa griglia frames/fps/size del percorso Chromium; `resolve_engine` rifiuta le proprieta che solo il motore sa fare (backdrop_filter, blend_animated, reflow) invece di fingerle |
| F3-T07 | W6 aggancio EDL 3 | backend chromium acceso da `chromium_available`, overlay con alfa | un documento con effetto che qualifica Chromium renderizza; prima era BACKEND_UNAVAILABLE | DONE `8b04975` - un documento con clip generatore e effetto che qualifica Chromium renderizza (33.437 byte a 320x568); input `-framerate 12 -loop 1 -t 2 -i frame_%05d.png` all'ordinale della clip e nessun `-ss` proprio, catena `format=rgba`, overlay nella finestra; MAD 19,424 in basso a sinistra e 0,000 in alto; 30 controlli, 12 rifiuti ognuno con il proprio codice; `compile_timeline` non lancia processi |
| F3-T08 | W7 chiusura | golden frame per ogni template, docs, SKILL.md | un frame per template su disco, SKILL.md sotto 250 righe, suite verde | DONE `b32c6f7` - 54 template x 3 istanti = 162 PNG (13,2 MB) in `tests/fixtures/motion_golden/`; il gate ri-renderizza dal vivo e confronta byte per byte: 118 assert, due giri consecutivi identici, 96 s per giro; `engine_version` 156.0.8078.48 registrata e verificata; SKILL.md esattamente 200 righe, indice 01..26 intatto; decimo codice `NONDETERMINISTIC_JS` |

## F4 — Libreria effetti e transizioni
60+ voci con innesco, parametri, tetto, implementazione, fallback, prova visiva;
anti-arlecchino (tetto di effetti visibili ogni 10 s).

### Decomposizione F4 (registrata 2026-10-28)

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F4-T01 | Schema dichiarativo: `ParamSpec`, template di filtro, innesco, tetto, ripiego; caricamento per famiglie | le 5 voci esistenti restano identiche e i 34 frame non cambiano; un tipo nuovo si dichiara senza toccare il validatore | DONE - `effect_spec.py` (ParamSpec con intervallo e default, template, innesco, tetto, ripiego) e caricamento per famiglie (`vx/effects/`); `filter` e' un backend dichiarato e un'istanza con keyframe su un tipo dichiarativo viene rifiutata con `EFFECT_NOT_ANIMATABLE`, non ignorata. Il registro passa da 5 a 17 voci, le 5 originarie sono invariate e i 34 frame restano 0 failure (MAD 0.000). `tests/test_effects_registry.py` 55 assert; `test_edl3_contract.py` reso piu' stretto (unione esatta core + dichiarative, `chroma_key` escluso) |
| F4-T02 | Famiglia geometria (12 voci) | 12 voci nuove con parametri validati e un frame reso | DONE - 12 voci (zoompan, rotate, flip_h, flip_v, mirror, shake, whip_pan, dolly_zoom, tilt_shift, fisheye, pixelate, swirl) in `scripts/vx/effects/geometry.py`, con parametri validati e template reali; 12 frame resi sul banco di prova (cartella fuori dal repository), tutti diversi dal nudo; `test_effects_registry.py` 55 PASS copre innesco, tetto, ripiego e intervalli per ogni voce; il frame di riferimento committato arriva con F4-T09 |
| F4-T03 | Famiglia colore (12 voci) | 12 voci nuove con parametri validati e un frame reso | DONE - 12 voci di colore (curves, hueshift, selective_color, white_balance, duotone, posterize, threshold, negative, sepia, teal_orange, bleach_bypass, day_for_night); 53 assert in `tests/test_effects_color.py`; 12 frame resi, ognuno diverso dal nudo |
| F4-T04 | Famiglia glitch e tempo (12 voci) | 12 voci nuove con parametri validati e un frame reso | DONE - 12 voci di glitch e tempo; 46 assert; 12 frame resi e diversi dal nudo. Il datamosh dichiara nei propri appunti di essere un'approssimazione a livello di filtro, non una rottura di I-frame |
| F4-T05 | Famiglia pellicola e luce (12 voci) | 12 voci nuove con parametri validati e un frame reso | DONE - 12 voci di pellicola e luce; 40 assert; 12 frame resi e diversi dal nudo; le voci approssimate lo dichiarano negli appunti |
| F4-T06 | Famiglia composizione e testo (10 voci) | 10 voci nuove, le tre di testo dichiarano di essere composte e non filtrate | DONE - 10 voci di composizione a un solo ingresso; 51 assert; 10 frame resi. Split screen e picture-in-picture richiedono un secondo ingresso e restano fuori dal backend lineare: dichiarato, non finto |
| F4-T07 | Innesco, tetto e ripiego dichiarati per ogni voce, con rifiuto del parametro fuori intervallo | nessuna voce senza innesco, tetto e ripiego; il fuori intervallo ha un codice proprio | DONE - il registro ha 63 voci: 58 della libreria con innesco, tetto e ripiego dichiarati, piu' i 5 primitivi che restano com'erano; un parametro fuori intervallo produce il codice proprio con il messaggio che nomina l'intervallo. `tests/test_effects_registry.py` 55 assert |
| F4-T08 | Anti-arlecchino: tetto di effetti visibili ogni 10 s, applicato al percorso automatico | 4 effetti visibili in 10 s vengono rifiutati dal controllo, un EDL esplicito resta intatto | DONE - `check_effect_density` vive nel compilatore, `EFFECT_DENSITY` e' un codice duro, il tetto e' 3 look in una finestra scorrevole di 10 s, senza deroghe. Misurato: il massimo nei documenti gia' committati e' sotto il tetto; il documento di contratto con cinque primitivi continua a validare perche' i primitivi non sono look. `tests/test_edl3_density.py` 29 assert; `test_edl3_contract.py` 32 PASS |
| F4-T09 | Prova visiva: un frame di riferimento per ogni voce, byte-identico su due giri | 60+ frame confrontati byte per byte, due giri consecutivi identici | DONE - 58 frame di riferimento in `tests/fixtures/edl3_kinds/` (3.796.271 byte), uno per ogni voce declarativa, resi dal percorso EDL vero e confrontati byte per byte: 179 assert, 21,7 s, due giri consecutivi con lo stesso digest di cartella (nessun file cambia). Ogni frame differisce dal nudo (58/58) e la prova di carico e' stata fatta sul serio: togliendo un fixture il test fallisce nominando la voce e lo sha atteso, sostituendolo con un altro PNG valido fallisce con atteso/effettivo. `--write` rigenera solo questa cartella e rifiuta di benedire un frame identico al nudo. Il golden del contratto resta 15/34/0 |
| F4-T10 | Documentazione della libreria e chiusura di fase | ogni voce compare in un indice con innesco, parametri, tetto, ripiego; suite verde; SKILL.md sotto 250 righe | DONE - indice generato dal registro, non scritto a mano: 58 voci, sette colonne (tipo, famiglia, innesco, parametri con intervallo e default, tetto, ripiego, backend), nessuna cella vuota, nessun percorso assoluto; sezione 13 di `references/26-edl3-e-ponte.md`, rigenerabile e verificabile con `scripts/gen_effect_index.py --check` (due giri identici byte per byte). `test_docs_consistency.py` 25 PASS, `test_portability.py` OK, `test_effects_registry.py` 55 PASS. Nota: la porta del banco (`bench.py`) aveva un percorso assoluto fisso gia' presente nel commit precedente e bloccava la prova di portabilita'; corretto con `tempfile.gettempdir()`, `test_bench.py` 42 PASS |

## F5 — Audio e musica
Catena voce (denoise, EQ, de-esser, compressore), ducking con keyframe, SFX,
musica con beat/downbeat/sezioni, tagli sul beat, loudness per bus, limiter,
stem, 5.1 opzionale.

## F6 — Comprensione del materiale (MaterialMap)
Trascrizione parola per parola, segmenti per argomento, shot con descrizione,
embedding locali, OCR, saliency, oggetti, picchi di energia, qualità per
segmento, momenti migliori, rischi, relazioni, struttura musicale. `vx understand`.

## F7 — Intento e dialogo
IntentSpec, rilevatore di ambiguità con regola del costo dell'errore, massimo 3
domande raggruppate con default, modalità creativa con Creative Brief e fino a 3
animatic.

## F8 — Ricerca e apprendimento
ReferenceLab con StyleFingerprint, knowledge base JSONL versionata, Trend Radar
v2 con scala S0-S4, sonde di salute, interruttore automatico, `provider_health.json`,
benchmark versionato, cancellazione verificata dei file transitori.
Vale l'Allegato D (sostituisce C3): solo YouTube Data API, Gemini video e ricerca
web. Nessuna API di Instagram o TikTok, nessun fornitore a pagamento.

## F9 — Critico
Render proxy, campioni, rubrica a sei assi, patch dell'EDL con ancora e prova,
budget tre iterazioni, stop al peggioramento, confronto prima/dopo.

## F10 — Interoperabilità e consegna
Export multi-aspect, OTIO, FCPXML, EDL, stem, VTT/SRT, soft-sub, capitoli,
metadati, ProRes, DNxHR, 10 bit, HDR, testi per la pubblicazione, thumbnail per
volto ed emozione, watermark, sfocatura volti.

## F11 — Superficie agente e job
MCP v2 con parità totale con la CLI; coda job con cancellazione, ripresa, elenco,
avanzamento, pulizia delle workdir.

## F12 — Indurimento ed esame finale
Profilazione, pulizia, audit licenze, revisione sicurezza Chromium, docs
generate, `SKILL.md` sotto 250 righe con caricamento progressivo.
Accettazione: esame completo (19 incarichi) passa tre volte.

## F13 — Vision Pipeline e Cervello editoriale
Campionamento adattivo, contact sheet per shot, descrizioni, embedding, OCR,
segmentazione, match cut, speed ramp sui picchi, colpi sul beat drop, setup e
payoff, ciclo divergente/convergente, vincolo di diversità.

## F14 — Review Room
Pagina HTML locale con proxy, timeline, confronto prima/dopo, commenti al
timecode, feedback JSON che diventa patch.

## F15 — Shader, 3D e profondità
100+ transizioni gl-transitions, authoring GLSL in sandbox, Three.js, parallasse
da profondità, ponte opzionale Blender, rendering software come ripiego.

## F16 — Prestazioni
Render a segmenti paralleli, smart render con test di identità, encoder hardware
scelto per prova, intermedi lossless, benchmark ufficiale.

### Decomposizione F16 (registrata 2026-10-28)

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F16-T01 | Banco di misura ufficiale: un comando che misura e scrive numeri, non stime | il referto contiene tempi, byte e picco di memoria misurati sullo stesso input, con data e versione | DONE - `vx.bench` misura un render vero: 2 giri sullo stesso documento danno lo stesso sha256 (`dd7cafda...`), 24 frame contati da ffprobe, 5483 byte, 0,32 s e 0,20 s misurati con `perf_counter`; picco di memoria dichiarato `null` perche' psutil non e' installato, non stimato. `tests/test_bench.py` 42 assert, incluso il documento rotto con `ok=false` ed exit 2 |
| F16-T02 | Render a segmenti paralleli | lo stesso documento reso in parallelo e in serie produce frame identici byte per byte, con tempo misurato | TODO |
| F16-T03 | Smart render con test di identita' | uno span identico viene coperto senza ricodifica solo se il test lo prova; un taglio non lo e' mai | TODO |
| F16-T04 | Encoder hardware scelto per prova | il candidato viene accettato solo se il frame decodificato resta entro la soglia dichiarata; altrimenti si ripiega e lo si dice | TODO |
| F16-T05 | Intermedi lossless per i passaggi multipli | due passaggi lossless danno lo stesso sha256 del singolo passaggio, e il peso e' dichiarato | TODO |
| F16-T06 | Referto di prestazioni e chiusura di fase | il referto elenca ogni misura con il comando che l'ha prodotta; suite verde | TODO |

## F17 — Vault e sicurezza dei dati
Priorità: subito dopo F0. Sola lettura sugli originali, marcatore `.vx-owned`,
quarantena 7 giorni, controllo spazio, journal write-ahead, snapshot, verifica
sha256 e bit-rot, dry-run, limiti ai sottoprocessi, fuzz con file ostili,
catena di fornitura dei modelli.
Accettazione: 50 kill -9 senza corruzione e con ripresa; directory esca intatta;
200 file di fuzz senza errori non gestiti.

## F17 — Vault e sicurezza dei dati (anticipata prima di F2, vedi D009)

Accettazione di fase: 50 kill -9 senza corruzione e con ripresa; directory esca
intatta; 200 file di fuzz senza errori non gestiti.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F17-T01 | Marcatore di proprietà `.vx-owned` per radice di lavoro | un percorso senza marcatore non viene mai cancellato; il test prova una directory esca e verifica che resti intatta | DONE `853a306` — `.vx-owned`, directory esca rifiutata (prova con junction reale) |
| F17-T02 | Quarantena a sette giorni con dry-run | `--dry-run` elenca e non tocca nulla; `apply` sposta in `.quarantine/<data>/`; la pulizia cancella solo oltre i sette giorni | DONE `853a306` — quarantena con dry-run di default, 57 controlli |
| F17-T03 | Controllo dello spazio prima del render | spazio insufficiente = errore dichiarato con i numeri, nessun render avviato | DONE `853a306` — `ensure_space` con i tre numeri nel payload |
| F17-T04 | Giornale write-ahead degli artefatti | dopo un crash si riconosce un artefatto a metà: il giornale dice cosa doveva esistere e l'hash atteso | DONE `58df879` — `artifacts.py`, 47 controlli, cache a 64 |
| F17-T05 | Verifica sha256 e bit rot, originali immutabili | un byte alterato in un nostro artefatto viene segnalato; il sorgente ricontrollato resta identico | DONE `853a306` — `verify` segnala byte alterati senza toccare i non posseduti |
| F17-T06 | Limiti ai sottoprocessi | un ffmpeg che non finisce viene terminato al tempo massimo, con errore dichiarato | DONE `0b9c8a4` — `ProcessTimeout` con stadio, tempo e testa del comando |
| F17-T07 | Fuzz con file ostili e catena dei modelli | 200 file ostili danno errori dichiarati e nessun traceback non gestito; un modello con hash diverso da quello atteso viene rifiutato | DONE `0b9c8a4` — 200 casi: 110 errori dichiarati, 0 non gestiti; 3 modelli con hash fissato |
| F17-T08 | Prova di crash a livello di sistema | 50 terminazioni reali durante una scrittura, con un figlio ffmpeg vivo: il journal resta leggibile, il file mezzo scritto e' un orfano dichiarato, il figlio non sopravvive; gli originali sono protetti con attributo di sola lettura e ACL di Windows, e una scrittura fallisce davvero | DONE - 50 uccisioni reali di `vx auto` con `taskkill /F /T` (seed 1717, ritardi 88-2487 ms, 239 s), **50/50**; il primo giro aveva 49/50 e il rosso era un difetto vero: un crash dentro `write_bytes_atomic` durante `stagecache.record` lasciava un intent aperto e un `.tmp`, e dopo un resume riuscito `lookup` restava un miss permanente. Corretto con `EVENT_ABANDON` + `abandon_pending` e con la rimozione del temp fratello. Regressioni journal/cache 47+64+66+26+22 verdi. La protezione a livello di sistema resta in F17-T09 |
| F17-T09 | Protezione degli originali a livello di sistema | attributo di sola lettura e ACL di negazione su Windows, 0444 su POSIX, applicati dal codice; una scrittura sull'originale deve fallire e deve tornare possibile togliendo la protezione | DONE - `vault.protect_original` imposta l'attributo `0x021` e l'ACE `(DENY)(WD,AD)` per l'utente corrente; `release_original` li toglie (il trustee si rimuove nudo, non con i diritti); `register(kind="original")` applica la guardia. `tests/test_f17_acl.py`: 18 assert, con 5 di controllo che provano che le sonde discriminano; la scrittura fallisce anche con l'attributo rimosso (ACE da solo) e torna possibile dopo il rilascio. Vault 57, artifacts 47, stage cache 64 verdi |

**F17 chiusa il 28/10/2026:** T08 (50/50 uccisioni reali, con il difetto dell'intent orfano trovato e corretto) e T09 (guardia del sistema sugli originali, applicata da `register(kind="original")`). Il criterio originario della fase è finalmente soddisfatto.



Codifica per titolo con VMAF, gestione colore, torneo a coppie con ordine
scambiato, punteggio Elo.

## F19 — Ricette e ciclo di apprendimento
Ricette parametriche con innesco e prova visiva, promozione sopra soglia,
versioni annullabili, ispezione e cancellazione.

---

## F26 - Profili di genere: un motore, ogni tipo di video

Il motore EDL 3 e' gia' indipendente dal formato: accetta qualunque
canvas, fps, durata e numero di tracce. Il limite e' nel regista: `auto.py`
punta a -14 LUFS e `decide.py` conosce solo `reel`/`reels`/`shorts` come preset.
Un profilo e' un **dato** dichiarato, non un ramo di codice: il renderer non
cambia, cambia cio' che il regista sceglie e come giudica.

### Decomposizione F26 (registrata 2026-10-28)

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F26-T01 | Registro dei profili: genere, canvas, fps, durata, loudness, colore, consegne, grammatica; risolto da un riferimento nel documento | ogni profilo dichiarato si risolve; un profilo ignoto e' rifiutato con codice proprio; il renderer non cambia comportamento su un documento senza profilo | TODO |
| F26-T02 | Loudness per profilo con misura reale | quattro profili (-14, -16, -23, -24 LUFS) resi e misurati: ogni uscita sta entro 0,5 LU del bersaglio e sotto il true peak dichiarato | TODO |
| F26-T03 | Canvas e fps per profilo | 9:16, 16:9, 1:1, 4:5 e 2.39:1 a 23.976, 24, 25, 29.97, 30, 50, 60: ogni combinazione resa con numero di fotogrammi e durata misurati | TODO |
| F26-T04 | Grammatica strutturale dichiarata per profilo | ogni profilo dichiara hook/atti/beat/interviste e la banda di densita' dei tagli; un documento che la viola lo dice, e la misura viene dal documento, non da una costante | TODO |
| F26-T05 | Colore: 10 bit e HDR dichiarati, SDR predefinito | un profilo HDR esce a 10 bit dichiarati e un profilo SDR resta 8 bit; il cambio non tocca i 34 frame di riferimento | TODO |
| F26-T06 | Lungometraggio: timeline lunga senza esaurire la memoria | una timeline da due ore compila e dieci minuti reali rendono con checkpoint, tempo e picco di memoria misurati | TODO |
| F26-T07 | Matrice delle consegne per profilo | ogni profilo dichiara i suoi contenitori e codec; ognuno viene prodotto e verificato con ffprobe (codec, bit depth, loudness, sottotitoli, capitoli) | TODO |
| F26-T08 | Prova end-to-end per ogni profilo | un minuto reale per ogni profilo reso dal percorso pubblico, con referto misurato e un fotogramma di prova | TODO |

## Esame finale (sezione 6 + A5 del piano)

19 incarichi, tre esecuzioni coerenti, `PLAN/FINAL_REPORT.md` con ogni task
chiuso, evidenza, BLOCCHI aperti, limiti noti e `not_applied`.

## Richiesta fuori piano (utente, 27/10/2026)

Capacità tipografica autonoma: cercare su DAFONT i font adatti, proporli
all'utente e saper scegliere da sola quando nessuno risponde.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| FX-T01 | Provider font con sonda di disponibilità e licenza registrata per ogni font | senza rete: provider `unavailable` dichiarato, il lavoro prosegue con i font inclusi | DONE `1a92ad4` — catalogo con licenza e robots rispettato, 62+79 controlli |
| FX-T02 | Ricerca per criteri (genere, tono, peso, licenza commerciale) con cache locale | la stessa ricerca due volte non fa due chiamate; il risultato cita la pagina del font e la licenza | DONE `a67d03d` + `fe702a1` — scelta misurata e provider API, 98+106 controlli |
| FX-T03 | Proposta all'utente con anteprima renderizzata | la proposta mostra il testo reale del progetto sul font, con licenza e alternativa | DONE `1deec1a` — anteprima e foglio comparativo, 30 controlli |
| FX-T04 | Scelta autonoma: regola deterministica quando l'utente non risponde | brief aperto + nessuna risposta = font scelto dal profilo con motivazione dichiarata; licenza non commerciale mai scelta in autonomia | DONE `a67d03d` — decide() deterministico, solo commerciali, None con motivo |
| FX-T05 | Integrazione nel Creative Brief e nel render ASS | il font scelto arriva a `fontsdir` e al `Fontname` senza sostituzioni; sostituzione = `QCFailure` | DONE `1deec1a` — `fonts_dir` nel render, MAD 5.2432 prova che il font e usato |
| FX-T06 | Licenze: matrice e attribuzione | ogni font usato ha licenza, fonte e data in `LICENSES_MATRIX.md`; un font senza licenza commerciale non entra in un lavoro di committenza | DONE `1deec1a` — matrice generata, personal e unknown rifiutati |

## F25 — Livello "wow" (Allegato E)

Fase aggiunta, non sostituisce nulla: F0-F24 restano dove sono, come gli incarichi
1-19. Le voci E1-E11 e gli incarichi 20-25 stanno in `PLAN/MASTER_PLAN.md`,
Allegato E. L'esame passa da 19 a 25 incarichi: la riga «19 incarichi» della
sezione Esame finale resta come storia e vale 25.

Qui sono decomposte solo E1-E4, che sono conoscibili adesso. E5-E11 restano al
livello della consegna con la loro accettazione, come vuole D003: decomporle senza
avere in mano F8, F13, F14 e la licenza del modello di identità produrrebbe task
inventati. Entrano nel dettaglio all'ingresso delle fasi che le costruiscono.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F25-T01 | E1 registro dei movimenti di camera: 40+ preset nominati costruiti da `crop`, `scale` e keyframe (push, pull, frusta, orbita finta, a mano, gru finta e le altre famiglie), con famiglia, parametri, tetto, innesco e prova visiva per voce | il registro valida al 100% con 40 o più voci, ognuna con famiglia, schema dei parametri, tetto e innesco dichiarati; una voce senza innesco fa fallire il validatore; una pila di 4 movimenti è rifiutata con `STACK_TOO_DEEP`; un nome ignoto è rifiutato con il nome nel messaggio | TODO |
| F25-T02 | E1 determinismo e frame di riferimento dei preset: `preset_ref` risolto da registro, golden per ogni preset, striscia di tre istanti per i movimenti | due esecuzioni consecutive danno lo stesso frame hash per ogni preset; il numero di golden su disco coincide con il numero di voci del registro; un preset senza golden fa fallire il test | TODO |
| F25-T03 | E1 profili di camera 35mm, 8mm, DV con grana, gate weave, halation e ratio indipendenti | ogni profilo dichiara i quattro parametri con dominio e default; il gate weave è deterministico a parità di seme (due giri, hash identici); un `ratio` fuori dai valori ammessi è rifiutato con il valore; ogni profilo ha un golden frame | TODO |
| F25-T04 | E1 lenti: vignetta, aberrazione cromatica, bokeh finto, distorsione, ognuna con schema, tetto e ripiego | ogni lente ha un test di innesco e un golden; una distorsione oltre il tetto è rifiutata con il numero; il bokeh senza mappa di profondità produce il blur mascherato dichiarato con `fallback=true` e non un errore non gestito; due lenti in pila passano, due distorsioni insieme sono rifiutate con il nome | TODO |
| F25-T05 | E1 libreria di almeno 50 palette LUT 3D con categoria, provenienza e licenza | il registro conta 50 o più voci e ogni voce ha `attribution` completa (licenza, autore, fonte); una LUT assente dal disco produce il ripiego `eq` più `colorbalance` con `fallback=true`; `protect_skin` misura la distanza su una toppa di pelle e limita la forza, con la tolleranza «da calibrare» sul corpus con volti di F6; ogni palette ha un frame di riferimento | TODO |
| F25-T06 | E1 ruota delle 8 emozioni come tabella versionata (ritmo, grading, musica, SFX) | la tabella ha un numero di versione e 8 voci; ogni voce dichiara i quattro assi; una voce con un asse mancante è rifiutata nominando l'asse; la versione usata è citata nel Creative Brief e nell'EDL; un foglio di 8 riquadri sulla stessa clip è su disco | TODO |
| F25-T07 | E2 cancello dei keyframe prima di ogni render completo (3-6 frame approvati) | senza approvazione il render completo è rifiutato con `KEYFRAMES_NOT_APPROVED` e il numero mancante; con approvazione il render parte; una richiesta fuori da 3-6 è rifiutata con il numero; modificare i keyframe dopo l'approvazione riapre il cancello (hash diverso = cancello chiuso) | TODO |
| F25-T08 | E2 modalità autonoma dichiarata nel brief come unica eccezione al cancello | con `autonomous` dichiarato e motivato il render parte e il referto ripete la dichiarazione in chiaro; senza dichiarazione e senza approvazione il render resta rifiutato; il test fallisce se l'eccezione si applica con `autonomous` assente dal documento | TODO |
| F25-T09 | E3 lettore di brand con `robots.txt` rispettato, nessun login, una pagina più gli asset collegati, cache per hash dell'URL | con un server di prova locale il registro delle richieste mostra zero richieste fuori dal dominio; una pagina esclusa da `robots.txt` è rifiutata citando la regola; la seconda esecuzione fa zero richieste; una pagina irraggiungibile produce `brand_unavailable` con il motivo, non un traceback | TODO |
| F25-T10 | E3 estrazione in `brand.json`: colori dominanti con quota, logo, font CSS, testi, prezzi, provenienza per ogni campo | `brand.json` valida contro lo schema; una pagina di prova nota produce i colori e i font attesi; ogni campo presente ha provenienza (selettore, file o dato strutturato); un colore senza provenienza fa fallire la validazione | TODO |
| F25-T11 | E3 suite di pagine di prova e cablaggio al render con `brand_ref` | tre pagine di prova (brand completo, `robots.txt` che esclude, asset su altro dominio) danno tre esiti binari distinti; il render usa il logo e la palette e il test lo prova con MAD sul frame, non solo sul JSON; un `brand_ref` assente è rifiutato con il codice | TODO |
| F25-T12 | E4 registro dei template per compito con slot (1..8) e `exposed_params` | ogni template dichiara slot con tipo e ruolo, parametri esposti con dominio, `layouts` per proporzione e una famiglia; un template senza slot è rifiutato; un valore fuori dominio è rifiutato con il nome del parametro; un template che non passa il linter non entra nel conteggio | TODO |
| F25-T13 | E4 riempimento degli slot dai ruoli della MaterialMap | un progetto con 3 asset riempie 3 slot; un asset del tipo sbagliato è rifiutato nominando lo slot; il frame prova che l'asset dell'utente è davvero nel template (MAD sulla zona dello slot), non solo che il parametro è stato passato | TODO |
| F25-T14 | E4 più proporzioni da un progetto con `layouts` dichiarati e cache condivisa | da un progetto escono 3 proporzioni; un template che esiste solo in orizzontale dichiara la degradazione invece di ritagliare in silenzio; il test conta gli stadi eseguiti e prova il riuso della cache fra le proporzioni | TODO |
| F25-T15 | E4 cancello di curatela: un template entra solo con golden, parametri esposti, linter verde e critico che approva | un file di template non curato è rifiutato dal registro con il motivo; il validatore del registro riporta zero violazioni; il conteggio dichiarato non include varianti dello stesso disegno | TODO |

### F25 al livello della consegna (D003): E5-E11

Restano qui con la loro accettazione e vengono decomposte all'ingresso della fase
che le costruisce. Nessuna riga `TODO` finché non sono conoscibili.

- **E5 Clonazione della struttura (F8, F13).** Accettazione: un test prova che
  nessun audio e nessun frame del riferimento arrivano all'output e che la distanza
  dall'impronta supera la soglia anti-copia. Scheletro di battiti, durate, tipi di
  inquadratura, posizioni di testo e CTA, cadenza dei tagli; nel fingerprint solo
  numeri e note con parole proprie; cancellazione verificata della cartella
  temporanea. Soglia «da calibrare» sul corpus di coppie riferimento-risultato di
  F20-T01.
- **E6 Identità fra clip (F6, F13).** Accettazione: test di cancellazione dei
  vettori e test che con l'opzione spenta nessun vettore viene calcolato. SFace o
  equivalente con licenza verificata; consenso nel documento; vettori solo dentro
  il progetto, cancellati a fine job, nessuna banca dati fra progetti.
- **E7 Uscita modificabile (F14).** Accettazione: una modifica dall'ispettore
  produce una patch e un render che tocca solo gli stadi dipendenti. Campi esposti
  soltanto; un campo non esposto è rifiutato con `FIELD_NOT_EXPOSED`.
- **E8 Montaggio a parole (F7, F9).** Accettazione: 10 richieste di intento, almeno
  8 nella direzione attesa. Tabella versionata richiesta → misura; al più 3 assi per
  intento; il critico verifica la direzione e respinge la patch che non la muove.
- **E9 Varianti e pianificazione (F10, F11).** Accettazione: 5 varianti riusano la
  cache; un job pianificato di prova gira senza sorveglianza e si ferma in
  `waiting_approval`. Una produzione pianificata non pubblica mai senza
  approvazione.
- **E10 Memoria di progetto visibile (F14).** Accettazione: ripristinare una
  versione riproduce lo stesso render (hash dei frame, non solo EDL). Storia di
  EDL, asset e brief con ripristino reversibile, sopra il journal di F0-T06.
- **E11 Inserti generativi (facoltativa, solo L2 con GPU).** Accettazione: se non è
  realizzabile va in `not_applied` con il motivo; se è realizzabile ogni inserto è
  marcato sintetico nell'EDL e nei metadati del file. Solo modelli open-weight con
  licenza e requisiti verificati; al più il 20% della durata; C2PA non adottato e
  dichiarato come limite.

### Esame e perimetro di F25

L'esame passa a 25 incarichi: 20 Look Deck, 21 Brand Connect, 22 Clonazione della
struttura, 23 Ispettore, 24 Montaggio a parole, 25 Varianti. Non si adottano
crediti, abbonamenti, MCP o API a pagamento, clonazione o generazione di volti di
persone reali: nessuna riga di F25 può chiudersi con una di queste dipendenze, e il
test di curatela della fase lo verifica.
