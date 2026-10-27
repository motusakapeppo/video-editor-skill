# WOW INTEGRATION

Estensione di `PLAN/MASTER_PLAN.md` e `PLAN/MASTER_LEDGER.md` con il livello che
decide se il risultato è memorabile, non solo corretto. Data: 27 ottobre 2026.

Questo documento **non sostituisce** nulla: aggiunge fasi, task ed emendamenti.
Le righe pronte da incollare sono in `PLAN/LEDGER_PATCH.md`; le decisioni nuove
in `PLAN/DECISIONS_PATCH.md`.

Legenda dei verdetti: **da creare** (non esiste nulla), **da estendere** (esiste
una regola, un criterio o un meccanismo, manca l'artefatto o il cancello),
**assorbito** (esiste già un meccanismo che il task nuovo deve riusare, non
duplicare).

---

## 1. Verdetto

Il piano è forte su ciò che si misura dentro il sistema e debole su ciò che si
misura fuori. Delle cinque assenze dichiarate, tre sono assenze letterali:
`retention`, `blind` e `curva` hanno **zero occorrenze** in `MASTER_PLAN.md` e
`MASTER_LEDGER.md`. La quarta è una regola senza artefatto: `MASTER_PLAN.md:177`
dice che musica e SFX devono essere «CC0 o CC-BY con attribuzione registrata», ma
nessun file dice quale traccia è stata usata, con quale licenza, verificata quando.
La quinta è un concetto che esiste solo al negativo: il piano conosce i difetti
(`MASTER_PLAN.md:102`, «10 EDL con difetti seminati») e una classifica dei momenti
migliori (`MASTER_PLAN.md:82`), ma non un'entità positiva — l'opportunità — con la
stessa dignità probatoria del finding.

Il piano è **sovradimensionato dove il volume è facile**: F4 chiede 60 voci di
registro con prova visiva (`MASTER_PLAN.md:70`), F15 importa 100+ transizioni
(`MASTER_PLAN.md:244`). Sono lavori giusti, ma sono elenchi: costano tempo
proporzionale al numero. È **sottodimensionato dove il volume è difficile**: F9 non
ha un set di calibrazione, quindi il Critico è giudice di sé stesso; F19 promuove
le ricette su un benchmark interno (`MASTER_PLAN.md:270`) e il ledger ha già perso
il segnale utente che il piano chiedeva (`MASTER_PLAN.md:268` contro
`MASTER_LEDGER.md:219`); l'esame finale non contiene un solo numero esterno
(`MASTER_PLAN.md:153-166`).

Due difetti documentali trovati leggendo, non dedotti: il ledger **non ha
l'intestazione `## F18`**, la riga `MASTER_LEDGER.md:214-215` è un paragrafo
orfano; e `MASTER_LEDGER.md:219` riduce la promozione delle ricette a «promozione
sopra soglia», cancellando la «valutazione dell'utente» che il piano richiede.

---

## 2. Tabella di tracciamento

| gap | dove esiste già nel piano o nel codice | verdetto | nuovo ID | fase | dipendenze |
|---|---|---|---|---|---|
| A. Segnale esterno: blind test contro montaggi umani | Solo parziale: `MASTER_PLAN.md:264` (accordo con l'utente 7/10 su coppie), `:268` (valutazione utente nella consegna F19), `:145` (esiti dopo la consegna). Nessun protocollo cieco, nessun corpus contro montaggi umani | da estendere | F20-T01..T04 | F20 | F9, F18, materiale reale |
| B. Ciclo di risultato reale (retention) | Assente: zero occorrenze di `retention`/`ritenzione`. Vicini non equivalenti: `MASTER_PLAN.md:308` (statistiche Insights/Analytics come input), `:355` (metriche di mercato S2) | da creare | F20-T05, T06 | F20 | F19, F20-T01 |
| C. Legge delle opportunità | Parziale: `MASTER_PLAN.md:82` («classifica dei momenti migliori»), `:102` (solo difetti), `MASTER_LEDGER.md:88` (il finding è l'unica àncora). Il meccanismo dei tre punti esiste ed è riusabile: `scripts/vx/contract.py:630`, `:700`, `:851`, `scripts/vx/gates.py:153`, `scripts/vx/render.py:1882` | da estendere | F21-T01..T06 | F21 | F6, `contract.py` |
| D. Curva di intenzione per segmento | Assente: zero occorrenze di `curva`. Vicini non equivalenti: `MASTER_PLAN.md:88` («struttura, ritmo» in una lista), `:82` (picchi misurati), `:141` (energia di movimento misurata) | da creare | F22-T01..T03 | F22 | F7, F6 |
| E. Ritmo come grandezza con contrasto | Parziale: `MASTER_PLAN.md:100` (asse «ritmo», senza metrica), `:141` (tagli ogni 10 s, mediana e IQR), `MASTER_LEDGER.md:53` (densità di taglio annotata). Il contrasto *fra due brief* esiste (`references/14-lezioni-impresse.md:86`), quello *dentro un montaggio* no | da estendere | F22-T04..T06 | F22 | F22-T01, F0-T09 |
| F. Design del suono legato alle opportunità | Parziale: `MASTER_PLAN.md:76` (motore SFX), `:221` (stinger/whoosh/riser), `:141` (densità di SFX misurata). La legge delle prove **non elenca gli SFX**: `MASTER_PLAN.md:30` dice «taglio, effetto, grafica, musica, stile» | da estendere | F5-T05, T06 | F5 | F21, F5 |
| G. Licenza musicale per traccia | Parziale: regola senza artefatto. `MASTER_PLAN.md:177`, `:221`, `:127`, `:204` (matrice per componenti), `MASTER_LEDGER.md:90` («dichiarate», non «licenziate») | da estendere | F0-T11, F1-T05 | F0, F1 | D021 |
| H. Shot matching e continuità colore come cancello | Parziale: `MASTER_PLAN.md:70` (LUT con match tra shot), `:100` (asse «coerenza visiva»), `:72` (accettazione F4 senza criterio di continuità), `:160` (incarico 6 «color match») | da estendere | F23-T01..T03 | F23 | F4, F9 |
| I. Stato `PROVISIONAL`: chiusa ≠ certificata | Parziale nel codice: la stringa `provisional` esiste (`scripts/vx/registry.py:347`) e `CORPUS_PROVISIONAL` pure (`:234`), ma il cancello legge il testo del ledger (`tests/test_threshold_provenance.py:154-176`) e non esiste uno stato di fase | da estendere | F0-T12 | F0 | D020, T09 |
| J. T09 accelerato con tool locale | Regola senza tool: il modulo esiste (`tests/fixtures/T09_annotation_template.xlsx`, 15.128 byte), l'importatore esiste (`tests/import_annotations.py`), nessuna interfaccia. **Gli assi non coincidono**: l'importatore valida `shake/exposure/cast/noise/volume/silences/music/hook`, il ledger chiede `palette/grade/animation/cut_density` (`MASTER_LEDGER.md:51-55`) | da creare | F0-T13 | F0 | importatore, D020 |
| K. Riempimento predefinito | Aperto: `PLAN/MASTER_LEDGER.md` non lo fissa, `RIPRESA` lo elenca come decisione pendente. Il valore attuale è in `scripts/vx/render.py` (`FILL_POLICIES`) | decisione, nessun task fino a F12 | D022 | F12 | golden esistenti |
| L. Fetta verticale da ingest a pubblicazione | Non è una fase: è una milestone. Oggi l'ipotesi più importante si verifica solo alla fine (`MASTER_PLAN.md:120`, esame) | milestone M1, nessun ID nuovo | M1 | trasversale | F1, F5, F9, F20-T04 |
| M. Interoperabilità come posizionamento | Parziale: `MASTER_PLAN.md:106` (OTIO/FCPXML in una lista di export), `:108` (validazione OTIO), `references/26-edl3-e-ponte.md:415` (D2 aperto), `:562` (posizionamento generico «non un sostituto di DaVinci») | da estendere | F24 (consegna) | F24 | F10, libreria OTIO |
| N. Golden edit, non solo golden frame | Parziale: `MASTER_PLAN.md:96` (frame per ogni effetto), F2-T09 e F3-T08 esistono. Nessun sigillo del **documento creativo** né della curva di loudness | da estendere | F2-T10 (`AMENDS F2-T09`) | F2 | F2-T09 |
| O. Budget di durata della suite | Assente: la suite è a 757 s (`RIPRESA`), nessuna fase dichiara un tetto. `MASTER_PLAN.md:250` (F16) è la leva, ma non è anticipata | da creare | D026 + emendamento F12 | F12, F16 | F16 |
| P. Definizione di «wow» nell'esame | Assente: `MASTER_PLAN.md:153` elenca criteri comuni senza numeri esterni; `:155-166` dodici incarichi senza preferenza, indistinguibilità o retention | da estendere | F20-T04 + D018 | F20, esame | A, B |
| Debito 1. T09 punto unico di fallimento | `MASTER_PLAN.md:84` (corpus 20 clip per F6), `MASTER_PLAN.md:78` (beat sotto soglia F0), `:102` (critico sopra soglia F0), `:264` (torneo calibrato in F0). Un solo dato umano blocca quattro fasi | da estendere | F0-T12, F0-T13 | F0 | D020 |
| Debito 2. Riempimento predefinito | Vedi K | assorbito in D022 | D022 | F12 | — |
| Debito 3. Interoperabilità come export | Vedi M | assorbito in F24 | F24 | F24 | — |
| Debito 4. Golden solo sui frame | Vedi N | assorbito in F2-T10 | F2-T10 | F2 | — |

### Cosa il piano già copre e non va rifatto

1. **I tre punti della legge delle prove.** `scripts/vx/contract.py:630`
   (`validate_evidence_shape`, forma), `scripts/vx/gates.py:153`
   (`validate_evidence`, pre-render), `scripts/vx/render.py:1882` (forma, render).
   Le opportunità si innestano **qui**, non in una legge nuova.
2. **Il torneo a coppie con ordine scambiato.** `MASTER_PLAN.md:262` e `:264`.
   F20 aggiunge il protocollo cieco e il corpus contro montaggi umani; non
   reinventa il torneo.
3. **La validazione OTIO.** `MASTER_PLAN.md:108` («i file passano ffprobe e la
   validazione OTIO»). F24 aggiunge il posizionamento e il round-trip; non
   riscrive l'export.
4. **Il contrasto di ritmo fra due brief.** `references/14-lezioni-impresse.md:86`
   (durata bersaglio diversa di almeno 0.4 s) e `:94-96` (dichiaratamente non
   certifica il montaggio). F22 estende al contrasto **dentro** un montaggio.

---

## 3. Nuove fasi

### F20 — Segnale esterno e certificazione

**Consegna.** Un numero che viene da fuori: blind test contro montaggi umani,
import della retention, e un cancello che impedisce di certificare l'esame senza
quei numeri. Il Critico propone; solo il segnale esterno promuove.

**Accettazione di fase.** Su un corpus a coppie con ordine scambiato, il report
produce preferenza, indistinguibilità e intervallo di Wilson; `test_exam_certification.py`
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

**Dipendenze.** F9 (il Critico da calibrare), F18 (il torneo da estendere), F19
(la promozione), materiale reale. **Rischio.** Il costo vero non è il codice: è
trovare valutatori e montaggi umani di confronto. Se il corpus umano non esiste,
F20-T01 resta BLOCKED e il cancello non si chiude — che è il comportamento
corretto.

### F21 — Legge delle opportunità

**Consegna.** Un'opportunità è un momento di valore con la stessa dignità
probatoria di un difetto. Un trattamento creativo cita un finding **o**
un'opportunità; il controllo vive nei tre punti in cui vive già la legge.

**Accettazione di fase.** Un trattamento creativo con solo un'opportunità passa;
senza né finding né opportunità fallisce in piano, pre-render e render; ogni
rilevatore dichiara la misura che ha prodotto l'opportunità; il referto elenca
trovate, usate e scartate.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F21-T01 | Schema `opportunity` in `contract.py` | un'opportunità senza `tipo`, `t0`, `t1`, `intensita` o `fonte` è rifiutata; i tipi ammessi sono un insieme chiuso e un tipo ignoto è rifiutato | TODO |
| F21-T02 | Enforcement nei tre punti (piano, pre-render, render) | un trattamento creativo con solo un'opportunità passa in tutti e tre; senza né finding né opportunità fallisce in tutti e tre; un'opportunità con span che non combacia fallisce | TODO |
| F21-T03 | Rilevatore picchi di energia | su un segnale sintetico con tre picchi noti restituisce tre opportunità, ognuna con la misura che l'ha prodotta | TODO |
| F21-T04 | Rilevatore pause significative | una pausa di durata nota produce un'opportunità con `t0`/`t1` entro un frame; una pausa sotto soglia non produce nulla | TODO |
| F21-T05 | Referto delle opportunità | il referto elenca trovate, usate e scartate con motivo; il conteggio delle usate coincide con i trattamenti dell'EDL che le citano | TODO |
| F21-T06 | Suite di severità equivalente a `test_evidence_law.py` | copre opportunità stantia, span che non combacia, trattamento senza evidenza, tipo ignoto; nessun caso passa per caso | TODO |

**Dipendenze.** F6 (MaterialMap) per i rilevatori che leggono il testo e il volto.
**Decomposizione parziale (D003).** I rilevatori di risate e picchi di gesto
richiedono F6/F13 e non sono decomposti oggi; entrano come task all'ingresso di
quelle fasi. **Rischio.** Un rilevatore che produce opportunità a ogni frame
trasforma la legge in una formalità: la soglia di intensità va calibrata e il
referto deve mostrare gli scarti.

### F22 — Direzione e ritmo

**Consegna.** Un artefatto unico che rappresenta l'intenzione di montaggio nel
tempo (`intent_curve`: densità, energia, silenzio, movimento, testo) e il ritmo
come grandezza con contrasto misurabile (`rhythm_curve`). Il piano dichiara per
ogni segmento come realizza il target o perché non lo realizza.

**Accettazione di fase.** Tre curve di riferimento (talking head, intervista,
promo) hanno golden EDL; lo scostamento misurato/target è nel referto per ogni
segmento; un video a densità costante **fallisce** il cancello di contrasto.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F22-T01 | Schema `intent_curve` | ogni segmento dichiara densità, energia, silenzio, movimento e testo; un segmento senza target è rifiutato; la somma dei segmenti copre la timeline senza buchi | TODO |
| F22-T02 | Verifica al render per segmento | lo scostamento misurato/target è nel referto per ogni segmento; uno scostamento oltre la tolleranza dichiarata fallisce | TODO |
| F22-T03 | Tre curve di riferimento con golden EDL | talking head, intervista e promo producono tre EDL con hash del documento e frame; il test fallisce se una curva cambia senza un golden nuovo | TODO |
| F22-T04 | `rhythm_curve` da misura e intenzione | la curva deriva dai tagli misurati e dai target; due EDL con la stessa curva hanno lo stesso hash della curva | TODO |
| F22-T05 | Cancello di contrasto | un video a densità costante fallisce; uno con varianza sopra la soglia calibrata passa; nessuna sezione oltre la quota dichiarata può avere densità costante | TODO |
| F22-T06 | Referto del ritmo | il referto porta densità per segmento, varianza e sezioni costanti con la loro quota di durata | TODO |

**Dipendenze.** F7 (IntentSpec/Creative Brief) e F6 (misure). **Riuso.** Il
contrasto fra due brief esiste già (`references/14-lezioni-impresse.md:86`): qui
si estende al montaggio, non si reinventa. **Rischio.** Una curva di intenzione
troppo dettagliata diventa un EDL parallelo: il target è per segmento, non per
frame.

### F23 — Continuità visiva

**Consegna.** La distanza colore fra inquadrature consecutive è un cancello, non
un'opinione. Esposizione normalizzata, soglia dichiarata, scostamento nel referto.

**Accettazione di fase.** Due clip volutamente diverse superano la soglia e il
documento fallisce; due clip con lo stesso look passano; ogni coppia dichiara
distanza, soglia ed esito.

| ID | Task | Accettazione binaria | Stato |
|---|---|---|---|
| F23-T01 | Distanza colore shot-to-shot normalizzata sull'esposizione | due clip con lo stesso look hanno distanza sotto soglia; due clip volutamente diverse la superano; il valore è riproducibile a parità di input | TODO |
| F23-T02 | Cancello di continuità | un EDL con un salto oltre soglia fallisce la validazione, con i due shot e i due valori nominati | TODO |
| F23-T03 | Referto della continuità | il referto elenca coppie, distanza, soglia ed esito; il conteggio coincide con le coppie adiacenti dell'EDL | TODO |

**Dipendenze.** F4 (grading e LUT) e F9 (coerenza visiva). **Rischio.** Una soglia
unica su tutto il montaggio è sbagliata per un video che cambia look di proposito:
la soglia deve poter essere per sezione, con la deroga dichiarata.

### F24 — Interoperabilità come posizionamento

**Consegna.** OTIO e FCPXML come formato di consegna a un NLE professionale: «il
reparto montaggio che lavora mentre l'NLE è chiuso». Non è un export minore.

**Accettazione di fase.** Un EDL del sottoinsieme dichiarato fa andata e ritorno
con perdita zero documentata; un NLE reale apre il file; il sottoinsieme è scritto
e i casi fuori sottoinsieme sono dichiarati, non persi in silenzio.

**Decomposizione all'ingresso (D003).** La scelta della libreria OTIO, la versione
e il sottoinsieme supportato non sono conoscibili oggi: si decompone quando la
libreria è scelta e provata. Oggi restano la consegna e l'accettazione.
**Dipendenze.** F10. **Rischio.** La libreria OTIO può non essere installabile
senza rete: in quel caso F24 diventa un writer minimale dichiarato, non un
round-trip.

---

## 4. Emendamenti a task esistenti

| task esistente | cosa cambia | perché | nuovo criterio di accettazione |
|---|---|---|---|
| `F1-T04` | le due tracce portano `sha256` e `licence` nel referto; in regime privato la licenza non blocca | D021: la licenza è un regime, non un divieto | due tracce mixate con ducking distinto; ogni traccia ha `sha256` e `licence`; in `publication=public` una traccia senza licenza fallisce, in `private` no |
| `F2-T09` | il sigillo copre anche il documento creativo: hash dell'EDL + curva di loudness + tre frame | un golden sui soli frame non accorge di una modifica creativa che non cambia i pixel campionati | `F2-T10`: un EDL con hash o curva di loudness diversi dal golden fallisce |
| `F9` | il Critico è calibrato su giudizi umani (F20-T07) | senza calibrazione il Critico è giudice di sé stesso | la correlazione con i giudizi umani resta sopra la soglia calibrata |
| `F12` | ogni fase dichiara un tetto di durata della suite; superarlo fallisce | 757 s oggi, e cresce a ogni fase: senza tetto il cancello diventa inusabile | `D026`: la suite chiude entro il tetto dichiarato per la fase |
| `F18` | il torneo si estende al protocollo cieco e al corpus contro montaggi umani | il torneo attuale confronta varianti interne, non il sistema contro un umano | `F20-T04`: preferenza e indistinguibilità sopra soglia con IC coerente |
| `F19` | la promozione richiede provenienza esterna; il ledger non può più dire solo «sopra soglia» | `MASTER_LEDGER.md:219` ha già perso il segnale utente | `F20-T06`: una ricetta senza osservazione esterna resta `candidate` |
| `F0-T10` | il cancello distingue «provvisoria perché T09 aperto» da «provvisoria perché asse senza driver» | `composition` e `typography` non hanno driver: restano provvisorie per sempre (`scripts/vx/registry.py:347`) e il ramo «tutte non provvisorie» fallirebbe | `F0-T12`: due stati distinti (`provisional`, `unmeasured`); i 22 assert esistenti restano verdi |

---

## 5. Grafo delle dipendenze

```
T09 (umano) ──┬─> F5 ──> F23
              ├─> F6 ──> F13 ──┐
              ├─> F9 <─────────┘
              └─> F18

F9 ──> F20-T01..T04 ──> ESAME (certificazione)
F19 ──> F20-T05/T06
F6 ──> F21 ──> F5-T05
F7 ──> F22
F4 ──> F23
F10 ──> F24

F1 ──> F20-T05 (materiale reale)
F0-T13 ──> T09 ──> F0-T12 ──> certificazione
```

Le frecce verso l'esame sono **di certificazione**, non di costruzione: con D020
una fase può chiudere `PROVISIONAL` e l'esame resta non certificabile.

---

## 6. Percorso critico ricalcolato

Catena più lunga:

```
F0-T13 (tool annotazione)
  -> T09 (9 clip annotate a mano)
    -> F6 (MaterialMap su 20 clip annotate)
      -> F13 (vision pipeline)
        -> F9 (Critico)
          -> F20-T01 (corpus a coppie)
            -> F20-T02 (raccolta voti)
              -> F20-T03 (statistica)
                -> F20-T04 (cancello)
                  -> ESAME certificato
```

**Dove T09 taglia.** T09 è l'unico punto in cui la catena dipende da una persona.
Con lo stato `PROVISIONAL` (D020) il taglio non ferma la costruzione: F5, F6, F9 e
F18 si chiudono col codice e restano non certificabili. Il taglio resta però sul
percorso critico dell'esame: senza le 9 annotazioni, `F0-T12` non può separare i
due stati e l'esame non si certifica.

**Se T09 slitta di tre mesi.** Il piano perde la certificazione, non il lavoro:
F4, F5, F6, F13, F7, F8, F9, F21, F22, F23 procedono. Il primo numero esterno
(M1) slitta con F20. La data dell'esame slitta di tre mesi, non di più, perché
F20-T01 richiede materiale reale e valutatori, non le soglie.

---

## 7. Stima riveduta

**Task nuovi.** 29: F0 tre (T11, T12, T13), F1 uno (T05), F2 uno (T10), F5 due
(T05, T06), F20 sette, F21 sei, F22 sei, F23 tre. **Emendamenti.** 7 (F1-T04,
F2-T09, F9, F12, F18, F19, F0-T10).

**Difetti attesi.** Tasso osservato in F3: 3 difetti veri su 8 task (37,5%). Applicato
ai 29 task nuovi: **11 difetti attesi**. Applicato ai 7 emendamenti: **2-3**.
Totale atteso: **12-14 difetti veri**, cioè uno ogni due o tre task. L'assunzione:
il tasso di F3 vale per lavoro che tocca formati e filtri; i task di sola
statistica (F20-T03) ne produrranno meno, quelli che toccano il contratto (F21-T02,
F0-T12) di più.

**Sessioni.** Il piano attuale vale 15-20 sessioni (115-130 task su un ritmo
osservato di ~8 task per sessione). I 29 task nuovi valgono ~3,6 sessioni, gli
emendamenti ~1, più **due sessioni umane** (20 minuti per le 9 annotazioni; una
sessione di voti ciechi). Totale: **20-25 sessioni**, di cui 2 non sono di agenti.
Lo scostamento rispetto alle 15-20 è di **+5 sessioni (+30%)**, meno che
proporzionale: F20-F23 riusano F9, F6, F7 e F10 invece di ricostruire.

**Dove finiscono le cinque sessioni in più.** Due nel segnale esterno (protocollo,
raccolta, statistica, cancello), una nelle opportunità (schema, tre punti,
rilevatori), una nelle curve (intenzione, ritmo, tre golden), una nella continuità
e nell'interoperabilità. La fetta verticale M1 non aggiunge sessioni: è un punto di
giunzione di task già contati.

---

## 8. Ordine di esecuzione consigliato

1. **F1-T02, T03, T04** — chiude la fase in corso, nessuna dipendenza.
2. **F0-T11** (registro di licenza) e **F1-T05** — il regime, prima che altre
   tracce entrino.
3. **F0-T13** (tool di annotazione) — sblocca T09, che è il taglio del percorso
   critico. Costa poco e vale tre mesi di calendario.
4. **F4** ∥ **F16** — la libreria e il budget; F16 va anticipato perché il tetto
   di durata (O) dipende da lui.
5. **F5** (con T05/T06) → **F6** → **F13** → **F7** → **F8** → **F9**.
6. **F20-T01..T07** e **M1** (fetta verticale) — **qui arriva il primo numero
   esterno**. È il punto in cui il piano smette di verificare solo ipotesi interne.
7. **F21** → **F22** → **F23** → **F24**.
8. **F10** e **F11**; **F14** e **F15** in parallelo dove non bloccano.
9. **F12** — l'esame, con D026 e il cancello di certificazione.

**Effetto sul valore percepito.** Anticipare F16 e F20 porta il primo numero
esterno al ~60% del percorso invece che al 100%. Posticipare F14 (Review Room) non
costa nulla al valore misurato; posticipare F15 (shader) costa spettacolo, non
qualità. Anticipare F10 serve solo se M1 richiede il multi-aspect: non lo richiede,
perché il percorso esistente produce già verticale nativo.

---

## 9. Cosa non ho potuto verificare

- Il contenuto di `tests/fixtures/T09_annotation_template.xlsx`: esiste (15.128
  byte) ma è binario e non l'ho aperto. Il test dichiara 20 id e zero righe di
  dati; non l'ho verificato io.
- Se una libreria OTIO è installabile su questa macchina senza rete: non provato.
- Se un NLE reale apre FCPXML: nessun NLE installato, nessuna prova.
- La fattibilità del blind test: richiede valutatori umani, non disponibili oggi.
- Il formato reale di un export retention da una piattaforma: nessun campione.
- Se `ffmpeg` locale ha `libvmaf` per F18: non provato.
- Se il corpus di 9 clip è rappresentativo: dipende dalle annotazioni mancanti.
- La soglia di preferenza (40%) e di indistinguibilità (50%) sono **proposte da
  calibrare**, non verità: il corpus e il protocollo sono in F20-T01.
