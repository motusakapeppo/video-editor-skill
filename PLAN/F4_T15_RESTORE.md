# F4-T15 — il passo di ripristino per il materiale da telefonino

**Nato da una frase dell'utente:** «si vede che sono girati col cellulare».
**Stato:** specifica eseguibile, non ancora implementata. Scritta il 2/11/2026.

Un clip di telefono non si scarta: si ripristina. Il passo è piccolo, si vede subito,
e non deve inventare dettaglio che non c'è — quello che il codice può fare è
restituire contrasto, micro-contrasto e pulizia, dichiarando quanto.

## Cosa misura (il finding)

| finding | quando | misura |
|---|---|---|
| `video.phone.soft` | nitidezza **affidabile** (luma ≥ 60, D-21) e sotto soglia | `sharpness` misurata, soglia provvisoria nel contratto |
| `video.phone.lowlight` | luma media sotto la soglia di ripristino e clip non già nera | `mean_luma`, `clip_low_pct` |

Due finding separati perché i due difetti vogliono due gesti diversi: uno alza il
micro-contrasto, l'altro alza le ombre senza bruciare le alte luci.

**Regola non negoziabile:** la soglia è **provvisoria** finché T09 non è chiusa
(`test_threshold_provenance` la marca tale). Nessun rifiuto automatico di clip:
il ripristino **migliora**, non scarta (D036).

## Cosa fa (il trattamento)

`restore`, tecnico, parametri dichiarati e con dominio:

| parametro | dominio | default | effetto |
|---|---|---|---|
| `sharpen` | 0.0–1.5 | 0.6 | `unsharp=5:5:<v>` — micro-contrasto, non un filtro “sharp” da telefono |
| `denoise` | 0.0–1.0 | 0.35 | `hqdn3d=<v>:<v>:<2v>:<2v>` — leggero: togliere grana, non la pelle |
| `lift` | 0.0–0.25 | 0.0 | `eq=brightness=<v>` — solo per `lowlight`, alzato dalle ombre |

Tetto: un solo passaggio di ripristino per clip, e il trattamento **non è creativo**
(non entra nel tetto dei 3). Se `restore` e `stabilize` convivono, l'ordine nel grafo
è `restore` → `stabilize` → colore: stabilizzare prima di nitidizzare moltiplicherebbe
il mosso.

## Evidenza

`ALLOWED_EVIDENCE_MAP["restore"] = {"video.phone.soft", "video.phone.lowlight"}`.
Nessun finding, nessun trattamento: la legge dei testimoni vale anche qui, e il
testimone è lo span del finding, non l'intero clip.

## Come si prova (accettazione binaria)

1. **Il trattamento arriva ai pixel.** Stessa clip, con e senza `restore`:
   - la nitidezza misurata **sale** (stesso metro, stessa soglia di affidabilità);
   - il frame di riferimento dell'effetto è su disco e il test fallisce se cambia
     (`tests/fixtures/edl3_kinds/restore.png`).
2. **Non inventa.** Su un clip già nitido il finding non esiste e `restore` resta
   disabilitato con la ragione scritta nel piano (nessun trattamento senza finding).
3. **Non brucia.** Su un clip sottoesposto, dopo `restore` la percentuale di
   pixel clippati in alta luce non cresce oltre la soglia gia' usata da
   `exposure` (+1%).

## Dove vive il codice

- **Percorso EDL 3:** una voce dichiarativa nella famiglia `color` con
  `filter_template` (`unsharp` + `hqdn3d` + `eq`), quindi golden e indice generato.
- **Percorso piano (`vx auto`):** `plan.detect_findings` emette i due finding;
  `build_plan` abilita `restore` con i parametri derivati dalla misura (più soft →
  più `sharpen`, più scuro → più `lift`), e `render` lo mette in catena prima della
  stabilizzazione.

## Cosa NON fa (dichiarato)

- Non ricostruisce dettaglio assente (niente upscaling generativo, niente AI).
- Non corregge la rotazione: quella è D-9, chiusa in `probe`.
- Non tocca il rumore audio: quello è `denoise`.
