# Piano - Revisione 8

Generato da scripts/gen_plan_rev8.py dal commit 30c96fc del 2026-11-02.

Copia in sola lettura: la fonte di verita' e' il repository (PLAN/MASTER_PLAN.md, PLAN/MASTER_LEDGER.md, PLAN/ANNEX_F.md). Non modificare questo file a mano.

## Dove siamo

Una riga per fase, F0..F31. Lo stato viene dalle righe di task di `PLAN/MASTER_LEDGER.md`: `DONE` se tutte le righe sono `DONE`, `PARZIALE` se qualcuna lo e', `APERTO` se nessuna, `BLOCCATO` se una riga dice `BLOCKED` (il blocco vince, perche' e' cio' che impedisce di chiudere la fase).

Le fasi F20..F24 non hanno ancora righe nel ledger: i loro task sono pronti in `PLAN/LEDGER_PATCH.md` e restano aperti. F29..F31 non hanno righe ne' sezione in nessun documento del repository: il loro titolo non si inventa.

| fase | titolo | stato | task chiusi/totali | prova |
|---|---|---|---|---|
| F0 | Fondamenta di esecuzione | BLOCCATO | 10/11 | `ca375d8`, `4670932`, `dcb4f15` +2 |
| F1 | Ponte analisi-assemblaggio e fondamentali | DONE | 4/4 | ledger: 4 righe DONE senza hash |
| F2 | EDL 3 e renderer | DONE | 14/14 | `d1c4dd4`, `32357c7`, `9327c89` +4 |
| F3 | Motion Studio (Chromium) | DONE | 8/8 | `958eabb`, `988cff5`, `d779921` +5 |
| F4 | Libreria effetti e transizioni | DONE | 10/10 | ledger: 10 righe DONE senza hash |
| F5 | Audio e musica | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md; 2 task pronti in PLAN/LEDGER_PATCH.md, tutti aperti |
| F6 | Comprensione del materiale (MaterialMap) | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F7 | Intento e dialogo | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F8 | Ricerca e apprendimento | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F9 | Critico | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F10 | Interoperabilità e consegna | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F11 | Superficie agente e job | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F12 | Indurimento ed esame finale | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F13 | Vision Pipeline e Cervello editoriale | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F14 | Review Room | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F15 | Shader, 3D e profondità | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F16 | Prestazioni | PARZIALE | 1/6 | ledger: 1 riga DONE senza hash |
| F17 | Vault e sicurezza dei dati | DONE | 10/10 | `853a306`, `58df879`, `0b9c8a4` +1 |
| F18 | Qualità di consegna e torneo A/B | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F19 | Ricette e ciclo di apprendimento | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F20 | Segnale esterno e certificazione | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md; 7 task pronti in PLAN/LEDGER_PATCH.md, tutti aperti |
| F21 | Legge delle opportunità | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md; 6 task pronti in PLAN/LEDGER_PATCH.md, tutti aperti |
| F22 | Direzione e ritmo | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md; 6 task pronti in PLAN/LEDGER_PATCH.md, tutti aperti |
| F23 | Continuità visiva | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md; 3 task pronti in PLAN/LEDGER_PATCH.md, tutti aperti |
| F24 | Interoperabilità come posizionamento | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F25 | Livello "wow" (Allegato E) | APERTO | 0/15 | nessuna prova: nessun task chiuso |
| F26 | Profili di genere: un motore, ogni tipo di video | DONE | 8/8 | ledger: 8 righe DONE senza hash |
| F27 | Preset come punto di partenza, non come stampo | DONE | 5/5 | ledger: 5 righe DONE senza hash |
| F28 | Il Motion Studio e il sistema dei font sono scollegati | DONE | 5/5 | ledger: 5 righe DONE senza hash |
| F29 | non definita nei documenti | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F30 | non definita nei documenti | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |
| F31 | non definita nei documenti | APERTO | 0/0 | nessuna riga in MASTER_LEDGER.md |

**Blocco T09** - `F0-T09` (corpus annotato) e' **BLOCCATO** nel ledger e tiene provvisorie le soglie di **F5**, **F6**, **F9** e dell'esame: finche' manca l'annotazione umana, nessuna fase che misura su quelle soglie si chiude (D010).

### Fasi dell'Allegato F (F32..F43)

Gli ID sono F32..F43 perche' F25..F28 sono gia' occupate; l'etichetta originale dell'allegato resta accanto. Lo stato viene dalla riga di sintesi in `PLAN/MASTER_LEDGER.md`, i task atomici da `PLAN/ANNEX_F.md`.

| fase | titolo | stato | task chiusi/totali | prova |
|---|---|---|---|---|
| F32 | AF4/F20 - Vincoli, affetti, autonomia | APERTO | 0/7 | ledger: riga `F32` TODO, task in PLAN/ANNEX_F.md |
| F33 | AF4/F21 - Atlante e genoma | APERTO | 0/7 | ledger: riga `F33` TODO, task in PLAN/ANNEX_F.md |
| F34 | AF4/F22 - Divergenza | APERTO | 0/5 | ledger: riga `F34` TODO, task in PLAN/ANNEX_F.md |
| F35 | AF4/F23 - Esploratore qualita'-diversita' | APERTO | 0/5 | ledger: riga `F35` TODO, task in PLAN/ANNEX_F.md |
| F36 | AF4/F24 - Pannello dei critici | APERTO | 0/5 | ledger: riga `F36` TODO, task in PLAN/ANNEX_F.md |
| F37 | AF4/F25 - Gusto | APERTO | 0/4 | ledger: riga `F37` TODO, task in PLAN/ANNEX_F.md |
| F38 | AF4/F26 - Ricerca e curiosita' | APERTO | 0/4 | ledger: riga `F38` TODO, task in PLAN/ANNEX_F.md |
| F39 | AF4/F27 - Memoria e riflessione | APERTO | 0/5 | ledger: riga `F39` TODO, task in PLAN/ANNEX_F.md |
| F40 | AF4/F28 - Stili e originalita' | APERTO | 0/4 | ledger: riga `F40` TODO, task in PLAN/ANNEX_F.md |
| F41 | AF4/F29 - Meta-creativita' | APERTO | 0/4 | ledger: riga `F41` TODO, task in PLAN/ANNEX_F.md |
| F42 | AF4/F30 - Dialogo e interfaccia | APERTO | 0/4 | ledger: riga `F42` TODO, task in PLAN/ANNEX_F.md |
| F43 | AF4/F31 - Banco di prova ed esame | APERTO | 0/5 | ledger: riga `F43` TODO, task in PLAN/ANNEX_F.md |

## Registro delle deviazioni

Ogni riga e' un punto in cui il costruito non coincide con il piano originale, con il motivo. La colonna `fonte` cita il file e l'ancora esatta: il generatore verifica che l'ancora esista ancora, quindi un documento rinominato fa fallire la generazione invece di lasciare una riga muta.

| # | cosa e' stato costruito | cosa diceva il piano | perche' | fonte |
|---|---|---|---|---|
| 1 | Schema EDL 3 additivo: `vx.edl3/1.1`, poi `vx.edl3/1.2` | `vx.edl3/2`, con un migratore 3.0 -> 3.1 | D028: un campo opzionale con default e' un aumento minore, non una migrazione. Nessun migratore; il lettore accetta `/1`, `/1.1` e `/1.2`, e i campi `x-` si conservano al round-trip. | PLAN/DISCREPANZE.md D-6; PLAN/DECISIONS_PATCH.md D028; PLAN/MASTER_LEDGER.md F26-T01 |
| 2 | `F0-T11`: matrice delle licenze con gate vincolante | `F0C`, il task che crea `LICENSES_MATRIX.md` | `F0C` non esiste nel ledger: lo stesso lavoro e' `F0-T11`, protetto da `tests/test_licenses_matrix.py` (una riga mancante o una verifica scaduta fanno fallire il test). | PLAN/DOC_VS_REPO.md riga 4; PLAN/MASTER_LEDGER.md F0-T11 |
| 3 | E1 (Look Deck) ed E5 (clonazione) nell'Allegato E e in `F25-T01`..`F25-T15` | `E1`, `E5`, `F2B` e `F17B` citati come se fossero nel documento | E1 ed E5 vivono nell'Allegato E, decomposte in F25; `F2B` e' diventato lo schema 1.1 e poi 1.2; `F17B` sono `F17-T08` e `F17-T09` (uccisioni reali e guardia di sistema sugli originali). | PLAN/DOC_VS_REPO.md riga 1; PLAN/MASTER_LEDGER.md F25; PLAN/MASTER_LEDGER.md F17-T08 |
| 4 | Terminazione reale: `taskkill /F /T`, `SIGKILL`, albero dei figli | Il segnale 9 mandato a mano (`kill` con `-9`) | La prova deve uccidere davvero processo e figli, su Windows e su POSIX, e poi riprendere dal journal: 50 uccisioni reali in `F17-T08`. | PLAN/DOC_VS_REPO.md riga 3; PLAN/DISCREPANZE.md D-16; PLAN/MASTER_LEDGER.md F17-T08 |
| 5 | Nessun tetto al numero di trattamenti: resta il tetto di cio' che si vede | il gate anti-arlecchino del board, che rifiutava piu' di 2 trattamenti distinti | D035: immaginare e' gratis, riempire lo schermo no. Il limite misura il montaggio, non le varianti che il piano ha immaginato. | PLAN/DECISIONS_PATCH.md D035 |
| 6 | `EFFECT_DENSITY`: 3 look che iniziano in una finestra di 10 s, codice duro | Un tetto di effetti visibili ogni 10 s, senza un numero | D031: il tetto vive nel compilatore e non ha deroghe, nemmeno per un EDL esplicito; i primitivi di contratto non sono look. | PLAN/DECISIONS_PATCH.md D031; PLAN/MASTER_LEDGER.md F4-T08; PLAN/MASTER_PLAN.md F4 |
| 7 | `F27` preset e `F28` font | Il piano non le aveva: sono nate leggendo il codice | Due misure: i preset erano trasportati e mai risolti; 53 template su 54 usavano un font di sistema mentre il font incluso non era usato da nessuno. | PLAN/MASTER_LEDGER.md F27; PLAN/MASTER_LEDGER.md F28 |
| 8 | Le fasi dell'Allegato F come `F32`..`F43` | L'allegato le numera `F20`..`F31` | F25..F28 sono occupate: l'ID slitta di dodici posizioni e l'etichetta originale resta accanto (`F32 (AF4/F20)`), cosi' il documento dell'utente resta citabile. | PLAN/ANNEX_F.md conflitto; PLAN/ANNEX_F.md tabella ID |
| 9 | Esame a 27 incarichi: 1..19 e 26..33, con 20..25 definiti nel ledger | Il documento si ferma a 19 e salta a 26 | 20..25 (Look Deck, Brand Connect, clonazione della struttura, ispettore, montaggio a parole, varianti) sono definiti in F25; 26..33 in `AF5`. | PLAN/DOC_VS_REPO.md riga 2; PLAN/ANNEX_F.md conteggi; PLAN/MASTER_LEDGER.md F25 |
| 10 | 32 fasi, 27 incarichi, sei addendum | "Versione 1", sezione 4 "F0-F12", sezione 6 "12 incarichi" | Le intestazioni del documento sorgente sono superate dal repository: questo file le cita solo per dire dove diverge. | PLAN/DOC_VS_REPO.md riga 5; PLAN/ANNEX_F.md conteggi |

## Fasi e task

Le fasi come le elenca `PLAN/MASTER_PLAN.md` (sezione 4 per F0..F12, Allegato A4 per F13..F19): per ognuna la consegna e l'accettazione del piano, e i task atomici che il ledger le attribuisce oggi. Una fase senza task nel ledger resta al livello della consegna, come vuole D003.

### F0 - Fondamenta di esecuzione

Consegna: ledger dei task, cache incrementale degli stage (hash di input e parametri), re-render parziale, cronologia e undo dell'EDL, log eventi, interruttori di funzione, harness a frame di riferimento, stimatore dei tempi prima del render, corpus annotato per calibrare le soglie.

Accettazione: modificare una clip o un sottotitolo riesegue solo gli stage dipendenti (il test conta gli stage eseguiti); suite verde.

| ID | stato |
|---|---|
| F0-T01 | DONE |
| F0-T02 | DONE |
| F0-T03 | DONE |
| F0-T04 | DONE |
| F0-T05 | DONE |
| F0-T06 | DONE |
| F0-T07 | DONE |
| F0-T08 | DONE |
| F0-T09 | BLOCKED |
| F0-T10 | DONE |
| F0-T11 | DONE |

### F1 - Ponte analisi-assemblaggio e fondamentali

Consegna (reference 26, backlog A): multi-A-roll in sequenza; politica di riempimento per clip (crop\_fill, blur\_band, fit) scelta e dichiarata; loudness per clip misurata sul segmento usato, senza tetto silenzioso; trascrizione che produce tagli, sottotitoli sulla tela e B-roll ancorato alla frase; silenzi dal VAD; reframe dalle tracce volto; stabilizzazione, denoise e compress in auto; SNR e music ratio misurati; dedup percettivo; deduzione della camera; B-roll sovrapposti risolti su layer; più musiche e loop della musica.

Accettazione: vx auto sulle otto clip di «Quella Sera» produce otto clip in sequenza, riempimento dichiarato, loudness per clip entro ±1 LU sul segmento, sottotitoli sulla tela, silenzi tagliati e musica con ducking, senza intervento manuale.

| ID | stato |
|---|---|
| F1-T01 | DONE |
| F1-T02 | DONE |
| F1-T03 | DONE |
| F1-T04 | DONE |

### F2 - EDL 3 e renderer

Consegna: effect con keyframe (lineare, bezier, hold), adjustment layer, text layer e caption track, sequenze annidate, transizioni, speed ramp, time remap, reverse, freeze, PiP, maschere, blend, chroma key; validatore; migratore 2.2 → 3.0; compilatore che traduce l'EDL 3 in grafi ffmpeg più overlay Chromium.

Accettazione: un documento EDL per ogni funzione, tutti renderizzano; round trip senza perdite; golden frame.

| ID | stato |
|---|---|
| F2-T01 | DONE |
| F2-T02 | DONE |
| F2-T03 | DONE |
| F2-T04 | DONE |
| F2-T05 | DONE |
| F2-T06 | DONE |
| F2-T07 | DONE |
| F2-T08 | DONE |
| F2-T09 | DONE |
| F2-T10 | DONE |
| F2-T11 | DONE |
| F2-T12 | DONE |
| F2-T13 | DONE |
| F2-T14 | DONE |

### F3 - Motion Studio (Chromium)

Consegna: Chromium headless via Playwright; template HTML, CSS, SVG, Canvas e WebGL con timeline sospesa e posizionabile su ogni istante; cattura deterministica dei frame; uscita con trasparenza (sequenza PNG, ProRes 4444 o WebM con alpha) e composizione sul video; rete bloccata, font locali, Lottie; grafici generati da dati; libreria di almeno 40 template (lower third, titoli cinetici, callout agganciati a un soggetto, barre di avanzamento, contatori, grafici, sfondi animati, sticker, card, intro e outro, transizioni a forma, 10 stili di sottotitoli animati) parametrizzabili dal brand; cache per hash; fallback Pillow. Authoring al volo: l'agente può scrivere un template nuovo, che passa da un linter (niente rete, niente script esterni, tetto di dimensione), da un'anteprima a frame e dal critico.

Accettazione: ogni template ha frame di riferimento in tre istanti; un render 1080x1920 da 5 s rispetta il budget dichiarato; un test prova che non parte nessuna richiesta di rete; parametri non validi vengono respinti.

| ID | stato |
|---|---|
| F3-T01 | DONE |
| F3-T02 | DONE |
| F3-T03 | DONE |
| F3-T04 | DONE |
| F3-T05 | DONE |
| F3-T06 | DONE |
| F3-T07 | DONE |
| F3-T08 | DONE |

### F4 - Libreria di effetti e transizioni

Consegna: registro di almeno 60 voci: transizioni (almeno 30 da xfade più whip, glitch, flash, zoom, rotazione, luma wipe, reveal a forma via Chromium), speed ramp, shake, punch-in, film look (grana, alone, vignetta), LUT 3D con match tra shot e protezione dei toni pelle, grading adattivo, split screen e layout di reazione, freeze con contorno, sfocatura e rimozione dello sfondo, testo dietro il soggetto, upscaling. Ogni voce dichiara innesco, schema dei parametri, tetto, implementazione, fallback e prova visiva.

Accettazione: il validatore del registro passa al 100%; ogni effetto ha test di innesco e frame di riferimento; il pianificatore impone un tetto di effetti visibili ogni 10 s (anti-arlecchino).

| ID | stato |
|---|---|
| F4-T01 | DONE |
| F4-T02 | DONE |
| F4-T03 | DONE |
| F4-T04 | DONE |
| F4-T05 | DONE |
| F4-T06 | DONE |
| F4-T07 | DONE |
| F4-T08 | DONE |
| F4-T09 | DONE |
| F4-T10 | DONE |

### F5 - Audio e musica

Consegna: catena voce (riduzione rumore con RNNoise o DeepFilterNet previa verifica licenza, EQ, de-esser, compressore); ducking con keyframe; motore SFX (sintesi ffmpeg più libreria CC0); musica con beat, downbeat e sezioni, tagli sul beat, estensione e loop con crossfade; loudness per clip e per bus; limiter di true peak; stem separati; 5.1 opzionale.

Accettazione: LUFS per clip entro ±1 LU; errore di allineamento al beat sotto la soglia fissata in F0 su un set annotato; nessun clipping.

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F6 - Comprensione del materiale (MaterialMap)

Consegna: per ogni asset, trascrizione a livello di parola (con speaker se disponibile), segmenti per argomento, inquadrature con descrizione (l'agente guarda i contact sheet; embedding CLIP locali per la ricerca), OCR, saliency e tracking di oggetti non volto, picchi di energia ed emozione (audio, risate, applausi), qualità per segmento (nitidezza, esposizione, rumore, tremolio), classifica dei momenti migliori, rischi (persone terze, dati sensibili), relazioni (stessa scena, stesso soggetto, duplicati), struttura della musica. Ogni voce porta evidenza e confidenza. Comando: vx understand.

Accettazione: su un corpus annotato di almeno 20 clip, segmentazione e classifica dei momenti migliori superano le soglie fissate in F0 (dichiarate prima di misurare).

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F7 - Intento e dialogo

Consegna: IntentSpec (obiettivo, piattaforma, pubblico, tono, durata, obblighi e divieti, brand, ruoli degli asset, consegne) e rilevatore di ambiguità con regola del costo dell'errore: si chiede solo se la decisione è costosa o irreversibile E non è deducibile da materiale, knowledge base o contesto. Al massimo 3 domande raggruppate, ciascuna con un default consigliato e l'opzione «decidi tu». Modalità creativa quando il brief è aperto: il Creative Brief (concept, struttura, ritmo, sistema grafico, palette, tipografia, musica, SFX, hook) nasce da MaterialMap, playbook e tendenze; le assunzioni sono dichiarate; fino a 3 varianti economiche (storyboard e animatic di 10 s) tra cui sceglie il critico o l'utente.

Accettazione: 30 richieste, 10 chiare (zero domande), 10 ambigue (al massimo 3 domande, quelle giuste), 10 aperte (nessuna domanda, concept coerente, assunzioni dichiarate), giudicate con rubrica.

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F8 - Ricerca e apprendimento

Consegna: ReferenceLab e knowledge base come da sezione 5.

Accettazione: prove con fonti simulate; cancellazione verificata; ogni aggiornamento della knowledge base ha provenienza; benchmark senza regressioni; possibilità di tornare indietro.

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F9 - Critico

Consegna: render proxy, campioni (ogni 0,5 s, su ogni taglio, su ogni testo), metriche audio, rubrica a sei assi (hook, ritmo, leggibilità, coerenza visiva, audio, aderenza al brief) con punteggio ed evidenza; correzioni come patch dell'EDL con ancora e prova; budget di tre iterazioni; stop quando una correzione peggiora il risultato; confronto prima e dopo.

Accettazione: su 10 EDL con difetti seminati (flash troppo lungo, sottotitoli fuori safe zone, ducking assente, audio sbilanciato e simili), il critico trova almeno il 90% dei difetti e ne corregge almeno l'80% senza crearne di nuovi.

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F10 - Interoperabilità e consegna

Consegna: export multi-aspect, OTIO, FCPXML ed EDL, stem, VTT e SRT, soft-sub, capitoli e metadati, ProRes, DNxHR, 10 bit, HDR con tone mapping o passthrough, testo per la pubblicazione (titolo, descrizione, hashtag per piattaforma dalla knowledge base), thumbnail scelta per volto ed emozione, watermark e brand, strumento per sfocare volti.

Accettazione: i file passano ffprobe e la validazione OTIO; ogni formato ha un test.

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F11 - Superficie agente e job

Consegna: MCP v2 (understand, intent, research, propose\_edl, apply\_edit\_ops, render\_preview, critique, deliver, memory, job) con parità totale con la CLI; coda dei job con cancellazione, ripresa, elenco, notifica di avanzamento e pulizia delle workdir vecchie.

Accettazione: test di parità tra strumenti e CLI; test di cancellazione e ripresa.

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F12 - Indurimento ed esame finale

Consegna: profilazione, politica di pulizia, audit delle licenze, revisione di sicurezza di Chromium, documentazione generata, SKILL.md sotto le 250 righe con caricamento progressivo delle reference.

Accettazione: l'esame della sezione 6 passa per intero.

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F13 - Vision Pipeline e Cervello editoriale

Consegna: campionamento adattivo, contact sheet per shot, descrizioni, embedding locali, OCR, segmentazione, cercatore di match cut, speed ramp sui picchi di movimento, colpi sul beat drop, struttura di setup e payoff, ciclo divergente e convergente, vincolo di diversità.

Accettazione: su 20 clip il cercatore propone almeno 5 match cut e il critico ne approva almeno 3 con motivazione; la seconda analisi dello stesso materiale non rifa il lavoro (tempo di analisi vicino a zero, provato da un test).

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F14 - Review Room

Consegna: pagina HTML locale con player del proxy, timeline dell'EDL, confronto prima e dopo, commenti al timecode, esportazione del feedback in JSON e suo ingresso come patch.

Accettazione: commenti su 5 timecode diversi diventano 5 patch applicate e verificate dal critico.

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F15 - Shader, 3D e profondità

Consegna: importazione di almeno 100 transizioni gl-transitions con test a frame di riferimento; authoring di shader GLSL in sandbox; scene Three.js; parallasse da mappa di profondità; ponte opzionale con Blender; rendering software di WebGL come ripiego.

Accettazione: una transizione richiesta a parole (per esempio «come un'onda di inchiostro») viene scritta, compilata, vista, corretta e usata; lo stesso test passa su una macchina senza GPU.

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F16 - Prestazioni

Consegna: render a segmenti paralleli, smart render con test di identità dei segmenti riusati, scelta dell'encoder hardware per prova, intermedi lossless, benchmark ufficiale versionato.

Accettazione: raggiunti gli obiettivi di velocità del punto 5 di A3 (o ricalibrati e motivati in DECISIONS.md); lo smart render produce un file identico bit a bit nelle parti non modificate.

| ID | stato |
|---|---|
| F16-T01 | DONE |
| F16-T02 | TODO |
| F16-T03 | TODO |
| F16-T04 | TODO |
| F16-T05 | TODO |
| F16-T06 | TODO |

### F17 - Vault e sicurezza dei dati

Consegna: originali aperti in sola lettura e mai scritti; scritture solo in directory con file marcatore .vx-owned e cancellazioni solo lì; cestino con quarantena di 7 giorni per i file della skill (il materiale di terzi del ReferenceLab si cancella subito); controllo dello spazio prima di ogni render, con rifiuto se non basta; journal write-ahead per EDL e ledger; snapshot periodico dello stato di progetto (EDL, MaterialMap, knowledge, senza i media); verifica degli sha256 all'apertura e prima della consegna per scoprire il bit-rot; dry-run per ogni operazione distruttiva; limiti di tempo, memoria e CPU per ogni sottoprocesso; test di fuzz con file multimediali corrotti o ostili; catena di fornitura con hash e checksum dei modelli.

Accettazione: dopo 50 uccisioni casuali del processo (terminazione reale: `taskkill /F /T` su Windows, `SIGKILL` su POSIX, con l'albero dei figli, durante render e scrittura) gli sha256 delle sorgenti sono invariati, nessun file di progetto è corrotto e il lavoro riprende dall'ultimo checkpoint; un test con una directory esca prova che nulla viene cancellato fuori dalle directory marcate; 200 file di fuzz non causano errori non gestiti.

| ID | stato |
|---|---|
| F17-T01 | DONE |
| F17-T02 | DONE |
| F17-T03 | DONE |
| F17-T04 | DONE |
| F17-T05 | DONE |
| F17-T06 | DONE |
| F17-T07 | DONE |
| F17-T08 | DONE |
| F17-T09 | DONE |
| F17-T10 | DONE |

### F18 - Qualità di consegna e torneo A/B

Consegna: codifica per titolo con VMAF, gestione esplicita del colore, torneo a coppie tra varianti (l'agente giudica due volte con ordine scambiato per eliminare il bias di posizione, poi punteggio Elo).

Accettazione: su 10 montaggi la codifica per titolo ottiene VMAF almeno 95 con dimensione non superiore a quella del CRF fisso; il torneo concorda con il giudizio dell'utente almeno 7 volte su 10 su coppie di prova (soglia da calibrare in F0).

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### F19 - Ricette e ciclo di apprendimento

Consegna: libreria di ricette parametriche con innesco e prova visiva, promozione di una ricetta solo con punteggio del critico e valutazione dell'utente sopra soglia, versioni annullabili, ispezione e cancellazione da parte dell'utente.

Accettazione: dopo 10 lavori valutati, il benchmark mostra che l'uso delle ricette non peggiora nessun asse della rubrica e ne migliora almeno uno; ogni ricetta è spiegabile (da quali lavori nasce).

Nessun task atomico nel ledger: la fase resta al livello della consegna.

### Fasi presenti solo nel ledger (F25..F28)

Il piano non le elenca: sono nate dopo, e il ledger e' la sola fonte.

| fase | titolo | task chiusi/totali |
|---|---|---|
| F25 | Livello "wow" (Allegato E) | 0/15 |
| F26 | Profili di genere: un motore, ogni tipo di video | 8/8 |
| F27 | Preset come punto di partenza, non come stampo | 5/5 |
| F28 | Il Motion Studio e il sistema dei font sono scollegati | 5/5 |

### Allegato F: F32..F43 (da PLAN/ANNEX_F.md)

I task come li trascrive l'allegato, con l'etichetta originale accanto.

#### F32 (AF4/F20) - Vincoli, affetti, autonomia

- T01 schema `vx.constraints/1` (duro/morbido/libero, frase, interpretazione, proxy, verifica) — 30 casi.
- T02 compilatore prompt -> registro, copertura delle frasi — 200 prompt it/en, 100%, zero dure violate.
- T03 risolutore di contraddizioni — 40 coppie, >= 85% accettabili.
- T04 dizionario dei limiti (elegante, pacchiano, energico...) -> proxy — >= 300 termini.
- T05 modello degli affetti calibrato su annotazione utente (30 clip) — correlazione riportata; **BLOCCATO-UTENTE**.
- T06 autonomia 0-5, costituzione creativa, licenza di rottura — 1.000 casi, zero linee rosse violate.
- T07 cartolina d'intenzione prima del montaggio, con referto di fedelta'.

Stato nel ledger: **TODO**.

#### F33 (AF4/F21) - Atlante e genoma

- T01 schema `vx.atlas/1`: voce senza proxy o implementazione rifiutata.
- T02 genoma 16 famiglie, serializzato nell'EDL via `preset_ref` ed `ext`.
- T03 compilatore genoma -> EDL — 10.000 genomi, zero EDL invalidi.
- T04 popolamento a ondate, generazione assistita, validazione automatica, verifica a vista su 5% stratificato — >= 5.000 voci, >= 2.500 validate al primo rilascio; **verifica a vista BLOCCATA-UTENTE**.
- T05 contatore delle possibilita' — esatto su sottospazi piccoli.
- T06 campionatore diversificato (Sobol / ipercubo latino / stratificato) — copertura su 1.000 campioni.
- T07 mappa 2D nella Review Room con navigazione direzionale — 20 comandi, verso atteso.

Stato nel ledger: **TODO**.

#### F34 (AF4/F22) - Divergenza

- T01 >= 12 operatori creativi (inversione, esagerazione, rimozione, combinazione, sostituzione, analogia, rilassamento, mutazione, vincolo artificiale...).
- T02 8-12 concept per brief con motivazione, coordinate, distanza minima misurata.
- T03 >= 3 analogie da domini non video per brief, con fonte.
- T04 mutazione di regola (livello 5) con linee rosse.
- T05 rilevatore di cliche' su archivio proprio e snapshot, lista versionata.
- Prova: 90% dei set supera la distanza minima; test cieco su 10 brief, >= 8 giudicati diversi.

Stato nel ledger: **TODO**.

#### F35 (AF4/F23) - Esploratore qualita'-diversita'

- T01 descrittori e griglia; T02 animatic a bassa risoluzione con budget; T03 modello surrogato con errore fuori campione; T04 MAP-Elites con archivio persistente e ripresa; T05 modalita' Atelier con tetto di risorse.
- Prova: 300 nicchie in 30 minuti (da calibrare); successo dei finalisti > campionamento casuale con test statistico; archivio che sopravvive a un crash.

Stato nel ledger: **TODO**.

#### F36 (AF4/F24) - Pannello dei critici

- T01 rubriche per ruolo, versionate; T02 misure numeriche per asse; T03 avvocato del diavolo e accordo riportato; T04 torneo a coppie con ordine scambiato ed Elo; T05 calibrazione sul giudizio umano — **BLOCCATO-UTENTE**.
- Prova: distingue 10 EDL difettosi e 10 buoni nel 90% dei casi.

Stato nel ledger: **TODO**.

#### F37 (AF4/F25) - Gusto

- T01 coppie nella Review Room, max 20 all'avvio (**BLOCCATO-UTENTE**); T02 Bradley-Terry per contesto con incertezza; T03 passaporto esportabile e cancellabile; T04 deriva e sovradattamento con diversita' minima garantita.
- Prova: accuratezza sopra il caso dopo 20 coppie (indicativamente 65%, da calibrare); cancellazione verificata.

Stato nel ledger: **TODO**.

#### F38 (AF4/F26) - Ricerca e curiosita'

- T01 3-5 domande per brief; T02 analogie con le regole dell'Allegato D; T03 estrazione di affermazioni e proposte di voci (>= 3 fonti indipendenti o verifica a vista); T04 budget di curiosita' verso le nicchie vuote.
- Prova: fonti simulate, nessuna chiamata fuori dai domini consentiti, provenienza su ogni voce.

Stato nel ledger: **TODO**.

#### F39 (AF4/F27) - Memoria e riflessione

- T01 memoria episodica; T02 dimenticanza regolata e trasparenza; T03 dichiarazione d'intenzione, monitoraggio, post-mortem; T04 rilevatore di sorpresa e lezioni; T05 diario leggibile.
- Prova: dopo 10 lavori una lezione cambia il comportamento, misurato A/B, differenza positiva su almeno un asse e nessuna regressione.

Stato nel ledger: **TODO**.

#### F40 (AF4/F28) - Stili e originalita'

- T01 impronte -> coordinate del genoma; T02 algebra degli stili; T03 guardia di originalita' (distanza + confronto percettivo, frame dei riferimenti non conservati, blocco sotto soglia); T04 dichiarazione dei contenuti sintetici.
- Prova: un output da riferimento non contiene frame ne' audio del riferimento e supera la soglia di distanza.

Stato nel ledger: **TODO**.

#### F41 (AF4/F29) - Meta-creativita'

- T01 bandit (Thompson sampling) con registro; T02 sandbox di invenzione con linter, test, prova visiva; T03 promozione solo se il benchmark non peggiora, ritiro di cio' che non rende; T04 registro delle invenzioni.
- Prova: >= 5 invenzioni, >= 2 promosse, zero regressioni, nessuna uscita dalla sandbox (test di rete e filesystem).

Stato nel ledger: **TODO**.

#### F42 (AF4/F30) - Dialogo e interfaccia

- T01 max 3 domande, solo a costo d'errore alto, sempre con default; T02 spiegazioni di una riga; T03 cartolina e mappa con "piu' cosi'"; T04 critica dell'utente come vincolo persistente.
- Prova: 30 conversazioni, mai piu' di 3 domande, nessuna domanda su cio' che il contesto diceva.

Stato nel ledger: **TODO**.

#### F43 (AF4/F31) - Banco di prova ed esame

- T01 12 brief x 3 modalita' (letterale, creativa L2, creativa L4); T02 metriche (aderenza, mestiere, novita', sorpresa utile, coerenza, cliche', copertura); T03 test ciechi a coppie (**BLOCCATO-UTENTE**); T04 regressioni automatiche; T05 FINAL_REPORT creativo.

Stato nel ledger: **TODO**.


## Esame

27 incarichi: 1..19 (sezione 6 del piano piu' A5) e 26..33 (`AF5` dell'Allegato F). La numerazione salta 20..25 perche' e' quella del documento sorgente; quegli incarichi esistono e sono definiti nel ledger (F25), non nel piano: sono elencati sotto la tabella.

| # | incarico | fonte |
|---|---|---|
| 1 | «Quella Sera»: 8 clip, verticale nativo, senza fascia sfocata, loudness per clip entro ±1 LU, sottotitoli sulla tela, silenzi tagliati, ducking. | sezione 6 |
| 2 | Talking head di 60 s trasformato in reel con sottotitoli animati, hook ricavato dal contenuto e reframe. | sezione 6 |
| 3 | Podcast di 30 minuti ridotto a 3 clip con titolo. | sezione 6 |
| 4 | Spot di 15 s da 10 clip con musica sul beat e brand kit. | sezione 6 |
| 5 | Tutorial in screen recording da 10 minuti ripulito, con capitoli, zoom sui click e lower third. | sezione 6 |
| 6 | Recap di un evento con musica, titoli e color match. | sezione 6 |
| 7 | Richiesta vaga («fammi qualcosa di bello con questo girato»): al massimo una domanda, concept con assunzioni dichiarate, 3 animatic. | sezione 6 |
| 8 | Richiesta ambigua («fallo più dinamico» su un'intervista): al massimo 2 domande pertinenti. | sezione 6 |
| 9 | «Come questo reel» con un file di riferimento fornito dall'utente: imitazione dello stile, impronta salvata, cancellazione verificata. | sezione 6 |
| 10 | Reel per una palestra con ricerca di tendenze: playbook con fonti e date e nessun download dalle piattaforme. | sezione 6 |
| 11 | Intervista a due camere: sincronizzazione e cambio camera sull'oratore attivo. | sezione 6 |
| 12 | Spiegazione di dati con grafici animati e testo cinetico, con un template creato al volo. | sezione 6 |
| 13 | Prova di crash: terminazione reale del processo a metà render (con i figli), ripresa, sorgenti intatti, nessun file orfano. | A5 |
| 14 | Macchina senza GPU: gli incarichi 2 e 6 completano, e il referto indica i livelli usati. | A5 |
| 15 | Montaggio a match cut su 20 clip, come da F13. | A5 |
| 16 | Transizione scritta al volo in GLSL, come da F15. | A5 |
| 17 | Review Room: feedback dell'utente convertito in patch, come da F14. | A5 |
| 18 | Velocità: benchmark ufficiale di F16. | A5 |
| 19 | Materiale lungo: 4 ore di riprese, 3 clip finali, con analisi riusata alla seconda richiesta. | A5 |
| 26 | Brief vincolato: registro corretto, contraddizione risolta e dichiarata, zero violazioni. | AF5 |
| 27 | Brief aperto: 8-12 concept distanti, 3 animatic, scelta motivata. | AF5 |
| 28 | Esplorazione: >= 300 nicchie, mappa funzionante, navigazione che sposta davvero i descrittori. | AF5 |
| 29 | Gusto: 20 coppie dell'utente e previsione sopra soglia. | AF5 |
| 30 | Riflessione: un post-mortem con una lezione che cambia un lavoro successivo. | AF5 |
| 31 | Invenzione: un effetto nuovo promosso dal ciclo di meta-creativita'. | AF5 |
| 32 | Stile: stile da riferimento fornito, output originale sopra soglia, cancellazione verificata. | AF5 |
| 33 | Test cieco: 12 brief x 3 modalita'; la creativa batte la letterale in >= 70% delle coppie, senza violazioni ne' cliche'. | AF5 |

**20..25, definiti nel ledger (`PLAN/MASTER_LEDGER.md`, F25 "Esame e perimetro di F25"):**

- `20` - Look Deck
- `21` - Brand Connect
- `22` - Clonazione della struttura
- `23` - Ispettore
- `24` - Montaggio a parole
- `25` - Varianti

<!-- APPENDICE: TESTO STORICO (non operativo) -->

## Storico

Testo storico, non operativo: superato dall'Allegato D.

Precedenza (`PLAN/PRECEDENCE.md`): l'Allegato D supera C3 per la parte dei provider e ogni uso di API di social; l'Allegato C supera B2, B5, B6 e B7.

### B2. Fonti, distinte per tipo di segnale

> **NON SEGUIRE: superato dall'Allegato D.** Il testo resta per memoria storica.

Ogni fonte insegna una cosa diversa, e la skill deve saperlo:

- Segnali di mercato (quali temi funzionano, durate tipiche, schemi dei titoli, hashtag, velocità di crescita): ricerca web (articoli, linee guida ufficiali, rapporti) e YouTube Data API v3. L'API restituisce metadati (titolo, durata, visualizzazioni, like, data, categoria, miniatura), non i file video: da lì non si ricava la struttura del montaggio.
- Struttura di montaggio (ritmo dei tagli, sottotitoli, colore, musica, hook): analisi locale e transitoria di video con licenza aperta (Wikimedia Commons, Pexels, Pixabay, PeerTube, Internet Archive), dei file che l'utente fornisce e, se presenti nella cartella del progetto, delle statistiche dei suoi account (Insights, Analytics).
- Le miniature restituite dall'API si analizzano in memoria (presenza di testo e volti, contrasto, colore) e non si conservano.
- Non esiste un modo lecito e automatico di analizzare il montaggio dei video di Instagram, TikTok o YouTube senza i file. La skill lo dichiara nel referto (not\_applied) invece di fingere di averlo fatto.

### B5. Passi automatici di ogni lavoro

> **NON SEGUIRE: superato dall'Allegato D.** Il testo resta per memoria storica.

1. Se esiste uno snapshot valido per piattaforma e genere, lo usa.
2. Altrimenti lancia la ricerca (web, e API se c'è la chiave) con limite di tempo e di quota.
3. Per la struttura di montaggio usa le impronte di stile già in knowledge base e, se servono, analizza in locale nel ReferenceLab video con licenza aperta, con cancellazione verificata.
4. Aggiorna la knowledge base e scrive nel referto le fonti usate e quelle saltate, con il motivo.
5. Il critico verifica che il montaggio non copi un riferimento: la distanza dalle impronte usate deve superare una soglia fissata in F0.

### B6. Accettazione (F8 bis) - SUPERATO da C

> **NON SEGUIRE: superato dall'Allegato C.** Il testo resta per memoria storica.

- Con un server API simulato: la quota viene rispettata, la cache funziona (la seconda richiesta non fa nessuna chiamata) e la chiave non compare in nessun file generato (test che la cerca nei log, nei referti e nel progetto).
- Senza chiave: il lavoro si completa e il referto dichiara la fonte saltata.
- Un test di rete con elenco dei domini consentiti prova che non parte nessuna richiesta verso Instagram o TikTok e che non avviene nessun login.
- L'incarico 10 dell'esame (reel per una palestra) passa con uno TrendSnapshot datato e con le fonti.
- I file transitori vengono cancellati con verifica.

### B7. Addendum al prompt (da incollare in coda alla sezione 9) - SUPERATO da C

> **NON SEGUIRE: superato dall'Allegato C.** Il testo resta per memoria storica.

Il Trend Radar (Allegato B) sostituisce ogni riferimento alla ricerca sui social. Nessun login, nessuna credenziale, nessun accesso automatizzato a Instagram o TikTok, nessun download da piattaforme che lo vietano. La YouTube Data API si usa solo con la chiave in VX\_YOUTUBE\_API\_KEY: la chiave non compare mai in repository, log, referti o prompt, e senza chiave si salta la fonte dichiarandolo. Il Trend Radar parte da solo a ogni lavoro con piattaforma, genere o tema, come descritto in B5. Implementalo in F8 e verifica tutto B6 prima di chiudere la fase.

### C3. Mappa dei provider noti - SUPERATO IN PARTE da D (TikTok, Instagram, fornitori terzi) (da verificare dal vivo: è il compito della sonda)

> **NON SEGUIRE: superato dall'Allegato D** per TikTok, Instagram e fornitori terzi.

- YouTube. Data API: S2 e S1 (metadati e miniature). Gemini su URL pubblico: S4 approssimato, gratuito con un tetto giornaliero di ore di video, previa lettura dei termini del livello gratuito.
- TikTok. Creative Center pubblico: S1 sugli hashtag (gli altri feed risultano ridotti in un aggiornamento recente e vanno verificati). oEmbed ufficiale su un URL di video: S3 (didascalia con hashtag, autore, miniatura, nome del suono), senza metriche e senza file. Commercial Content e Ad Library: annunci, non contenuti organici, con accesso all'API su domanda. Research API: riservata a istituzioni accademiche e non profit, quindi non disponibile.
- Instagram. API Graph, ricerca per hashtag e Business Discovery su account professionali: S2 (like, commenti, visualizzazioni) e, solo dove l'API restituisce l'URL del file, S4. Il file manca per i video con audio con licenza (compreso quello della libreria di Instagram) e per i reel con download disattivato. Richiede un account Instagram professionale collegato a una Pagina Facebook e un'app Meta; per un uso pubblico serve la revisione dell'app. Limite di 30 hashtag unici ogni 7 giorni.
- Fornitori terzi (MCP o API a crediti): spesso S1-S3. Opzionali, spenti di default, con tetto di spesa zero e conferma esplicita prima di qualunque costo. SUPERATO da D: nessun fornitore a pagamento, nemmeno come opzione.
- Web: S0.
