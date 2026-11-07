---
name: video-editor
description: |-
  Senior Video Editor & Social Media Strategist. Editing adattivo con giudizio
  per singolo video, mai effetti a pioggia: si applica solo ciò che i dati
  giustificano. Supporta .mp4 .mov .mkv .avi .webm .m4v .mts.
  Un solo comando monta (`vx reel`), una vista mostra il lavoro, un contratto
  dichiara se il risultato è pubblicabile. Il file sorgente non viene mai toccato.

  Attivazione automatica: "monta/edita/taglia questo video", "togli i silenzi",
  "sottotitoli", "verticale per TikTok", "reel/shorts/tiktok", "auto-framing",
  "inquadra il volto", "hook", "prepara per i social", "color correction",
  "stabilizza", "normalizza l'audio", "analizza il video", "qc", "consegna",
  "deliverables". Si attiva anche su qualsiasi file .mp4 .mov .mkv .avi .webm
  .m4v .mts e su ogni richiesta di editing o adattamento di formato.

  Vedere il lavoro mentre si fa: "mostrami cosa succede", "fammi vedere",
  "segui il lavoro", "guardiamo mentre lavora", "apri il pannello",
  "pannello live", "watch", "voglio vedere il progresso". Con una di queste
  frasi la vista è OBBLIGATORIA e il comando `/reel` la apre prima del lavoro.
license: MIT
compatibility: opencode
metadata:
  version: "1.5.0"
  engine: vx (python 3.10 + ffmpeg 8.1 + opencv + faster-whisper)
  venv: "<VENV_DIR>"
  ffmpeg: "8.1"
  schema: vx.report/1.0
  workroot: "<WORK_DIR>"
---

# video-editor

Toolkit di montaggio assistito. Ogni decisione nasce da una misura, non da un
preset; il sorgente non viene mai toccato. Questo file dice **come si pensa** un
montaggio e **come si giudica** un risultato. L'esecuzione (comandi, flag,
percorsi) sta in `references/31-protocollo.md`: qui non si ripete.

## Come si lavora: una porta sola

```powershell
& $PY $VX reel --prompt "<cosa vuole l'utente>" --assets "<cartella>" --out "<file>"
```

`vx reel` è l'unico ingresso. Fa tutto, scrive il contratto del lavoro e dice se
il risultato è pubblicabile. Non si compone la pipeline a mano: se una capacità
manca si dichiara e ci si ferma, non si improvvisa (`vx auto`, `edl3`, `run`
restano strumenti di livello più basso, non il percorso normale).

La vista non è un optional: `vx up` accende il daemon che resta vivo, `/reel`
apre la pagina prima di ogni altra cosa. Una vista promessa e non aperta è un
difetto, non una distrazione. La pagina mostra il **contratto**, non solo la
percentuale: otto stadi con valore e soglia, e il timbro `NON PUBBLICABILE` col
nome vero del file quando il contratto non ha firmato.

Due lavori sulla stessa cartella non convivono: `<workdir>/.vx/lock` è un lease
con battito fuori banda, e chi arriva secondo esce `2` dicendo chi lo tiene. Su
cartelle diverse si lavora in parallelo.

## Protocollo

Otto stadi, sempre, in quest'ordine. Ognuno **firma** il proprio risultato con il
valore misurato e la soglia, in `.vx/run.jsonl` (append-only, con checksum):

| stadio | firma |
|---|---|
| `doctor` | verdetto dell'ambiente |
| `ingest` | sorgenti usate |
| `analyze` | sorgenti misurate |
| `decide` | clip e densità di taglio |
| `gates` | secondi di attesa umana |
| `render` | durata del master contro quella richiesta |
| `qc` | LUFS misurati contro il target |
| `deliver` | contrasto del titolo contro il minimo |

Regole non negoziabili:

1. **Nessuno stadio salta in silenzio**: o firma, o è `skip` col motivo.
2. **Un valore fuori soglia non si consegna**: il file diventa
   `NON-PUBBLICABILE-<nome>.mp4`, l'esito è `failed` e il motivo sta nel report,
   nella pagina e **nel nome del file**. Il master resta sul disco per guardarlo.
3. **La misura la fa la macchina, la scelta la firma la persona**: sono due campi
   distinti, mai uno solo.
4. **La ripresa non rifà il già fatto**: `--resume` legge `run.jsonl` e riparte
   dallo stadio non firmato; una coda tronca da crash si scarta, non si interpreta.
5. **Nessun cancello aspetta il deserto**: se nessuno guarda, si usa la proposta e
   si dichiara `by: auto`. Se qualcuno guarda, la pagina dice che sta aspettando lui.

## Legge fondamentale

1. Nessun finding, nessun trattamento. Ogni trattamento attivo porta `evidence`,
   una lista non vuota di testimoni `{finding_id,t0,t1}`: `evidence[0].finding_id`
   è il `reason.finding_id`, ogni testimone copia lo span sorgente, e per
   `cut_silence` ogni intervallo rimosso cade dentro l'unione dei testimoni.
   Un finding stantio o uno span che non combacia ferma il render.
2. Massimo **3 trattamenti creativi** (`autoframe`, `zoom`, `hook_overlay`, `fit`).
   Un solo passaggio di stabilizzazione. Un effetto senza `evidence` non si applica:
   senza una misura che lo giustifichi su **quella** clip, la spec si scarta e si
   conta (`dropped_no_evidence`); oltre il terzo trattamento distinto si rifiuta.
3. **Meglio corto che ripetuto**: se più di due inquadrature consecutive sono
   quasi identiche, il montaggio si accorcia tenendo la migliore, e il report
   dichiara `variety: {removed, before, after}`. Sotto la durata minima il
   lavoro non si consegna. Violare la 2 o la 3 è `delivered: false`, non un avviso.
4. Il brief dell'utente vince sempre sulla rilevazione automatica. Se dice
   "lascia il rumore, è ambientale", il denoise non si applica.

## La scaletta

Ogni lavoro compila sei sezioni — `hook`, `beat`, `payoff`, `cta`, `proof`,
`broll` — dalle misure già prese. Ognuna porta la **competenza** che l'ha
prodotta, il **materiale** su cui poggia e il **numero** che la giustifica: senza
numero non è compilata, è `missing` o `absent` col motivo. La scaletta non spegne
nessuna competenza e non inventa sezioni: le sei sono chiuse.

Se la richiesta nomina un hook o una CTA, quella sezione è **obbligatoria**: se
resta scoperta il lavoro **non si consegna**, e il buco sta in
`report["sections"]["gaps"]`.

La scaletta **compone** il montaggio con leve misurate: la durata del gancio
(nitidezza dell'apertura — triage, o profilo per-frame quando il triage è cieco —,
BPM, corsa utile, quota della durata), il numero di clip in timeline (contato sui
due piani: baseline e composto), l'ordine dei b-roll (nitidezza) e i loro
ingressi (sul taglio più vicino, non a metà frase), più una **coda libera**
quando la richiesta chiede una CTA. Con la griglia BPM il gancio è il primo
shot: si dichiara, non si inventa. Se una misura manca la leva **declina col
motivo** e il piano resta quello di prima; il report porta `proposed` e
`applied` separati, così si vede cosa è stato proposto e cosa il piano mostra
davvero.

Le composizioni che un grafo di filtri non sa dire — split screen, confronto,
sottopancia — si dichiarano e le rende **Chromium** (`vx scene`), un fotogramma
alla volta, pubblicate in `live/` mentre si costruiscono. La grafica sta al
browser, il montaggio dei flussi video a ffmpeg.

I sottotitoli si chiedono col flag **o a parole** («con sottotitoli»). Se il
parlato non c'è il report dice `no speech`; se nessuno li aveva chiesti dice
`sottotitoli non richiesti` — sono due cose diverse.

Se il reel esce più corto perché ha tolto i silenzi, si tollera fino al **20%
(max 3 s)** e il report lo scrive con i due numeri; oltre quella soglia non si
consegna.

## Come si giudica

I numeri dicono se il lavoro è **corretto**, non se è **buono**. Queste sono le
soglie che decidono; sotto, il lavoro non è pubblicabile:

- **titolo leggibile**: contrasto ≥ 4.5 sul fotogramma che sta sotto, misurato —
  se serve si aggiunge contorno, ombra o placca, e si dichiara quale;
- **titolo nel posto giusto**: fuori dalla zona bassa dove la piattaforma mette i
  suoi comandi (≈13% dell'altezza in un verticale); apertura nei primi 0,5 s;
- **audio a target**: LUFS di consegna ± 1.5, true peak sotto il tetto;
- **durata al frame**: quella chiesta, tolleranza un frame; se il materiale non
  basta, si dichiara il massimo raggiungibile e non si finge;
- **meglio corto che ripetuto**: se il materiale non regge la durata con varietà,
  il reel si accorcia invece di mostrare la stessa inquadratura dieci volte;
- **l'apertura mostra qualcosa**: primo fotogramma nitido e con un soggetto, mai
  un movimento cieco; le clip inutilizzabili si escludono, gli inizi si spostano
  sul picco di nitidezza.

Il gusto non si inventa: si misura ciò che è misurabile e, dove non lo è, si
dichiara la scelta e l'alternativa scartata nel report.

## Cosa misura

Jitter (optical flow DIS), esposizione, cast colore, freeze, scene cut, noise
floor e SNR, dead air, LUFS e true peak, music ratio, volti e speaker, WPM,
nitidezza per-frame. Soglie in `scripts/vx/contract.py`: tabella read-only,
`settings` unico scrittore. Trattamenti: `references/30-trattamenti.md`.

## Deliverable

Quattro artefatti in `out/`, dichiarati con sha256 in `auto_report.json`
(`deliverables[]`): master, sottotitoli (solo se c'è parlato), copertina, contact
sheet. Le varianti per piattaforma non esistono. Il voto 1–5 si chiede a lavoro
finito e si legge **solo su richiesta** (`vx learn`): ricalibrare a ogni corsa
costa secondi che non si spendono.

## Regola zero

Ogni fase passa da un comando `vx`. Niente ffmpeg a mano, niente script di
pipeline, niente UI nuove. `vx guard <dir>` dice se un lavoro è passato davvero
di qui. All'inizio si dichiara se si lavora in autonomia o con le scelte
dell'utente; in autonomia ogni scelta va motivata con l'alternativa scartata.

I **limiti** (piattaforma, un lavoro per cartella, nessuna variante per
piattaforma, una macchina sola) stanno in un posto solo:
`references/31-protocollo.md`, sezione 10. Qui non si ripetono.

### Exit codes

`0` ok · `exit 2` usage error · `3` needs input (briefing e fermati) · `4`
dipendenza mancante · `exit 5` QC failed · `exit 6` render failed (tail di ffmpeg).
`1` non è di `vx`: la shell ha unito `stderr`; il progresso va su stdout, gli
errori su stderr.

## Riferimenti

- `references/01-ambiente.md` ambiente e GPU; `references/02-metriche-analisi.md` metriche; `references/03-matrice-decisionale.md` finding e gate; `references/04-specifiche-social.md` safe zone e hook; `references/05-auto-framing.md` volti e crop; `references/06-sottotitoli-ass.md` whisper e ASS; `references/07-render.md` stadi e loudnorm; `references/08-qc-e-consegna.md` codici QC; `references/09-accelerazione-e-vad.md` hardware e VAD; `references/10-grammatica-del-montaggio.md` ritmo; `references/11-retention-e-piattaforme.md` retention; `references/12-lezioni-dal-campo.md` lezioni misurate
- `references/13-lezioni-impresse.md` le 15 regole; `references/14-direzione-visiva-adattiva.md` i sei assi; `references/15-open-montage-rnd.md` studio esterno; `references/16-doppio-regime.md` STRICT/AUTONOMOUS; `references/17-ingest.md` stage 00; `references/18-clip-lunghi-checkpoint.md` clip lunghe; `references/19-edl-multisorgente.md` EDL 2.1; `references/20-render-multisorgente.md` render N sorgenti; `references/21-regia-semantica-keyword.md` B-roll; `references/22-grammatica-e-mixer-audio.md` mixer A/B; `references/23-triage-e-auto.md` triage e auto; `references/24-api-e-mcp.md` API e MCP; `references/25-job-engine.md` job asincroni; `references/26-edl3-e-ponte.md` EDL 3; `references/27-studio-higgsfield.md` studio documentale; `references/28-font-proposta-e-cancello.md` font; `references/29-vedere-il-lavoro.md` vista; `references/30-trattamenti.md` trattamenti; `references/31-protocollo.md` comandi, flag, percorsi, eccezioni
