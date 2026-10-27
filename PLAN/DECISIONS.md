# DECISIONS

Decisioni di prodotto prese in autonomia, con la motivazione. Ogni voce è
datata e revocabile: se una decisione si rivela sbagliata, si aggiunge una voce
che la sostituisce, non si riscrive la storia.

## D001 — I font di Dafont entrano solo con la licenza verificata (2026-10-27)

Richiesta dell'utente: la skill deve cercare da sola i font su Dafont, proporli,
e sceglierne uno quando nessuno risponde.

Il problema: su Dafont la maggior parte dei font è «free for personal use».
Usarli in un video per un cliente è una violazione, e la skill non può sapere per
chi è il lavoro. Quindi:

- ogni font è un record con `licence` (`commercial`, `personal`, `unknown`),
  ricavata dalla pagina del font e dalla presenza di un file di licenza;
- **solo `commercial` può essere scelto in autonomia**;
- un font `personal` può essere **proposto** all'utente con l'avvertenza, mai
  selezionato senza una risposta esplicita;
- un font `unknown` non entra mai in un lavoro: si scarta e lo si dichiara;
- se il provider non è raggiungibile o nessun font supera il filtro, si usa il
  font incluso (`assets/fonts/`, Antonio, OFL) e la scelta viene dichiarata come
  ripiego nel referto.

Motivo: la regola 6 del piano (legalità) e la regola 2 (dichiarato è eseguito)
valgono più della comodità. Un font sbagliato costa una causa; un ripiego
dichiarato costa una riga di referto.

## D002 — Nessuna rete accesa per default (2026-10-27)

Tutto ciò che tocca la rete (Dafont, Trend Radar, YouTube) è un provider con una
sonda di disponibilità. Default `off`: nessuna chiamata. `light` usa solo cache.
`deep` esegue le sonde. Ogni job dichiara i provider usati e quelli saltati.

Motivo: il piano lo impone per il Trend Radar (C6/D6) e la stessa logica serve ai
font; inoltre un render non deve mai dipendere dalla rete.

## D003 — Il ledger si decompone per fase, non tutto in anticipo (2026-10-27)

F0 è decomposta adesso in task atomici. Le fasi F1-F19 sono elencate al livello
della consegna e vengono decomposte all'ingresso, perché decomporre F15 (shader)
senza avere in mano il Motion Studio di F3 produrrebbe task inventati.

Motivo: il piano chiede task atomici con accettazione binaria. Un task atomico
scritto senza conoscere l'implementazione che lo precede è una finzione, e le
finezze si pagano dopo.

## D004 — EDL 3 è un modulo nuovo, 2.2 resta congelato (2026-10-27)

`edl_plan.py` (2.2.0) non cambia forma: EDL 3 vive in un modulo separato con il
proprio validatore, e un migratore traduce 2.2 → 3.0. I render 2.2 continuano a
funzionare identici, e i test di identità del grafo restano la rete di sicurezza.

Motivo: 26 test dipendono dalla forma 2.2 e il percorso `vx auto` è appena stato
verificato su materiale reale. Riscrivere il documento in place avrebbe
significato rifare tutto il collaudo della Fase A.

## D005 — La prova visiva è un artefatto, non un'opinione (2026-10-27)
Ogni effetto e ogni grafica produce un frame di riferimento su disco e un
confronto numerico (hash o distanza dichiarata). «L'ho guardato e mi sembra
giusto» non è evidenza: la regola 3 del piano chiede il percorso dell'evidenza,
e un frame su disco lo è.

Motivo: i due difetti più gravi della Fase A (true peak e nomi file) sono emersi
solo guardando l'output reale. La stessa disciplina va imposta dal primo giorno.

## D006 — I font di un lavoro vivono in una cartella del lavoro (2026-10-27)

libass riceve **una** `fontsdir` (`subs.py:796`) e l'ASS porta solo il nome della
famiglia (`subs.py:647`). Oggi quella cartella è `assets/fonts`, che contiene
Antonio e nient'altro; il selettore "misurato" di `creative.py` guarda anche i
font di sistema, ma quelli non arrivano a libass — è codice morto e non va
riacceso così com'è.

Regola: ogni lavoro ha la propria `<workdir>/fonts/`, popolata con il font scelto
più il font incluso, e il render punta lì. Tre conseguenze, tutte volute:

1. il lavoro è riproducibile: il font viaggia con il progetto, non con la macchina;
2. nessuna scrittura in `assets/fonts` a runtime, che è materiale della skill e non
   dell'utente;
3. la sostituzione resta un fallimento duro: `verify_fonts` continuerà a leggere
   `fontselect` e a rifiutare un ripiego, con la sola differenza che ora il ripiego
   non può più accadere per sbaglio.

Un font scaricato non entra mai direttamente nella cartella del lavoro: prima
finisce in una cache dell'utente indirizzata dal suo sha256, poi viene copiato —
così due lavori che usano lo stesso font non lo scaricano due volte e la cache si
può ispezionare e cancellare.

## D007 — Il record di un font è dato, non decorazione (2026-10-27)

Ogni font, incluso o scaricato, è un record con: `family`, `file`, `sha256`,
`licence` (`commercial` | `personal` | `unknown`), `licence_url`, `source_url`,
`retrieved_at`, `styles`. `fonts.discover()` oggi espone solo famiglia e file
(`fonts.py:84-88`) e `creative._measure_font()` solo nome, percorso, advance e stem
(`creative.py:61-86`): nessuno dei due basta a decidere se un font si può usare.

Senza questi campi la scelta autonoma è un rischio legale travestito da funzione.
Con questi campi `LICENSES_MATRIX.md` si genera invece di essere scritto a mano,
e un font senza licenza verificata non può entrare in un lavoro di committenza
perché il codice non ha un valore con cui ammetterlo.

## D008 — Su Dafont l'autonomia si ferma dove dicono loro (2026-10-27)

Letto e verificato il `robots.txt` di `dafont.com`: vieta `/search.php`,
`/alpha.php`, `/dl.php` e `/download/`. Non è un dettaglio implementativo, è la
risposta alla richiesta: **la ricerca e il prelievo automatici su Dafont non sono
percorribili**, e la skill non aggira un divieto — non cambia User-Agent, non
ruota indirizzi, non finge di essere un umano. Un divieto aggirato è un rischio
che si paga dopo, e stavamo costruendo una funzione che serve a ridurre i rischi,
non ad aggiungerne.

Quindi la funzione si divide in due, e nessuna delle due è un ripiego:

1. **Dove l'autonomia è lecita, è totale.** Esiste un provider per cataloghi che
   permettono l'accesso programmatico (licenza aperta, uso commerciale
   dichiarato): cerca, classifica, sceglie e usa senza chiedere nulla a nessuno.
   È la strada per «essere autonoma qualora serva».
2. **Dove non è lecita, resta un passo umano e il resto è automatico.** Su Dafont
   la skill prepara una rosa di candidati con il link diretto, dice per ognuno la
   licenza letta dalla pagina, e aspetta che il file arrivi in una cartella. Da lì
   riprende da sola: verifica la licenza, calcola l'hash, registra il font, lo
   copia nella cartella del lavoro, lo usa e lo mette in `LICENSES_MATRIX.md`.

Il primo punto non è un contentino: un catalogo con licenza aperta e uso
commerciale esplicito è **meglio** di Dafont per un lavoro di committenza, perché
la licenza è verificabile e non c'è da interpretare «free for personal use».
Dafont resta utile per il gusto, dove il passo umano costa dieci secondi.

## D009 — F17 prima di EDL 3 (2026-10-27)

Scelta di ordine, richiesta dall'utente («la migliore per sicurezza e
funzionamento»): la cassaforte dei dati viene prima del nuovo formato di
montaggio, benché il piano elenchi EDL 3 come F2.

Motivo: EDL 3, Motion Studio e la libreria effetti moltiplicano i file che
scriviamo — layer, cache, frame, asset generati. Ogni file in più è un'occasione
di cancellare qualcosa che non era nostro. Le regole di proprietà, quarantena e
verifica si applicano bene a un sistema che ha pochi scrittori; applicarle dopo
significa rincorrerle in un sistema che ha già imparato a scrivere ovunque.

Quattro regole non negoziabili che F17 impone da qui in avanti:

1. **Si cancella solo ciò che è nostro.** Ogni radice di lavoro porta un
   marcatore `.vx-owned` con l'elenco dei percorsi e dei loro hash. Un percorso
   senza marcatore non viene mai toccato, nemmeno se sembra un residuo.
2. **Non si cancella: si mette in quarantena.** Un file nostro che non serve più
   va in `.quarantine/<data>/`, e da lì sparisce solo dopo sette giorni. Un
   `--dry-run` dice sempre cosa accadrebbe prima che accada.
3. **Prima si dichiara, poi si scrive.** Il giornale write-ahead registra
   l'intenzione di scrivere un artefatto prima di scriverlo: dopo un crash si sa
   cosa doveva esistere, e un file a metà si riconosce invece di sembrare valido.
4. **Un originale non si tocca mai, e si verifica.** Lo sha256 del sorgente è
   scritto all'ingest e ricontrollato alla fine; un file nostro viene ri-hashato
   per scoprire il degrado silenzioso del disco.

Limiti ai sottoprocessi: ogni chiamata a ffmpeg ha un tempo massimo e viene
terminata se lo supera, con errore dichiarato e non con un'attesa infinita.

## D010 — Soglie provvisorie: nessuna fase si chiude con numeri scelti a occhio (2026-10-27)

Regola proposta dall'utente, e ha ragione: T09 blocca molto più di sé stesso. Le
soglie che aspettano il corpus compaiono nell'accettazione di F5 (beat), F6
(segmentazione e momenti migliori), F9 (critico) e F18 (torneo). Chiudere una fase
con soglie provvisorie significa propagare un errore per quattro fasi prima di
vederlo.

Quindi, da adesso:

1. Finché T09 è aperto, `registry` marchetta le soglie derivate dal corpus come
   **provvisorie**, con la provenienza dichiarata nel referto.
2. `tests/test_threshold_provenance.py` legge lo stato di T09 in
   `PLAN/MASTER_LEDGER.md`: se T09 non è `DONE`, il test verifica che la marcatura
   provvisoria ci sia; se qualche fase risulta chiusa sull'esame mentre T09 è
   aperto, il test **fallisce**. La regola non è una promessa in un documento, è un
   cancello che si apre solo quando i dati ci sono.
3. Il modulo di annotazione non si interpreta a occhio: si importa con
   `tests/import_annotations.py`, che valida formato `mm:ss`, livelli ammessi,
   tre momenti non sovrapposti fra 3 e 30 secondi, e produce
   `tests/fixtures/corpus_annotations.json` con l'hash del modulo di partenza.

## D011 — F2 a fetta verticale, con l'elenco delle ondate già nel ledger (2026-10-27)

F2 è la fase più grande del piano e l'unica con un rischio architetturale vero:
se il modello degli effetti è sbagliato, si scopre venti funzioni dopo. Quindi:

1. **Prima una fetta verticale completa**: un solo effetto con keyframe e una sola
   transizione, dall'EDL 3 fino al render e a un frame di riferimento. Se
   l'architettura ha un difetto, lo si paga su una funzione.
2. **Tutte le ondate stanno nel ledger da subito**, altrimenti «a ondate» diventa
   «e il resto dopo», che è il modo gentile di non finirlo.

Tre trappole decise prima di scrivere il compilatore, non scoperte dentro:

- **Backend per effetto.** Ogni voce del registro dichiara come si realizza —
  espressione ffmpeg, `sendcmd`, overlay Chromium, o frame per frame — con una
  regola esplicita: oltre un certo numero di keyframe l'espressione diventa
  illeggibile e si passa a `sendcmd`; se l'effetto non è esprimibile in ffmpeg si
  passa a Chromium o al processore di frame.
- **Speed ramp e audio.** `atempo` non varia nel tempo. Il contratto dichiara la
  politica: audio a segmenti con `atempo` per tratto (preferita), oppure
  mantenimento del pitch, oppure silenzio. Una politica dichiarata nel documento,
  non una sorpresa nel renderer.
- **Reverse e freeze** hanno un tetto di durata dichiarato, perché decodificano
  l'intera clip in memoria.
