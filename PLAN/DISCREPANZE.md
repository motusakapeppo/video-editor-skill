# Discrepanze fra istruzioni e repository

Regola: quando un'istruzione cita qualcosa che nel repository non c'è, o lo descrive
diversamente, non si esegue alla lettera e non si tace: si registra qui con la prova.
Data: 27/10/2026.

| # | Premessa dell'istruzione | Realtà verificata | Prova | Cosa ho fatto |
|---|---|---|---|---|
| D-1 | «F2 in corso, riprendi dal primo task aperto» | F2 è **9/9 DONE**: non esiste un task aperto | `PLAN/MASTER_LEDGER.md`, righe `F2-T01`..`F2-T09` | F2 riaperta come ondata **W8** con ID nuovi, non "ripresa" |
| D-2 | «`provenance` per ogni scelta: verifica che sia già nello schema» | Non c'è: **zero occorrenze** di `provenance` e `evidence` in `edl3.py` | `grep -c` su `scripts/vx/edl3.py` | Registrata come campo **da creare**, non da verificare |
| D-3 | «Verifica che le 50 uccisioni usassero TerminateProcess» | **Non esiste alcuna terminazione reale** nei test: nessun `taskkill`, `TerminateProcess`, `SIGKILL`, `kill(` | grep su `tests/*.py`; `test_artifacts.py:134` simula il crash in-process | F17 **non è chiusa** secondo il criterio originario; aperta `F17-T08` |
| D-4 | «`LICENSES_MATRIX.md`» come se esistesse | **Assente** in radice e in tutto il repo | ricerca ricorsiva sul nome file | Creato il 27/10 in `9ff15dd`, con versioni lette dai metadati e dal binario |
| D-5 | «Aggiungi il test per il modulo di lettura web» | Il modulo **non esiste**: F8 (Trend Radar) non è iniziata | nessun modulo di rete in `scripts/vx`; `tests/test_events.py` prova solo l'assenza di import di rete | Registrato come task di F8: il test nasce col modulo, non prima |
| D-6 | «schema `vx.edl3/2`» e, nello stesso testo, «migratore 3.0 → 3.1» | Contraddizione: lo schema attuale è **`vx.edl3/1`**. La politica proposta dice «campi opzionali = aumento minore senza migratore» | `scripts/vx/edl3.py:3` dichiara `vx.edl3/1` | Proposta `vx.edl3/1.1`: additiva, il lettore accetta `/1` e `/1.1`, **nessun migratore** (vedi sotto) |
| D-7 | «sezione 5 livello D» | Non esiste un «livello D»: i livelli sono **L0, L1, L2** | `PLAN/MASTER_PLAN.md:209` | Nessuna correzione necessaria per quel motivo |
| D-8 | «la dissolvenza incrociata è una transizione disponibile» | `TRANSITION_KINDS` elenca `crossfade`, ma il compilatore la rifiuta: `implemented: ['fade_in', 'fade_out']` | montaggio del vivo: `UNSUPPORTED_TRANSITION: transition kind 'crossfade' is not implemented (clips[27])` | o si implementa o si toglie dal contratto; intanto il montaggio usa tagli secchi |
| D-9 | «la dimensione del canvas si legge dal file» | `ffprobe` riporta la dimensione **codificata** (1920x1080), ma i file dei live hanno un display matrix che li ruota: il contenuto e' **1080x1920** | primo render a 1920x1080: contenuto verticale con bande nere laterali; misurato sui tre file, poi rifatto a 1080x1920 e il contenuto riempie il fotogramma | la rotazione va letta prima di scegliere il canvas: task per F6 (comprensione del materiale) |

## Sulla D-6, la decisione che propongo

La politica che hai chiesto — *additiva, campi opzionali, campi sconosciuti
conservati* — rende il migratore inutile. Se nessun campo nuovo è obbligatorio e
nessun campo vecchio cambia significato, un documento `/1` è già un documento
valido; l'unica cosa che cambia è l'etichetta di versione.

Quindi:

1. La versione diventa **`vx.edl3/1.1`**, non `/2`.
2. Il lettore accetta **sia `/1` sia `/1.1`** e conserva i campi `x-` sconosciuti.
3. **Nessun migratore nuovo.** Al suo posto un test di compatibilità: i documenti
   `/1` reali validano e producono gli stessi frame di prima.
4. Se un giorno serve una modifica non compatibile, quella sarà `/2` e avrà il suo
   migratore. Non prima.

Questo è più economico, coerente con la politica, e toglie di mezzo il «secondo
migratore» che era il rischio principale.

## Effetto sui task appena registrati

`F2-T10` e `F2-T11` sono riscritti di conseguenza: la fetta verticale resta, il
migratore sparisce, al suo posto c'è la prova di compatibilità e l'identità dei 34
frame.

## Come si usa questo documento

Ogni nuova istruzione che citi file, moduli, prove o numeri viene confrontata con il
repository **prima** di essere eseguita. Se non corrisponde, si aggiunge una riga
qui e si procede con la realtà, non con l'istruzione.
