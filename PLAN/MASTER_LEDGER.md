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
| F2-T10 | W8 schema v2 | EDL 3 `vx.edl3/2`: `preset_ref` risolto da registro, `stack` di camera (max 3, regola di composizione dichiarata), `template_ref` con `exposed_params` (il documento salva solo i valori), `synthetic`, `provenance` per ogni scelta | un documento con i cinque campi valida e fa round-trip; uno `stack` di 4 movimenti e' rifiutato con codice; un trattamento senza `provenance` e' rifiutato; un documento `vx.edl3/1` resta leggibile e dichiara la migrazione | TODO |
| F2-T11 | W8 migratore v2 | 2.2 -> `vx.edl3/2` e riallineamento dei golden EDL 3 | un documento 2.2 reale, migrato, produce lo stesso render del 2.2 originale a parita' di frame entro la tolleranza dichiarata (MAD <= 0.001); i 34 frame golden esistenti restano identici o vengono rigenerati con un atto esplicito e il secondo giro e' byte-identico | TODO |
| F2-T12 | W8 backend e regola di scelta | ogni voce del registro dichiara backend e regola per numero di keyframe e durata | un test generato da `EFFECT_REGISTRY` prova che nessuna voce resta senza regola: <=3 keyframe -> expr, oltre -> sendcmd, overlay per i testi; una voce senza regola fa fallire il test | TODO |
| F2-T13 | W8 speed ramp e limiti | politica audio dello speed ramp nel contratto e tetto dichiarato per reverse e freeze | la politica (atempo a scalini, pitch mantenuto) e' in DECISIONS e il test prova che il picco spettrale resta entro la tolleranza dichiarata; reverse o freeze oltre il tetto falliscono con un codice che nomina il tetto | TODO |
| F2-T14 | W8 fetta verticale v2 | un documento minimo con un effetto a 2 keyframe e una transizione, dal validatore al frame di riferimento | il documento valida, rende e produce un frame di riferimento byte-identico in due esecuzioni; nessuna ondata successiva parte prima che questo sia verde | TODO |

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
| F17-T08 | Prova di crash a livello di sistema | 50 terminazioni reali durante una scrittura, con un figlio ffmpeg vivo: il journal resta leggibile, il file mezzo scritto e' un orfano dichiarato, il figlio non sopravvive; gli originali sono protetti con attributo di sola lettura e ACL di Windows, e una scrittura fallisce davvero | TODO |


Codifica per titolo con VMAF, gestione colore, torneo a coppie con ordine
scambiato, punteggio Elo.

## F19 — Ricette e ciclo di apprendimento
Ricette parametriche con innesco e prova visiva, promozione sopra soglia,
versioni annullabili, ispezione e cancellazione.

---

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
