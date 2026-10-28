# video-editor: Piano Maestro e Prompt di Esecuzione

Versione 1 · 27 ottobre 2026 · per un agente di coding (OpenCode) che lavora sul repository video-editor-skill

## 0. Come si usa

Il documento ha due parti. Il PIANO (sezioni 1-8) dice cosa costruire, perché e come si verifica. Il PROMPT (sezione 9) è il testo da incollare nell'agente così com'è. Prima di partire, copia il piano nel repository come PLAN/MASTER\_PLAN.md: il prompt lo usa come fonte di verità.

## 1. Obiettivo e limiti onesti

La skill deve diventare un montatore che: (1) capisce il materiale che ha; (2) capisce la richiesta e chiede solo quando serve; (3) quando il brief è aperto, inventa un approccio sensato e lo dichiara; (4) crea effetti, grafiche, transizioni e animazioni quasi senza limiti; (5) si documenta su cosa funziona e impara; (6) guarda il proprio lavoro e lo corregge.

Limiti da dichiarare, non da nascondere:

- «Possibilità infinite» significa un insieme di primitive componibili più un motore grafico Chromium programmabile, dove l'agente può scrivere un template nuovo al volo. Ciò che non è realizzabile finisce in not\_applied, mai in silenzio.
- «Potenza di DaVinci o Filmora» significa parità di risultato sui casi d'uso comuni e avanzati, non una GUI né una timeline visuale.
- «Imparare» significa conoscenza persistente e verificata (playbook, impronte di stile, esiti, preferenze), non addestramento di pesi.
- La ricerca in rete segue le regole legali della sezione 5.

## 2. Architettura target

Un ciclo unico, con l'EDL 3 come documento centrale:

COMPRENDERE (MaterialMap) → INTENDERE (IntentSpec) → DECIDERE (Creative Brief + EDL 3) → COMPORRE (effetti, grafica, audio) → CRITICARE (guarda e corregge) → RICORDARE (knowledge base)

Nuovi moduli: understand/, intent/, research/, knowledge/, motion\_studio/, fx/, critic/, bridge/. Superficie agente: MCP v2 più CLI con parità completa.

## 3. Regole non negoziabili

1. Legge dell'evidenza estesa. Ogni scelta (taglio, effetto, grafica, musica, stile) cita un'evidenza: misura, intervallo della MaterialMap, istruzione dell'utente, voce della knowledge base con confidenza, oppure assunzione creativa dichiarata con motivazione. Senza evidenza niente azione.
2. Dichiarato è eseguito. Ogni capacità documentata è raggiungibile da CLI e MCP e coperta da un test. Ciò che non si applica va in not\_applied.
3. Prova visiva. Ogni effetto e ogni grafica hanno test a frame di riferimento (golden frame) e una verifica in cui l'agente guarda i frame, con il percorso dell'evidenza registrato.
4. Sorgente intatto, sha256 all'ingresso e all'uscita.
5. Determinismo. Stesso input e stesso seme danno lo stesso output entro una tolleranza dichiarata. Il rendering Chromium usa tempo virtuale.
6. Legalità e privacy come da sezione 5.
7. Budget di tempo e memoria dichiarati e misurati.
8. Portabilità Windows, Linux, macOS. Nessun percorso o nome di macchina nel codice.
9. Sicurezza. Chromium senza rete, profilo temporaneo, nessuno script esterno nei template.

## 4. Piano per fasi (F0-F12)

Ogni fase ha obiettivo, consegna e accettazione binaria. Le fasi sono ordinate per dipendenza, ma il lavoro è uno solo: finisce quando sono chiuse tutte.

### F0. Fondamenta di esecuzione

Consegna: ledger dei task, cache incrementale degli stage (hash di input e parametri), re-render parziale, cronologia e undo dell'EDL, log eventi, interruttori di funzione, harness a frame di riferimento, stimatore dei tempi prima del render, corpus annotato per calibrare le soglie.

Accettazione: modificare una clip o un sottotitolo riesegue solo gli stage dipendenti (il test conta gli stage eseguiti); suite verde.

### F1. Ponte analisi-assemblaggio e fondamentali

Consegna (reference 26, backlog A): multi-A-roll in sequenza; politica di riempimento per clip (crop\_fill, blur\_band, fit) scelta e dichiarata; loudness per clip misurata sul segmento usato, senza tetto silenzioso; trascrizione che produce tagli, sottotitoli sulla tela e B-roll ancorato alla frase; silenzi dal VAD; reframe dalle tracce volto; stabilizzazione, denoise e compress in auto; SNR e music ratio misurati; dedup percettivo; deduzione della camera; B-roll sovrapposti risolti su layer; più musiche e loop della musica.

Accettazione: vx auto sulle otto clip di «Quella Sera» produce otto clip in sequenza, riempimento dichiarato, loudness per clip entro ±1 LU sul segmento, sottotitoli sulla tela, silenzi tagliati e musica con ducking, senza intervento manuale.

### F2. EDL 3 e renderer

Consegna: effect con keyframe (lineare, bezier, hold), adjustment layer, text layer e caption track, sequenze annidate, transizioni, speed ramp, time remap, reverse, freeze, PiP, maschere, blend, chroma key; validatore; migratore 2.2 → 3.0; compilatore che traduce l'EDL 3 in grafi ffmpeg più overlay Chromium.

Accettazione: un documento EDL per ogni funzione, tutti renderizzano; round trip senza perdite; golden frame.

### F3. Motion Studio (Chromium)

Consegna: Chromium headless via Playwright; template HTML, CSS, SVG, Canvas e WebGL con timeline sospesa e posizionabile su ogni istante; cattura deterministica dei frame; uscita con trasparenza (sequenza PNG, ProRes 4444 o WebM con alpha) e composizione sul video; rete bloccata, font locali, Lottie; grafici generati da dati; libreria di almeno 40 template (lower third, titoli cinetici, callout agganciati a un soggetto, barre di avanzamento, contatori, grafici, sfondi animati, sticker, card, intro e outro, transizioni a forma, 10 stili di sottotitoli animati) parametrizzabili dal brand; cache per hash; fallback Pillow. Authoring al volo: l'agente può scrivere un template nuovo, che passa da un linter (niente rete, niente script esterni, tetto di dimensione), da un'anteprima a frame e dal critico.

Accettazione: ogni template ha frame di riferimento in tre istanti; un render 1080x1920 da 5 s rispetta il budget dichiarato; un test prova che non parte nessuna richiesta di rete; parametri non validi vengono respinti.

### F4. Libreria di effetti e transizioni

Consegna: registro di almeno 60 voci: transizioni (almeno 30 da xfade più whip, glitch, flash, zoom, rotazione, luma wipe, reveal a forma via Chromium), speed ramp, shake, punch-in, film look (grana, alone, vignetta), LUT 3D con match tra shot e protezione dei toni pelle, grading adattivo, split screen e layout di reazione, freeze con contorno, sfocatura e rimozione dello sfondo, testo dietro il soggetto, upscaling. Ogni voce dichiara innesco, schema dei parametri, tetto, implementazione, fallback e prova visiva.

Accettazione: il validatore del registro passa al 100%; ogni effetto ha test di innesco e frame di riferimento; il pianificatore impone un tetto di effetti visibili ogni 10 s (anti-arlecchino).

### F5. Audio e musica

Consegna: catena voce (riduzione rumore con RNNoise o DeepFilterNet previa verifica licenza, EQ, de-esser, compressore); ducking con keyframe; motore SFX (sintesi ffmpeg più libreria CC0); musica con beat, downbeat e sezioni, tagli sul beat, estensione e loop con crossfade; loudness per clip e per bus; limiter di true peak; stem separati; 5.1 opzionale.

Accettazione: LUFS per clip entro ±1 LU; errore di allineamento al beat sotto la soglia fissata in F0 su un set annotato; nessun clipping.

### F6. Comprensione del materiale (MaterialMap)

Consegna: per ogni asset, trascrizione a livello di parola (con speaker se disponibile), segmenti per argomento, inquadrature con descrizione (l'agente guarda i contact sheet; embedding CLIP locali per la ricerca), OCR, saliency e tracking di oggetti non volto, picchi di energia ed emozione (audio, risate, applausi), qualità per segmento (nitidezza, esposizione, rumore, tremolio), classifica dei momenti migliori, rischi (persone terze, dati sensibili), relazioni (stessa scena, stesso soggetto, duplicati), struttura della musica. Ogni voce porta evidenza e confidenza. Comando: vx understand.

Accettazione: su un corpus annotato di almeno 20 clip, segmentazione e classifica dei momenti migliori superano le soglie fissate in F0 (dichiarate prima di misurare).

### F7. Intento e dialogo

Consegna: IntentSpec (obiettivo, piattaforma, pubblico, tono, durata, obblighi e divieti, brand, ruoli degli asset, consegne) e rilevatore di ambiguità con regola del costo dell'errore: si chiede solo se la decisione è costosa o irreversibile E non è deducibile da materiale, knowledge base o contesto. Al massimo 3 domande raggruppate, ciascuna con un default consigliato e l'opzione «decidi tu». Modalità creativa quando il brief è aperto: il Creative Brief (concept, struttura, ritmo, sistema grafico, palette, tipografia, musica, SFX, hook) nasce da MaterialMap, playbook e tendenze; le assunzioni sono dichiarate; fino a 3 varianti economiche (storyboard e animatic di 10 s) tra cui sceglie il critico o l'utente.

Accettazione: 30 richieste, 10 chiare (zero domande), 10 ambigue (al massimo 3 domande, quelle giuste), 10 aperte (nessuna domanda, concept coerente, assunzioni dichiarate), giudicate con rubrica.

### F8. Ricerca e apprendimento

Consegna: ReferenceLab e knowledge base come da sezione 5.

Accettazione: prove con fonti simulate; cancellazione verificata; ogni aggiornamento della knowledge base ha provenienza; benchmark senza regressioni; possibilità di tornare indietro.

### F9. Critico

Consegna: render proxy, campioni (ogni 0,5 s, su ogni taglio, su ogni testo), metriche audio, rubrica a sei assi (hook, ritmo, leggibilità, coerenza visiva, audio, aderenza al brief) con punteggio ed evidenza; correzioni come patch dell'EDL con ancora e prova; budget di tre iterazioni; stop quando una correzione peggiora il risultato; confronto prima e dopo.

Accettazione: su 10 EDL con difetti seminati (flash troppo lungo, sottotitoli fuori safe zone, ducking assente, audio sbilanciato e simili), il critico trova almeno il 90% dei difetti e ne corregge almeno l'80% senza crearne di nuovi.

### F10. Interoperabilità e consegna

Consegna: export multi-aspect, OTIO, FCPXML ed EDL, stem, VTT e SRT, soft-sub, capitoli e metadati, ProRes, DNxHR, 10 bit, HDR con tone mapping o passthrough, testo per la pubblicazione (titolo, descrizione, hashtag per piattaforma dalla knowledge base), thumbnail scelta per volto ed emozione, watermark e brand, strumento per sfocare volti.

Accettazione: i file passano ffprobe e la validazione OTIO; ogni formato ha un test.

### F11. Superficie agente e job

Consegna: MCP v2 (understand, intent, research, propose\_edl, apply\_edit\_ops, render\_preview, critique, deliver, memory, job) con parità totale con la CLI; coda dei job con cancellazione, ripresa, elenco, notifica di avanzamento e pulizia delle workdir vecchie.

Accettazione: test di parità tra strumenti e CLI; test di cancellazione e ripresa.

### F12. Indurimento ed esame finale

Consegna: profilazione, politica di pulizia, audit delle licenze, revisione di sicurezza di Chromium, documentazione generata, SKILL.md sotto le 250 righe con caricamento progressivo delle reference.

Accettazione: l'esame della sezione 6 passa per intero.

## 5. Ricerca in rete e apprendimento

Livelli di fonte, in ordine di preferenza:

- A. Riferimenti forniti dall'utente (file o link).
- B. Corpora con licenza aperta (Pexels, Pixabay, Wikimedia Commons, Internet Archive, Creative Commons), con licenza verificata e registrata per ogni elemento.
- C. Testi: articoli, linee guida ufficiali delle piattaforme, rapporti sulle tendenze, tramite ricerca e lettura web.
- D. Video delle piattaforme: nessuno scraping e nessun download dove i termini di servizio lo vietano. Si usano le API ufficiali per i metadati, gli oEmbed e i file che l'utente fornisce. Se un percorso richiede di aggirare protezioni o violare i termini, non si fa e si scrive in not\_applied.

Ciclo di vita del ReferenceLab:

1. Manifest (origine, licenza, data, scopo, hash).
2. Cartella temporanea sotto la work root.
3. Analisi che produce una StyleFingerprint.
4. Cancellazione nel blocco finally, con verifica: cartella rimossa e registro con numero di file e byte eliminati.
5. Se il processo muore, uno spazzino all'avvio elimina le cartelle più vecchie di un'ora.

Dei contenuti di terzi non si conservano frame né audio: solo numeri e note scritte con parole proprie.

Campi della StyleFingerprint: formato, durata, tagli ogni 10 s, mediana e IQR della durata delle inquadrature, istante del primo taglio, caratteristiche della finestra hook (testo nei primi 2 s, volto nel primo secondo, movimento), sottotitoli (posizione, dimensione, cadenza, quota di parole enfatizzate), densità di testo, colore (palette, contrasto, saturazione, tendenza di grading), energia di movimento, istogramma dei tipi di transizione, audio (LUFS, rapporto parlato e musica, BPM, densità di SFX), presenza di call to action.

Knowledge base (knowledge/, JSONL versionato): ogni voce ha affermazione, ambito (piattaforma, genere, lingua), evidenze (fonte, data, numero di osservazioni), confidenza, scadenza e stato. Regola di aggiornamento: almeno 3 osservazioni indipendenti e nessuna contraddizione. Le tendenze scadono dopo 90 giorni e vanno riverificate. Ogni cambiamento mostra una differenza e si può annullare.

Apprendimento dagli esiti: valutazioni, accettazioni e rifiuti e modifiche manuali dopo la consegna alimentano un profilo di preferenze locale, con memoria su consenso esplicito e ispezionabile o cancellabile dall'utente. Le varianti A e B vengono registrate.

Prova del miglioramento: una suite di benchmark versionata. Ogni modifica alla knowledge base riesegue vx bench e confronta i punteggi; una regressione blocca la modifica.

Limite dichiarato: le affermazioni sulle tendenze sono ipotesi con una confidenza, non verità.

## 6. Esame finale: 12 incarichi

Criteri comuni: controlli automatici verdi, punteggio del critico sopra la soglia fissata in F0, deliverable presenti con sha256, nulla di dichiarato e non eseguito, tre esecuzioni con esito coerente.

1. «Quella Sera»: 8 clip, verticale nativo, senza fascia sfocata, loudness per clip entro ±1 LU, sottotitoli sulla tela, silenzi tagliati, ducking.
2. Talking head di 60 s trasformato in reel con sottotitoli animati, hook ricavato dal contenuto e reframe.
3. Podcast di 30 minuti ridotto a 3 clip con titolo.
4. Spot di 15 s da 10 clip con musica sul beat e brand kit.
5. Tutorial in screen recording da 10 minuti ripulito, con capitoli, zoom sui click e lower third.
6. Recap di un evento con musica, titoli e color match.
7. Richiesta vaga («fammi qualcosa di bello con questo girato»): al massimo una domanda, concept con assunzioni dichiarate, 3 animatic.
8. Richiesta ambigua («fallo più dinamico» su un'intervista): al massimo 2 domande pertinenti.
9. «Come questo reel» con un file di riferimento fornito dall'utente: imitazione dello stile, impronta salvata, cancellazione verificata.
10. Reel per una palestra con ricerca di tendenze: playbook con fonti e date e nessun download dalle piattaforme.
11. Intervista a due camere: sincronizzazione e cambio camera sull'oratore attivo.
12. Spiegazione di dati con grafici animati e testo cinetico, con un template creato al volo.

## 7. Ledger e commit

- PLAN/MASTER\_LEDGER.md elenca ogni task con ID (F3-T07), stato, test di accettazione, percorso dell'evidenza e hash del commit.
- Un commit per task, messaggio \[F3-T07\] descrizione. Tag phase-F3-done a fine fase.
- Ogni commit lascia l'albero pulito e la suite completa verde.
- DECISIONS.md raccoglie le decisioni di prodotto prese in autonomia, con motivazione.

## 8. Rischi e licenze

Verificare prima dell'integrazione: RNNoise, DeepFilterNet, Real-ESRGAN, CLIP, YOLO e simili (le licenze di alcuni sono restrittive), RVM (GPL-3), Playwright e Chromium, qualunque libreria di animazione, musica e SFX (solo CC0 o CC-BY con attribuzione registrata). ffmpeg in build GPL impone obblighi se si ridistribuisce. Rischi pratici: peso e tempi dei modelli, hardware GPU assente, deriva del contesto dell'agente (mitigata dal ledger).

## 9. Prompt di esecuzione (da incollare così com'è)

Sei l'ingegnere responsabile del repository video-editor-skill. La tua missione è portare a termine TUTTO il piano in PLAN/MASTER\_PLAN.md, fasi F0-F12 compresa la sezione 6 (esame finale). NON ESISTE CONSEGNA PARZIALE: il lavoro è finito solo quando ogni task del ledger è chiuso con evidenza e l'esame finale passa per intero.

Come lavori:

1. Leggi PLAN/MASTER\_PLAN.md e la reference 26. Se PLAN/MASTER\_LEDGER.md non esiste, crealo scomponendo ogni fase in task atomici, ciascuno con ID, test di accettazione binario, stato e percorso dell'evidenza. Questo è lo stato del lavoro: ad ogni ripresa lo rileggi e continui dal primo task non chiuso.
2. Lavora nell'ordine delle fasi. Per ogni task: scrivi prima il test (deve fallire), implementa, esegui la suite completa, verifica con materiale reale e GUARDANDO i frame prodotti, correggi, poi fai commit con messaggio \[Fx-Tyy\] e aggiorna il ledger con hash ed evidenza. Un task senza evidenza non è chiuso. Albero pulito a ogni commit. Tag phase-Fx-done a fine fase.
3. NON spacchettare, NON proporre di fermarti, NON chiedere il permesso di continuare, NON consegnare specifiche al posto di implementazioni. Se il contesto finisce, il ledger è la memoria: alla ripresa continua da dove eri.
4. Ti fermi solo per un BLOCCO reale (credenziali, hardware, vincolo legale, requisiti contraddittori). In quel caso scrivi BLOCCO nel ledger con motivo e prova, prosegui con i task che non ne dipendono e riportalo alla fine. Non scartare mai un task in silenzio.
5. Quando una decisione di prodotto non è specificata, decidi tu in modo sensato, scrivila in DECISIONS.md con la motivazione e vai avanti.
6. Rispetta le regole non negoziabili della sezione 3: legge dell'evidenza estesa, dichiarato è eseguito, prova visiva, sorgente intatto, determinismo, budget, portabilità, sicurezza. Ciò che non applichi finisce in not\_applied. Non dichiarare mai fatto qualcosa che non hai eseguito e visto.
7. Ricerca e apprendimento: segui alla lettera la sezione 5. Nessuno scraping o download da piattaforme che lo vietano; solo fonti A, B, C e le API ufficiali. Ogni file di terzi scaricato per analisi va cancellato nel blocco finally con verifica. Non conservare frame o audio di terzi. Verifica la licenza di ogni modello, libreria, musica e SFX prima di integrarli e registra la verifica.
8. Non toccare o cancellare materiale dell'utente. Non scrivere path o nomi di macchina nel codice. Non introdurre telemetria.
9. Prima di dichiarare concluso: esegui l'esame finale (sezione 6) per intero, tre volte, e produci PLAN/FINAL\_REPORT.md con ogni task chiuso (ID, commit, evidenza), risultati dell'esame, BLOCCHI aperti, limiti noti e not\_applied.

Inizia ora dal ledger. Non rispondere con un piano: esegui.

## Allegato A. Abbattere i limiti, gratis

Principio: un limite è un requisito che manca. Per ogni muro qui sotto c'è una tecnica gratuita, una fase e una prova. Dove il muro è fisico o legale lo dico, e indico la via legittima per aggirarlo.

### A1. Carta del gratuito

- Ogni dipendenza è gratuita, locale e senza account, chiave API, quota o abbonamento. Niente modelli che richiedono registrazione, niente servizi a pagamento, nessun upload del materiale dell'utente a terzi.
- Ogni componente è registrato in LICENSES\_MATRIX.md (nome, versione, licenza, uso: libreria, processo esterno, opzionale). Da evitare nel nucleo: AGPL (per esempio Ultralytics YOLO), pesi non commerciali (per esempio MusicGen, NLLB, Depth Anything nelle versioni grandi), GPL-3 collegato in-process (RVM va isolato come processo esterno opzionale o sostituito con MediaPipe o U2Net). Alcune licenze cambiano nel tempo (Piper, per esempio): verificare sempre alla data di integrazione. La matrice è una guida, non una consulenza legale.
- Installazione riproducibile: dipendenze bloccate con hash, wheelhouse offline, checksum dei modelli verificati all'installazione, download una sola volta.

### A2. Livelli di capacità

Ogni funzione ha tre livelli dichiarati: L0 (CPU, modelli piccoli, soluzione procedurale), L1 (CPU potente o GPU modesta), L2 (GPU). Il doctor rileva cosa c'è e la pipeline sceglie il livello più alto disponibile, scrivendo nel referto quale ha usato e cosa avrebbe fatto con di più. Così nessuna funzione muore per mancanza di hardware. Esempi: WebGL senza GPU gira su rendering software (SwiftShader) in Chromium, più lento ma identico; Whisper passa da small su CPU a large su CUDA; la segmentazione passa da U2Net a SAM2.

### A3. Dodici muri e come si abbattono

1. «Non guarda davvero il video». Vision Pipeline: campionamento adattivo (più fitto su tagli, movimento e testo), contact sheet per inquadratura con timestamp, descrizione di ogni shot scritta dall'agente (che è multimodale), embedding locali (open\_clip o SigLIP) per cercare per significato, OCR, segmentazione (MediaPipe, U2Net, SAM2 dove c'è GPU), profondità (Depth Anything small) per la parallasse 2.5D delle foto. La MaterialMap si salva per sha256 e si riusa: la seconda richiesta sullo stesso materiale non rifa l'analisi.
2. «Nessun player, nessun A/B, nessun feedback». Review Room: una pagina HTML locale e statica generata dal progetto, con player del proxy, timeline disegnata dall'EDL, confronto prima e dopo con cursore e commenti agganciati al timecode. I commenti producono un file di feedback strutturato che l'agente legge e trasforma in patch. Gratis, senza server.
3. «La grafica ha un tetto». Motion Studio con GLSL come linguaggio degli effetti: importa gl-transitions (MIT, oltre 100 transizioni), esegue shader in Chromium WebGL con due frame come texture e lascia che l'agente scriva shader nuovi in una sandbox (tempo massimo, niente rete, errori di compilazione mostrati e corretti). Three.js per titoli e scene 3D, D3 per i grafici, Lottie per animazioni vettoriali, e un ponte opzionale con Blender headless (processo esterno, script Python) per il 3D di alta qualità quando è installato. Sui pixel, ffmpeg offre geq, displace, remap, blend, perspective, lenscorrection e libplacebo con shader personalizzati.
4. «Niente GPU». Livelli di capacità (A2), ONNX Runtime su CPU con modelli quantizzati, analisi sul proxy a risoluzione ridotta, lavoro a blocchi.
5. «Troppo lento». (a) Cache a contenuto di ogni stage. (b) Render a segmenti paralleli, divisi su confini di GOP chiusi e poi concatenati. (c) Smart render: i segmenti invariati si riusano dal render precedente, non dalla sorgente. È l'unica eccezione al divieto di -c copy, limitata a segmenti prodotti dalla skill con parametri identici e dimostrata da un test. (d) Encoder hardware scelto per prova (NVENC, QSV, AMF, VideoToolbox) con ripiego su x264. (e) Il critico lavora su proxy a bassa risoluzione e frame rate ridotto. (f) Intermedi lossless veloci (FFV1 o UT Video) per le cache. (g) Analisi con faster-whisper e distil-whisper, VAD per saltare il silenzio, inferenza in batch. Obiettivi iniziali, da ricalibrare in F0: anteprima almeno 3 volte il tempo reale, render finale almeno 1 volta il tempo reale su un 1080p semplice con una CPU a quattro core.
6. «Rischio di perdere i materiali». Vault e sicurezza dei dati (F17).
7. «Qualità finale». Codifica per titolo: si cerca il CRF più economico che mantiene un VMAF di almeno 95 rispetto al master (libvmaf in ffmpeg). Gestione esplicita del colore (BT.709 con tag corretti, tone mapping HDR con libplacebo o zscale), scaling con lanczos o libplacebo, deband e dithering, nitidezza solo se misurata, torneo A/B (F18).
8. «Creatività piatta». Cervello editoriale: ciclo divergente e convergente (6 concept diversi, punteggio su intento, fattibilità, novità e rischio, poi scelta e raffinamento); cercatore di match cut (similarità degli embedding, direzione del movimento e baricentro di saliency tra inquadrature); speed ramp sui picchi di movimento; colpi sul beat drop (freeze, flash, zoom); setup e payoff, motivi ripetuti, regola del tre; vincolo di diversità che impedisce di ripetere le stesse transizioni da un lavoro all'altro.
9. «Musica e SFX con licenza». Solo CC0 o CC-BY con attribuzione registrata, più musica e SFX procedurali sintetizzati (ffmpeg, SoX o Python) per stinger, whoosh, riser e impatti. Separazione degli stem con Demucs per riusare solo la voce o solo lo strumentale.
10. «Impara poco». Ricette: ogni montaggio con punteggio alto del critico e valutazione positiva dell'utente diventa una ricetta parametrica (innesco, struttura, parametri, prova visiva) in una libreria ispezionabile. La knowledge base della sezione 5 resta la fonte per le tendenze; il benchmark impedisce le regressioni.
11. «Lingue». Whisper trascrive, l'agente stesso traduce i sottotitoli (nessun servizio esterno), una voce sintetica locale (Piper o Kokoro, previa verifica della licenza) può doppiare.
12. «Deriva del contesto dell'agente». Ledger più un Context Pack generato in automatico a ogni ripresa: stato del repository, task aperti, ultime decisioni, test rossi.

### A4. Nuove fasi (F13-F19), parte dello stesso lavoro

Ordine consigliato: F17 subito dopo F0; F14 dopo F2; F15 dopo F3; F13 dopo F6; F16 in parallelo con F4-F5; F18 e F19 dopo F9.

#### F13. Vision Pipeline e Cervello editoriale

Consegna: campionamento adattivo, contact sheet per shot, descrizioni, embedding locali, OCR, segmentazione, cercatore di match cut, speed ramp sui picchi di movimento, colpi sul beat drop, struttura di setup e payoff, ciclo divergente e convergente, vincolo di diversità.

Accettazione: su 20 clip il cercatore propone almeno 5 match cut e il critico ne approva almeno 3 con motivazione; la seconda analisi dello stesso materiale non rifa il lavoro (tempo di analisi vicino a zero, provato da un test).

#### F14. Review Room

Consegna: pagina HTML locale con player del proxy, timeline dell'EDL, confronto prima e dopo, commenti al timecode, esportazione del feedback in JSON e suo ingresso come patch.

Accettazione: commenti su 5 timecode diversi diventano 5 patch applicate e verificate dal critico.

#### F15. Shader, 3D e profondità

Consegna: importazione di almeno 100 transizioni gl-transitions con test a frame di riferimento; authoring di shader GLSL in sandbox; scene Three.js; parallasse da mappa di profondità; ponte opzionale con Blender; rendering software di WebGL come ripiego.

Accettazione: una transizione richiesta a parole (per esempio «come un'onda di inchiostro») viene scritta, compilata, vista, corretta e usata; lo stesso test passa su una macchina senza GPU.

#### F16. Prestazioni

Consegna: render a segmenti paralleli, smart render con test di identità dei segmenti riusati, scelta dell'encoder hardware per prova, intermedi lossless, benchmark ufficiale versionato.

Accettazione: raggiunti gli obiettivi di velocità del punto 5 di A3 (o ricalibrati e motivati in DECISIONS.md); lo smart render produce un file identico bit a bit nelle parti non modificate.

#### F17. Vault e sicurezza dei dati

Consegna: originali aperti in sola lettura e mai scritti; scritture solo in directory con file marcatore .vx-owned e cancellazioni solo lì; cestino con quarantena di 7 giorni per i file della skill (il materiale di terzi del ReferenceLab si cancella subito); controllo dello spazio prima di ogni render, con rifiuto se non basta; journal write-ahead per EDL e ledger; snapshot periodico dello stato di progetto (EDL, MaterialMap, knowledge, senza i media); verifica degli sha256 all'apertura e prima della consegna per scoprire il bit-rot; dry-run per ogni operazione distruttiva; limiti di tempo, memoria e CPU per ogni sottoprocesso; test di fuzz con file multimediali corrotti o ostili; catena di fornitura con hash e checksum dei modelli.

Accettazione: dopo 50 uccisioni casuali del processo (kill -9 durante render e scrittura) gli sha256 delle sorgenti sono invariati, nessun file di progetto è corrotto e il lavoro riprende dall'ultimo checkpoint; un test con una directory esca prova che nulla viene cancellato fuori dalle directory marcate; 200 file di fuzz non causano errori non gestiti.

#### F18. Qualità di consegna e torneo A/B

Consegna: codifica per titolo con VMAF, gestione esplicita del colore, torneo a coppie tra varianti (l'agente giudica due volte con ordine scambiato per eliminare il bias di posizione, poi punteggio Elo).

Accettazione: su 10 montaggi la codifica per titolo ottiene VMAF almeno 95 con dimensione non superiore a quella del CRF fisso; il torneo concorda con il giudizio dell'utente almeno 7 volte su 10 su coppie di prova (soglia da calibrare in F0).

#### F19. Ricette e ciclo di apprendimento

Consegna: libreria di ricette parametriche con innesco e prova visiva, promozione di una ricetta solo con punteggio del critico e valutazione dell'utente sopra soglia, versioni annullabili, ispezione e cancellazione da parte dell'utente.

Accettazione: dopo 10 lavori valutati, il benchmark mostra che l'uso delle ricette non peggiora nessun asse della rubrica e ne migliora almeno uno; ogni ricetta è spiegabile (da quali lavori nasce).

### A5. Esame esteso (incarichi 13-19)

13. Prova di crash: kill -9 a metà render, ripresa, sorgenti intatti, nessun file orfano.
14. Macchina senza GPU: gli incarichi 2 e 6 completano, e il referto indica i livelli usati.
15. Montaggio a match cut su 20 clip, come da F13.
16. Transizione scritta al volo in GLSL, come da F15.
17. Review Room: feedback dell'utente convertito in patch, come da F14.
18. Velocità: benchmark ufficiale di F16.
19. Materiale lungo: 4 ore di riprese, 3 clip finali, con analisi riusata alla seconda richiesta.

### A6. Pareti che restano, e come le si aggira

- Il dettaglio che non c'è non si recupera: l'upscaling lo inventa. Lo si usa dichiarandolo, con alternative (banda sfocata, crop moderato).
- Nessuno predice un successo virale: la knowledge base dà ipotesi con una confidenza e il benchmark misura solo ciò che è misurabile.
- Senza GPU alcune cose sono lente: restano possibili, con livello dichiarato e tempo stimato.
- La lettura visiva dell'agente è approssimata: si campiona fitto, si affiancano misure numeriche e il critico non si fida di una sola occhiata.
- Copyright e termini di servizio non si aggirano: si usano le fonti lecite della sezione 5.
- Consenso e privacy delle persone nei video non si risolvono con la tecnica: servono sfocatura, redazione e avviso all'utente.

### A7. Addendum al prompt (da incollare in coda alla sezione 9)

Il piano comprende anche l'Allegato A: le fasi F13-F19 e gli incarichi d'esame 13-19. Includili nel ledger e portali a termine con le stesse regole di tutto il resto. Rispetta la Carta del gratuito (A1): nessuna dipendenza a pagamento, con account, chiave API o quota; nessun upload del materiale a terzi; licenze registrate in LICENSES\_MATRIX.md. Ogni funzione ha livelli di capacità L0-L2 e dichiara nel referto quello usato. La sicurezza dei materiali (F17) è prioritaria: subito dopo F0 scrivi Vault, journal e test di morte improvvisa, prima di toccare il rendering. Quando incontri un limite non fermarti: trova la via gratuita per abbatterlo; se è una parete reale (A6), documentala e applica la mitigazione. Non chiudere nulla senza prova visiva e metrica.

## Allegato B. Trend Radar

Precedenza: questo allegato precisa la sezione 5 e la fase F8 dove i testi differiscono. Non allenta nulla di quanto c'è lì: niente scraping, niente download dove i termini lo vietano, cancellazione verificata dei file transitori.

### B1. Che cos'è

Un modulo (research/trend\_radar) che parte da solo all'inizio di ogni lavoro quando il brief indica una piattaforma, un genere o un tema, oppure quando la modalità creativa ha bisogno di un concept. Nessun intervento manuale, nessun login in account di social, nessuna credenziale data all'agente.

### B2. Fonti, distinte per tipo di segnale

Ogni fonte insegna una cosa diversa, e la skill deve saperlo:

- Segnali di mercato (quali temi funzionano, durate tipiche, schemi dei titoli, hashtag, velocità di crescita): ricerca web (articoli, linee guida ufficiali, rapporti) e YouTube Data API v3. L'API restituisce metadati (titolo, durata, visualizzazioni, like, data, categoria, miniatura), non i file video: da lì non si ricava la struttura del montaggio.
- Struttura di montaggio (ritmo dei tagli, sottotitoli, colore, musica, hook): analisi locale e transitoria di video con licenza aperta (Wikimedia Commons, Pexels, Pixabay, PeerTube, Internet Archive), dei file che l'utente fornisce e, se presenti nella cartella del progetto, delle statistiche dei suoi account (Insights, Analytics).
- Le miniature restituite dall'API si analizzano in memoria (presenza di testo e volti, contrasto, colore) e non si conservano.
- Non esiste un modo lecito e automatico di analizzare il montaggio dei video di Instagram, TikTok o YouTube senza i file. La skill lo dichiara nel referto (not\_applied) invece di fingere di averlo fatto.

### B3. Configurazione della YouTube Data API

- Un account Google dedicato va benissimo, e il tuo feed non viene toccato: l'API non usa la cronologia né il feed di nessun account. L'account serve solo a creare un progetto Google Cloud e una chiave API limitata alla YouTube Data API v3. Di norma non richiede carta e la quota gratuita è di 10.000 unità al giorno, ma le condizioni vanno verificate al momento della creazione.
- La chiave è un segreto. Sta nella variabile d'ambiente VX\_YOUTUBE\_API\_KEY, mai nel repository, nel prompt, nei log o nei referti (i log la mascherano). Senza chiave la skill funziona lo stesso: salta quella fonte e lo dichiara.
- Condizioni d'uso dell'API: nessun download, cache dei metadati con scadenza, attribuzione dove richiesta. I termini correnti vanno verificati prima dell'integrazione.
- Gestore di quota: ogni chiamata ha un costo (la ricerca costa molto più di un elenco per id, quindi si preferiscono elenchi e id), cache di 24 ore, backoff in caso di errore, mai oltre l'80% della quota giornaliera.

### B4. Che cosa produce

Uno TrendSnapshot per piattaforma e genere: durate tipiche, schemi dei titoli, hashtag, velocità di crescita, caratteristiche delle miniature, argomenti emergenti. Ogni voce ha fonte, data, numero di osservazioni, confidenza e scadenza a 90 giorni. Entra nella knowledge base solo con le regole della sezione 5 (almeno 3 osservazioni indipendenti, nessuna contraddizione). Il Creative Brief lo cita come evidenza, mai come verità.

### B5. Passi automatici di ogni lavoro

1. Se esiste uno snapshot valido per piattaforma e genere, lo usa.
2. Altrimenti lancia la ricerca (web, e API se c'è la chiave) con limite di tempo e di quota.
3. Per la struttura di montaggio usa le impronte di stile già in knowledge base e, se servono, analizza in locale nel ReferenceLab video con licenza aperta, con cancellazione verificata.
4. Aggiorna la knowledge base e scrive nel referto le fonti usate e quelle saltate, con il motivo.
5. Il critico verifica che il montaggio non copi un riferimento: la distanza dalle impronte usate deve superare una soglia fissata in F0.

### B6. Accettazione (F8 bis) - SUPERATO da C

- Con un server API simulato: la quota viene rispettata, la cache funziona (la seconda richiesta non fa nessuna chiamata) e la chiave non compare in nessun file generato (test che la cerca nei log, nei referti e nel progetto).
- Senza chiave: il lavoro si completa e il referto dichiara la fonte saltata.
- Un test di rete con elenco dei domini consentiti prova che non parte nessuna richiesta verso Instagram o TikTok e che non avviene nessun login.
- L'incarico 10 dell'esame (reel per una palestra) passa con uno TrendSnapshot datato e con le fonti.
- I file transitori vengono cancellati con verifica.

### B7. Addendum al prompt (da incollare in coda alla sezione 9) - SUPERATO da C

Il Trend Radar (Allegato B) sostituisce ogni riferimento alla ricerca sui social. Nessun login, nessuna credenziale, nessun accesso automatizzato a Instagram o TikTok, nessun download da piattaforme che lo vietano. La YouTube Data API si usa solo con la chiave in VX\_YOUTUBE\_API\_KEY: la chiave non compare mai in repository, log, referti o prompt, e senza chiave si salta la fonte dichiarandolo. Il Trend Radar parte da solo a ogni lavoro con piattaforma, genere o tema, come descritto in B5. Implementalo in F8 e verifica tutto B6 prima di chiudere la fase.

## Allegato C. Trend Radar v2: segnali a gradini e provider che si auto-diagnosticano

Precedenza: questo allegato sostituisce l'Allegato B dove i testi differiscono (B2, B5, B6, B7). Restano validi B3 (chiavi e quota YouTube) e B4 (TrendSnapshot). A sua volta, l'Allegato D sostituisce C3 e C6 per i provider, e ogni uso di API di Instagram o TikTok.

### C1. Principio

Ogni fonte è un provider con una sonda di capacità. La skill non dà nulla per scontato: a ogni lavoro in modalità deep prova, registra cosa funziona, usa il livello di segnale più alto disponibile per ogni piattaforma e non blocca mai il lavoro. Se un provider cambia o smette di funzionare, la skill lo scopre da sola, lo declassa, usa gli altri e lo dichiara nel referto.

### C2. Scala dei segnali (dal più ricco al più povero)

- S4, video visibile: un file o un URL che la skill può analizzare per ricavare la struttura del montaggio (ritmo, hook, sottotitoli, colore, musica).
- S3, descrizione: caption, hashtag, nome del suono, miniatura.
- S2, metriche: like, commenti, visualizzazioni, velocità di crescita.
- S1, liste di tendenza: hashtag, suoni, argomenti con la loro posizione.
- S0, solo testo e cache: articoli, linee guida, rapporti, conoscenza già in knowledge base.

Ogni segnale porta fonte, data, livello e confidenza.

### C3. Mappa dei provider noti - SUPERATO IN PARTE da D (TikTok, Instagram, fornitori terzi) (da verificare dal vivo: è il compito della sonda)

- YouTube. Data API: S2 e S1 (metadati e miniature). Gemini su URL pubblico: S4 approssimato, gratuito con un tetto giornaliero di ore di video, previa lettura dei termini del livello gratuito.
- TikTok. Creative Center pubblico: S1 sugli hashtag (gli altri feed risultano ridotti in un aggiornamento recente e vanno verificati). oEmbed ufficiale su un URL di video: S3 (didascalia con hashtag, autore, miniatura, nome del suono), senza metriche e senza file. Commercial Content e Ad Library: annunci, non contenuti organici, con accesso all'API su domanda. Research API: riservata a istituzioni accademiche e non profit, quindi non disponibile.
- Instagram. API Graph, ricerca per hashtag e Business Discovery su account professionali: S2 (like, commenti, visualizzazioni) e, solo dove l'API restituisce l'URL del file, S4. Il file manca per i video con audio con licenza (compreso quello della libreria di Instagram) e per i reel con download disattivato. Richiede un account Instagram professionale collegato a una Pagina Facebook e un'app Meta; per un uso pubblico serve la revisione dell'app. Limite di 30 hashtag unici ogni 7 giorni.
- Fornitori terzi (MCP o API a crediti): spesso S1-S3. Opzionali, spenti di default, con tetto di spesa zero e conferma esplicita prima di qualunque costo. SUPERATO da D: nessun fornitore a pagamento, nemmeno come opzione.
- Web: S0.

### C4. Sonda di capacità e salute

1. Sonda per provider a ogni job deep: una chiamata leggera con timeout che restituisce disponibile sì o no, livello massimo ottenibile, quota residua, latenza, schema valido, ultimo successo.
2. Validatore di schema: se la risposta cambia forma, il provider passa a degradato senza fermare il lavoro.
3. Interruttore automatico: dopo N fallimenti consecutivi il provider si disattiva per un tempo che cresce in modo esponenziale e poi riprova da solo.
4. Registro persistente della salute (provider\_health.json) con lo storico.
5. Selezione: per ogni piattaforma si usa il livello più alto con un provider sano, combinando segnali di fonti diverse con pesi per livello e freschezza.
6. Confidenza dello snapshot in funzione di livello, numero di fonti indipendenti ed età: non può essere alta con soli S0 e S1.
7. Referto del trend (trend\_report) con, per piattaforma, livello raggiunto, provider usati e saltati, motivi e cosa è stato dedotto.
8. Se tutto fallisce, il lavoro prosegue con knowledge base e playbook e lo dichiara con trend\_unavailable.

### C5. Che cosa se ne fa

- Da S4: StyleFingerprint calcolata in memoria o in un file temporaneo, con cancellazione verificata. Non si conservano frame o audio.
- Da S3 e S2: velocità di crescita, schemi di didascalie, hashtag, suoni e miniature.
- Da S1: liste e posizioni.
- Quando S4 e S2 coesistono per gli stessi contenuti, si cerca cosa distingue quelli ad alte prestazioni, con cautela: sono correlazioni, e sotto un numero minimo di campioni non si traggono conclusioni.
- Il Creative Brief cita livello e confidenza di ogni segnale, mai come verità.

### C6. Interruttore per lavoro

Nel brief, trends con valori off, light o deep, più platforms, niche e region. Il default è off e non parte nessuna chiamata di rete. Light usa solo cache e S0. Deep esegue le sonde e usa i provider sani. Ogni job ha un budget di tempo e di quota, mostrato prima di partire.

### C7. Regole legali e di sicurezza

- Nessun login automatico, nessun account della skill, nessun aggiramento dei controlli anti-bot (proxy rotanti, captcha, impersonazione del browser).
- Pagine pubbliche lette solo se funzionano senza evasione, con frequenza bassa, cache e rispetto dei segnali di esclusione leggibili dalle macchine (robots.txt) e dei termini.
- Video di terzi analizzati in memoria o in cartella temporanea e cancellati; nessuna ridistribuzione.
- Nell'UE, per il data mining conta anche l'opt-out dei titolari: da verificare con un legale. Questo documento non è una consulenza legale.
- Chiavi e token (YouTube, Gemini, Meta) solo in variabili d'ambiente, mai nel repository, nei log o nei referti.
- Tutto gratuito: nessun fornitore a pagamento, nessun credito, nessun abbonamento, nemmeno come opzione (Allegato D).

### C8. Accettazione (F8 ter)

Con provider simulati:

- tutti sani: viene usato S4 dove disponibile;
- uno cambia schema: passa a degradato e gli altri continuano;
- tutti non disponibili: il lavoro si completa con trend\_unavailable;
- quota esaurita: il provider viene saltato con il motivo;
- l'interruttore si riapre dopo il tempo previsto;
- il referto elenca per ogni piattaforma livello, provider e motivi;
- nessuna chiamata verso domini fuori dall'elenco consentito;
- le chiavi non compaiono nei log;
- i file temporanei vengono cancellati con verifica;
- con trends uguale a off non parte nessuna chiamata di rete.

Prova dal vivo, facoltativa, con le chiavi dell'utente: sonda reale per ogni provider configurato, con esito salvato in provider\_health.json.

### C9. Addendum al prompt (da incollare in coda alla sezione 9)

Il Trend Radar v2 (Allegato C) sostituisce l'Allegato B dove differiscono. Implementa la scala dei segnali S0-S4, la sonda di capacità, il validatore di schema, l'interruttore automatico e il registro della salute dei provider. Nessun login, nessun account della skill, nessun aggiramento dei controlli anti-bot, nessuna conservazione di frame o audio di terzi. Se un provider non funziona, declassalo e usa gli altri, senza mai bloccare il lavoro. Con trends uguale a off non deve partire nessuna chiamata di rete. Verifica tutto C8 prima di chiudere la fase.

## Allegato D. Trend Radar gratuito: YouTube Shorts e ricerca web

Precedenza: questo allegato sostituisce C3 (mappa dei provider) e ogni riferimento ad API di Instagram e TikTok o a fornitori terzi a pagamento, ovunque compaia nel documento. Restano validi la scala dei segnali S0-S4 (C2), la sonda di salute con validatore di schema e interruttore automatico (C4), le regole di C7, B3 (chiavi e quota YouTube) e B4 (TrendSnapshot).

### D1. Perimetro

- Tutto gratuito. Nessun fornitore a pagamento, nessun credito, nessun abbonamento, nemmeno come opzione.
- Nessuna API di Instagram o TikTok, nessun login, nessun account della skill, nessun fornitore terzo che le raccolga per conto nostro.
- Instagram e TikTok si studiano solo attraverso testi pubblici: articoli, rapporti, dati, documentazione ufficiale e discussioni, letti con la normale ricerca e lettura web.
- Il video si vede solo su YouTube Shorts e video pubblici di YouTube.

### D2. Provider ammessi

Tre provider, ciascuno sotto la sonda di salute di C4:

- youtube\_data: YouTube Data API v3 con chiave gratuita (S2 e S1: durate, visualizzazioni, like, data, categoria, miniature). Le miniature si analizzano in memoria e non si conservano.
- gemini\_video: analisi di URL pubblici di YouTube tramite l'API di Gemini con chiave gratuita di AI Studio (S4 approssimato). Ha un tetto giornaliero di ore di video e termini del livello gratuito da leggere prima dell'integrazione. La lettura è una stima, non una misura: i tempi e il numero di tagli sono approssimati e vanno marcati come tali.
- web\_research: ricerca e lettura di pagine web (S0 e S1).

Senza le chiavi, la skill lavora lo stesso: youtube\_data e gemini\_video risultano non disponibili e il referto lo dice.

### D3. Ricerca testuale su TikTok, Instagram e Shorts

Fonti, con peso decrescente:

1. Documentazione e comunicazioni ufficiali delle piattaforme (newsroom, blog dei creator, centri assistenza, linee guida): peso alto per le regole (durate, formati, safe zone, requisiti di audio).
2. Rapporti e studi con metodologia dichiarata (ricerche di settore, studi accademici, benchmark pubblici): peso alto per i dati.
3. Articoli di settore recenti con cifre, date e fonti citate: peso medio.
4. Contenuti commerciali di chi vende strumenti o servizi sul tema: peso basso, perché hanno un interesse.
5. Forum e discussioni di comunità: peso basso, solo come segnale qualitativo.

Procedura per ogni lavoro con trends attivo:

1. Costruisce le query dal brief (piattaforma, nicchia, regione, lingua) in italiano e in inglese.
2. Filtra per data: massimo 90 giorni per le tendenze, massimo 12 mesi per le regole.
3. Elimina i duplicati (articoli che ripetono la stessa fonte).
4. Estrae affermazioni verificabili con cifra, data e fonte, scritte con parole proprie. Non copia testo.
5. Triangola: una tendenza richiede almeno 3 fonti indipendenti; con meno è un segnale debole e lo dichiara.
6. Rileva le contraddizioni tra fonti e le riporta invece di sceglierne una in silenzio.
7. Dichiara il ritardo: gli articoli seguono le tendenze di giorni o settimane, quindi per Instagram e TikTok l'onda è sempre vista con ritardo.

### D4. Dallo Shorts a TikTok e Reels

I contenuti di YouTube Shorts sono vicini a quelli di TikTok e Reels, ma non uguali: suoni, hashtag, pubblico e formati cambiano. Per questo:

- Le impronte di stile ricavate da Shorts si applicano a TikTok e Reels con uno sconto di confidenza dichiarato.
- Le regole specifiche della piattaforma (durata, safe zone, loudness, formati) vengono dalla documentazione ufficiale e prevalgono sempre sulle impronte.
- Suoni e hashtag di tendenza di TikTok e Instagram non si deducono da Shorts: vengono solo dalla ricerca testuale e hanno un tetto di confidenza.

### D5. Confidenza

- Per YouTube Shorts: fino a S4, con confidenza in funzione di fonti, campioni ed età.
- Per TikTok e Instagram: massimo S0-S1. La confidenza non supera il livello medio, e solo se almeno 3 fonti indipendenti e recenti concordano.
- Ogni voce del TrendSnapshot riporta livello, data, numero di fonti e scadenza a 90 giorni.
- Il Creative Brief cita queste voci come ipotesi, mai come verità, e dichiara quando una tendenza è vecchia o debole.

### D6. Interruttore per lavoro

Nel brief, trends con valori off, light o deep, più platforms, niche e region.

- off (predefinito): nessuna chiamata di rete.
- light: cache e ricerca testuale.
- deep: ricerca testuale più YouTube (Data API e Gemini), con le sonde di salute.

Ogni job mostra prima di partire il budget di tempo e di quota. Con i tetti gratuiti la skill non supera mai l'80% della quota giornaliera.

### D7. Accettazione (F8 quater)

Con provider simulati e pagine di prova:

- tutti i provider sani: Shorts arriva a S4, TikTok e Instagram a S0-S1;
- chiavi assenti: il lavoro si completa e il referto dichiara i provider non disponibili;
- gemini\_video senza quota: viene saltato con il motivo e si usa youtube\_data;
- una pagina cambia o scompare: la fonte viene scartata e le altre continuano;
- tendenza con meno di 3 fonti indipendenti: marcata come segnale debole;
- contraddizione tra fonti: riportata nel referto;
- nessuna chiamata verso domini di API di Instagram o TikTok e nessun login (test con elenco dei domini consentiti);
- nessun testo copiato oltre una soglia di parole per fonte;
- le chiavi non compaiono in log, referti o progetto;
- con trends uguale a off non parte nessuna chiamata di rete;
- cancellazione verificata dei file temporanei.

L'incarico 10 dell'esame (reel per una palestra) passa con un TrendSnapshot datato, fonti, livelli e tetti di confidenza dichiarati.

### D8. Addendum al prompt (da incollare in coda alla sezione 9)

L'Allegato D sostituisce ogni uso di API di Instagram o TikTok e di fornitori a pagamento. Tutto è gratuito. Per YouTube Shorts usa youtube\_data e gemini\_video con le sonde di salute; per TikTok e Instagram usa solo la ricerca testuale descritta in D3, con tetto di confidenza S0-S1 e dichiarazione del ritardo. Nessun login, nessun account della skill, nessun aggiramento dei controlli anti-bot, nessun testo copiato, nessuna conservazione di frame o audio di terzi. Con trends uguale a off non deve partire nessuna chiamata di rete. Verifica tutto D7 prima di chiudere la fase.
