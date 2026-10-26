# 03. Matrice decisionale

La matrice che trasforma un finding in un trattamento. Ogni riga ha una soglia
reale presa da `vx.contract.THRESHOLDS` o da una costante di modulo. Se la
soglia non scatta, il trattamento non esiste.

## Matrice

| Finding | Gate (soglia reale) | Trattamento | Veto |
|---------|---------------------|-------------|------|
| `jitter.verdict == shaky` | `score >= 0.60` (banda alta) | `stabilize` | `headroom_pct < 8.0` (`stab_min_headroom_pct`), oppure un `stabilize` già attivo |
| `jitter.verdict == slight` | `0.35 <= score < 0.60` | `stabilize` (opzionale) | come sopra |
| `exposure.verdict == dark` | `mean_luma < 45.0` (`luma_under`) | `exposure_fix` | `clip_low_pct >= 2.0`: ombre chiuse, nessun lift le recupera |
| `exposure.verdict == bright` | `mean_luma > 210.0` oppure `clip_high_pct >= 2.0` (`clip_high_pct`) | `exposure_fix` | `clip_high_pct >= 8.0` (`clip_high_block_pct`) |
| `exposure.verdict == clipped` | `clip_high_pct >= 8.0` | nessuno | veto totale, dati persi |
| `color.verdict != neutral` | `abs(cast_rg) > 12.0` oppure `abs(cast_by) > 12.0` | `color_fix` | un solo passaggio `eq`, mai due |
| `freezes[].duration` | `>= 1.5` (`freeze_min`) | taglio in `plan.cuts` | freeze totali oltre il 35% della durata (`dead_air_max_cut`) |
| `audio.verdict == noisy` | `snr_db < 12.0` (`noise_snr_low`) | `denoise` (`afftdn`) | `music_detected == true` (`music_ratio >= 0.45`) |
| `audio.verdict == clipped` | `clipping_pct >= 0.01` (`clip_pct`) | nessuno | veto `denoise` e `loudnorm` riparatore |
| dead air totale | `ratio >= 0.08` (`dead_air_ratio`) | `cut_silence` | `ratio > 0.35` (`dead_air_max_cut`): si taglia al massimo il 35% |
| loudness fuori target | `abs(input_i - target) > 0.3` (`lufs_delta` QC) | `loudnorm` | nessuno, si applica sempre al target scelto |
| `faces.framing == single` o `multi` | `span_x_ratio <= 0.95` (`FIT_SPAN_RATIO`) | `autoframe` | `framing == fit`, oppure nessun volto |
| `faces.framing == fit` | `span_x_ratio > 0.95` | `fit` (blur-pad) | `autoframe`: il crop taglierebbe i volti |
| nessun volto rilevato | `detected_ratio == 0` | crop centrale statico | `autoframe`, `fit` |
| `pacing.verdict == slow` | `mean_shot_len >= 4.0` (`hook_window`) | `zoom` oppure `hook_overlay` | budget creativo esaurito |
| `retention.risk_drop` non vuoto | almeno un elemento | `hook_overlay` | `hook_candidates` vuoto |
| `wpm > 190` | `speech_wpm_fast = 190` | split delle cue in `subs` | `cps > 25` (`sub_max_cps`) dopo lo split |
| `music_ratio >= 0.45` | `MUSIC_THRESHOLD = 0.45` | nessuno | veto `denoise` |

## Trattamenti creativi e tecnici

| Classe | Nomi | Limite |
|--------|------|--------|
| Creativi | `autoframe`, `zoom`, `hook_overlay`, `fit` | massimo 3 in totale (`max_creative_treatments`) |
| Tecnici | `cut_silence`, `denoise`, `loudnorm`, `stabilize`, `color_fix`, `exposure_fix`, `compress` | nessun limite numerico, ma `stabilize` massimo 1 |

Il conteggio dei creativi è sul numero di trattamenti attivi, non sul numero di
finding. Tre finding di ritmo non autorizzano tre zoom.

## Regole di validazione del piano

`vx.contract.validate_plan` rifiuta un piano se:

| Errore | Significato |
|--------|-------------|
| `treatment X has no reason.finding_id` | trattamento senza giustificazione |
| `treatment X cites unknown finding F` | il finding citato non esiste nel report |
| `treatment X has no evidence` | trattamento attivo senza lista di testimoni |
| `treatment X evidence[0] does not match reason.finding_id` | il primo testimone non è il finding del `reason` |
| `treatment X cites finding F with no span` | il finding non porta uno span sorgente |
| `treatment X witness F does not copy the finding span` | lo span del testimone non combacia col finding |
| `treatment X witness F outside 0..D` | lo span esce dalla durata del sorgente |
| `treatment X cites stale finding F` | il finding è stato ricalcolato e non è più fresco |
| `treatment X must carry exactly one witness` | solo `cut_silence` può citare più finding |
| `cut_silence removes a-b outside the witness union` | un intervallo rimosso cade fuori dai testimoni |
| `N creative treatments > 3` | budget creativo sfondato |
| `more than one stabilization pass` | doppia stabilizzazione |
| `output.aspect missing` | formato di uscita non dichiarato |

Un piano che non passa la validazione non si renderizza: `plan`/`run` escono 2,
`pre_render` esce 5, `render` esce 6. Non è un avviso, è un errore bloccante.

## Ordine di applicazione

L'ordine non è arbitrario: il render lo impone.

1. Tagli (`trim`/`atrim` + `concat`)
2. Stabilizzazione (`vidstabtransform`)
3. Crop e scala (`crop`, `scale`)
4. Zoom (`scale` + `crop`, mai `zoompan`)
5. Colore ed esposizione (`eq`)
6. Sottotitoli (`subtitles`)
7. Normalizzazione (`format`, `setsar`)
8. Loudness (`loudnorm`, secondo passaggio)

Stabilizzare dopo il crop non funziona: il `.trf` è calcolato sul video già
tagliato, e il crop virtuale ha bisogno delle coordinate del sorgente.

## Esempio di piano minimo valido

```json
{
  "output": { "aspect": "9:16", "loudness": "social" },
  "cuts": [{ "in": 0.0, "out": 12.4 }],
  "treatments": [
    {
      "name": "autoframe",
      "enabled": true,
      "reason": { "finding_id": "F-faces-01" },
      "evidence": [{ "finding_id": "F-faces-01", "t0": 0.0, "t1": 12.4 }],
      "params": { "mode": "track" }
    },
    {
      "name": "loudnorm",
      "enabled": true,
      "reason": { "finding_id": "F-audio-03" },
      "evidence": [{ "finding_id": "F-audio-03", "t0": 0.0, "t1": 12.4 }],
      "params": { "target": "social" }
    }
  ],
  "render": { "precision": "full", "crf": 18, "preset": "medium" }
}
```

Due trattamenti, uno creativo e uno tecnico. Ogni trattamento cita un finding e ne
copia lo span in `evidence`. `output.aspect` è dichiarato. Il piano passa.

`cut_silence` è l'unica eccezione: può citare più finding, e ogni intervallo
rimosso dai `cuts` deve cadere dentro l'unione dei loro span. Un taglio che tocca
un silenzio non testimoniato invalida il piano.

## Cosa non è un finding

| Non è un finding | Perché |
|------------------|--------|
| "il video è orizzontale" | è un fatto, non un difetto |
| "manca la musica" | è una scelta creativa dell'utente |
| "il soggetto non sorride" | non è misurabile dal toolkit |
| "il colore è triste" | nessuna soglia oggettiva |
| "andrebbe più corto" | decisione del brief, non della misura |

Su queste cose il toolkit tace. Se l'utente le chiede, si esegue il brief, non
si inventa un finding per giustificare il lavoro.

## Matrice visiva (`vx.visual/1.0`)

La matrice visiva è separata da quella dei finding: ogni riga ha un driver
misurato, una soglia con provenienza, un trattamento e un veto. Se la confidenza
cade fra 0.2 e 0.8, la scelta è inazione (`applied:false`, `executed.name:"none"`).

| Asse | Driver | Soglia (provenienza) | Trattamento | Veto |
|---|---|---|---|---|
| palette | `hue_mass_top3[0].mass` | p33 0.1817 / p67 0.5012 (`corpus_hash:704caf9f…`) | famiglia `mono`/`dual`/`neutral` e quattro colori; nessun filtro in questa ondata | confidenza 0.2–0.8; nessun filtro eseguibile |
| grade | `luma_p50` + cast | p33 0.2914 / p67 0.5028; cast 0.08 (`declared:p95_cast`) | nodo `curve`: alza ombre, abbassa alte luci, neutralizza il cast | confidenza 0.2–0.8; `chain_for_shot` vuota |
| animation | `transient_ratio` | p33 3.4758 / p67 4.9944, n=3 (`corpus_hash:704caf9f…`) | `fade` / `wipe` / `punch`, massimo 2 | confidenza 0.2–0.8; nessun filtro eseguibile |
| cut_density | `onsets_per_s` | p33 2.9887 / p67 4.9227 (`corpus_hash:704caf9f…`) | durata 3.0 / 2.0 / 1.2 s, poi `cutplan.snap` su onset dentro silenzio VAD | confidenza 0.2–0.8; nessun onset qualificato: confine invariato |
| composition | `subject_offset` | 0.05 = 1/20 larghezza (`declared:1/20_width`) | crop `offset` o `centre` | confidenza 0.2–0.8; crop uguale al centro |
| typography | `ink_coverage_pct` | 0.45 / 0.94 (`measured:delivery`) | corpo +8 / −8 / invariato | confidenza 0.2–0.8; nessun burn in questa ondata |

**Regola:** `applied:true` richiede un filtro eseguito. `check_ledger` rifiuta un
brief in cui una scelta applicata non punta a `executed.name != "none"`, filtro
non vuoto, `rc 0`, `frames > 0`. Nessun effetto dichiarato e non eseguito.

### I quattro cancelli visivi

| Cancello | Cosa prova | Cosa NON prova |
|---|---|---|
| `distinct_surface` | divergenza di superficie su almeno tre famiglie | la differenza di montaggio (rinviata) e la distinzione su materiale arbitrario futuro |
| `hook_distinct` | tipo di hook e animazione diversi nei primi 3 s | che l'hook trattenga |
| `coverage_gate` | inchiostro fra 0.30% e 2.00% su 12 frame | leggibilità o correttezza del testo; `overlay == ink` |
| `docs_match` | hash del ledger, hash del documento, effetti eseguiti, tre frasi | completezza o utilità della prosa |
