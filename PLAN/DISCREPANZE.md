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
| D-9 | "la dimensione del canvas non tiene conto del display matrix" | Un clip verticale di telefono, se il contenitore dichiara la rotazione, usciva orizzontale con bande nere: la misura leggeva la larghezza del flusso, non quella di visualizzazione | `PLAN/DISCREPANZE.md`, 28/10/2026; rinviata a F6 | **CHIUSA il 2/11/2026** (commit `d9`): `probe._rotation` legge la side data (e il tag `rotate` come ripiego) e `probe()` scambia larghezza e altezza per 90/270, quindi la tela nasce gia' verticale; il decode e' quello di ffmpeg, che autoruota. Prova su un clip sintetico con banda rossa sul terzo sinistro e matrice a 90: `probe` riporta **180x320, rotazione 90**, il proxy esce **180x320** e la banda finisce su un estremo orizzontale (rosso in alto -127 / in basso 252,8, ai lati 1,2), cioe' il fotogramma e' dritto e non incorniciato. `tests/test_rotation.py` 5 PASS |
| D-10 | «un effetto geometrico puo' cambiare il canvas, il palco lo riporta» | `zoompan` e `dolly_zoom` emettono **1280x720** (default hd720) su qualunque input: su un canvas verticale 1080x1920 il palco riscala 720 pixel a 1920 e l'immagine risulta molle | montaggio del coro, frame a 26,8 s visibilmente sfocato rispetto ai vicini; i template non dichiarano `s=` | servono `{canvas_w}`/`{canvas_h}` nell'emettitore e i due template aggiornati; i due fixture di riferimento vanno ri-benedetti **Risolta il 28/10/2026:** i segnaposto `{canvas_w}`/`{canvas_h}` entrano nell'emettitore (riservati, non impostabili da un documento) e i due template emettono `s=<canvas>`; la nitidezza su un 1080x1920 passa da varianza del laplaciano **4,8** (l'1,1% del clip nudo) a **208,2** (49,3%), e i due frame di riferimento sono ri-benedetti |
| D-11 | "le cartelle temporanee si puliscono da sole" | Nove moduli creavano con `tempfile.mkdtemp` e non rimuovevano mai: `vad.py`, `bpm.py`, `accel.py`, `grammar.py`, `prove.py`, `gates.py`, piu' due test (`test_typography`, `test_hostile_inputs`). Le cartelle `vx*` si accumulavano a ogni esecuzione | pulizia del 28/10/2026: **2034 cartelle, 2,5 GB** nella sola cartella temporanea di Windows, tutte prodotte dalla suite | **CHIUSA il 2/11/2026** (commit `d11`): i moduli usano `TemporaryDirectory` intorno al blocco che consuma i byte (vad, bpm, grammar, accel, prove) o una pulizia registata all'uscita quando il file deve sopravvivere alla funzione (il wav del probe whisper, la workdir di `gates --selftest`); i due test registrano `shutil.rmtree` con `atexit`. Il gate e' `tests/test_temp_hygiene.py`: chiama davvero `beat_grid`, `bpm.estimate`, `vad.detect` e `test_encode`, confronta la cartella temporanea prima e dopo e fallisce se compare una cartella `vx*` (5 PASS, nessuna cartella) |
| D-12 | "una timeline lunga si rende in un colpo solo" | Con decine di clip in un solo grafo, ffmpeg accumula ingressi e buffer: un render da **60 s** a 1080x1920 e' arrivato a **5,8 GB** di RSS e oltre 11 minuti su un solo processo, senza scrivere un fotogramma | processo ffmpeg del test dei profili (28/10/2026, 21:51-22:02): 249 s di CPU, 5.848 MB di working set | **CHIUSA il 2/11/2026** (commit `d12`): il percorso a chunk esisteva ma andava chiesto; ora `render()` lo sceglie da solo sopra **120 s** di documento (`auto_chunks`: chunk da 20 s, quindi 7 pezzi a 120,1 s e 360 a due ore) e lo dichiara con `chunks_auto`. Sotto i 120 s resta il grafo unico di sempre, intatto. La memoria del percorso a chunk e' quella misurata in F26-T06: **1.085-1.517 MB** per profilo contro i **5.848 MB** del grafo unico. Prova: `tests/test_auto_chunks.py` 7 PASS (soglie e percorso scelto) |
| D-13 | "il master per profilo sta entro 0,5 LU" | Su materiale dinamico (musica sotto voce reale, 40 s) il master a due passaggi si fermava a **0,9 LU** dal bersaglio: richiesto -14,0, misurato **-14,9**; `qc.audit_master` lo accettava perche' la sua tolleranza e' 1,5 LU, ma `profile_master` dichiarava 0,5 | `reel_amici_finale.mp4`, 28/10/2026 | **CHIUSA il 2/11/2026** (commit `d13`): dopo il secondo passaggio il master misura il file prodotto e, se manca piu' di **0,2 LU** e meno di **2,0 LU**, applica un guadagno lineare con lo stesso limitatore di picco e rimisura, fino a due volte; oltre 2 LU non e' piu' una rifinitura ed e' una dichiarazione. Il referto porta `tolerance_lu`, `within_tolerance` e `trim_passes`. Prova sullo stesso file: **-14,9 -> -14,1 LUFS**, un trim di +0,36 dB, scarto finale **0,100 LU** su tolleranza 0,5; `tests/test_profile_loudness.py` 31 PASS |
| D-14 | AF7 cita E1, E5, F2B, F17B che nel documento non esistono | vivono in `MASTER_PLAN.md` (Allegato E), nella versione `vx.edl3/1.2` e in F17-T08/T09 | `PLAN/DOC_VS_REPO.md` riga 1 |
| D-15 | l'esame del documento salta da 19 a 26 e manca l'Allegato E | gli incarichi 20-25 sono definiti nel ledger (righe 477-478) e l'Allegato E esiste | `PLAN/DOC_VS_REPO.md` riga 2 |
| D-16 | F17 e l'incarico 13 parlano ancora di `kill -9` | la prova usa `taskkill /F /T` su Windows e `SIGKILL` su POSIX, con l'albero dei figli | `tests/_crash.py:kill_tree`, `PLAN/DOC_VS_REPO.md` riga 3 |
| D-17 | `LICENSES_MATRIX.md` citata ma il task che la crea e' solo in chat | il file esiste, con gate da 95 assert (riga mancante o verifica scaduta) | `tests/test_licenses_matrix.py`, `PLAN/DOC_VS_REPO.md` riga 4 |
| D-18 | intestazioni vecchie: Versione 1, «due parti», «F0-F12», «12 incarichi» | 32 fasi, 27 incarichi, sei addendum | `PLAN/ANNEX_F.md` conteggi; `PLAN/DOC_VS_REPO.md` riga 5 |
| D-19 | testo superato ancora scritto (sezione 9 punto 7, Allegato C3), sostituito solo da una frase di precedenza | le sei frasi sono marcate `SUPERATO` con il sostituto accanto | `PLAN/PRECEDENCE.md`, commit `6c68390`, `PLAN/DOC_VS_REPO.md` riga 6 |
| D-20 | l'analisi Higgsfield e il modulo T09 non sono nel documento | T09 e' un file esterno e blocca F0-T09; Higgsfield non e' registrato da nessuna parte | `PLAN/DOC_VS_REPO.md` riga 7 |
| D-21 | "la nitidezza e' la varianza del laplaciano" | Sui fotogrammi scuri la varianza del laplaciano misura il rumore e il mosso, non il dettaglio: c01 (luma **38,7**, giudicata "mosso, sfuocata") misura **2,32** e supera c03 (**88,7**, "buona", **1,92**) | sette giudizi T09 raccolti prima dei numeri | **CHIUSA il 2/11/2026** (commit `d21`): cinque metriche candidate sono state misurate sugli stessi fotogrammi dei dieci clip - grezza, CLAHE, gamma 1/2,2, normalizzata per varianza, normalizzata per gradiente - e **nessuna** inverte la coppia c01/c03: il clip scuro ha davvero piu' energia in alta frequenza, ed e' rumore. La conclusione onesta non e' una normalizzazione: sotto **60** di luma media il numero e' una descrizione e il gate non lo usa. `visual.measure_sharpness` riporta `mean_luma` e `reliable`; `asset_triage` mette il valore in `sharpness_descriptive` e dichiara `video_sharpness` in `not_evaluated`. Prova: `tests/test_sharpness_lowlight.py` 5 PASS (clip scuro luma 34,2 unreliable; chiaro 126,6 reliable) |

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
