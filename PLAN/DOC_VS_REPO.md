# Il documento sorgente contro il repository

Il file `video-editor Piano Maestro e Prompt di Esecuzione (2).md` e' una **fotografia** del
piano al 27/10/2026. Il repository e' piu' avanti: le correzioni nate nei messaggi successivi
sono state applicate qui e non nel documento. Regola: **in caso di divergenza vince il
repository**; questo file dice, riga per riga, dove sta la verita'.

Verificato il 28/10/2026.

| # | Cosa dice il documento | Cosa dice il repository | Dove |
|---|---|---|---|
| 1 | AF7 e l'introduzione citano **E1, E5, F2B, F17B**, che nel documento non esistono | E1 (Look Deck) ed E5 (Structure Cloning) stanno nell'**Allegato E**; `F2B` e' diventato lo schema `vx.edl3/1.1` e poi `/1.2`; `F17B` e' F17-T08/T09 (50 uccisioni reali + guardia di sistema) | `PLAN/MASTER_PLAN.md` sezione "Allegato E" (E1 a riga 530); `PLAN/MASTER_LEDGER.md` F17-T08/T09 e F25-T01..; `PLAN/DECISIONS_PATCH.md` D028 |
| 2 | L'esame salta da 19 a 26; gli incarichi **20-25** e l'**Allegato E** mancano; le lettere sono A, B, C, D, F | Gli incarichi 20-25 sono definiti (Look Deck, Brand Connect, clonazione della struttura, ispettore, montaggio a parole, varianti) e l'Allegato E esiste | `PLAN/MASTER_LEDGER.md` righe 477-478 ("L'esame passa a 25 incarichi..."); `PLAN/MASTER_PLAN.md` "Allegato E" |
| 3 | F17 e l'incarico 13 parlano ancora di **`kill -9`** | La prova usa la terminazione reale del processo: `taskkill /F /T` su Windows, `SIGKILL` su POSIX, con l'albero dei figli | `tests/test_f17_crash.py`, `tests/_crash.py` (`kill_tree`), ledger F17-T08 |
| 4 | `LICENSES_MATRIX.md` e' citata tre volte, ma il task che la crea (F0C) e' solo in chat | Il file esiste ed e' protetto da un gate: 95 assert, fallisce su riga mancante o verifica piu' vecchia di dodici mesi | `LICENSES_MATRIX.md`, `tests/test_licenses_matrix.py`, ledger F0-T11 |
| 5 | Intestazione "Versione 1 - 27 ottobre 2026", sezione 0 "due parti", sezione 4 "F0-F12", sezione 6 "12 incarichi" | 32 fasi (F0-F31), 27 incarichi (1-19 e 26-33), sei addendum al prompt | `PLAN/ANNEX_F.md` "Conteggi dichiarati"; `PLAN/MASTER_LEDGER.md` |
| 6 | Il punto 7 della sezione 9 ("solo fonti A, B, C") e l'Allegato C3 (provider Instagram e TikTok) restano scritti per intero; l'Allegato D li sostituisce solo con una frase di precedenza | Le sei frasi superate sono marcate `SUPERATO` con il sostituto accanto | `PLAN/PRECEDENCE.md`; commit `6c68390` |
| 7 | L'analisi su **Higgsfield** e il modulo **T09** (`.xlsx`) non sono nel documento | T09 e' un file esterno citato dal ledger (blocco F0-T09); l'analisi Higgsfield non e' mai stata registrata nel repo | `PLAN/MASTER_LEDGER.md` F0-T09; nessun file per Higgsfield |

## Cosa farne

1. **Non riscrivere il documento dell'utente**: resta la sua fotografia datata.
2. **Prima di passarlo a un altro agente**, passare invece `MASTER_PLAN.md` + `MASTER_LEDGER.md` +
   `ANNEX_F.md` + questo file: sono lo stato corrente.
3. I due pezzi mai registrati (**Higgsfield**, **T09**) vanno chiesti all'utente: senza il file,
   T09 resta `BLOCKED` e nessuna soglia che ne dipende si puo' calibrare.
