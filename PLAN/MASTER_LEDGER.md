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
| F0-T06 | Storia e undo dell'EDL: journal append-only con checkpoint e ripristino | dopo N modifiche, `undo` torna allo stato precedente e l'hash del documento coincide | WIP |
| F0-T07 | Harness a frame di riferimento (golden frame) | un test fallisce se un frame cambia oltre la tolleranza dichiarata; due render identici danno lo stesso hash | DONE `1fea22a` — 21 controlli, provato anche su master reale (4 frame, hash identici) |
| F0-T08 | Stimatore dei tempi prima del render | su un set di prova l'errore mediano sta sotto la soglia dichiarata; il valore compare nel referto | DONE `1fea22a` — 20 controlli, erede mediano 0% sul punto tenuto fuori |
| F0-T09 | Corpus annotato per calibrare le soglie | il corpus esiste con annotazioni e un test verifica che le soglie dichiarate siano derivate da esso | BLOCKED — vedi sotto |

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
| F1-T02 | Compress automatico con finding | compress applicato solo con finding; assenza dichiarata con la misura | TODO |
| F1-T03 | SNR e music ratio nel referto | i due valori compaiono in `auto_report.json` con la soglia usata | TODO |
| F1-T04 | Più musiche con più tracce | due tracce musicali mixate con ducking distinto e dichiarate | TODO |

## F2 — EDL 3 e renderer
Effect + keyframe (linear/bezier/hold), adjustment layer, text layer e caption
track, sequenze annidate, transizioni, speed ramp, time remap, reverse, freeze,
PiP, maschere, blend, chroma key; validatore; migratore 2.2→3.0; compilatore
EDL 3 → ffmpeg + overlay Chromium.
Accettazione: un documento EDL per ogni funzione, tutti renderizzano; round trip
senza perdite; golden frame. → decomporre all'ingresso F2.

## F3 — Motion Studio (Chromium)
Playwright headless, timeline sospesa, cattura deterministica, alpha, 40+
template, authoring al volo con linter, cache, fallback Pillow.
Accettazione: golden frame in tre istanti per template; budget 5 s 1080x1920;
test che nessuna richiesta di rete parte; parametri invalidi respinti.

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

## F18 — Qualità di consegna e torneo A/B
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
| FX-T01 | Provider font con sonda di disponibilità e licenza registrata per ogni font | senza rete: provider `unavailable` dichiarato, il lavoro prosegue con i font inclusi | TODO |
| FX-T02 | Ricerca per criteri (genere, tono, peso, licenza commerciale) con cache locale | la stessa ricerca due volte non fa due chiamate; il risultato cita la pagina del font e la licenza | TODO |
| FX-T03 | Proposta all'utente con anteprima renderizzata | la proposta mostra il testo reale del progetto sul font, con licenza e alternativa | TODO |
| FX-T04 | Scelta autonoma: regola deterministica quando l'utente non risponde | brief aperto + nessuna risposta = font scelto dal profilo con motivazione dichiarata; licenza non commerciale mai scelta in autonomia | TODO |
| FX-T05 | Integrazione nel Creative Brief e nel render ASS | il font scelto arriva a `fontsdir` e al `Fontname` senza sostituzioni; sostituzione = `QCFailure` | TODO |
| FX-T06 | Licenze: matrice e attribuzione | ogni font usato ha licenza, fonte e data in `LICENSES_MATRIX.md`; un font senza licenza commerciale non entra in un lavoro di committenza | TODO |
