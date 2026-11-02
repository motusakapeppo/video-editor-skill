# HANDOFF — testa di ripresa per una sessione nuova

**Aggiornato:** 2 novembre 2026, commit `a9e5ddc`.

Questo file esiste per una ragione sola: una sessione che riparte non deve
ricostruire nulla. Se stai leggendo dopo una compattazione, leggi questo, poi
`python scripts/vx_tasks.py next`, e lavora.

## Stato in cinque righe

- Skill V2 installata in `~/.config/opencode/skills/video-editor`, MCP `connected`
  (sei tool), `vx doctor` = ready.
- Ultima suite interamente verde: **159 runner / 5274 assert / 0 fail / 2 skip in
  1338,6 s** (commit `9c17919`, tag `phase-F16-done`).
- Board: `PLAN/TASKS.json`, **82 chiusi con prova, 12 aperti, 1 bloccato**
  (`F0-T09`: servono venti giudizi umani, non si inventano).
- Chiusi in questa campagna: D-8, D-9, D-11, D-12, D-13, D-21, D-22, F17-T10, il port
  V2, F16 (6/6), F25-T01…T04.
- Albero pulito, remoto allineato, nessun processo in background.

## Come si lavora (le regole che hanno retto finora)

1. **Scenario prima del codice**: condizione binaria, superficie reale (CLI, file,
   fotogramma), e se un test esiste già lo si legge prima di toccarlo.
2. **Un task, un commit**, con la prova nel messaggio; poi `vx_tasks.py done … --evidence`.
3. **Nessun numero senza misura**: se non è stato eseguito, non è fatto. Nel dubbio,
   il codice vince sui documenti.
4. **La suite completa è il cancello**: si tagga una fase solo con la suite verde.
   Dopo ogni commit che tocca `PLAN/*.md`, `python scripts/gen_plan_rev8.py` (o il
   test `test_plan_rev8` diventa rosso: è voluto).
5. **Un processo pesante alla volta**; i test che misurano tempi non vanno in
   parallelo a render.

## Dove sono i pezzi

| cosa | dove |
|---|---|
| protocollo CLI | `SKILL.md`, `scripts/vx/cli.py` |
| contratto EDL 3 | `scripts/vx/edl3.py` (effetti, transizioni, ricette) |
| compilatore | `scripts/vx/edl3_render.py` |
| chunk + parallelo | `scripts/vx/edl3_chunks.py` (`jobs=`), `edl3_slice.py` |
| movimenti di camera | `scripts/vx/camera.py` (41 preset, 10 famiglie) |
| profili pellicola | `scripts/vx/camera_profiles.py` (35mm, 16mm, 8mm, dv) |
| lenti | `scripts/vx/lenses.py` (vignette, CA, bokeh, fisheye) |
| prestazioni | `scripts/gen_performance.py` → `PLAN/PERFORMANCE.md` |
| board | `scripts/vx_tasks.py` + `PLAN/TASKS.json` |
| discrepanze | `PLAN/DISCREPANZE.md` (D-22 chiusa; le storiche pure) |
| ripresa lunga | `../..  /RIPRESA SKILL VIDEO EDITOR.md` in `<WORK_DIR>` |

## Il prossimo task (già scritto, non da inventare)

`F25-T05` — **libreria di almeno 50 palette LUT 3D**: categoria, provenienza e
licenza per ognuna; una LUT assente dal disco produce il ripiego `eq` +
`colorbalance` con `fallback=true`; `protect_skin` misura la distanza su una toppa
di pelle e limita la forza; ogni palette ha un frame di riferimento.

Trappole già pagate, da non ripagare:

- i file `.cube` vanno **generati** e dichiarati come nostri (licenza MIT), non
  scaricati: nessun materiale di terzi senza provenienza;
- un golden per palette significa 50 render: usare il percorso `--write` come
  `test_edl3_kinds.py`, con sorgente deterministica 320x568 a 12 fps;
- `protect_skin` deve **misurare** (distanza su una toppa), non stimare: la toppa
  si costruisce da un frame reale e si dichiara il punto.

## Convenzioni di scrittura

Italiano, misure dentro le frasi, niente aggettivi al posto dei numeri. I messaggi
di commit dicono cosa è stato eseguito e con quale comando. I test stampano
`PASS/FAIL` con il valore accanto.
