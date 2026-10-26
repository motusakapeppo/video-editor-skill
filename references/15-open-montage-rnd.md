# 15 — Open Montage: cosa prendiamo e cosa lasciamo

Fonte: `github.com/calesthio/OpenMontage`, HEAD `e01dbf433f46eeb91d39bf905e194162921019a3`
(2026-10-23). Metodo: lettura di codice, schemi e manifesti; nessun fork, nessuna
dipendenza Node/React/Chromium nel core. Le citazioni `file:line` sono permalink a
quello SHA.

Filtro di adozione: si prende solo ciò che (a) chiude un difetto verificato,
(b) rende un cancello non aggirabile, o (c) sostituisce un'euristica con una misura
riproducibile con ffprobe/ffmpeg già presenti. Tutto il resto è materiale di studio.

---

## 1. QC audio e loudness

**Loro.** L'unico gate audio di produzione è `volumedetect`
(`tools/video/video_compose.py:2454-2497`): media e max in dB, silenzio sotto
−60 dB, clipping sopra −0.5 dB. È **picco campione, non vero picco**. Non esiste
alcun gate LUFS, TP o LRA. `loudnorm` è applicato a un solo passaggio, alla cieca,
senza `measured_*` e senza rilettura (`tools/audio/audio_mixer.py:220-229`).
L'unico parsing di `loudnorm=print_format=json` sta in uno script di test manuale
(`tests/qa/test_04_audio_mix.py:134-149`).

**Il loro difetto grave.** `elif issues: status = "pass"`
(`video_compose.py:2655-2677`): qualsiasi problema che non contenga una delle sei
keyword critiche diventa `pass`. Il clipping rilevato è un `issue`, non una
keyword, quindi il video viene presentato come pronto. Un errore di misurazione
(idem) diventa `pass`. Lo `status` dello schema è `pass|revise|fail`
(`schemas/artifacts/final_review.schema.json:14-18`): non esiste `skipped`, quindi
uno skip è **codificato** come pass.

**Noi.** Due passaggi con `measured_*`, misura reale di `input_i`/`input_tp`,
tolleranza `tp_limited`, e `status="skipped"` distinto dal verde. Siamo già più
avanti; non c'era niente da copiare sul merito.

**Adottato.** Il QC non ri-deriva più il target dal piano. `render.render`
restituisce `target: {i,tp,lra}` — i valori che sono davvero entrati nel grafo
dello stadio 04 — e `qc.check_render(..., executed_target=...)` confronta la
misura con quello. Se il piano abilita `loudnorm` e il target eseguito manca,
`loudness_target_recorded` **fallisce**: non è uno skip. `cli.cmd_run` scrive
`work/render.json`; `cmd_qc` legge il sidecar accanto al piano.
Inoltre `has_audio` non ha più default `True`: chiave assente = audio atteso,
non silenziosamente vero.

**Rifiutato.** Il classificatore a sottostringhe (`if "silent downgrade" in ...`)
e l'idea di un `volumedetect` come gate di delivery: misurano il picco campione e
non dicono nulla sul loudness integrato.

**Test.** `tests/test_qc_loudness.py` (8 assert: target eseguito vince sul piano,
target assente = fallimento, skip solo senza loudnorm, `has_audio` sconosciuto) e
`tests/test_cli_run.py` scenari 6 e 7 (apple −16, broadcast −23).

---

## 2. Prompt di regia

**Inventario.** La parte semantica vive in `skills/` (1 indice, 6 core, 39
creative, 11 meta, 11 set di registi per pipeline), in 13 manifesti
`pipeline_defs/*.yaml` e in 24 schemi `schemas/artifacts/*.schema.json`. Le altre
tre copie (`.claude/`, `.codex/`, `.cursor/`) sono mirror; solo `skills/` è
autoritativa.

**Il pattern che vale.** L'arco narrativo è un blocco temporale nominato, non
prosa (`skills/creative/storytelling.md:11-49`): `HOOK 0:00-0:08`, `TENSION`,
`CONCEPT`, `PAYOFF`. Ogni scena porta `narrative_role` con enum chiuso di 10
valori (`schemas/artifacts/scene_plan.schema.json:53-70`), `shot_intent` ("perché
questa inquadratura esiste"), `information_role`, `hero_moment`. Ogni taglio porta
un `reason` editoriale, non una metrica (`skills/pipelines/documentary-montage/
edit-director.md:328-330`). Il revisore impone le regole CHAI
(`skills/meta/reviewer.md:11-19`): un finding deve puntare a un campo, una riga o
un frame; un finding critico senza `proposed_fix` viene **declassato** a
`investigation` (`:42`).

**Il loro buco.** L'evidenza è imposta da prosa e severità, non da un validatore
referenziale: non esiste un controllo "il timestamp citato cade dentro il clip",
né una foreign key verso `transcript_id`/`span_id`. Noi possiamo farlo meglio,
perché `Transcript.segments` e `VideoAnalysis.scene_cuts` sono già nel contratto.

**Gap nostro.** `EdlShot.note` spiega il costo dello snap, mai perché lo spezzone
esiste. Non abbiamo `narrative_role`, `shot_intent`, `information_role`,
`hero_moment`. `validate_plan` restituisce stringhe piatte, senza severità né fix.
`decision_log` non ha alternativa scartata né confidenza.

**Adozione proposta — Fase 4, NON ora.** Un solo campo: `narrative_role` (enum
chiuso) obbligatorio su `EdlShot`, validato in `validate_plan`, più il controllo
referenziale che loro non hanno: se un taglio cita uno span, `span.start <= in` e
`span.end >= out`; se cita un frame, il frame cade in `[in, out]`.
Perché non ora: senza un livello semantico che assegni il ruolo, il campo sarebbe
un default decorativo — esattamente il "dichiarato ma non eseguito" che questa
fase combatte. Va introdotto insieme al pianificatore semantico, con il suo test.

**Rifiutato.** Il loop a 7 passi con checkpoint e i manifesti YAML come formato
runtime: sono un'architettura agent-first che duplica ciò che il nostro protocollo
CLI già fa in modo deterministico.

---

## 3. Compositing web (Remotion / HyperFrames)

**Cosa c'è davvero.** `remotion-composer/` è primo-party: `registerRoot` in
`src/index.tsx:1-4`, 12 composizioni in `src/Root.tsx:135-334`, 20 componenti e 4
grafici; entry `npx remotion render src/index.tsx <Id> <out.mp4> --props=<file>`
(`tools/video/video_compose.py:2050-2063`). HyperFrames **non è loro**: è il
pacchetto npm di HeyGen, consumato via `npx hyperframes`; OpenMontage fornisce solo
il bridge (`tools/video/hyperframes_compose.py`) e le skill. Registry upstream:
`raw.githubusercontent.com/heygen-com/hyperframes/main/registry` (`:578`).

**Contratto di render.** Sempre un **MP4 opaco**, mai una sequenza di frame, mai
un overlay con alpha. Remotion scrive direttamente `output_path`
(`video_compose.py:2121-2125`); il caption burn rende `TalkingHead` con
`--frames=0-{N-1} --codec=h264 --crf=18`
(`tools/video/remotion_caption_burn.py:332-340`). L'alpha è documentato in
`EndTag.tsx:16-21` ma non viene mai richiesto. Lo **spazio colore non è
specificato**: nessun `--pixel-format`, nessun `bt709` in tutto il repo. Il sync
audio è derivato dal frame (`<Audio startFrom={frames}>`, `Explainer.tsx:885-899`),
non muxato dopo.

**Il loro selettore non è un confine dichiarativo/browser.** È un confine
Remotion/HyperFrames/FFmpeg, e `_needs_remotion` risponde `True` appena Remotion è
installato (`video_compose.py:1410-1413`). Il criterio utile è più stretto.

**I quattro trigger difendibili per un motore browser** — servono tutte e tre le
condizioni: capacità nominata senza equivalente in un filtro, animata per frame,
su un layer che si muove.
1. GLSL/WebGL/WebGPU (`html-in-canvas-patterns.md:3`, `typegpu.md:12`);
2. `backdrop-filter` (`ProviderChip.tsx:92`);
3. layout reflow che guida la geometria, CSS grid/flex (`determinism-rules.md:43`);
4. blend non separabile su contenuto animato (`CollageBurst.tsx:495-509`).

**Overkill dimostrato.** Trim/concat/encode, burn sottotitoli, overlay PNG
temporizzato, Ken Burns, grade, fade/slide/scale/opacity: tutti in FFmpeg o
Pillow. Il loro stesso criterio lo dice: "does Remotion's primitive API solve this
in ≤ 20 lines?" (`animation-runtime-selector.md:106-116`).

**Costi che il repo mostra.** Chromium non è vendorizzato: arriva con
`@remotion/cli` (`package.json:12`) e il tool avverte "headless browser is slow to
start" (`video_compose.py:2109-2110`). Flag iniettati: `--enable-unsafe-webgpu`,
`--enable-features=CanvasDrawElement` (`typegpu.md:12`), `--gl=swiftshader
--concurrency=1` (`device-surface-showcase.md:44`). WebGPU + html-in-canvas
richiede un Chrome esterno via `PRODUCER_HEADLESS_SHELL_PATH`. Render da 60 minuti
(`hyperframes.md:374`), timeout 1800 s (`hyperframes_compose.py:840`), un Chromium
per render (`remotion.md:331`), `--workers 1` oltre 5 video
(`hyperframes.md:405-409`). Determinismo imposto a mano: niente `Date.now`,
`Math.random` non seedato, `repeat:-1`, rAF (`determinism-rules.md:22-35`); Space
Grotesk cade su Arial (`hyperframes.md:411-417`).

**Cosa adottiamo.** Solo la regola dei quattro trigger, e i vincoli che loro non
rispettano se il nostro Chromium verrà attivato: fps e risoluzione espliciti,
spazio colore dichiarato, sorgente di tempo deterministica, font dal repo, e un
contratto chiaro su alpha (o niente alpha, dichiarato). **Cosa non adottiamo:**
`_needs_remotion → True`, l'MP4 opaco usato come overlay, lo spazio colore
implicito, il download implicito di Chromium.

---

## 4. Contratti dichiarativi

**Il modello.** Dieci schemi di artefatto. La timeline sta in `edit_decisions`
(`required: version, cuts, render_runtime`; ogni cut: `id, source, in_seconds,
out_seconds`, poi `speed`, `layer`, `transform`, `transition_in/out`, `reason`).
`scene_plan` è solo intento (`narrative_role`, `shot_intent`, `information_role`,
`hero_moment`, `shot_language`), senza sorgente né frame. `action_timeline` è
l'unico con `fps`. `render_report` porta `outputs[]` con
`path/format/resolution/duration/fps/platform_target`.

**La mappa degli stadi** è hardcoded in `lib/checkpoint.py:30-40`:
research→research_brief, proposal→proposal_packet+decision_log, idea→brief,
script→script, scene_plan→scene_plan, assets→asset_manifest,
edit→edit_decisions, compose→render_report, publish→publish_log. Lo stadio `edit`
ha `tools_available: []`: è puro authoring LLM di JSON. `compose` è l'unico che
tocca FFmpeg/Chromium. Questa separazione è buona e va tenuta.

**Il buco.** La validazione dello schema avviene solo quando si scrive o legge un
checkpoint (`lib/checkpoint.py:124-157` → `schemas/artifacts/__init__.py:46-49`);
`tools/video/video_compose.py` non importa mai `jsonschema` (grep: zero hit). Il
runtime legge campi che lo schema vieta: `total_duration_seconds` (`:2364`),
`delivery_promise` (`:1438`), `narrative_role` dentro `cuts` (`:1465`) — mentre
`edit_decisions` ha `additionalProperties: false` (`:245`). Conseguenza: un
artefatto **schema-valido** disattiva in silenzio il gate della delivery promise,
e il punteggio di rischio vede `narrative_role = None`. La legge è dichiarata,
non eseguita.

**Due bypass reali.** (1) *Atelier*: `bespoke.entry` è un `.tsx` scritto
dall'agente ed eseguito da Chromium (`:1023-1044`); i controlli girano **dopo**
(`:1080`). È più potente di un comando shell. (2) `operation="compose"`
(`:438-447`) salta `_pre_compose_validation`, chiamata solo da `_render`
(`:1633`). Nessuna shell string raggiunge FFmpeg (`subprocess.run(list)`), ma
questi due percorsi bypassano la semantica del piano.

**Ambiguità di `in_seconds`/`out_seconds`.** Lo schema li chiama intervallo
sorgente; `Explainer` li tratta come posizione timeline (`from = in*fps`) e usa
`source_in_seconds` per il trim; `_cuts_to_cinematic_scenes` li tratta come
sorgente. Stesso artefatto, due significati, scelti da `renderer_family`.

**Confronto.** Loro hanno ciò che a noi manca: `speed`, `layer`
(primary/overlay/background), transizioni per taglio, `overlays[]`, letto musicale
con `ducking`, `subtitles{position, max_words_per_line}`, `renderer_family`. Noi
siamo avanti su `frames`, `t_in/t_out` espliciti, `cut_on`
(bar/beat/onset/inaction/unsnap/veto), `contrast` e
`treatments[].reason.finding_id`. Loro hanno un `reason` per taglio, ma **non
obbligatorio** e senza alcun puntatore a una misura.

**L'aggiunta minima, e perché è meglio di `narrative_role`.** Un oggetto
`evidence {finding_id, t0, t1, frame}` per taglio, validato in
`gates.pre_render`: `finding_id` deve esistere nel report, e l'intervallo del
taglio deve essere **contenuto** nell'evidenza citata (`t0 <= in`, `out <= t1`).
Il contenimento è la parte che rende la citazione falsificabile. `narrative_role`
invece non può essere sbagliato — qualunque taglio può essere etichettato
`deliver_payload` — e vive a granularità di scena. Va tenuto come etichetta
derivata opzionale, non come gate.

**Perché non ora.** È un cambio di contratto (`EditPlan.cuts` è
`List[Dict[str, float]]`) e richiede che il pianificatore semantico produca
l'evidenza. Introdurlo prima significherebbe un default, cioè di nuovo il
dichiarato-non-eseguito. Va in Fase 4, con il test di contenimento.

---

## Registro delle adozioni

| Data | Cosa | Dove | Prova |
|---|---|---|---|
| 2026-10-26 | QC legge il target **eseguito** dal render, non lo ri-deriva | `qc.check_render`, `cli.cmd_run`, `cli.cmd_qc` | `test_qc_loudness` 8/0; `test_cli_run` 58/0 |
| 2026-10-26 | `has_audio` senza default `True` | `qc.check_render` | `test_qc_loudness` |
| 2026-10-26 | Quattro trigger per il motore browser (shader, backdrop-filter, layout reflow, blend su animato) | `references/15` §3 | documentazione, nessun codice |
| — | `evidence {finding_id,t0,t1,frame}` + contenimento in `gates.pre_render` | Fase 4, non implementato | da scrivere col pianificatore semantico |
