# Referto di prestazioni

Generato da `python scripts/gen_performance.py`. Ogni riga porta la misura e
il comando che l'ha prodotta: nessun numero e' copiato da un'altra sessione.

| misura | valore | comando |
|---|---|---|
| compilazione di una timeline da 2 ore (1800 clip) | **0.315 s**, 1801 righe di grafo | `python scripts/gen_performance.py` (misura interna) |
| frame identici in serie e in parallelo, con il tempo di entrambe | vedi `[INFO] tempo misurato` nel test | `python tests/test_parallel_chunks.py` |
| chunk riusati quando nulla cambia, e solo quelli toccati quando cambia | vedi `rendered`/`reused` nel test | `python tests/test_smart_render.py` |
| fedelta' di un encoder candidato (MAD sul frame decodificato) | soglia dichiarata `2.0` | `python tests/test_encoder_fidelity.py` |
| peso dell'intermedio lossless rispetto alla consegna | vedi `[INFO] peso dichiarato` | `python tests/test_lossless.py` |
| suite completa (runner, assert, durata) | vedi la riga finale di `run_all` | `python tests/run_all.py` |

## Codici di validazione incontrati

`nessuno`

