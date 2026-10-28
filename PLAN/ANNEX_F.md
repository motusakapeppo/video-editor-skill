# Allegato F - Coscienza creativa: il motore di invenzione

Trascrizione dal documento dell'utente
`video-editor Piano Maestro e Prompt di Esecuzione (2).md`, righe 510-695, letta il
28/10/2026. Le soglie marcate "da calibrare" si fissano in F0 sull'hardware di riferimento e
sulle annotazioni dell'utente: finché sono provvisorie, nessuna fase si chiude su un esame che
le usa.

## Conflitto di numerazione, risolto

L'allegato numera le fasi **F20-F31**. Nel ledger quei numeri sono occupati: F25 (Allegato E),
F26 (profili), F27 (preset), F28 (font). Le fasi dell'allegato prendono quindi gli ID
**F32-F43**, con l'etichetta originale accanto:

| ID ledger | Etichetta | Titolo |
|---|---|---|
| F32 | AF4/F20 | Vincoli, affetti, autonomia (M01, M03, M11) |
| F33 | AF4/F21 | Atlante e Genoma (M02) |
| F34 | AF4/F22 | Divergenza (M04) |
| F35 | AF4/F23 | Esploratore qualita'-diversita' (M05) |
| F36 | AF4/F24 | Pannello dei critici (M06) |
| F37 | AF4/F25 | Modello del gusto (M07) |
| F38 | AF4/F26 | Ricerca e curiosita' (M08) |
| F39 | AF4/F27 | Memoria e riflessione (M09, M10) |
| F40 | AF4/F28 | Laboratorio degli stili e originalita' (M12, M16) |
| F41 | AF4/F29 | Meta-creativita' (M13) |
| F42 | AF4/F30 | Dialogo e interfaccia (M14) |
| F43 | AF4/F31 | Banco di prova ed esame (M15) |

## Conteggi dichiarati dal documento (revisione 7)

- 32 fasi: F0-F31. Nel nostro ledger la numerazione diverge: F25-F28 sono gia' usate per
  Allegato E, profili, preset e font, quindi le fasi F20-F31 dell'allegato vivono qui come
  **F32-F43**. Il totale delle fasi resta 32, cambia solo l'etichetta.
- 27 incarichi d'esame: 1-19 (sezione 6 piu' A5) e 26-33 (AF5). La numerazione salta 20-25:
  e' quella del documento, non un errore nostro.
- 59 task in AF4 (7+7+5+5+5+4+4+5+4+4+4+5).
- Sei addendum al prompt: sezione 9, A7, B7, C7, D8, AF7.

## AF1. Tesi

1. Creativita' = cio' che e' nuovo, sorprendente **e di valore** (Boden). Senza il valore resta
   casualita'. Tre capacita': generare, valutare, imparare.
2. Numeri e linguaggio insieme: il modello propone e interpreta, i numeri vincolano e misurano.
3. Coscienza **funzionale**: nessuna pretesa di coscienza; equivalenti verificabili di
   intenzione, riflessione, gusto, curiosita', memoria, sorpresa. Ognuno e' un modulo con test.
4. Autonomia regolata: i limiti dell'utente diventano un registro dei vincoli; dentro quelli,
   liberta' al grado scelto.
5. Infinito onesto: atlante, genoma continuo, strato aperto; "infinito" solo per lo strato
   aperto, dove e' vero per costruzione. Per gli altri si da' il numero.

## AF2. Tre strati

**Atlante** (>= 5.000 voci, italiano e inglese) per famiglia: umori ed emozioni 500; look e
stili visivi 800; movimenti e inquadrature 250; transizioni 350; tipografia 400; motion
graphics 500; palette 600; sound design e musica 400; strutture narrative e hook 450; idiomi
di montaggio 300; idiomi di piattaforma 450. Ogni voce: identificativo, nomi, definizione,
sinonimi, famiglia, coordinate nel genoma, **proxy misurabile**, **implementazione**,
controindicazioni, fonte e data, stato (proposta, validata, verificata a vista). Una voce senza
proxy o senza implementazione non entra.

**Genoma**: 16 famiglie (struttura, ritmo, hook, camera, inquadratura, colore, luce, tipografia,
grafica in movimento, transizioni, effetti, suono, musica, tono, narrazione, rottura di regola);
~40 opzioni per famiglia = ~4x10^25 combinazioni discrete, prima dei continui.

**Strato aperto**: l'agente inventa effetto GLSL, template Chromium, operatore o voce; ogni
invenzione passa sandbox, linter, test, prova visiva, promozione.

**Contatore**: spazio nominale, spazio dopo i vincoli, nicchie esplorate su totali, voci usate,
invenzioni. Calcola, non dichiara. **Mappa navigabile** nella Review Room con comandi
direzionali ("piu' caldo", "piu' strano").

## AF3. Sedici moduli

M01 vincoli · M02 atlante e genoma · M03 affetti · M04 divergenza · M05 esploratore
qualita'-diversita' · M06 pannello dei critici · M07 gusto · M08 curiosita' · M09 memoria ·
M10 riflessione · M11 autonomia e costituzione creativa · M12 laboratorio degli stili ·
M13 meta-creativita' · M14 dialogo · M15 banco di prova · M16 etica e originalita'.

## AF4. Task per fase

### F32 (F20) - Vincoli, affetti, autonomia
- T01 schema `vx.constraints/1` (duro/morbido/libero, frase, interpretazione, proxy, verifica) — 30 casi.
- T02 compilatore prompt -> registro, copertura delle frasi — 200 prompt it/en, 100%, zero dure violate.
- T03 risolutore di contraddizioni — 40 coppie, >= 85% accettabili.
- T04 dizionario dei limiti (elegante, pacchiano, energico...) -> proxy — >= 300 termini.
- T05 modello degli affetti calibrato su annotazione utente (30 clip) — correlazione riportata; **BLOCCATO-UTENTE**.
- T06 autonomia 0-5, costituzione creativa, licenza di rottura — 1.000 casi, zero linee rosse violate.
- T07 cartolina d'intenzione prima del montaggio, con referto di fedelta'.

### F33 (F21) - Atlante e genoma
- T01 schema `vx.atlas/1`: voce senza proxy o implementazione rifiutata.
- T02 genoma 16 famiglie, serializzato nell'EDL via `preset_ref` ed `ext`.
- T03 compilatore genoma -> EDL — 10.000 genomi, zero EDL invalidi.
- T04 popolamento a ondate, generazione assistita, validazione automatica, verifica a vista su 5% stratificato — >= 5.000 voci, >= 2.500 validate al primo rilascio; **verifica a vista BLOCCATA-UTENTE**.
- T05 contatore delle possibilita' — esatto su sottospazi piccoli.
- T06 campionatore diversificato (Sobol / ipercubo latino / stratificato) — copertura su 1.000 campioni.
- T07 mappa 2D nella Review Room con navigazione direzionale — 20 comandi, verso atteso.

### F34 (F22) - Divergenza
- T01 >= 12 operatori creativi (inversione, esagerazione, rimozione, combinazione, sostituzione, analogia, rilassamento, mutazione, vincolo artificiale...).
- T02 8-12 concept per brief con motivazione, coordinate, distanza minima misurata.
- T03 >= 3 analogie da domini non video per brief, con fonte.
- T04 mutazione di regola (livello 5) con linee rosse.
- T05 rilevatore di cliche' su archivio proprio e snapshot, lista versionata.
- Prova: 90% dei set supera la distanza minima; test cieco su 10 brief, >= 8 giudicati diversi.

### F35 (F23) - Esploratore qualita'-diversita'
- T01 descrittori e griglia; T02 animatic a bassa risoluzione con budget; T03 modello surrogato con errore fuori campione; T04 MAP-Elites con archivio persistente e ripresa; T05 modalita' Atelier con tetto di risorse.
- Prova: 300 nicchie in 30 minuti (da calibrare); successo dei finalisti > campionamento casuale con test statistico; archivio che sopravvive a un crash.

### F36 (F24) - Pannello dei critici
- T01 rubriche per ruolo, versionate; T02 misure numeriche per asse; T03 avvocato del diavolo e accordo riportato; T04 torneo a coppie con ordine scambiato ed Elo; T05 calibrazione sul giudizio umano — **BLOCCATO-UTENTE**.
- Prova: distingue 10 EDL difettosi e 10 buoni nel 90% dei casi.

### F37 (F25) - Gusto
- T01 coppie nella Review Room, max 20 all'avvio (**BLOCCATO-UTENTE**); T02 Bradley-Terry per contesto con incertezza; T03 passaporto esportabile e cancellabile; T04 deriva e sovradattamento con diversita' minima garantita.
- Prova: accuratezza sopra il caso dopo 20 coppie (indicativamente 65%, da calibrare); cancellazione verificata.

### F38 (F26) - Ricerca e curiosita'
- T01 3-5 domande per brief; T02 analogie con le regole dell'Allegato D; T03 estrazione di affermazioni e proposte di voci (>= 3 fonti indipendenti o verifica a vista); T04 budget di curiosita' verso le nicchie vuote.
- Prova: fonti simulate, nessuna chiamata fuori dai domini consentiti, provenienza su ogni voce.

### F39 (F27) - Memoria e riflessione
- T01 memoria episodica; T02 dimenticanza regolata e trasparenza; T03 dichiarazione d'intenzione, monitoraggio, post-mortem; T04 rilevatore di sorpresa e lezioni; T05 diario leggibile.
- Prova: dopo 10 lavori una lezione cambia il comportamento, misurato A/B, differenza positiva su almeno un asse e nessuna regressione.

### F40 (F28) - Stili e originalita'
- T01 impronte -> coordinate del genoma; T02 algebra degli stili; T03 guardia di originalita' (distanza + confronto percettivo, frame dei riferimenti non conservati, blocco sotto soglia); T04 dichiarazione dei contenuti sintetici.
- Prova: un output da riferimento non contiene frame ne' audio del riferimento e supera la soglia di distanza.

### F41 (F29) - Meta-creativita'
- T01 bandit (Thompson sampling) con registro; T02 sandbox di invenzione con linter, test, prova visiva; T03 promozione solo se il benchmark non peggiora, ritiro di cio' che non rende; T04 registro delle invenzioni.
- Prova: >= 5 invenzioni, >= 2 promosse, zero regressioni, nessuna uscita dalla sandbox (test di rete e filesystem).

### F42 (F30) - Dialogo e interfaccia
- T01 max 3 domande, solo a costo d'errore alto, sempre con default; T02 spiegazioni di una riga; T03 cartolina e mappa con "piu' cosi'"; T04 critica dell'utente come vincolo persistente.
- Prova: 30 conversazioni, mai piu' di 3 domande, nessuna domanda su cio' che il contesto diceva.

### F43 (F31) - Banco di prova ed esame
- T01 12 brief x 3 modalita' (letterale, creativa L2, creativa L4); T02 metriche (aderenza, mestiere, novita', sorpresa utile, coerenza, cliche', copertura); T03 test ciechi a coppie (**BLOCCATO-UTENTE**); T04 regressioni automatiche; T05 FINAL_REPORT creativo.

## AF5. Esame di creativita' (26-33)

26. Brief vincolato: registro corretto, contraddizione risolta e dichiarata, zero violazioni.
27. Brief aperto: 8-12 concept distanti, 3 animatic, scelta motivata.
28. Esplorazione: >= 300 nicchie, mappa funzionante, navigazione che sposta davvero i descrittori.
29. Gusto: 20 coppie dell'utente e previsione sopra soglia.
30. Riflessione: un post-mortem con una lezione che cambia un lavoro successivo.
31. Invenzione: un effetto nuovo promosso dal ciclo di meta-creativita'.
32. Stile: stile da riferimento fornito, output originale sopra soglia, cancellazione verificata.
33. Test cieco: 12 brief x 3 modalita'; la creativa batte la letterale in >= 70% delle coppie,
    senza violazioni ne' cliche'.
Criteri comuni: controlli verdi, nessuna soglia provvisoria, tre esecuzioni coerenti, numeri
misurati.

## AF6. Limiti ed etica

Metriche di novita', sorpresa e affetto sono **proxy**: l'ultima parola e' umana, per questo
l'esame e' cieco. Rischio Goodhart (ottimizzare il numero invece del risultato): rubriche a
rotazione e ricalibrazione periodica. Il contatore misura lo spazio nominale; la qualita'
dell'esplorazione si misura con le nicchie occupate e i test ciechi. Stili: citare, non copiare;
contenuti sintetici sempre dichiarati. Gusto: dato personale, locale, ispezionabile,
esportabile, cancellabile. Tutto su CPU con modelli leggeri (gradient boosting o processo
gaussiano per il surrogato, UMAP o PCA per la mappa), licenze verificate in
`LICENSES_MATRIX.md`; la GPU accelera, non e' richiesta.

## AF7. Dipendenze dichiarate

L'allegato poggia su F4, F7, F8, F9, F14, F19, E1, E5, F2B, F17B. **Va chiusa prima la fase da
cui dipende**, senza sostituti provvisori. Se una premessa non coincide col repository, si
scrive DISCREPANZA con la prova e si lavora sulla realta'.

## Elementi che richiedono l'utente

Quattro moduli `.xlsx` con liste a tendina, istruzioni, riga d'esempio ignorata dal codice e
avanzamento, per: calibrazione degli affetti (F32-T05), coppie di gusto (F37-T01), test ciechi
(F43-T03), verifica a vista dell'Atlante (F33-T04). Quei task vanno segnati **BLOCCATO-UTENTE**
con il percorso del modulo, e le soglie che ne dipendono restano provvisorie.
