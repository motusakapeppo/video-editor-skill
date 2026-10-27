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
