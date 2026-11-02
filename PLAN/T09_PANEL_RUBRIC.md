# T09 — rubric del panel visivo (v1)

**Che cos'è.** Un panel di modelli che *guardano tre fotogrammi* di ogni clip e
danno un giudizio categorico su quattro assi. Non è una giuria di persone: è un
panel di modelli, e va dichiarato così nel corpus. Il valore sta nel fatto che il
giudizio arriva dalle immagini, non dai numeri che poi si calibrano, e che il
disaccordo fra annotatori viene misurato invece di essere nascosto da una media.

**Cosa vede l'annotatore.** Un foglio con 5 righe (5 clip) e 3 colonne
(fotogrammi al 25%, 50%, 75% della durata), con l'identificativo della riga in
alto a sinistra. Ogni annotatore riceve tutti i fogli, da solo, senza vedere i
numeri né gli altri annotatori.

## I quattro assi e i loro token

| asse | token ammessi | quando |
|---|---|---|
| `shake` | `ok`, `light`, `evident`, `unwatchable` | confronta i tre fotogrammi: se lo stesso oggetto si sposta in modo incoerente tra un frame e l'altro, c'è tremolio |
| `exposure` | `ok`, `dark`, `very_dark`, `bright`, `blown` | il soggetto è leggibile? un interno scuro voluto è `ok`; un volto nero è `very_dark`; le alte luci senza dettaglio sono `blown` |
| `cast` | `none`, `light`, `evident` | dominante di colore non voluta (verde fluorescente, giallo tungsteno), non lo stile |
| `noise` | `absent`, `light`, `evident`, `annoying` | grana/rumore che copre il dettaglio, non la grana artistica dichiarata |

## Le regole che rendono il giudizio usabile

1. **Giudica solo ciò che vedi nei tre fotogrammi.** Se non basta per decidere,
   scrivi `na`: un'assenza dichiarata vale più di un'invenzione.
2. **Non indovinare la causa.** Un'immagine scura può essere una scena notturna:
   giudica la leggibilità, non l'intenzione.
3. **Un asse per volta.** Non sommare i difetti in un voto unico: il corpus
   conserva quattro colonne, non una media.
4. **Nessun numero.** Non scrivere punteggi, percentuali o "voto". Solo token.
5. **Nessun confronto fra clip.** Ogni clip sta da sola: non c'è una classifica.

## Formato della risposta (JSON, nient'altro)

```json
{
  "annotator": "<nome>",
  "rubric": "t09-panel/v1",
  "clips": {
    "c01": {"axes": {"shake": "ok", "exposure": "ok", "cast": "none", "noise": "absent"}, "note": "una frase, cosa si vede"},
    "c02": {"axes": {"shake": "evident", "exposure": "dark", "cast": "light", "noise": "light"}, "note": "..."}
  }
}
```

Regole di combinazione (fuori dal panel): il token è quello su cui concorda la
**maggioranza**; se nessun token raggiunge la maggioranza il clip è `contested`
e l'asse non entra nel conteggio. L'accordo medio si riporta nel corpus.

## Controprova umana (holdout, non negoziabile)

I sette giudizi umani già raccolti restano fuori dal panel e servono da prova:

- `c02` giudicata **scarsa** (tremolio misurato 3,59) → `shake` deve essere
  `evident` o `unwatchable`;
- `c09` giudicata **la migliore** (tremolio 0,85) → `shake` deve essere `ok` o
  `light`;
- `c08` giudicata **buona** (nitidezza 4,74, la piu' alta del corpus) → `noise`
  deve essere `absent` o `light`: se il panel vede rumore dove l'umano vede
  dettaglio, le due letture non stanno guardando la stessa cosa.

`c01` **non** e' un'ancora di tremolio, e va detto perche': l'umano l'ha
giudicata "mosso, sfuocata", ma il tremolio misurato e' 0,42 - sotto ogni altra
clip. Il difetto e' motion blur in un interno buio, e D-21 ha gia' dimostrato che
la nitidezza non si inverte su quei fotogrammi. Usarla come ancella di `shake`
sarebbe una trappola costruita a tavolino: resta nel corpus come caso noto che
non separa, e non decide la calibrazione.

Se il panel contraddice uno di questi tre, la calibrazione **non si spedisce**:
significa che il panel non sta vedendo la stessa cosa dell'umano, e le soglie
derivate sarebbero numeri senza fondamento.
