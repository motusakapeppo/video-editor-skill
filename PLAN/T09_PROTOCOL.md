# T09 - Modulo di annotazione: protocollo

Scopo: sbloccare le soglie. Fino a quando T09 e' aperto, F0-T09 resta `BLOCKED` e ogni soglia
che ne dipende e' **preliminare**: F5, F6, F9 e l'esame non si possono chiudere su un esito che
le usa.

## Il punto che non si puo' aggirare

Le annotazioni sono **giudizi umani**. L'agente non le inventa, non le deduce dalle misure e non
le compila al posto dell'utente: se lo facesse, la taratura misurerebbe se stesso. Il modulo va
compilato dall'utente, **prima** di guardare i numeri che la skill produce sulle stesse clip,
altrimenti i numeri influenzano il giudizio e il confronto perde valore.

## Corpus pronto

- Cartella: `<WORK_DIR>\t09\clips` (10 clip, 57 MB).
- Manifesto: `<WORK_DIR>\t09\manifest.csv` - nome file, durata, larghezza, altezza, fps,
  audio, luma media, sorgente. Sono fatti meccanici: **nessun giudizio di qualita'**.
- Scelta: dieci clip che coprono condizioni diverse (luma da 50,6 a 119,5; verticale 474x850 e
  576x1024, orizzontale 848x480 e 1024x576, 720x1280 a 120 fps; durate da 5,9 a 91,9 s).
  La varieta' e' nelle condizioni, non nel giudizio: se una clip e' "brutta" lo decide
  l'utente, non il manifesto.
- La colonna **"Nome file"** va scritta **identica** al nome in `manifest.csv`: senza le clip
  l'agente ha i giudizi ma non puo' misurare nulla.

## Due passate

1. **v1**: 10 clip annotate (circa 25 minuti), mix di clip con difetti chiari e clip pulite.
   Tutte brutte o tutte belle non tarano nulla. Le soglie escono marcate **preliminari (v1)**.
2. **v2**: altre 10 clip, ricalibrazione. Solo con 20 clip le soglie possono essere dichiarate
   non provvisorie.

## Regole di lettura del modulo

- La colonna **"Completa?" e' una formula**: se il file e' stato salvato da un programma che
  non ricalcola, puo' risultare vecchia. Il controllo giusto e' a mano: una riga e' completa se
  le colonne **da B a M e la colonna P** sono tutte compilate.
- Le righe con `clip_id = ESEMPIO` si ignorano **sempre**, anche se sembrano complete.
- Le righe incomplete si segnalano nel ledger e **non si completano d'ufficio**: nessun valore
  inventato, nemmeno "0" o "n/d".

## Mappatura colonne -> soglie

| Colonna del modulo | Soglia o set di valutazione |
|---|---|
| Tremolio | `jitter` |
| Esposizione | `luma` e clipping |
| Dominante colore | `cast` |
| Rumore audio | `SNR` e noise floor |
| Volume | LUFS |
| Silenzi | dead air |
| Musica sotto il parlato | music ratio |
| Hook | metriche del primo secondo |
| BPM e Primo battere | set di valutazione del beat |
| Momenti migliori | set di valutazione di F6 |
| Cambi di argomento | set di valutazione di F6 |

## Prompt da incollare quando il modulo compilato arriva

```
T09 sbloccato. Il modulo compilato e' in <percorso>. Leggi i fogli Clip, Momenti migliori e
Cambi di argomento. Ignora le righe con clip_id ESEMPIO e le righe incomplete (colonna
Completa? diversa da fatto): segnalale nel ledger, non inventare valori. Mappa le colonne alle
soglie: Tremolio -> jitter, Esposizione -> luma e clipping, Dominante colore -> cast, Rumore
audio -> SNR e noise floor, Volume -> LUFS, Silenzi -> dead air, Musica sotto il parlato ->
music ratio, Hook -> metriche del primo secondo, BPM e Primo battere -> set di valutazione del
beat, Momenti migliori e Cambi di argomento -> set di valutazione di F6. Per ogni soglia
riporta il valore scelto, il metodo, il numero di clip usate e l'errore sul campione. Se le
clip compilate sono meno di 20, marca tutte le soglie come preliminari e ripeti la
calibrazione quando ne arrivano altre. Nessuna fase si chiude su un esame che usa soglie
preliminari.
```

## Stato del file

`T09_modulo_annotazioni.xlsx` **non e' presente** in questa macchina (cercato in `Downloads`):
va esportato dalla chat in cui e' stato creato. Se non e' piu' recuperabile, il ripiego e'
`<WORK_DIR>\t09\template_annotazioni.csv`, che ha le stesse colonne ma senza liste a tendina.
