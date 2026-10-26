# 18 — Clip lunghe: analisi a chunk e checkpoint

Un podcast di 45 minuti non si analizza in un colpo solo. Whisper, il VAD e
l'estrazione audio possono fallire al minuto 42; senza stato su disco tutto il
lavoro precedente sparisce. Questo modulo divide **parlato e VAD** in blocchi da
300 s, salva ogni blocco in un file atomico e, alla riesecuzione, ricalcola solo
ciò che manca.

## Piano dei blocchi

`s  = 300` secondi (configurabile con `analysis.chunk_sec` o `--chunk-sec`),
`sovrapposizione = 1` secondo.

- **Intervallo proprio**: `[i*s, (i+1)*s]`, ritagliato sulla durata; partizione
  esatta, nessun blocco vuoto in coda. 2700 s → 9 blocchi; 300 s → 1.
- **Intervallo decodificato**: `[own_start - 1, own_end + 1]`, ritagliato su
  `[0, durata]`. Il primo blocco parte da 0, l'ultimo finisce sulla durata.
- Un confine a 300 s può cadere dentro una parola. La sovrapposizione fa sì che
  entrambi i blocchi la vedano; la fusione la assegna a **uno solo** in base al
  punto medio. Nessuna parola persa, nessuna parola duplicata, e i sottotitoli
  possono attraversare la giuntura perché i cue si costruiscono **dopo** la
  fusione, mai per blocco.

## Fusione

`merge_transcripts` (in `analyze.py`):

- offset di ogni parola/segmento di `decode_start`, poi clamp su `[0, durata]`;
- scarto di parole con `start >= end` dopo il clamp;
- proprietà per punto medio: `own_start <= mid < own_end`, con l'ultimo blocco
  che include il proprio estremo destro;
- ordina per `(start, end)`; conserva le chiavi originali (`probability`,
  `avg_logprob`, `no_speech_prob`);
- `duration` è la **durata globale**, non la fine dell'ultima parola;
- `wpm = parole / (durata/60)`, arrotondato a una cifra;
- `text` dai segmenti posseduti, in ordine di tempo;
- `skipped_reason` vuoto se è sopravvissuta almeno una parola, altrimenti elenca
  i blocchi falliti.

`merge_vad`:

- offset e clamp delle regioni, poi merge dei gap sotto 0.35 s;
- `speech_ratio = secondi di parlato / durata globale`;
- `speech_dbfs`/`noise_dbfs` sono medie **pesate sull'energia**:
  `10*log10(sum(10**(dB/10)*secondi)/sum(secondi))`, pesate sulla durata propria
  del blocco. La media aritmetica dei dB è sbagliata e il test lo verifica
  esplicitamente (-20 dB per 1 s + -40 dB per 1 s → −22,97 dB, non −30);
- se **un** blocco manca o è `available=False`, il risultato globale è
  `available=False` con una ragione. Le regioni possono restare, ma
  `analyze_audio` non le usa: un mask parziale non deve sostituire il dead air
  dell'intero file.

## Checkpoint

Identità (`checkpoint.identity`), tutto ciò che può cambiare il significato di
un blocco:

- `source_sha256` (sha256 dell'**intero** file: il primo blocco da 64 KiB
  collide su file con lo stesso header);
- `analysis_sha256` (sha256 del file di stage: un cambio di normalizzazione
  cambia il timeline);
- `chunk_sec`, `model`, `language`, `transcribe`, `vad_config`;
- `engine_version`.

Cartella: `<work>/stage/checkpoints/<sha16>-<cfg8>/`. `<sha16>` identifica la
sorgente; `<cfg8>` è lo hash della configurazione **senza** `source_sha256`, così
`find_previous()` può riconoscere la stessa configurazione applicata a un'altra
sorgente.

File: `manifest.json` e `analysis_chunk_XX.json`. Ogni blocco porta un header
(schema, engine version, identità, indice, finestre propria e decodificata,
stato, sha256 del corpo) e il corpo `transcript`/`vad` (oppure `error`).

Regole:

- scritture atomiche nella stessa cartella (temp + `os.replace`); un errore
  lascia intatto il file precedente e non lascia `.tmp`;
- un blocco è marcato completato solo dopo che il file è stato riletto e il
  checksum del corpo combacia;
- manifest invalido (schema, identità, versione, totale) → ricostruito, non
  riusato;
- file corrotto, header sbagliato o checksum diverso → `None`, il blocco viene
  ricalcolato; nessuna eccezione;
- un'eccezione del loader non scrive nulla: il blocco resta mancante e la
  riesecuzione riprova **solo** quello;
- `invalidate()` cancella manifest e blocchi; `find_previous()` segnala una
  cartella con la stessa configurazione ma sha diversa (`stale_source`).

Contatori restituiti: `chunks_total`, `chunks_computed`, `chunks_cached`,
`chunk_failures`, `manifest_rebuilds`, `stale_source`, `checkpoint_dir`.

## Consumo

`_ingest` chiama `analyze.analyze_chunked` **una volta** quando
`should_chunk(durata, has_audio)` è vero. Il risultato contiene `transcript`,
`vad` e `stats`; il VAD viene passato ad `audio.analyze_audio(..., vad_result=...)`,
che non rifà la passata completa. La trascrizione non viene ripetuta: `cmd_run`
riusa `state["transcript"]`. Con `--no-speech` si calcolano solo VAD e checkpoint,
senza Whisper.

Clip sotto la soglia: percorso invariato (`analyze_audio` calcola il proprio VAD,
`_transcribe` fa la trascrizione completa). Un VAD fornito ma **senza regioni**
(musica, silenzio) non viene usato per il noise floor: si torna a p10/p90, come
prima, per non dichiarare un SNR di −218 dB.

Report: `analysis.chunked.stats` e `analysis.chunked.vad` (conteggi, non parole);
`analysis.speech` resta il blocco statistiche già usato dal piano. Nessuna
sezione nuova obbligatoria per `report.validate`.

## Cosa non è a chunk

Restano passate globali, perché sono economiche o perché la finestra romperebbe
la misura: scene cut, freeze, jitter/esposizione/colore/motion, loudness, true
peak, clipping, music ratio. Non sono dichiarate a chunk e non lo diventano di
nascosto.

## Limiti dichiarati

- faster-whisper e il modello Silero restano opzionali: senza di loro il VAD
  degrada con una ragione e la trascrizione restituisce `skipped_reason`; i test
  saltano, non falliscono.
- La sovrapposizione di 1 s copre una parola a cavallo del confine; una parola
  più lunga di 2 s è fuori scala e non è garantita.
- Il merge non ricostruisce la punteggiatura tra blocchi: il testo è la
  concatenazione dei segmenti posseduti, non una riscrittura.

## Test

`tests/test_chunk_checkpoints.py`: piano, fusione (parola al confine una sola
volta, clamp, dB pesati, VAD parziale), store (atomicità, corruzione, identità,
`find_previous`), resume 9 blocchi con 8 in cache, corruzione di un blocco,
invalidazione, retry dopo un errore, e integrazione reale con ffmpeg (+ ASR
quando disponibile).

`tests/test_vad_injection.py`: `analyze_audio` usa il VAD fornito una sola volta,
non lo usa senza regioni, non riprova quando è dichiarato non disponibile.
