# HANDOFF — testa di ripresa per una sessione nuova

**Aggiornato:** 2 novembre 2026, commit `0cf9677`, tag `phase-F25-done`.

Questo file esiste per una ragione sola: una sessione che riparte non deve
ricostruire nulla. Se stai leggendo dopo una compattazione, leggi questo, poi
`python scripts/vx_tasks.py next`, e lavora.

## Stato in cinque righe

- Skill V2 in `~/.config/opencode/skills/video-editor`; MCP `connected` (sei tool);
  `vx doctor` = ready.
- Suite completa: **174 runner / 5652 assert / 0 fail / 2 skip in 1309,8 s**
  (dopo `0cf9677`).
- Board: **95 chiusi con prova, 1 bloccato** (`F0-T09`: venti giudizi umani, non
  si inventano). Nessun task aperto.
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
   parallelo a un render.

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

## L'unico task bloccato

`F0-T09` — corpus annotato per calibrare le soglie. Servono **almeno venti
giudizi umani** su clip veri; ce ne sono sette e non separano. Non si inventano:
il task resta `blocked` finche' qualcuno non li fornisce. Quando arriveranno:
`python scripts/vx_tasks.py block F0-T09` (sblocco) e poi il criterio nel campo
`acceptance` di `PLAN/TASKS.json`.

## Convenzioni di scrittura

Italiano, misure dentro le frasi, niente aggettivi al posto dei numeri. I messaggi
di commit dicono cosa e' stato eseguito e con quale comando. I test stampano
`PASS/FAIL` con il valore accanto.
