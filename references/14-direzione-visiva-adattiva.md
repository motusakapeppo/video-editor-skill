# 14 — Direzione visiva adattiva

Questo documento registra la tornata del 26 ottobre 2026: cosa è stato costruito,
quali decisioni sono state prese e da chi, quali numeri le sostengono, e cosa
resta aperto. Non è un riassunto del codice: è il motivo per cui il codice è
scritto così.

## 1. Il problema, nelle parole dell'utente

«Non ha un'adattabilità su palette, colori, animazioni, scelta di tagli da fare,
estetica della schermata, decorazioni eventuali, effetti grafici, effetti video
ecc. Io voglio davvero che sia una roba super super super complessa e
competente.» E ancora: «basta che venga tutto scritto nella skill, non deve
rimanere tutto qua»; «tutto adattato in base a materiale, richiesta, contesto».

Il criterio con cui si giudica il risultato non è "quanti effetti ci sono", ma
questo: due progetti diversi, guardati senza audio per dieci secondi, devono
essere riconoscibili come due lavori diversi e non come due tagli dello stesso.

## 2. La regola che tiene insieme tutto

Ogni scelta ha un **driver misurato** e una **provenienza**. Nessun numero nudo.
Le provenienze ammesse sono tre:

- `corpus_hash:<sha256>` — derivata dalle 9 clip del corpus, con l'hash del
  corpus che la rende riproducibile;
- `measured:<cosa>` — misurata sul materiale di quel progetto;
- `declared:<perché>` — dichiarata, con la ragione scritta accanto.

Se la confidenza di una scelta cade fra **0.2 e 0.8**, la scelta è **inazione**:
non si applica il trattamento, si registra `applied:false` e `executed.name:"none"`.
Nel dubbio, il sistema non indovina.

## 3. I sei assi, i driver e le soglie

Soglie derivate dal corpus `704caf9f394d92ae5603288e340fec81804bc01d34d66db5f5109a24f6b6b293`
(9 clip: 5 sintetiche + 4 reali).

| Asse | Driver | Soglie | n | Cosa decide | Provenienza |
|---|---|---|---|---|---|
| palette | massa del primo hue (`hue_mass_top3[0].mass`) | p33 0.1817 / p67 0.5012 | 9 | famiglia `mono` / `dual` / `neutral`; 4 colori (bg, accent, text, deco) costruiti dagli hue misurati, con rapporto di contrasto WCAG calcolato e corretto fino a 4.5:1 (testo) e 3.0:1 (deco) | corpus |
| grade | `luma_p50` + cast normalizzato | p33 0.2914 / p67 0.5028; cast 0.08 | 9 | nodo `curve` che alza le ombre o abbassa le alte luci; neutralizza il cast sui canali | corpus + `declared:p95_cast` |
| animation | `transient_ratio` = p90(Δluma agli onset di parola ±2 frame) / mediana(Δluma) | p33 3.4758 / p67 4.9944 | **3 di 9** | `fade` / `wipe` / `punch`, massimo 2 per video | corpus, **direzione asserita** |
| cut_density | `onsets_per_s` | p33 2.9887 / p67 4.9227 | 9 | durata bersaglio 3.0 / 2.0 / 1.2 s, poi snap su onset **dentro una finestra di silenzio VAD** | corpus |
| composition | offset del soggetto, mediana su 5 campioni dell'intero clip | 0.05 = 1/20 della larghezza | 5 per clip | crop `offset` oppure `centre` | `declared:1/20_width` |
| typography | `ink_coverage_pct` sul render | 0.45 / 0.94 (misurate sul delivery approvato) | delivery | corpo +8 px / −8 px / resta | `measured:delivery` |

### La direzione dell'animazione è asserita, non misurata

Il sistema sa **misurare** il transient ratio. Non sa **dimostrare** che un ratio
più alto richieda un'animazione più dura: quella è un'ipotesi di regia, scritta
come tale. Solo i punti di taglio (p33/p67) sono empirici. La frase è ripetuta
nella documentazione e nel verbale perché nessuno la legga come una legge.

## 4. Il brief `vx.visual/1.0`

Sei assi esatti: `palette`, `grade`, `animation`, `cut_density`, `composition`,
`typography`. Ogni scelta porta `driver`, `value`, `provenance`, `inaction`,
`confidence`, `applied`; l'animazione aggiunge `name`, `count` e
`direction_basis:"asserted"`.

Ogni brief porta anche:

- `measurements` — i valori misurati, con `transient_p33`/`transient_p67`;
- `reasons[]` — sei voci, una per asse, con driver, valore, soglia, provenienza e
  decisione;
- `executed[]` — sei voci, una per asse, con `name`, `engine`, `filter`, `rc`,
  `frames`: **la prova che il filtro è passato davvero**;
- `skeleton:"deferred"` — dichiara che la scelta e l'ordine degli spezzoni non
  sono stati toccati in questa tornata;
- `distinct_surface` — l'esito del confronto fra due progetti;
- `docs_match` — l'hash del documento e del ledger.

**Regola resa esecutiva:** `applied:true` richiede un filtro reale. Se una
decisione è presa ma nessun filtro la esegue, il brief la registra come inazione.
Nessun effetto dichiarato e non eseguito.

## 5. I quattro cancelli

### `distinct_surface(a, b)`

Confronta due brief e chiede che almeno **tre famiglie** su quattro divergano:

- **colore** — famiglia di palette diversa, oppure nodo di grade diverso, oppure
  L1 delle masse hue ≥ 0.15;
- **ritmo** — durata bersaglio diversa di almeno 0.4 s, oppure animazione diversa,
  oppure conteggio diverso;
- **geometria** — entrambe le composizioni applicate e offset diversi di almeno
  0.05. La tipografia non può sostituire la geometria;
- **tipografia** — font diverso, oppure corpo diverso di almeno 8 px.

Se la geometria non è disponibile, servono **tutte e tre** le altre famiglie.

**Cosa NON certifica:** solo la divergenza di superficie fra due brief. Non
certifica la differenza di montaggio (che è rinviata) e non pretende di
certificare la distinzione su materiale arbitrario futuro.

### `hook_distinct(a, b)`

Guarda solo i primi 3 secondi: il tipo di hook deve essere diverso **e**
l'animazione del primo cue deve essere diversa. Il tipo è `text` se il parlato
parte entro 0.4 s su un clip con volto, `visual` se il moto dei primi 3 s
raggiunge il p67 del corpus, altrimenti `title`. Il ramo «primo taglio diverso di
0.4 s» è stato eliminato perché morto: su una sorgente singola si può tagliare
solo dentro un silenzio misurato, quindi non si può scegliere di tagliare a 1.2 s.

**Cosa NON certifica:** che l'hook trattenga. Certifica solo che due progetti
aprono con un tipo e un'animazione diversi.

### `coverage_gate(brief, render)`

Dodici frame equispaziati. L'inchiostro del testo deve stare fra **0.30% e 2.00%**
del frame. Le decorazioni sono fuori da questa tornata, quindi `overlay == ink`:
il cancello non finge una seconda misura.

La maschera è «distanza dal colore d'inchiostro dichiarato ≤ 0.06», validata sul
delivery approvato (0.458% misurato contro 0.45% documentato). Non è la maschera
letterale del repo (`max>24` su fondo nero), che su un frame intero misura 70–94%
e non potrebbe mai riprodurre l'ancora del delivery. La differenza è dichiarata.

**Cosa NON certifica:** che il testo sia leggibile, corretto o ben composto.
Misura una copertura, non una qualità.

### `docs_match(brief, testo)`

L'hash del ledger deve comparire nel testo, l'hash del documento deve coincidere,
l'insieme degli effetti dichiarati deve essere uguale all'insieme degli effetti
eseguiti, le tre frasi obbligatorie devono essere presenti alla lettera, e il
testo non deve nominare effetti che non sono stati eseguiti: né un nome del
vocabolario corrente né un nome ritirato (`registry.RETIRED_EFFECTS`). Un nome
nella prosa è una promessa; se il video non la mantiene, il cancello fallisce.

**Cosa NON certifica:** che la prosa sia completa o utile. Certifica che non
contraddice il ledger e che non promette effetti inesistenti.

## 6. Le misure di stanotte

- corpus: 9 clip (5 sintetiche + 4 reali), hash
  `704caf9f394d92ae5603288e340fec81804bc01d34d66db5f5109a24f6b6b293`;
  36 coppie pairwise, 2 passano `distinct_surface`, 2 passano entrambi i
  cancelli (`anthem` vs `street`, `interview` vs `street`);
- coppia designata: `real_interview_169` vs `real_street_169`,
  `designated_by:"verdict"`;
- baseline: 104 assert verdi (15 decisioni + 53 corpus + 36 colore); nuovi:
  gradeplan 35, cutplan 15, animplan 49, palette 32, screen 23, ledger 8,
  distinct 13, docs 6, autonomia 7, visual corpus 16, graph identity 12;
- palette: `bright_45` neutral `#2e2e2e/#f22424`, `dark_916` mono `#231e2e/#6924f2`,
  distanza di hue 100°, contrasti 5.03:1 e 15.50:1 per il testo;
- transient: i tre clip con parole riproducono i valori memorizzati con
  differenza 0.0 (7.6358, 3.6337, 3.1694);
- animazioni: `fade`, `wipe`, `punch` eseguite a 125→125 frame, 1080×1350;
- inquadratura: `interview` mediana x = −0.1537 su 4 volti su 5; `street`
  x = −0.0774 su 2 volti su 5; differenza 0.0763, sopra la soglia 0.05;
- crop: `interview` `crop=810:1080:250:0`; `street` `crop=540:720:542:0`;
- geometria: `true`; la matrice ha 36 coppie e 2 passano entrambi i cancelli;
- coverage: `interview` 0.5044%, `street` 1.5745% (il crop ha cambiato
  l'inquadratura; entrambi restano nella banda 0.30–2.00);
- renderer delta: dopo ogni ondata 0.0 contro un baseline di 0.038337;
- commit della tornata: W0 `e3e3dc3`, W1a `593e306`, W1b `4bb724b`, W2 `c8c17d5`,
  W2.5 `92f6a97`, W2.6 `7ec60f3`, W2.7 `9740f55`;
- delivery approvato: sha256
  `189605431798F66A3A96DC4FFA4AEC9042DD0AE30B2AC47C79EEEF400D3364EB`,
  1080×1440, 25 fps, 21.52 s, intatto.

## 7. Fragilità dichiarate

1. **Transient con n=3.** Solo 3 clip su 9 hanno parole. Le soglie p33/p67 vengono
   da tre punti: sono fragili. Sui clip senza parlato il driver è nullo e l'asse
   animazione va in inazione (`name:"none"`, `count:0`). Non è nascosto.
2. **Il clip `real_interview_169` sta 0.006 sopra il deadband** dell'animazione
   (0.1579 contro 0.15186): un valore appena diverso lo avrebbe mandato in
   inazione. Il deadband è dichiarato, non misurato.
3. **La maschera dell'inchiostro** è una ridefinizione dichiarata e validata
   sull'ancora del delivery, non la maschera letterale del repo.
4. **Le finestre di render del test** sono state scelte per ragioni tecniche
   misurate: la finestra `[20,25)` dell'intervista evita un lower-third già
   bruciato nella sorgente (2.25–2.65% di inchiostro, testo che il sistema non ha
   dichiarato); la finestra `[15,20)` di `street` normalizza l'audio in modo
   lineare, mentre le prime finestre spingono `loudnorm` in modo dinamico e
   creano uno scarto A/V che Gate 3 rifiuta. Questo significa che il cancello è
   provato su finestre scelte, non su qualunque finestra.
5. **Clip acromatici.** Per `bright_45` la massa hue è zero; la palette è
   `neutral` e l'accent usa `h0 = 0`. È il caso degenere, non una scelta di gusto.
6. **Cast in unità pixel.** `analyze.measure_color` restituisce il cast in unità
   0–255; `gradeplan` ragiona in 0–1. Il ponte è la normalizzazione `/255`,
   registrata come `cast_normalised:true`. Senza, la soglia 0.08 sarebbe
   confrontata con 55.9.

## 8. Riduzione di scope e priorità di domani

Riduzione di scope rispetto all'ODG: le **decorazioni** sono fuori da questa
tornata per motivo di **budget**, non per motivo di gate; il gate non le richiede
e la loro assenza non è un fallimento.

La **scelta e l'ordine degli spezzoni** (il montaggio) restano rinviati: la
macchina del ritmo esiste (`edl`, `grammar`, `bpm`, `cutplan`), ma il renderer
multi-sorgente non è stato cablato. Il primo lavoro della prossima tornata è
questo, non un'altra decorazione: è l'osso, non il vestito.

## 9. Come si esegue

```powershell
$PY = "<VENV_DIR>\Scripts\python.exe"
$env:PYTHONPATH = "<SKILL_DIR>\scripts"
& $PY -m vx.registry --selftest
& $PY -m vx.brief --selftest
& $PY -m vx.prove --selftest
& $PY -m vx.gradeplan --selftest
& $PY -m vx.cutplan --selftest
& $PY -m vx.animplan --selftest
& $PY -m vx.palette --selftest
& $PY -m vx.screen --selftest
& $PY tests\run_visual_corpus.py
```

`vx visual --source <clip> --measurements <json> --out <brief.json>` costruisce il
brief; `vx render --visual-brief <brief.json>` lo esegue.

### La corsa notturna

`ingest --autonomous` e `run --autonomous` non restituiscono mai exit 3. Quando il
brief non dichiara formato, hook e sottotitoli, il comando registra nel report
`triage.status="autonomous"` con i tre default e la provenienza
`declared:autonomous`, poi prosegue: il formato viene dal materiale, l'hook resta
inazione, i sottotitoli seguono il ritmo del parlato (`_sub_style`). `plan` e
`triage` restano comandi interattivi: chiedono solo quando c'è qualcuno davanti.

Senza questa modalità il percorso legacy si fermava con `EXIT_NEEDS_INPUT`, che in
una corsa notturna significa una notte ferma. La modalità è una scelta esplicita,
non un default: chi vuole essere interpellato non la usa.

## 10. Le tre frasi che devono restare

1. La direzione ratio↑→animazione più dura è asserita, non misurata; solo i punti di taglio sono empirici.
2. Il gate `distinct_surface` certifica solo la divergenza di superficie fra due brief; non certifica la differenza di montaggio e non pretende di certificare la distinzione su materiale arbitrario futuro.
3. Riduzione di scope rispetto all'ODG: le decorazioni sono fuori da questa tornata per motivo di budget, non per motivo di gate; il gate non le richiede e la loro assenza non è un fallimento.
