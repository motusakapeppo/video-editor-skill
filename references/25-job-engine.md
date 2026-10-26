# 25: Motore di job asincrono

`vx.jobs` è lo store su file che disaccoppia la richiesta dal render. Non è una
coda con retry e non è un servizio: è una directory con un record JSON per job,
pensata perché un `vx_auto` lungo non debba vivere dentro il timeout di un tool
MCP. La CLI resta il protocollo normativo; il motore è la sua variante
asincrona.

## Perché

`vx_auto` è sincrono e può durare minuti. Il timeout è del client, non del
server: quando il client chiude la chiamata, il render sincrono non ha più un
interlocutore. Il motore separa le due cose. `vx_job_submit` scrive un record
`queued` e torna subito con l'id; un worker separato esegue il lavoro; lo stato
si legge dal disco con `vx_job_status`. Il client può chiudere, riaprire e
chiedere di nuovo lo stesso id.

## Lo store

Un job è una directory: `<work_root>/jobs/<job_id>/job.json`. L'id è
`uuid4().hex`, quindi 32 caratteri esadecimali minuscoli, ed è validato contro
`^[0-9a-f]{32}$` prima di qualsiasi join di percorso: un id costruito a mano non
può uscire dalla radice dei job.

Ogni scrittura passa da un file temporaneo nella stessa directory, `flush` più
`os.fsync`, poi `os.replace`. Da qui le due invarianti:

- **un solo scrittore per job**: solo il worker che possiede il job lo muta, non
  c'è read-modify-write fra scrittori;
- **lettori senza lock**: chi legge vede il file precedente completo o quello
  nuovo completo, mai un file a metà, perché `os.replace` è atomico su POSIX e
  su Windows.

Gli stati sono quattro: `queued`, `running`, `completed`, `failed`. Le uniche
transizioni legali sono `queued→running`, `queued→failed`, `running→completed`,
`running→failed`. Ogni altra transizione è un `VxError`. `completed` e `failed`
sono terminali.

**Non esiste la cancellazione.** Non c'è un comando cancel, non c'è uno stato
`cancelled`, non c'è un segnale al worker. Un job già avviato arriva a
`completed` o a `failed`, oppure resta `running` finché qualcuno non lo legge e
lo reap.

## Claim

Il worker prende il job creando `<root>/<job_id>.claim` con
`O_CREAT|O_EXCL|O_WRONLY`. La prima chiamata riesce e restituisce `True`; la
seconda trova il file e restituisce `False`. Un secondo worker che perde il
claim esce senza toccare il record: il job ha un solo esecutore. Un id ben
formato ma sconosciuto restituisce `False`; un id malformato solleva `VxError`.

## Reaping degli orfani

Un job `running` porta il `pid` del worker. Quando lo si legge, se il pid non è
più vivo il record viene marcato atomicamente `failed` con
`error="worker died"` prima di essere restituito. La lettura con `reap=False`
salta questo passaggio.

La liveness la chiede al sistema operativo, mai all'orologio. Su Windows il
controllo usa `OpenProcess` con `PROCESS_QUERY_LIMITED_INFORMATION` e
`GetExitCodeProcess`, e considera vivo solo il codice `259` (`STILL_ACTIVE`).
`os.kill(pid, 0)` non viene mai usato lì, perché su Windows mappa su
`TerminateProcess` e ucciderebbe il processo che stava solo chiedendo se è vivo.
Sugli altri sistemi resta `os.kill(pid, 0)`, con `PermissionError` trattato come
vivo.

## Progresso

Il worker riceve i callback di stage di `auto.run` e li traduce in percentuale:
5 `triage`, 20 `ingest`, 25 `render`, 92 `render_done`, 96 `qc`, 100
`deliverables`. Fra 25 e 88 una coda di frame letta da `-progress` di ffmpeg
affina il valore dentro la fase di render. Il secondo passaggio loudnorm usa
`-c:v copy` ed è audio-only: non emette progresso, quindi non sposta la
percentuale.

La percentuale è monotona e limitata a 0..100. Un valore più basso del
precedente viene scartato, non riscritto; un valore fuori scala viene tagliato
al bordo. Il record conserva `progress_percent` come numero, non come stringa.

## Durabilità

Il worker viene lanciato staccato, senza console e con gli stream su `DEVNULL`.
Sopravvive all'uscita del server MCP: chiudere lo stdio del server non chiude il
render. Lo stato vive sul disco, quindi una sessione nuova può leggere un job
che non ha creato, purché punti alla stessa `work_root`.

## MCP

Il target è sei tool: i quattro già presenti (`vx_doctor`, `vx_triage`,
`vx_edl_validate`, `vx_auto`) più i due del motore.

| Tool | Argomenti obbligatori | Argomenti opzionali | Restituisce |
|---|---|---|---|
| `vx_job_submit` | `prompt`, `assets`, `out` | `workdir`, `fps`, `manifest`, `autonomous`, `crf`, `preset` | l'id del job, subito |
| `vx_job_status` | `job_id` | nessuno | il record del job |

`vx_job_submit` valida gli stessi parametri di `AutoRequest`: i tre obbligatori
devono essere stringhe non vuote e `assets` deve essere una directory esistente;
le chiavi sconosciute sono un errore. `vx_job_status` accetta solo un `job_id` di
esattamente 32 esadecimali minuscoli; un id malformato è un errore di parametri.

`vx_auto` resta per i job brevi: è sincrono e il timeout è del client. Per un
render lungo la coppia submit/status è la strada.

## Limiti dichiarati

- Nessuna cancellazione: un job avviato non si ferma.
- Un kill duro del worker lascia il record `running` finché una lettura
  successiva non lo reap; non c'è un demone che ripulisce da solo.
- Lo store è una directory su filesystem locale, non una coda con retry: un job
  fallito non viene ritentato.
- Nessuna coordinazione multi-host: due macchine che montano la stessa directory
  non sono un caso supportato.

## Container

**Non costruito e non verificato.** Su questa macchina non c'è un demone Docker,
quindi non esiste un'immagine provata e nessuna riga che segue è stata eseguita.
La ricetta è un punto di partenza, non una promessa.

Tre correzioni rispetto a un Dockerfile ingenuo:

1. **Install editable dentro l'immagine.** `config.SKILL_ROOT` è derivato da
   `__file__` (`parents[2]`) e `assets/fonts/` deve restare sotto quella radice,
   perché libass la riceve come `fontsdir`. Un `pip install .` semplice sposta il
   pacchetto in `site-packages`, fuori dall'albero: la scoperta di
   `vx.config.json` fallisce e i font non sono più raggiungibili. Serve
   `pip install -e .` con il repo copiato nell'immagine.
2. **Non contare su `ENV SKILL_ROOT`.** È inerte: il codice non legge quella
   variabile, la calcola dal file. Impostarla non cambia nulla.
3. **Non usare `docker run -i --rm` per il lavoro asincrono.** Chiudere lo stdin
   termina il container e con esso il worker. Serve un container motore
   long-lived e, per ogni sessione, `docker exec -i <container> vx-mcp`: così il
   worker staccato viene ri-parentato dentro il container e sopravvive alla
   chiusura della sessione.

```dockerfile
FROM <python-3.10-base>
RUN <install-ffmpeg-with-libass-vidstab-loudnorm-silencedetect>
WORKDIR <app_dir>
COPY . <app_dir>
RUN <venv>/bin/python -m pip install --no-cache-dir -e .
RUN <venv>/bin/vx doctor --json
```

L'ultima riga è un gate di build: se `doctor` non dice `ready`, l'immagine non
si costruisce. Il `work_root` deve essere un volume montato, altrimenti lo stato
dei job muore con il container. Tutti i percorsi restano segnaposto: nessuna
lettera di unità, nessun percorso utente.

## Test

- `tests/test_jobs.py`: record atomici, id validati, transizioni legali,
  progresso monotono e limitato, claim una volta sola, reaping, liveness.
- `tests/test_docs_consistency.py`: l'indice delle reference copre 01..25.
