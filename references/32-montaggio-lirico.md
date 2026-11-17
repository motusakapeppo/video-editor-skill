# 32 - Montaggio lirico: il testo a tempo, e il loop

Quando il materiale e' **testo cantato** — una strofa, un ritornello, un coro —
il montaggio non e' "un video con i sottotitoli". E' un video in cui il testo e'
un livello con una sua griglia, e quella griglia e' la musica. Questo file dice
come si costruisce e come si verifica; i comandi sono in `31-protocollo.md`.

La fonte di questo metodo e' un caso vero: un post 1080x1440 per Instagram,
sette versioni, un'esecuzione live di "Scendi" con strofa rap veloce e due
ritornelli. Il risultato buono e' arrivato quando i tre orologi hanno smesso di
essere un unico file e sono diventati tre documenti. Prima, ogni correzione
all'audio spostava il testo e ogni correzione al testo richiedeva un render.

## I tre orologi

| orologio | documento | chi lo muove |
|---|---|---|
| suono | una traccia continua, gia' tagliata | la partitura (§1) |
| immagine | clip con `src_in`/`dst_in` | il montaggio (§2) |
| testo | eventi `[t0, t1]` con stile, taglia, righe, parola enfatizzata | la spec (§3) |

Il montaggio e' **l'accordo** fra i tre, non un effetto applicato a un video.
Se il suono e' una sola traccia continua, l'allineamento si misura una volta e
vale per tutto: e' la ragione per cui il caso e' arrivato a un solo offset
(`7,92 s`) per testa, strofa e coda. Se il suono fosse tagliato a pezzi, ogni
giunzione avrebbe il suo offset e nessuno sarebbe verificabile.

## 1. Prima la partitura, non il taglio

Si misurano BPM e griglia dei battiti dall'audio (`vx analyze`), non a orecchio.
Poi si scrivono le sezioni con i **numeri di battuta**, non con i secondi:

```
intro parlata | ritornello 1 (battute 1-8) | strofa | ritornello 2 (1-8) | strofa 2 | ritornello 3 | outro
```

Una battuta in 4/4 dura `240 / BPM` secondi. Con 136 BPM: `1,7647 s`. Da li'
tutti i confini sono multipli di quella unita', e una modifica di 2 BPM si
propaga senza ricalcolare a mano.

**Perche' conta**: un taglio "a 12,3 secondi" e' un taglio che non si puo'
difendere; un taglio "a battuta 5" e' un taglio che si puo' verificare e
spostare. Nel caso, la testa era "battute 5-8 del primo ritornello", la coda
"battute 1-8 del secondo": quattro e otto, non "un po' meno della meta'".

## 2. Allineare la voce, poi tagliare

L'ordine che funziona:

1. **Demux dell'audio** dal girato (`vx analyze` / ffmpeg `-vn`): la voce da
   sola e' il segnale su cui si misura, non il mix con la sala.
2. **Trascrizione con timestamp di parola** (`vx subs --json` produce le parole,
   non solo le frasi).
3. **Confronto con il testo scritto** dell'artista, se esiste. Il testo scritto
   vince sempre: la trascrizione sbaglia i nomi propri e le parole rare.
4. **Un offset unico** se le camere riprendono la **stessa esecuzione** (si
   misura una volta, campione-accurato). **Un'ancora per frase** se l'esecuzione
   e' diversa (un live e uno studio): in quel caso non si allinea in continuo,
   si taglia su battuta e su riga, e lo si dichiara.

Regole che non si negoziano:

- **Mai un taglio a meta' parola o a meta' sillaba.** Un taglio sbagliato non si
  sente come un errore di montaggio: si sente come un errore di labiale, e
  l'utente lo vede subito perche' e' lui che canta.
- **La correzione di una parola va nel testo sorgente**, non solo nel render.
  "Sotto solo capre e cimici" non e' "sono solo capre e cimici"; "io cantavo" non
  e' "e cantavo". Se la parola resta sbagliata nella spec, la successiva
  versione la reintroduce.
- **Il labiale e' un testimone**: se il video mostra una bocca che dice una cosa
  e l'audio un'altra, nessun effetto lo copre. Si cambia lo spezzone.

## 3. La spec del testo animato

Il testo non si scrive dentro l'HTML: si scrive in un **documento** che la
grafica legge. La forma minima che ha retto sette versioni (i nomi dei campi sono
un esempio: contano i ruoli, non le chiavi):

```json
{
  "fps": 25, "duration": 50.88,
  "structure": {
    "head": {"master": [0.0, 7.44], "source": [7.92, 15.36], "bars": "5-8"},
    "body": {"master": [7.44, 36.76], "source": [15.36, 44.68]},
    "tail": {"master": [36.76, 50.88], "source": [44.68, 58.8], "bars": "1-8"}
  },
  "events": [
    {"t0": 7.44, "t1": 8.57, "style": "hero", "size": 108, "y": "low",
     "lines": [{"text": "Ho gia' vissuto"}, {"text": "questo posto"}]}
  ],
  "hits": [7.63, 8.51, 9.40], "wordHits": [7.63, 9.40], "hitsPerBar": 2,
  "hitRule": "un colpo sul battere 2 e uno sul 4 di ogni battuta; ogni colpo ha la sua interazione"
}
```

- `events[]` e' il **testo**: quando appare, con che stile, a che altezza, quali
  righe, quale parola e' quella che conta (`em`).
- `hits[]` e' la **musica**: i colpi che si sentono. `wordHits[]` sono i colpi che
  aggiungono una parola scritta; non sono tutti.
- `hitsPerBar` e `hitRule` sono la regola, scritta: senza, la prossima versione
  non sa perche' c'e' un colpo e non due.

Sette regole che il caso ha pagato a proprie spese:

1. **Ogni colpo ha la sua interazione**, anche quando la parola scritta non
   cambia. Un effetto ogni due colpi fa zoppicare il ritmo, e l'utente lo dice
   cosi': "non sta a tempo".
2. **Il riquadro arriva con la parola**, non prima. Un riquadro vuoto che si
   riempie dopo e' un errore che si vede al primo fotogramma.
3. **L'evidenziazione cambia due cose**: se il fondo diventa rosso, il testo non
   puo' restare bianco; se il testo diventa nero, il fondo non puo' essere nero.
   Un quadrato rosso senza testo e' il difetto piu' frequente.
4. **Mai sopra il volto**, tranne il momento in cui il testo *e'* il soggetto
   (il ritornello urlato). E mai cosi' tanto da coprire l'esecutore per piu' di
   un istante: l'artista che canta e' il motivo per cui il video esiste.
5. **La taglia si calcola sul caso peggiore**, non sulla riga media. La prova e'
   la lettura a un terzo della larghezza (un telefono in mano): se la riga piu'
   lunga non si legge li', non si legge.
6. **Un carattere per film.** Il cambio di font e' un cambio di film, non una
   variazione. Se il carattere cambia a meta', si vede come un errore.
7. **Una parola enfatizzata per riga**, e il dispositivo che la mostra deve
   significarla. Se la parola e' "poltrona" il dispositivo e' una poltrona; se e'
   "Ditto" e' Ditto. Un effetto che non significa la sua parola e' decorazione,
   e la decorazione si toglie.

**Perche' un documento e non l'HTML**: la spec si verifica senza guardare (tempi,
copertura, colpi, parole), si corregge in un file da venti righe, e il designer
puo' cambiare tutto il disegno senza toccare un tempo. Nel caso, il designer ha
rifatto la grafica sei volte e i tempi sono rimasti quelli validati dall'artista.

## 4. Il loop

Un loop non e' un taglio: e' una **ripresa**. Il video finisce dove comincia, e
chi lo guarda non deve accorgersene.

- **Testa e coda su battute intere.** La testa e' la seconda meta' del primo
  ritornello (battute 5-8), la coda e' il secondo ritornello intero (1-8), oppure
  meta' e meta'. La scelta si dichiara, e la simmetria non e' obbligatoria: nel
  caso la versione buona e' 4 + 8.
- **Il salto cade su un downbeat.** Ultimo fotogramma e primo devono essere lo
  stesso istante musicale: se il salto cade a meta' battuta si sente un "vai"
  che non c'e'.
- **Il salto audio e' zero.** Si misura il delta fra ultimo e primo campione
  contro il movimento normale del segnale. Nel caso era `4,2x` — un clic a ogni
  giro, che nessun flash copre. Sei millisecondi di dissolvenza ai due bordi,
  lontani da qualsiasi sillaba cantata, lo portano a `0,0`.
- **Il salto immagine e' zero o dichiarato.** Ultimo fotogramma contro il primo:
  `<= 2/255` e' continuita'; un flash che li copre e' una scelta, e va dichiarata
  come scelta.
- **La fine non e' vuota e l'inizio non e' vuoto.** Un secondo di nero in testa o
  in coda rompe il loop piu' di un errore di colore.

## 5. La batteria di controlli

Prima di dire "fatto" si misurano tutte. Nessuna e' un'opinione.

| controllo | soglia | se fallisce |
|---|---|---|
| durata / fotogrammi | quella dichiarata, un frame | si rifa' il piano, non si arrotonda |
| offset audio | uno solo, con 9-10 sonde a copertura | si riallinea; due offset = due esecuzioni |
| colpi | colpi eseguiti / colpi dichiarati = N/N | si corregge la spec, non il render |
| loop immagine | ultimo vs primo `<= 2/255` | si sposta il taglio sulla battuta |
| loop audio | salto campione `<= 1x` il movimento normale | dissolvenza 6 ms ai bordi |
| loudness / true peak | `-14 LUFS ± 1,5` / sotto il tetto | si rifa' la catena audio |
| testo vs sorgente | diff parola per parola, zero differenze | si corregge il testo, poi la spec |
| leggibilita' | si legge a 1/3 di larghezza | si riduce la taglia o si spezza la riga |
| volto | dentro il canvas e non coperto | si rifa' l'inquadratura o si sposta il testo |
| elementi | niente fuori dal canvas | si ridimensiona il dispositivo |

Nel caso, la tabella finale della v7 leggeva: `50,88 s / 1272 frame`;
`-14,02 LUFS / -1,64 dBTP`; loop `2/255`; `9 sonde, offset 0,000 s`; colpi
`24/24`. Sono i numeri che rendono "fatto" una parola verificabile.

## 6. Chi fa cosa

- **Chi misura non disegna.** L'orchestratore misura il master (fotogrammi,
  colpi, contrasto, volto) e rifiuta con il numero. Il designer disegna e non
  tocca i tempi.
- **Il brief porta le parole dell'utente, verbatim.** "Non si puo' togliere
  questo scheletro di linee rosse?" e' un brief migliore di "riduci gli
  elementi decorativi": il designer vede cosa ha visto l'artista.
- **Ogni rifiuto arriva con la misura.** "Otto righe su diciassette non si
  leggono a 360 px" e' un ordine di lavoro; "non mi piace" non lo e'.
- **Due versioni in parallelo solo se i brief sono indipendenti** e le cartelle
  sono separate (`vx-scendi-lyrics-v5` e `-v6`). Il confronto si fa sui master,
  non sulle intenzioni.

## 7. Le versioni

- **Non si sovrascrive una versione.** `vN` e' un artefatto con un nome, un
  master e un report. Se ne serve una nuova, si crea `vN+1`.
- **Il mix e' una versione.** La v7 non e' una patch della v6: e' "il Ditto della
  v6, piu' piccolo e rosso, il ritornello della v6, le banconote della v5, il
  loop 4+8". Si dichiara cosa si eredita e da dove.
- **Si tiene la struttura, si buttano gli esperimenti.** I tempi e le regole
  restano; gli effetti che coprono l'esecutore si tolgono anche se sono "belli".
- **La richiesta dell'utente si copia, non si interpreta.** Sette versioni sono
  costate anche perche' "piu' WOW" e' stato letto come "piu' roba": la v6 e'
  stata rifiutata perche' era tutto coperto e monotono. "Piu' spettacolare" per
  un artista significa **piu' chiaro e piu' vario**, non piu' pieno.
- **Il template che un lavoro inventa non entra nel catalogo.** Si marca con un
  file `.project`: resta caricabile per nome (l'EDL lo cita), ma non chiede una
  golden e non falsifica la promessa della libreria (`31-protocollo.md` §11).

## 8. Gli errori visti, e quanto costano

| errore | perche' si vede | prevenzione |
|---|---|---|
| parola sbagliata ("sono" per "sotto") | la legge chi ha scritto la canzone | §2: diff col testo scritto |
| effetto su un colpo su due | il ritmo zoppica | §3 regola 1, conteggio N/N |
| testo nero su fondo nero | la parola sparisce | §3 regola 3, contrasto misurato |
| riquadro che copre il testo | il quadrato resta senza parola | §3 regola 3, due colori distinti |
| barra a fondo schermo che non arriva ai lati | sembra un errore di export | si misura la larghezza del dispositivo |
| elemento grafico sulla faccia | l'artista non si vede | §3 regola 4, box del volto |
| inizio vuoto o fine vuota | il loop si rompe | §4, nero in testa/coda vietato |
| font che cambia a meta' | sembra due video diversi | §3 regola 6 |
| "piu' WOW" = "piu' elementi" | la v6 copre tutto ed e' monotona | §7, si chiede cosa deve significare |

## 9. Cosa resta fuori

Questo file non dice come si disegna: quello e' il mestiere del designer, e le
regole visive stanno in `14-direzione-visiva-adattiva.md`. Non dice nemmeno
come si monta un video senza testo cantato: la`, `10-grammatica-del-montaggio.md`.
Il confine e' il materiale: se c'e' una voce che canta e un testo che la segue,
si parte da qui.
