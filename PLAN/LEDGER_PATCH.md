# LEDGER PATCH

Righe pronte da incollare in `PLAN/MASTER_LEDGER.md`. Stato `TODO` per tutte.
Nessuna riga esistente viene riscritta o rinumerata: le righe con `AMENDS`
dichiarano l'emendamento e lasciano l'originale intatto.

Convenzione colonne, identica al ledger: fase esistente F0/F1/F5 usa
`| ID | Task | Accettazione binaria | Stato |`; F2 usa
`| ID | Ondata | Contenuto | Accettazione binaria | Stato |`. Le fasi nuove
F20-F24 usano la forma a quattro colonne.

---

## F0 — Fondamenta (aggiungere dopo `F0-T10`)

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F0-T11 | Registro di licenza musicale per traccia, con regime `private`/`public` | in `publication=public` un lavoro con una traccia priva di `licence_type`, `licence_url` e `retrieved_at` fallisce; in `publication=private` nessun cancello e il record porta `licence=unverified` con `sha256` | TODO |
| F0-T12 | Stato `PROVISIONAL` e separazione delle due cause di provvisorietà | il test di certificazione fallisce se l'esame è certificato con una fase `PROVISIONAL` o con un asse `unmeasured`; i 22 assert di `test_threshold_provenance.py` restano verdi; `composition` e `typography` non sono più `provisional` per assenza di driver | TODO |
| F0-T13 | Tool locale di annotazione a quattro slider, senza prefill | il tool non scrive nulla finché l'operatore non muove uno slider; il file prodotto passa `tests/import_annotations.py`; un test verifica che nessun valore sia precompilato e che gli assi prodotti siano `palette`/`grade`/`animation`/`cut_density` | TODO |

---

## F1 — Ponte (aggiungere dopo `F1-T04`)

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F1-T05 | `AMENDS F1-T04` — le due tracce portano `sha256` e `licence` nel referto | due tracce mixate con ducking distinto; ogni traccia ha `sha256` e `licence`; in `publication=public` una traccia senza licenza verificata fallisce, in `private` no | TODO |

---

## F2 — EDL 3 e renderer (aggiungere dopo `F2-T09`)

| ID | Ondata | Contenuto | Accettazione binaria | Stato |
|---|---|---|---|---|
| F2-T10 | W7 | `AMENDS F2-T09` — sigillo del documento creativo: hash dell'EDL, curva di loudness e tre frame | un EDL con hash o curva di loudness diversi dal golden fallisce nominando il campo; un EDL identico passa con zero differenze | TODO |

---

## F5 — Audio e musica (aggiungere dopo `F5-T04`)

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F5-T05 | Design del suono legato alle opportunità: riser, impatto, whoosh come trattamenti con evidenza | un whoosh senza opportunità fallisce la validazione; un riser prima di un climax con opportunità passa; ogni evento SFX cita la sua evidenza nel referto | TODO |
| F5-T06 | Libreria SFX con provenienza e licenza per file | ogni file SFX ha `sha256`, `source`, `licence`; in `publication=public` un file senza licenza verificata non entra, in `private` entra come `unverified` | TODO |

---

## F20 — Segnale esterno e certificazione

Accettazione di fase: su un corpus a coppie con ordine scambiato il report produce
preferenza, indistinguibilità e intervallo di Wilson; `test_exam_certification.py`
fallisce se l'esame è certificato senza un blind test registrato sopra le soglie
calibrate; una ricetta senza osservazione esterna resta `candidate`.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F20-T01 | Protocollo blind test e corpus a coppie | con lo stesso seed due esecuzioni producono lo stesso ordine; ogni coppia dichiara durata uguale entro un frame, stessa risoluzione, nessun branding, audio allo stesso LUFS misurato | TODO |
| F20-T02 | Strumento di raccolta voti che scrive un artefatto | dopo N voti esiste un JSON con coppia, ordine, scelta (`A`/`B`/`pari`), tempo di risposta e sha256 dei due video; il file non cambia più dopo la chiusura | TODO |
| F20-T03 | Statistica: preferenza, indistinguibilità, Wilson, numerosità | su un campione sintetico con esito noto i tre numeri coincidono entro `1e-9`; la numerosità minima è dichiarata per la potenza scelta | TODO |
| F20-T04 | Cancello di certificazione dell'esame | `test_exam_certification.py` fallisce se l'esame è certificato senza blind test registrato, o con preferenza o indistinguibilità sotto le soglie calibrate | TODO |
| F20-T05 | Import retention CSV e attribuzione al tempo dell'EDL | un CSV con timestamp e retention produce una mappa tempo→decisione; un calo senza decisione associata è dichiarato `unattributed`, non attribuito d'ufficio | TODO |
| F20-T06 | Promozione ricette solo con provenienza esterna (`AMENDS F19`) | una ricetta senza osservazione esterna resta `candidate`; una ricetta senza dati dichiara `no_data`; il test fallisce se una ricetta viene promossa senza provenienza | TODO |
| F20-T07 | Set di calibrazione del Critic da giudizi umani (`AMENDS F9`) | il Critic è confrontato con giudizi umani registrati sugli assi; il test fallisce se la correlazione scende sotto la soglia calibrata | TODO |

---

## F21 — Legge delle opportunità

Accettazione di fase: un trattamento creativo con solo un'opportunità passa; senza
né finding né opportunità fallisce in piano, pre-render e render; ogni rilevatore
dichiara la misura che ha prodotto l'opportunità; il referto elenca trovate, usate
e scartate.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F21-T01 | Schema `opportunity` in `contract.py` | un'opportunità senza `tipo`, `t0`, `t1`, `intensita` o `fonte` è rifiutata; i tipi ammessi sono un insieme chiuso e un tipo ignoto è rifiutato | TODO |
| F21-T02 | Enforcement nei tre punti (piano, pre-render, render) | un trattamento creativo con solo un'opportunità passa in tutti e tre; senza né finding né opportunità fallisce in tutti e tre; un'opportunità con span che non combacia fallisce | TODO |
| F21-T03 | Rilevatore picchi di energia | su un segnale sintetico con tre picchi noti restituisce tre opportunità, ognuna con la misura che l'ha prodotta | TODO |
| F21-T04 | Rilevatore pause significative | una pausa di durata nota produce un'opportunità con `t0`/`t1` entro un frame; una pausa sotto soglia non produce nulla | TODO |
| F21-T05 | Referto delle opportunità | il referto elenca trovate, usate e scartate con motivo; il conteggio delle usate coincide con i trattamenti dell'EDL che le citano | TODO |
| F21-T06 | Suite di severità equivalente a `test_evidence_law.py` | copre opportunità stantia, span che non combacia, trattamento senza evidenza, tipo ignoto; nessun caso passa per caso | TODO |

---

## F22 — Direzione e ritmo

Accettazione di fase: tre curve di riferimento (talking head, intervista, promo)
hanno golden EDL; lo scostamento misurato/target è nel referto per ogni segmento;
un video a densità costante fallisce il cancello di contrasto.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F22-T01 | Schema `intent_curve` | ogni segmento dichiara densità, energia, silenzio, movimento e testo; un segmento senza target è rifiutato; la somma dei segmenti copre la timeline senza buchi | TODO |
| F22-T02 | Verifica al render per segmento | lo scostamento misurato/target è nel referto per ogni segmento; uno scostamento oltre la tolleranza dichiarata fallisce | TODO |
| F22-T03 | Tre curve di riferimento con golden EDL | talking head, intervista e promo producono tre EDL con hash del documento e frame; il test fallisce se una curva cambia senza un golden nuovo | TODO |
| F22-T04 | `rhythm_curve` da misura e intenzione | la curva deriva dai tagli misurati e dai target; due EDL con la stessa curva hanno lo stesso hash della curva | TODO |
| F22-T05 | Cancello di contrasto | un video a densità costante fallisce; uno con varianza sopra la soglia calibrata passa; nessuna sezione oltre la quota dichiarata può avere densità costante | TODO |
| F22-T06 | Referto del ritmo | il referto porta densità per segmento, varianza e sezioni costanti con la loro quota di durata | TODO |

---

## F23 — Continuità visiva

Accettazione di fase: due clip volutamente diverse superano la soglia e il
documento fallisce; due clip con lo stesso look passano; ogni coppia dichiara
distanza, soglia ed esito.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F23-T01 | Distanza colore shot-to-shot normalizzata sull'esposizione | due clip con lo stesso look hanno distanza sotto soglia; due clip volutamente diverse la superano; il valore è riproducibile a parità di input | TODO |
| F23-T02 | Cancello di continuità | un EDL con un salto oltre soglia fallisce la validazione, con i due shot e i due valori nominati | TODO |
| F23-T03 | Referto della continuità | il referto elenca coppie, distanza, soglia ed esito; il conteggio coincide con le coppie adiacenti dell'EDL | TODO |

---

## F24 — Interoperabilità come posizionamento

Nessuna riga: fase al livello della consegna, come impone D003. Intestazione da
incollare dopo F23.

> ## F24 — Interoperabilità come posizionamento
>
> OTIO e FCPXML come formato di consegna a un NLE professionale, non come export
> minore. Accettazione di fase: un EDL del sottoinsieme dichiarato fa andata e
> ritorno con perdita zero documentata; un NLE reale apre il file; i casi fuori
> sottoinsieme sono dichiarati.
>
> Decomposizione all'ingresso (D003): la libreria OTIO, la versione e il
> sottoinsieme supportato non sono conoscibili oggi.

---

## Emendamenti di fase senza riga di task

Queste fasi non hanno una tabella di task: l'emendamento vive nella sezione 4 di
`PLAN/WOW_INTEGRATION.md` e si applica quando la fase entra.

- **F9** — l'accettazione aggiunge la correlazione con i giudizi umani (F20-T07).
- **F12** — aggiunge il tetto di durata della suite (D026) e il cambio dichiarato
  del riempimento predefinito (D022), una volta sola.
- **F18** — il torneo si estende al protocollo cieco e al corpus contro montaggi
  umani (F20-T04).
- **F19** — la promozione richiede provenienza esterna (F20-T06).

---

## Nota di igiene già applicata

`F0-T10` non è più `TODO`: il meccanismo esiste dal commit `1fea22a` e la riga è
stata allineata il 27 ottobre con le tre verifiche di
`tests/test_threshold_provenance.py`. Non è un task nuovo, è una riga che diceva
il falso.
