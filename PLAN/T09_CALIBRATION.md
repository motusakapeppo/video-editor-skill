# T09 - Calibrazione v1 (preliminare)

Data: 28/10/2026. Sette clip dello stesso corpus, giudizio dell'utente raccolto **prima** di
vedere i numeri, uno alla volta, mentre guardava i fotogrammi e il video. Parole originali in
`<WORK_DIR>\t09\giudizi.jsonl`.

## Il campione

| clip | giudizio | nitidezza | tremolio | luma | saturazione | LUFS | risoluzione |
|---|---|---|---|---|---|---|---|
| c02 | **scarsa** | **1,02** | **3,59** | 85 | 34 | −11,8 | 848x480 |
| c01 | riserve (mosso, sfuocata) | 2,32 | 0,42 | **40** | 84 | −16,6 | 474x850 |
| c04 | riserve (correzione colore e nitidezza) | 1,53 | 0,23 | 101 | 40 | −19,4 | 576x1024 |
| c10 | buona (cruda ma adatta) | 1,45 | 2,11 | 121 | 28 | −13,9 | 848x480 |
| c03 | buona | 1,92 | 0,23 | 91 | 40 | −20,3 | 1024x576 |
| c09 | **la migliore** | 2,66 | 0,85 | 118 | **24** | −18,6 | 576x1024 |
| c08 | buona | **4,74** | 0,02 | 110 | 30 | **−32,0** | 832x464 |

## Cosa separa, e cosa no

**Separano** (una sola clip dall'altra parte, ma il margine e' largo):

| metrica | lato "scarsa" | lato accettato | soglia v1 (preliminare) |
|---|---|---|---|
| nitidezza (varianza del laplaciano) | 1,02 | 1,45 - 4,74 | **< 1,25 = da correggere** |
| tremolio (`jitter.score`) | 3,59 | 0,02 - 2,11 | **> 2,80 = da correggere** |

**Non separano**, e non vanno usate come soglia di qualita':

- **volume**: la clip piu' silenziosa di tutte (−32 LUFS) e' piaciuta; la piu' forte (−11,8) e'
  quella giudicata scarsa. Il volume e' una correzione (normalizzare), non un difetto.
- **saturazione**: la migliore ha la saturazione **piu' bassa** (24). Misura lo stile, non la
  qualita'.
- **luminosita' media**: dal 40 al 121, senza rapporto col giudizio. Il buio puo' essere la
  scena, non un errore.
- **risoluzione**: 848x480 compare sia nella clip scarsa sia in una accettata.

## Difetto della misura trovato grazie a c01

c01 e' giudicata "sfuocata" con nitidezza **2,32**, piu' alta di c03 (1,92) giudicata buona. La
misura e' **inaffidabile sui fotogrammi scuri**: su un'immagine quasi nera ci sono pochi
contorni, ma forti, e la media del laplaciano sale. La nitidezza va calcolata solo sui
fotogrammi sopra una soglia di luminosita', oppure normalizzata. Registrato come D-21.

## Come si usa questo risultato

1. Le due soglie (1,25 e 2,80) restano **preliminari**: un solo campione sul lato "scarsa" non
   fa una curva. Servono almeno venti clip con giudizi diversi.
2. Il giudizio non produce un punteggio: produce **una lista di correzioni**. Le parole
   dell'utente sono state tutte della forma "serve aggiustare X", mai "voto 6/10".
3. Le misure che non separano restano nel referto come **descrizione**, mai come respingimento.

### D-21 - la nitidezza sui fotogrammi scuri (chiusa il 2/11/2026)

Sugli stessi fotogrammi dei dieci clip sono state misurate cinque metriche:
varianza del laplaciano grezza, dopo CLAHE, dopo gamma 1/2,2, normalizzata per la
varianza dell'immagine, normalizzata per il gradiente medio. I numeri sono in
`<WORK_DIR>/t09/sharpness_candidates.json`. **Nessuna** inverte la coppia che conta:
c01 (luma 38,7, giudicata "mosso, sfuocata") resta sopra c03 (88,7, "buona") in
tutte e cinque. Il clip scuro ha davvero piu' energia in alta frequenza, ed e' rumore
e mosso, non dettaglio.

Quindi la correzione non e' una formula: sotto **60** di luma media la nitidezza
resta una descrizione misurata e il triage non la usa come cancello. La soglia cade
tra il clip piu' scuro giudicato (38,7) e il successivo (83,9).

