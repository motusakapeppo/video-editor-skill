# HANDOFF — testa di ripresa per una sessione nuova

**Aggiornato:** 2 novembre 2026, commit `0cf9677`, tag `phase-F25-done`.

Questo file esiste per una ragione sola: una sessione che riparte non deve
ricostruire nulla. Se stai leggendo dopo una compattazione, leggi questo, poi
`python scripts/vx_tasks.py next`, e lavora.

## Stato in cinque righe

- Skill V2 in `~/.config/opencode/skills/video-editor`; MCP `connected` (sei tool);
  `vx doctor` = ready.
- Suite completa: **176 runner / 5692 assert / 0 fail / 1 skip in 1698,8 s**
  (commit `f816fc1`).
- Board: **96 chiusi con prova, 0 aperti, 0 bloccati**. `F0-T09` è chiuso: corpus
  annotato, scala di difetti, soglie derivate e test che le verifica.
- Fasi chiuse e taggate: F1, F2, F3, F4, F16, F26, F27, F28, **F25** (T01…T15).
- Albero pulito, remoto allineato, nessun processo in background.

## Cosa e' entrato con F25 (T01…T15)

| task | cosa | prova |
|---|---|---|
| T01 | 41 movimenti di camera, 10 famiglie | 14 PASS |
| T02 | 41 strisce di riferimento; `zoompan` per il crop animato | 83 PASS |
| T03 | profili pellicola 35mm/16mm/8mm/dv | 17 PASS, 4 frame |
| T04 | quattro lenti con tetto e ripiego dichiarato | 12 PASS, MAD 12,8-31,8 |
| T05 | 51 palette LUT 3D con attribuzione | 116 PASS, 51 frame |
| T06 | ruota delle 8 emozioni, citata in brief ed EDL | 16 PASS, foglio 4x2 |
| T07 | cancello dei keyframe prima del render | 12 PASS |
| T08 | modalita' autonoma come unica eccezione | 12 PASS |
| T09 | lettore di brand con robots e cache | 12 PASS |
| T10 | `brand.json` con provenienza per ogni campo | 12 PASS |
| T11 | tre pagine di prova + brand sul frame | 14 PASS |
| T12 | registro template con slot e linter | 15 PASS |
| T13 | riempimento slot dalla MaterialMap | 9 PASS, MAD 147 |
| T14 | tre proporzioni e cache condivisa | 9 PASS |
| T15 | cancello di curatela | 25 PASS |

## Come si lavora (le regole che hanno retto finora)

1. **Scenario prima del codice**: condizione binaria, superficie reale (CLI, file,
   fotogramma), e se un test esiste gia' lo si legge prima di toccarlo.
2. **Un task, un commit**, con la prova nel messaggio; poi `vx_tasks.py done … --evidence`.
3. **Nessun numero senza misura**: se non e' stato eseguito, non e' fatto. Nel
   dubbio, il codice vince sui documenti.
4. **La suite completa e' il cancello**: si tagga una fase solo con la suite verde.
   Dopo ogni modifica a `PLAN/*.md`, `python scripts/gen_plan_rev8.py`, o
   `test_plan_rev8` diventa rosso (e' voluto).
5. **Un processo pesante alla volta**; i test che misurano tempi non vanno in
   parallelo a un render. **Vale anche per la suite stessa**: lanciare `run_all.py`
   e contemporaneamente estrarre fotogrammi o costruire clip fa cadere i test che
   rendono golden (`test_motion_golden` è caduto due volte così, e passa sempre
   quando la macchina è dedicata). Da qui il default parallelo:
   `run_all.py` con `--jobs 0` usa `max(2, min(6, cpu-2))` (6 qui; 14 file in
   20 s contro 1418 s seriali), `--jobs 1` resta il seriale, e ogni FAIL del giro
   parallelo è rieseguito **da solo**: se passa è `FLAKY` (elencato nel sommario,
   fuori dall'exit code), se fallisce anche da solo resta FAIL.

6. **`test_f17_crash.py` non è nella suite di default.** Misura la velocità della
   macchina e uccide il processo a offset fissi: dopo 175 test la macchina è calda,
   gli offset derivano e i kill si ammassano in una fase (misurato: loudness 45,
   staging 0, lock 0, boot 0) — tre fasi non esercitate, cioè una prova in meno.
   Si esegue a macchina ferma con `run_all.py --only f17_crash --heavy` (50 PASS,
   267 s); il default lo salta e **lo dichiara** con una riga `[heavy] … SKIP`.

## Dove sono i pezzi

| cosa | dove |
|---|---|
| protocollo CLI | `SKILL.md`, `scripts/vx/cli.py` |
| contratto EDL 3 | `scripts/vx/edl3.py` |
| compilatore | `scripts/vx/edl3_render.py` |
| camera | `camera.py` (41 movimenti), `camera_profiles.py` (4 profili) |
| ottica | `lenses.py` (vignette, CA, bokeh, fisheye) |
| colore | `lut.py` (51 palette), `palette.py` (palette grafica) |
| emozioni | `emotions.py` (ruota + foglio 8 riquadri) |
| brand | `brand.py` (lettura), `brandjson.py` (estrazione), `branding.py` (render) |
| composizione | `templates.py` (7 disegni), `slotfill.py`, `multiratio.py`, `curation.py` |
| cancello keyframe | `keygate.py` |
| board | `scripts/vx_tasks.py` + `PLAN/TASKS.json` |
| ripresa lunga | `<WORK_DIR>/RIPRESA SKILL VIDEO EDITOR.md` |

## Il prossimo lavoro, che non è sul board perché è nato dopo

`D-24`: **la metrica del tremolio non distingue una panoramica da un tremore.** Su
materiale reale, un pan di inquadratura misura 1,717 e un tremolio vero 4,226 — un
fattore 2,5. Il candidato ovvio (residuo sulla velocità invece che sul percorso) non
separa: 0,263 contro 0,845, sempre 3,2×, perché il residuo è rumore di stima
proporzionale alla velocità.

Serve un **estimatore diverso** — omografia RANSAC su punti tracciati, o correlazione
di fase su frame sfocati — validato su `tests/fixtures/corpus_shake_real.json` (cinque
estratti reali con il giudizio umano accanto). Il test `tests/test_shake_real_set.py`
fallisce apposta il giorno in cui la metrica migliora: a quel punto la soglia va
rifatta su quel set, non ereditata dal sintetico.

Finché l'estimatore non cambia, la soglia del tremolio resta `null`, e nessun numero
inventato prende il suo posto.

**La stabilizzazione ora esiste e si misura.** `scripts/vx/stabilize.py`: due passaggi
`vidstab`, e il jitter rimisurato sull'output — 4,226 → 1,853 (−56,2%) su un estratto che
tremava; su una clip già ferma peggiora (0,081 → 0,236), per questo l'intento di default è
`propose` e chi forza `apply` riceve `effective: False` col `drop` negativo.

Resta **un solo lavoro**: agganciare `stabilize.apply` al percorso di render, chiedendo
l'intento al momento del piano. Il ponte oggi dichiara e non applica — e lo dice, dopo la
correzione in `c27091e`.

## Convenzioni di scrittura

Italiano, misure dentro le frasi, niente aggettivi al posto dei numeri. I messaggi
di commit dicono cosa e' stato eseguito e con quale comando. I test stampano
`PASS/FAIL` con il valore accanto.
