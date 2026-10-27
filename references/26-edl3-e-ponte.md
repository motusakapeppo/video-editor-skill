# 26 - EDL 3 e ponte tra i motori

> Specifica di progetto, non codice. Definisce il contratto EDL 3, il ponte tra
> analisi e assemblaggio, il ciclo critico e il backlog con criteri d'accettazione.
> Snapshot: 27 ottobre 2026. Contratto corrente: payload 1.5.0, EDL 2.2.0.

---

## 1. Perche un EDL 3

L'EDL 2.2 e un contratto di **montaggio**: sorgenti, tracce, clip, transizioni,
gain, ducking. Non e un contratto di **composizione**. Nel codice attuale:

- `ClipSegment` porta `gain_db` e una transizione in ingresso e una in uscita, ma
  non porta effetti, keyframe, velocita, maschere o posizione;
- `Transition` e una stringa piu una durata, e il renderer implementa solo taglio e
  dissolvenza;
- `Track` e una lista di clip, senza livelli di regolazione;
- `EDLPlan` ha tracce video e audio, senza livelli di testo e senza sequenze
  annidate;
- non esiste alcun tipo `Effect`, `Keyframe`, `AdjustmentLayer`, `TextLayer` o
  `NestedSequence`.

Aggiungere effetti a 2.2 significherebbe aggiungere campi speciali a un contratto
pensato per altro. EDL 3 e il substrato che rende esprimibile il resto: senza
keyframe non c'e speed ramp, senza livelli di regolazione non c'e grading coerente,
senza livelli di testo non ci sono titoli, senza sequenze annidate non c'e montaggio
modulare.

**Regola di progetto:** nessun effetto nuovo entra in 2.2. Ogni effetto nasce dentro
EDL 3 con un innesco misurato, un tetto e una prova visiva.

---

## 2. Il modello EDL 3

### 2.1 Struttura

```json
{
  "contract_version": "3.0.0",
  "timeline": {"fps": 30.0, "duration_sec": 45.0,
               "aspect_ratio": "9:16", "resolution": [1080, 1920]},
  "sources": {"c01": {"path": "<STAGE>/c01.mp4", "sha256": "<64 hex>",
                       "duration": 6.0, "camera": {}}},
  "tracks": {
    "video": [
      {"track_id": 0, "kind": "clip", "clips": []},
      {"track_id": 1, "kind": "adjustment", "ranges": []},
      {"track_id": 2, "kind": "text", "items": []}
    ],
    "audio": [
      {"track_id": 0, "role": "dialogue", "clips": []},
      {"track_id": 1, "role": "music", "clips": []}
    ]
  },
  "nested": [],
  "anchors": [],
  "ducking": {}
}
```

Il documento resta leggibile e diff-abile: JSON puro, nessun riferimento esterno
oltre alle sorgenti e agli asset di testo.

### 2.2 Effect e Keyframe

```json
{
  "effect_id": "fx_001",
  "type": "zoom",
  "target": {"kind": "clip", "clip_id": "main_0"},
  "params": {"scale": 1.12, "center": [0.5, 0.45]},
  "keyframes": [
    {"t": 0.0, "value": {"scale": 1.0}, "interp": "ease_out"},
    {"t": 2.4, "value": {"scale": 1.12}, "interp": "hold"}
  ],
  "evidence": [{"anchor_id": "a_face_03", "t0": 0.0, "t1": 2.4}],
  "cap": "one_push_in_per_shot"
}
```

Regole:

- ogni effetto ha un `type` preso dal registro (sezione 3), non una stringa libera;
- ogni keyframe ha un tempo assoluto sulla timeline e un'interpolazione fra
  `hold`, `linear`, `ease_in`, `ease_out`, `ease_in_out`, `bezier`;
- ogni effetto cita almeno un'ancora (sezione 4); un effetto senza ancora e un
  errore di validazione;
- i parametri sono validati da uno schema per tipo, non accettati alla cieca;
- un effetto dichiara il proprio tetto (`cap`), e la validazione conta quante volte
  compare nella stessa clip o nello stesso intervallo.

### 2.3 Adjustment layer

```json
{
  "range_id": "adj_001",
  "dst_in": 10.0,
  "dst_out": 18.0,
  "applies_to_layers": [0, 1],
  "effects": [
    {"type": "grade", "params": {"lut": "<ASSET>/look.cube", "mix": 0.6},
     "evidence": [{"anchor_id": "a_shot_04", "t0": 10.0, "t1": 18.0}]}
  ]
}
```

Serve per applicare un look o una correzione a piu clip senza ripetere l'effetto e
senza toccare il clip. E il modo corretto per un grading coerente.

### 2.4 Text layer e caption track

```json
{
  "item_id": "tx_001",
  "text": "QUELLA SERA",
  "style": {"font": "Antonio", "size": 134, "color": "#F4EBDD",
            "outline": 7, "shadow": 4, "align": "center", "margin_v": 160},
  "dst_in": 0.8,
  "dst_out": 5.2,
  "animation": {"type": "fade", "in": 0.52, "out": 0.52},
  "evidence": [{"anchor_id": "a_brief_card", "t0": 0.8, "t1": 5.2}]
}
```

La caption track e un caso speciale del text layer: cue con inizio, fine, testo e
stile, con enfasi per parola. Il margine e un parametro del livello, non un numero
fisso dentro uno stile globale: e questo che ha confinato i sottotitoli dentro la
fascia nel caso studio.

### 2.5 Nested sequence

```json
{
  "sequence_id": "seq_intro",
  "plan": {"...": "un EDL 3 completo, con la propria timeline"},
  "transform": {"scale": 0.42, "position": [0.08, 0.72], "opacity": 1.0}
}
```

Serve per riusare un blocco (intro, sigla, template) e per comporre picture in
picture senza duplicare le clip.

### 2.6 Transizioni, velocita, PiP, maschere, blend

- **Transizioni**: `cut`, `fade`, `dip`, `wipe`, `slide`, `zoom`, `whip`, `glitch`,
  ognuna con parametri, durata e curva. Il renderer mappa su `xfade` quando
  possibile, altrimenti su un grafo dedicato.
- **Velocita**: `speed` per clip o `speed_map` con keyframe; il time remap cambia la
  durata, quindi la timeline va ricalcolata e l'audio va risincronizzato.
- **PiP**: `placement` con rettangolo normalizzato, opacita, maschera, blend mode.
- **Maschere**: rettangolo, ellisse, poligono o asset alpha, con feather.
- **Blend**: `normal`, `screen`, `multiply`, `overlay`, `lighten`, `darken`.

### 2.7 Serializzazione e validazione

- I campi opzionali si omettono quando sono al valore neutro, come in 2.2.
- La versione e esatta: un documento 2.2 non viene interpretato come 3.0.
- La migrazione 2.2 -> 3.0 e un tool separato (`vx edl migrate`), non una modalita
  implicita del parser.
- La validazione e strutturale e semantica: un effetto sconosciuto, un keyframe
  fuori dall'intervallo della clip, un'ancora inesistente, un layer di regolazione
  che copre un layer superiore, un testo fuori dalla safe area sono errori.
- Il round-trip `to_dict` / `from_dict` deve essere identico, come in 2.2.

### 2.8 Perche 3.0 e non 2.3

Perche cambia la forma del documento: livelli tipizzati, effetti, ancore, sequenze
annidate. Una 2.3 sarebbe una bugia di compatibilita. La 2.2 resta valida e
leggibile; la 3.0 e un nuovo contratto con un migratore esplicito.

---

## 3. Il registro degli effetti

### 3.1 Contratto di un effetto

Ogni effetto dichiara:

| Campo | Significato |
|---|---|
| `type` | identificatore unico, es. `zoom`, `whip`, `speed_ramp` |
| `trigger` | tipi di finding o di ancora che lo giustificano |
| `params_schema` | schema dei parametri, con limiti |
| `cap` | quante volte per clip, per shot o per minuto |
| `implementation` | filtro o catena ffmpeg, o modulo Pillow |
| `fallback` | cosa fare se il filtro non e disponibile |
| `proof` | artefatto visivo o sonoro che dimostra l'effetto |
| `cost` | costo di render stimato |

### 3.2 Prime famiglie

| Famiglia | Effetti | Innesco tipico |
|---|---|---|
| Reframe | `reframe`, `punch_in`, `pan` | traccia volto, speaker attivo |
| Transizioni | `fade`, `dip`, `wipe`, `slide`, `zoom`, `whip`, `glitch` | confine di scena, beat |
| Velocita | `speed_ramp`, `freeze`, `reverse` | movimento, beat, enfasi |
| Colore | `grade`, `match_shot`, `vignette`, `grain`, `halation` | cast, luma, shot |
| Audio | `duck`, `denoise`, `deess`, `compress`, `sfx` | SNR, loudness, transizione |
| Testo | `card`, `lower_third`, `caption`, `kinetic` | brief, trascrizione |
| B-roll | `overlay`, `pip`, `mask` | ancora semantica |

### 3.3 La legge dell'evidenza applicata agli effetti

La legge resta la stessa: nessun effetto senza una prova. Cambia l'oggetto della
prova. Per un taglio e uno span di silenzio o di trascrizione; per un reframe e una
traccia di volto; per un colore e una misura di cast o di luma; per un beat e un
tempo stimato con una confidenza; per un testo e un brief o una parola trascritta.

Un effetto senza ancora non e "creativo": e un errore.

---

## 4. Il ponte analisi -> assemblaggio

### 4.1 Anchor

L'ancora e l'unita che collega una misura alla timeline.

```json
{
  "anchor_id": "a_word_0042",
  "kind": "word_span",
  "source_id": "c01",
  "t0": 12.31,
  "t1": 12.78,
  "text": "hotness",
  "confidence": 0.94,
  "payload": {"speaker": "S1", "energy": 0.71}
}
```

Tipi: `transcript_span`, `word_span`, `silence_span`, `face_track`, `shot`,
`beat`, `keyword`, `brief`, `music_section`.

### 4.2 BridgeReport

Il ponte produce un referto, non un effetto collaterale:

```json
{
  "anchors": [],
  "cuts": [{"t": 12.4, "anchor_id": "a_silence_07", "reason": "dead_air"}],
  "captions": [{"t0": 12.31, "t1": 13.9, "text": "...", "anchor_id": "a_span_11"}],
  "broll": [{"asset_id": "b03", "at": 12.4, "anchor_id": "a_keyword_02",
              "score": 0.81, "accepted": true}],
  "reframe": [{"t0": 0.0, "t1": 2.4, "face_track": "f01", "center": [0.47, 0.42]}],
  "audio": [{"clip_id": "main_2", "measured_i": -24.85, "gain_db": 4.85,
              "declared": "target -20"}],
  "not_applied": [{"anchor_id": "a_keyword_09", "reason": "no candidate above 0.8"}]
}
```

`not_applied` e obbligatorio: un'ancora senza effetto non sparisce, viene dichiarata.

### 4.3 Trascrizione -> tagli, sottotitoli, B-roll

- Le parole con tempo e confidenza diventano `word_span`.
- Le pause fra parole diventano candidati di taglio, ma solo se cadono dentro un
  `silence_span` misurato dal VAD.
- Le frasi diventano cue, con i limiti gia esistenti (0.4-7 s, 18 caratteri per
  riga nel 9:16, 20 cps).
- Le parole chiave (per frequenza, per enfasi o per embedding) diventano ancore per
  il B-roll. Ogni candidato ha uno score e una soglia; sotto soglia resta in
  `not_applied`.

### 4.4 Silenzi e dead air

Il taglio usa la stessa legge di oggi: ogni intervallo rimosso deve cadere dentro
l'unione dei `silence_span`. Il ponte non introduce una nuova tolleranza: usa le
soglie di `contract.THRESHOLDS`.

### 4.5 Volti -> reframe

Una `face_track` produce keyframe di centro e scala. Il reframe multi-soggetto
sceglie il volto attivo con la stessa logica dello speaker assignment gia presente
in `faces.py`; il punch-in si aggancia a un picco di energia o a una parola
enfatizzata.

### 4.6 Beat -> snap dei tagli

`bpm.py` e `grammar.py` esistono gia. Il ponte li usa per spostare un taglio di
pochi frame sul beat piu vicino, dentro un intervallo dichiarato, e per registrare
lo spostamento nel referto. Se la confidenza del tempo e bassa, il ponte non sposta
nulla e lo dichiara.

### 4.7 Audio -> gain, denoise, stabilizzazione

- Gain per clip: misurato **sul segmento usato**, non sull'intero file. Il tetto
  silenzioso sparisce: se il gain necessario supera il limite, il referto dichiara
  la carenza.
- Denoise: solo con SNR sotto soglia, con fallback `highpass`.
- Stabilizzazione: solo con jitter sopra soglia, una sola passata.
- Ducking: gia nel contratto 2.2, esteso al ponte per la musica.

### 4.8 Dedup percettivo e camera

- Dedup: pHash o embedding visivo con soglia; il duplicato entra in
  `rejected_assets` con `DUPLICATE_ASSET` e il punteggio.
- Camera: l'inferenza e opzionale e dichiarata. Se non c'e, resta l'esenzione
  camera gia esistente; nessun falso divieto a 30 gradi.

### 4.9 Cosa resta non applicato

Il ponte non deve "fare tutto". Deve dichiarare cosa non ha applicato e perche.
Un referto con `not_applied` vuoto e sospetto: significa che il ponte non ha
guardato abbastanza.

---

## 5. Il ciclo critico

### 5.1 Perche

Oggi il risultato e corretto, non bello. La differenza tra i due e un passo che
manca: **guardare** il risultato e correggerlo. E la lezione "guarda il frame" resa
automatica.

### 5.2 Protocollo

1. Render di un proxy a bassa risoluzione, con lo stesso EDL.
2. Campionamento: un frame ogni due secondi, piu i frame ai confini di taglio, piu
   una contact sheet.
3. Misure: loudness, true peak, durata, densita dei tagli, testo fuori safe area,
   volti tagliati, colore per shot.
4. Rubrica: composizione, testo, ritmo, colore, audio, storia.
5. Correzione: l'agente propone modifiche all'EDL, ognuna con un'ancora e una prova.
6. Nuovo render del proxy e confronto prima/dopo.
7. Stop quando non ci sono blocker o quando il budget di iterazioni e esaurito.

### 5.3 Rubrica

| Asse | Cosa si guarda | Blocker |
|---|---|---|
| Composizione | soggetto centrato, headroom, safe area | volto tagliato, soggetto fuori area |
| Testo | leggibilita, velocita, sovrapposizione | testo illeggibile o fuori area |
| Ritmo | durata dei tagli, beat, energia | taglio senza respiro o accavallato |
| Colore | continuita fra shot, pelle | salto di colore evidente |
| Audio | intelligibilita, musica, picchi | voce coperta o clipping |
| Storia | hook nei primi 3 s, chiusura | nessun hook, finale troncato |

### 5.4 Artefatti

- `critique.json`: finding, frame campione, asse, severita, correzione proposta.
- `before/` e `after/`: frame e contact sheet.
- `iteration.json`: budget, esito, motivi dello stop.

### 5.5 Stop

Il ciclo si ferma quando: nessun blocker, budget esaurito (default 3), oppure una
correzione peggiora la rubrica. L'ultimo caso e importante: il critico non deve
oscillare.

---

## 6. Backlog con criteri d'accettazione

Sforzo: S < 1 giorno, M 2-4 giorni, L 1-2 settimane, XL oltre.

### Gruppo A, fondamentali (chiudono "Quella Sera")

| ID | Capacita | Criterio d'accettazione | Sforzo |
|---|---|---|---|
| A1 | Multi-A-roll sequenziale | `vx auto --montage` su 8 clip produce 8 clip di layer 0 in ordine e durata somma; il referto elenca 8 `a_roll` | M |
| A2 | Politica di riempimento | `--fill crop` produce una clip 1080x1920 croppata e dichiara `upscale`; `fit` produce bande; `blur` produce sfondo sfocato e primo piano integro; tre grafi distinti | M |
| A3 | Loudness per clip sul segmento | ogni clip a_roll entro ±1 LU dal target, o carenza dichiarata; nessun tetto silenzioso | M |
| A4 | Ponte trascrizione | ogni taglio e ogni B-roll cita uno `word_span`; `not_applied` elenca le ancore senza effetto | L |
| A5 | Taglio silenzi dal VAD | ogni intervallo rimosso cade dentro l'unione dei `silence_span`; il gate lo verifica | M |
| A6 | Reframe da tracce volto | keyframe di centro e scala solo dove esiste una `face_track`; nessun keyframe orfano | M |
| A7 | Denoise e stabilizzazione | si attivano solo con il finding; una sola passata di stabilizzazione | S |
| A8 | B-roll semantico | ogni B-roll ha score e ancora; sotto soglia resta in `not_applied` | L |
| A9 | Dedup percettivo | due inquadrature simili producono `DUPLICATE_ASSET` con punteggio | M |
| A10 | Camera dichiarata o esente | nessun falso divieto 30/180; ogni esenzione e elencata | S |
| A11 | B-roll sovrapposti | politica esplicita: trim, shift o rifiuto con motivo; mai scarto silenzioso | S |
| A12 | Loop musica | una traccia corta viene loopata con crossfade e conteggio dichiarato | S |

**Stato dei criteri.** A1-A12: implementati, come descritto in 6.1. B1-B12,
C1-C7, D1-D6 ed E1-E6: aperti, non implementati.

### Gruppo B, espressione (EDL 3)

| ID | Capacita | Criterio d'accettazione | Sforzo |
|---|---|---|---|
| B1 | Modello EDL 3 | round-trip identico, versione esatta, migratore 2.2 -> 3.0 | L |
| B2 | Registro effetti | ogni effetto ha trigger, schema, tetto, fallback, prova | M |
| B3 | Keyframe | interpolazioni hold/linear/ease; validazione dei tempi | M |
| B4 | Adjustment layer | un look applicato a piu clip senza duplicare l'effetto | M |
| B5 | Text layer | card, lower third, caption con margine per livello | M |
| B6 | Transizioni | fade, dip, wipe, slide, zoom, whip, glitch, con prova | L |
| B7 | Speed ramp | time remap con audio risincronizzato e durata ricalcolata | L |
| B8 | PiP, maschere, blend | placement, maschera con feather, blend mode | L |
| B9 | Sequenze annidate | un blocco riusabile composto senza duplicare le clip | L |
| B10 | LUT e match shot | look coerente, pelle protetta, nessun salto di colore | L |
| B11 | Tagli sul beat | snap entro un intervallo dichiarato, confidenza minima | M |
| B12 | Multicamera | sync per cross-correlazione e switching sullo speaker | XL |

### Gruppo C, iterazione

| ID | Capacita | Criterio d'accettazione | Sforzo |
|---|---|---|---|
| C1 | Cache degli stage | un secondo run sugli stessi sorgenti non ri-stagia | M |
| C2 | Re-render parziale | cambiare una clip rifa solo quel segmento | L |
| C3 | Undo e cronologia | ogni modifica EDL e reversibile | M |
| C4 | Anteprima A/B | due varianti affiancate con gli stessi frame | M |
| C5 | Stima di tempo | prima del render, stima entro il 30 per cento | S |
| C6 | Coda e cancellazione | N job con limite di concorrenza e cancel reale | L |
| C7 | Progresso loudnorm | anche la seconda passata emette progresso | S |

### Gruppo D, interoperabilita

| ID | Capacita | Criterio d'accettazione | Sforzo |
|---|---|---|---|
| D1 | Export multi-aspect | un lavoro, tre master 9:16, 1:1, 16:9 | M |
| D2 | OTIO, FCPXML, AAF | round-trip con almeno un NLE | XL |
| D3 | Stem dialogo e musica | due file separati, somma uguale al mix | L |
| D4 | VTT e soft-sub | sidecar VTT e variante soft-sub | S |
| D5 | 10 bit, HDR, 5.1 | output 10 bit e HDR con metadati | XL |
| D6 | Capitoli e metadati | capitoli nel contenitore e metadati dichiarati | S |

### Gruppo E, percezione e critica

| ID | Capacita | Criterio d'accettazione | Sforzo |
|---|---|---|---|
| E1 | Ciclo critico | 3 iterazioni massime, `critique.json` con blocker e correzioni | L |
| E2 | Percezione semantica | segmenti per argomento, speaker, energia, cosa si vede | L |
| E3 | Sottotitoli pro | parola enfatizzata, 8-10 stili, nessun cue fuori area | M |
| E4 | Reframe multi-soggetto | il centro segue lo speaker attivo con punch-in | L |
| E5 | Impronta di stile | misure estratte da un riferimento e riapplicate | L |
| E6 | Disciplina degli effetti | ogni effetto ha trigger, tetto e prova; il gate rifiuta il resto | M |

---

## 6.1 Stato della Fase A

La Fase A non e piu una promessa: A1-A12 sono nel codice. Quello che segue
descrive cosa esiste davvero, dove vive e cosa si puo osservare. Dove un limite
resta, e scritto come limite, non come roadmap.

**A1. Montaggio multi-A-roll (`--montage`).** Con `--montage` ogni video con
audio che supera i cancelli del triage diventa un `a_roll`, nell'ordine di
scoperta degli asset; i video muti restano `b_roll` e un ruolo dichiarato nel
manifest vince sull'euristica. Il percorso predefinito resta governato dalla
regola del singolo `a_roll`. Il comportamento e in `auto.py`, che costruisce la
sequenza e la dichiara in `auto_report.json`: su otto clip in ingresso che
superano i cancelli il referto elenca otto `a_roll` e la timeline li monta in
sequenza. Una clip che non li supera resta fuori con il suo motivo e il suo
numero: nel caso studio un video richiedeva un upscale di 4.14 contro un limite
di 4.0 e il referto ha scritto esattamente quello.

**A2. Politica di riempimento (`--fill crop|fit|blur`).** `fit` resta il valore
predefinito ed e byte-identico al comportamento precedente; `crop` e `blur`
producono grafi distinti. Il fattore di upscale e dichiarato per clip in
`auto_report.json`, sotto `fill[]`. Il riempimento e un parametro di render, non
un campo dell'EDL: il contratto resta 2.2.0 e non esiste alcuna chiave `fill`
nel documento EDL. Il codice che possiede i tre grafi e il percorso di render.

**A3. Loudness per clip sul segmento usato.** Il gain si misura sui soli tratti
conservati, tramite `audio.loudness_spans`; sulle clip del caso studio i valori
originali coprivano 8 LU (da -16.8 a -24.9 LUFS) e ora risultano allineati. Un
gain richiesto oltre l'intervallo [-60, +24] viene limitato al bordo e la carenza
e dichiarata come `shortfall_lu`. Una misura non valida (`input_i` a 0.0 o fuori
intervallo) non produce alcun gain e viene dichiarata come `measurement_invalid`.
Il gain e anche consapevole dei picchi: una clip quieta ma con il picco gia alto
non riceve tutto il guadagno che la loudness chiederebbe, perche il suo true peak
non deve superare il tetto di dialogo dichiarato; la loudness che si perde in quel
caso e scritta in LU come `gain_limited_by_peak`. Se il picco misurato manca, il
gain non viene limitato e la lacuna e dichiarata come `peak_headroom_unknown`.
Il calcolo vive in `audio.py` e il ponte lo consuma in `bridge.py`.

Il master ha poi una garanzia di consegna: dopo `loudnorm` entra un limitatore di
true peak a quattro volte la frequenza di campionamento, con il tetto preso dalla
stessa funzione che usa il collaudo (`qc.true_peak_ceiling`), meno il margine
dichiarato per la tolleranza del limitatore e per l'overshoot misurato della
codifica AAC. Il limitatore e compensato in ritardo. Sul caso studio il mix
arrivava a `+5,04 dBFS` prima di `loudnorm`; dopo il gain consapevole dei picchi
scende a `-0,55 dBFS`, quindi il limiter lavora meno di mezzo decibel invece di
doverne recuperare cinque: un limitatore che toglie cinque decibel si sente, e
pompare non e montare.

**A5. Taglio dei silenzi dal VAD.** I tagli nascono dagli span VAD e `dead_air`.
Un intervallo rimosso fuori dall'unione dei silenzi misurati e un errore duro, non
una tolleranza. Il padding verso l'interno si applica solo sul lato che ha
contenuto adiacente: sul lato di bordo il taglio arriva al margine misurato, quindi
il silenzio in testa e in coda viene rimosso per intero e non resta uno stub.
I frammenti sotto la durata minima si ricuciono e vengono dichiarati. La logica e
in `bridge.py`.

**A4. Ancore di parola e sottotitoli sulla tela.** Le parole diventano ancore e i
sottotitoli vengono rimappati sulla timeline tagliata; un sottotitolo che
attraversa un taglio viene spezzato, e le parole cadute dentro un silenzio
rimosso vengono contate sul sottotitolo. L'ASS e bruciato sulla tela, non dentro
una fascia di piattaforma: `margin_v = round(out_h * 0.08)` con clamp a
[24, out_h/4]. Il ponte e in `bridge.py`, le primitive ASS in `subs.py`.
Le stesse voci producono anche il file SRT consegnato accanto al master:
l'ASS e l'SRT escono dalla stessa funzione, quindi non possono dire cose diverse.
Prima non era cosi e il master usciva con i sottotitoli impressi e con un SRT che
dichiarava "no speech".

**A6. Reframe dalle tracce volto.** Il reframe usa
`autoframe.build_crop_plan`, lo stesso pianificatore gia esistente. I keyframe
esistono solo dove esiste un campione di traccia volto, e la scala resta costante
per clip: il pianificatore fa pan, non zoom. Il renderer aggiunge poi un keyframe
di tenuta all'inizio della clip, copiando il primo campione: non inventa una
misura, ma il grafo puo contenere un istante in piu rispetto al piano. Vive in
`bridge.py` sopra `autoframe.py`.

**A7. Denoise e stabilizzazione.** Il denoise usa `afftdn` con il noise floor
misurato; la stabilizzazione usa `deshake` in una sola passata. Ognuno si applica
solo quando il proprio finding supera la soglia, e ogni salto viene dichiarato
con il valore misurato. Il percorso EditPlan e diverso: usa un noise floor fisso
di -25.0 dB e stabilizza con vidstab in due passate. Il ponte e in `bridge.py`.

**A8. Punteggio dei B-roll.** Lo score e la quota della finestra di destinazione
coperta dalle ancore di parola trascritte, moltiplicata per la loro confidenza
media pesata sul tempo coperto. E un proxy di copertura temporale, non una
similarita semantica fra il contenuto dello shot e le parole pronunciate: nessun
modello di embedding viene usato. La formula e in `bridge.py`.

**A9. Deduplica percettiva.** La deduplica usa un dHash su tre frame, al 15, 50 e
85 per cento della durata, per 192 bit, con distanza di Hamming normalizzata. La
soglia e 0.06, con intervallo dichiarato fino a 0.5. Misurato su materiale reale:
lo stesso take ricodificato segna 0.057, un take diverso alla stessa durata e
risoluzione segna 0.495. Vive in `asset_triage.py`.

**A10. Camera dichiarata.** Le finestre di destinazione che portano una
`CameraSpec` vengono ri-temporalizzate, limitate o scartate, e in ogni caso
dichiarate con lo span originale e quello nuovo; non esiste un divieto silenzioso.
Non e un effetto di movimento: e metadato di camera applicato alla finestra.

**A11. B-roll sovrapposti.** Quando due B-roll si sovrappongono vince lo score
piu alto; a parita vince il piu antico. La dichiarazione porta entrambi gli id e
entrambi gli score, quindi la scelta e verificabile. Vive in `bridge.py`.

**A12. Loop della musica.** Una traccia piu corta della timeline viene loopata
con `acrossfade`, con numero di copie `n = ceil((T-c)/(M-c))`, limitato a 256. Se
la traccia e gia abbastanza lunga il grafo resta byte-identico a prima. La logica
e in `bridge.py`, l'esecuzione nel render.

---

## 7. Fasi e demo

| Fase | Contenuto | Demo di chiusura |
|---|---|---|
| A | Ponte e fondamentali (gruppo A) | "Quella Sera" senza fascia, testo su tutte le clip, loudness bilanciata |
| A2 | Cache incrementale (C1, C2) | una correzione rifa solo il segmento |
| B | EDL 3 e registro (gruppo B) | un reel con keyframe, testo e una transizione vera |
| C | Primi WOW visibili (B3, B5, B6, E3, E4) | 30 secondi che sembrano montati a mano |
| D | Beat e audio (B11, audio) | clip a tempo di una canzone |
| E | Percezione semantica (A4, A8, E2) | podcast di 30 minuti, 3 clip con titolo |
| F | Critico (E1) | prima/dopo con correzioni motivate |
| G | Grading, stile, upscaling (B10, E5) | look coerente su clip diverse |
| H | Lunghi e multicamera (B12) | highlight e cambio camera |

Ogni fase si chiude con un artefatto da guardare, non solo con gate verdi.

---

## 8. Non-obiettivi e licenze

Non e un sostituto di DaVinci o Premiere: non punta al numero di funzioni manuali.
Punta all'automazione che guarda, ragiona, sceglie e si corregge.

Modelli esterni da valutare **con la licenza prima dell'integrazione**:

- RNNoise e DeepFilterNet per il denoise: verificare licenza e modello.
- Real-ESRGAN per l'upscaling: verificare licenza e pesi.
- CLIP per il B-roll semantico: verificare licenza e versione.
- Robust Video Matting: GPL-3, da isolare o evitare se il progetto non e GPL.
- Whisper: gia usato, verificare la versione del modello.
- Musica: solo CC0 o CC-BY con attribuzione; nessun download automatico senza
  licenza dichiarata.

---

## 9. Rischi

| Rischio | Contromisura |
|---|---|
| Effetti a pioggia | registro con trigger, tetto e prova; il gate rifiuta il resto |
| Overfitting dello stile | applicare solo assi misurabili, non copiare un video |
| Allucinazioni ASR | guardia esistente, `not_applied`, nessun testo inventato |
| Upscale 4x | dichiarare il fattore, preferire canvas piu piccolo o sorgente nativa |
| Tempo di render | cache, proxy, job asincroni |
| Ciclo critico costoso | budget 3, stop quando non ci sono blocker |
| Contratti che si rompono | versione esatta, migratore esplicito, round-trip testato |

---

## 10. Decisioni registrate

1. **EDL 3 prima di altri effetti.** Senza keyframe, livelli e testo, ogni effetto
   nuovo sarebbe un campo speciale dentro 2.2.
2. **Ponte prima di EDL 3 nell'esecuzione.** Il ponte chiude il caso studio e
   produce le ancore che EDL 3 dovra trasportare.
3. **Cache prima del critico.** Un ciclo che rifa tutto da capo non e un ciclo.
4. **Non inseguire DaVinci.** Il vantaggio e l'automazione intelligente, non il
   numero di pulsanti.
5. **Nessun effetto senza ancora.** La legge dell'evidenza vale anche per il colore,
   il testo e il reframe.

---

## 11. Cosa non e stato implementato

Niente di A1-A12 resta non implementato: la Fase A e nel codice e il caso studio
gira. Restano quattro limiti onesti, ognuno dichiarato e verificabile.

Il primo riguarda il VAD. Su alcune clip del caso studio il VAD (silero) non era
disponibile: quelle clip non hanno un silenzio misurato e non viene tagliato
nulla. Non e una omissione silenziosa, e una dichiarazione di assenza.

Il secondo riguarda lo score dei B-roll: e un proxy di copertura temporale, non
una similarita semantica. Misura quanto le parole trascritte coprono la finestra,
non se lo shot c'entra con cio che viene detto.

Il terzo riguarda il reframe: il pianificatore fa pan, non zoom. La scala resta
costante per clip, quindi non c'e punch-in.

Il quarto riguarda la stabilizzazione: il percorso EDL usa `deshake` in una
passata, mentre il percorso EditPlan usa vidstab in due passate. Sono due strade
diverse con due risultati diversi, e il documento non le confonde.
