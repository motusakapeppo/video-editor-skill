# Vedere il lavoro mentre si fa

Due livelli, e vanno detti per quello che sono.

## 1. Pannello (acceso di default)

`progress.html` nella workdir, riscritto a ogni fase, ricarica ogni secondo:
percentuale, fase in corso, cosa sta facendo, in italiano. `--no-panel` lo
spegne, `--panel DIR` lo sposta.

Se il lavoro ha una vista accesa (`--watch`), il pannello **ci porta dentro**
con un refresh di un secondo e un link: il pannello semplice e' un ripiego.

## 2. Vista locale (`vx serve DIR --port N`)

Serve tutti i lavori scoperti sotto `DIR` (fino a due livelli, il piu' recente
prima), con selettore quando sono piu' di uno, fotogrammi chiave, approvazione,
player del master su `/result` (attivo solo se il file esiste: `master_ready`) e
`/api/reveal` per aprire la cartella col file selezionato.

`/api/state` espone anche i dati della regia:

* `render`: `out_time_sec`, `duration_sec`, `percent`, `speed` letti dal file
  `-progress` di ffmpeg (`<workdir>/render/ffmpeg-progress.txt`). E' la
  posizione **reale** dell'encode, non una stima.
* `timeline`: gli shot del piano (`index`, `t_in`, `t_out`, `dur`).
* `shots`: gli stessi shot con `source`, `effects` (con i valori: `zoom 1.15`,
  `lut warm`, `fade in 0.3s`) e `thumb` (`/shots/<i>`, `thumb_ready`). Con
  `plan.json` gli effetti sono i trattamenti abilitati; con EDL 3 sono
  `clips[].effects` del singolo shot.
* `journal`: il racconto, una riga per fase (`t`, `phase`, `line`, `at`), in
  italiano e con i numeri. E' dati, non console: nasce da `panel.say()` e lo
  riempiono `narrate` e i comandi `ingest`/`run`.
* `approval`: `required`, `approved`, `waiting`, `reason` per il cancello dei
  fotogrammi (vedi `28-font-proposta-e-cancello.md` per la forma del cancello).
* `outcome`: `running` | `done` | `stopped` | `failed` | `incomplete` | `idle`.
  **Non** si deduce da `percent == 100`: un lavoro fermato puo' avere la barra
  piena. `done` esiste solo con un master che sta su disco.

`GET /shots/<i>?job=N` serve il JPEG di quello shot: prima la miniatura di
`analysis/` piu' vicina a `t_in`, poi la cache in `<work>/shots/<i>.jpg`,
altrimenti la estrae. Durante un render (file `-progress` fresco) risponde 404
dichiarato: la vista non deve rubare CPU a ffmpeg.

## Quello che non esiste

Un'anteprima video che cresce shot per shot: il renderer e' **un solo** ffmpeg
con un grafo unico (`concat=n=`), quindi non c'e' un artefatto per shot da
mostrare. L'unica eccezione sono i lavori lunghi, spezzati in chunk
(`edl3_chunks`), dove ogni chunk finito e' un file reale.

Se l'utente chiede di vedere il montaggio che si compone, va detto che non
esiste, invece di improvvisarlo: quello che esiste e' il playhead che avanza
sulla timeline secondo il progresso vero di ffmpeg.
