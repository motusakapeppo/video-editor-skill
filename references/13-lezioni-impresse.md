# 13 — Lezioni impresse

Ogni regola qui sotto è **eseguita dal codice**, non raccomandata. La colonna a destra
dice dove. Una regola senza un posto nel codice non è una lezione: è un proposito.

| # | Regola | Dove è imposta |
|---|---|---|
| 1 | Nessun render senza brief firmato dal direttore | `pipeline.run(verdict=...)` solleva `QCFailure` se `verdict` manca; `board.merge()` rifiuta un brief non firmato |
| 2 | Domini esclusivi: nessun ruolo invade un altro | `board.ROLES[*]["owns"]` + `board.validate()` |
| 3 | Una proposta deve citare una misura, o è scartata | `board.validate()` → `cited` obbligatorio |
| 4 | Il direttore risolve i conflitti e ha veto di coerenza | `board.convene()` → i valori del direttore sovrascrivono e il conflitto viene annotato |
| 5 | Anti-arlecchino: max 2 trattamenti distinti, eccezione solo su richiesta esplicita | `board.verify_animation()` |
| 6 | Un agente si convoca solo se il principio non chiude la questione | `board.open_domains()` + `CLOSED_BY_PRINCIPLE` |
| 7 | Il contesto porta il **metodo**, non solo i dati | `board.METHODS` + `board.context_for()` (formula WCAG con esempio svolto) |
| 8 | Il verificatore ricalcola i numeri che l'agente dichiara | `board.verify_numbers()` → rimanda indietro con i due rapporti |
| 9 | Il motore si deduce dalle capacità dell'animazione, non da preferenza | `board.required_engine()` + `ANIMATION_CAPABILITIES` |
| 10 | Il contrasto si calcola con linearizzazione sRGB, non con soglie | `board._contrast_ink()` |
| 11 | Il colore lo fa FFmpeg (YUV intero), la grafica il compositor | `pipeline` → passi `grade_ffmpeg` / `composite_*` |
| 12 | I frame vengono dal video e si quantizzano sui confini | `motion.expected_frames()` + `trim=end_frame` |
| 13 | Le tolleranze non si allargano per far passare un difetto | `gates.COLOR_TOLERANCE`, `post_render` |
| 14 | Una misura va presa nella regione dove sta l'elemento | **GAP**: `creative.analyse` misura ancora il fotogramma intero; la luma della fascia testo va calcolata dentro l'analisi |
| 15 | Il peso di un font si stima con la copertura d'inchiostro, non con la larghezza della "I" | **GAP**: `creative._measure_font` usa ancora lo stem della "I", che premia i corsivi |

## Le tre volte in cui il colpevole era il dato

1. **Desaturazione** — mancava il metodo (YUV vs replicazione di luma).
2. **Contrasto** — mancava la formula (linearizzazione sRGB).
3. **Inchiostro** — mancava la finestra di misura (fotogramma intero invece della fascia).

In tutti e tre i casi l'agente aveva ragionato correttamente su un input sbagliato. La
regola che ne deriva: **quando un agente sbaglia, si guarda prima il contesto che ha
ricevuto, poi il suo giudizio.** E quando l'errore si ripete, il contesto si corregge
nel codice, non nella conversazione.

## L'errore che ha generato la regola 1

Uno spot è stato renderizzato scrivendo a mano font, inchiostro e animazioni, saltando
la giuria, e presentato come lavoro della skill. Il difetto non era la distrazione: era
che il pipeline **permetteva** quel percorso. Ora non lo permette più.
