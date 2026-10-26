# 21 — Regia semantica: B-roll dalle parole chiave

Il B-roll non si mette "a sensazione". Una regola dichiara quale parola merita
quale footage; la trascrizione decide dove, il builder decide come, il witness
dice perché.

## Regola

```python
BrollRule(
    keyword="vino",
    broll_source_id="src_02",
    src_in=0.0,
    src_out=1.0,
    max_uses=1,
    min_gap_sec=2.0,
    role="broll_overlay",
    transition_sec=0.3,
)
```

- `keyword`: una parola; il confronto è case-insensitive, ignora accenti e
  punteggiatura (`Città` = `citta`, `vino,` = `vino`);
- `broll_source_id` deve esistere nella mappa `sources` del piano;
- `src_in`/`src_out` ritagliano il footage; `max_uses` limita le ripetizioni;
- `min_gap_sec` è la distanza minima dall'ultimo insert accettato, **fra tutte
  le regole**: due keyword non possono impilare lo stesso momento visivo;
- `transition_sec` passa al fade in/out del clip.

## Selezione

```python
select_broll(words, rules, *, source_id, finding_id="transcript.keyword_match")
```

- le parole sono ordinate per `start`, quindi l'output è deterministico anche
  se la trascrizione arriva fuori ordine;
- un match produce un `BrollCandidate` con:
  - `at` = punto medio della parola, in **tempo sorgente** sul dialogo
    principale (il builder lo mappa sul timeline);
  - `t0`/`t1` = inizio/fine della parola, cioè lo span esatto del finding;
  - `finding_source_id` = la sorgente del dialogo, mai quella del B-roll;
  - `finding_id` = `transcript.keyword_match`;
- una parola produce al massimo un insert: la prima regola che combacia vince;
- una regola invalida (keyword vuota, `src_out <= src_in`, `max_uses < 1`,
  `min_gap` negativo) solleva `ValueError` prima di produrre qualsiasi cosa.

## Dal match al piano

```python
candidates = select_broll(words, rules, source_id="src_01")
built = build_edl_plan(edl, source_id="src_01", sources=sources, broll=candidates)
```

- il builder rifiuta un finding il cui span attraversa un taglio A-Roll: la
  giustificazione sarebbe ambigua;
- il witness restituito da `BuiltEdl` usa `source_id="src_01"` (la fonte in cui
  `t0/t1` sono stati misurati), non `src_02`;
- chi redige il report deve aggiungere un finding `transcript.keyword_match`
  con lo stesso `t0/t1`; un piano multi-sorgente senza quel finding non passa
  `validate_evidence(..., sources=...)`.

## Limiti dichiarati

- Match su **una parola**: niente stemming, sinonimi o frasi di più parole.
  "vini" non è "vino"; "l'uva" è un token, non "uva".
- Il footage è scelto dalla regola, non da una similarità visiva: la regola è
  una decisione editoriale, non un modello.
- Nessun controllo di ritmo o di alternanza dei piani: `min_gap_sec` evita
  l'accavallamento, non impone una densità.
- Una parola a cavallo di un taglio A-Roll viene rifiutata dal builder; la
  regola non prova a spostarla.

## Test

`tests/test_semantic_broll.py` (20): match, accenti, punteggiatura, nessun
match, `max_uses`, `min_gap`, prima regola, quattro regole invalide, e il giro
completo `select_broll -> build_edl_plan` con B-roll al tempo mappato e witness
sulla fonte del dialogo.
