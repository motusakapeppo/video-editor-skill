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
| `N creative treatments > 3` | budget creativo sfondato |
| `more than one stabilization pass` | doppia stabilizzazione |
| `output.aspect missing` | formato di uscita non dichiarato |

Un piano che non passa la validazione non si renderizza. Non è un avviso, è un
errore bloccante.

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
      "params": { "mode": "track" }
    },
    {
      "name": "loudnorm",
      "enabled": true,
      "reason": { "finding_id": "F-audio-03" },
      "params": { "target": "social" }
    }
  ],
  "render": { "precision": "full", "crf": 18, "preset": "medium" }
}
```

Due trattamenti, uno creativo e uno tecnico. Ogni trattamento cita un finding.
`output.aspect` è dichiarato. Il piano passa.

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
