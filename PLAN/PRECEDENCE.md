# Precedenza fra gli allegati

Quando due allegati di `MASTER_PLAN.md` dicono cose diverse, vale questa tabella.
Un allegato successivo non cancella il testo precedente: lo supera, e il testo
resta leggibile con la marcatura `SUPERATO`.

## Tabella

| Fonte che prevale | Sostituisce | Resta valido |
|---|---|---|
| **Allegato D** (Trend Radar gratuito) | Allegato C, punto **C3** (mappa dei provider), punto **C6** per la parte provider, e **ogni** uso di API di Instagram o TikTok e di fornitori a pagamento, crediti o abbonamenti | D stessa; C2, C4, C7; B3, B4 |
| **Allegato C** (Trend Radar v2) | Allegato B, punti **B2, B5, B6, B7** | C2, C4, C7; B3, B4 |
| **Allegato B** | dove C non arriva | B1, B3, B4 |

Regola operativa: D > C > B, limitatamente ai punti elencati. Fuori da quelli, il
testo originale resta la fonte.

## Frasi corrette in MASTER_PLAN.md

| Dove | Cosa diceva | Perché era in conflitto | Come è ora |
|---|---|---|---|
| `MASTER_PLAN.md:361`, titolo C3 | mappa dei provider con TikTok Creative Center, oEmbed, Instagram Graph | D vieta ogni API di Instagram e TikTok | marcato `SUPERATO IN PARTE da D` |
| `MASTER_PLAN.md:366`, fornitori terzi | «opzionali, spenti di default, con tetto di spesa zero e conferma esplicita» | D dice «nessun fornitore a pagamento, nemmeno come opzione» | marcato `superato da D` |
| `MASTER_PLAN.md:399`, C7 | «i provider a pagamento sono opt-in» | D vieta anche l'opt-in | «nessun fornitore a pagamento, nemmeno come opzione» |
| `MASTER_PLAN.md:331`, B6 | accettazione con provider simulati e quote | C sostituisce B6 | marcato `SUPERATO da C` |
| `MASTER_PLAN.md:339`, B7 | addendum che rimanda a B5/B6 | C sostituisce B5, B6, B7 | marcato `SUPERATO da C` |

## Cosa resta valido di C3

I punti su **YouTube** (Data API per metadati e miniature; Gemini su URL pubblico
per S4 approssimato, con il tetto gratuito) e il punto **Web** (S0). Sono gli
stessi che D conferma.

## Nota su un riferimento inesistente

L'aggiornamento parlava di «sezione 5 livello D». Nel piano non esiste alcun
«livello D»: i livelli dichiarati sono **L0, L1, L2** (`MASTER_PLAN.md:209`). La
sezione 5 non contiene quindi nulla da correggere per questo motivo.

## Come si aggiunge un allegato nuovo

Un allegato nuovo entra con una riga in questa tabella e, se supera un punto
esistente, con la marcatura `SUPERATO` nel punto superato e la citazione
dell'allegato che lo supera. Mai una riscrittura silenziosa.
