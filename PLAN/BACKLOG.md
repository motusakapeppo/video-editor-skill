# BACKLOG dell'utente

Idee e integrazioni che arrivano dall'utente. Regola: qui dentro entra tutto, subito,
anche se riguarda una fase gia' chiusa. Nulla e' chiuso se porta un miglioramento.

## Stati

- **nuova** - registrata, non ancora lavorata;
- **in corso** - ha un task nel ledger;
- **fatta** - con il commit e la prova;
- **respinta** - con il motivo in una riga (mai in silenzio).

## Classificazione

| Tipo | Cosa succede |
|---|---|
| **additiva** | riga nuova nel ledger (es. `F4-T11`), la fase resta chiusa com'era |
| **correttiva** | nuova decisione che **supera** la precedente (`D0xx sostituisce D0yy`); la vecchia resta scritta |
| **strutturale** | si rifa' la parte toccata, si ri-eseguono le prove, si ri-benedice solo cio' che si muove, dichiarandolo |

## Confronto con il piano maestro v1 (28/10/2026)

Il documento `video-editor Piano Maestro e Prompt di Esecuzione (2).md` (F0-F12, Allegati
A-D) e' la fonte del lavoro in corso. Confronto con `PLAN/MASTER_LEDGER.md`:

| Cosa chiede il piano | Stato reale | Tipo | Sede |
|---|---|---|---|
| F0 fondamenta | 10/11 (T09 bloccato sulle annotazioni) | — | F0 |
| F1 ponte analisi-assemblaggio | chiusa (4/4) | — | F1 |
| F2 EDL 3 (incl. PiP, maschere, blend, time remap) | chiusa **tranne** PiP, maschere, blend: il backend composito e' a un solo ingresso | **strutturale** | F4-T13 |
| F3 Motion Studio | chiusa (54 template, 162 frame) | — | F3 |
| F4 60+ voci **con transizioni** (30 xfade + forme Chromium) | 58 look, **zero transizioni**: solo `fade_in`/`fade_out`, `crossfade` dichiarata e non implementata (D-8) | **additiva** | F4-T12 |
| F4 LUT 3D, pelle, rimozione sfondo, testo dietro il soggetto, upscaling | assenti | additiva | F4-T14 |
| F5 audio e musica (RNNoise, SFX, stem, 5.1) | solo catena voce + ducking + loudness per profilo | additiva | F5 |
| F6 MaterialMap (`vx understand`) | **assente**: e' la ragione per cui il montaggio del reel non ha senso | additiva | F6 |
| F7 IntentSpec e dialogo | assente | additiva | F7 |
| F8 ReferenceLab e Trend Radar (Allegati B-C-D) | assente | additiva | F8 |
| F9 Critico a sei assi | assente | additiva | F9 |
| F10 export OTIO, FCPXML, VTT, capitoli | consegne fatte, formati di scambio assenti | additiva | F10 |
| F11 MCP v2 e coda job | assente | additiva | F11 |
| F12 indurimento ed esame | assente | additiva | F12 |
| F13-F15, F18, F19 | assenti | additiva | fasi |
| F16 prestazioni | parziale: chunking e banco fatti, smart render via cache da completare | additiva | F16 |
| F17 cassaforte | chiusa (50 kill, guardia di sistema); **ritardi da ricalibrare** | correttiva | F17-T10 |
| A2 livelli di capacita' L0-L2 e doctor | assente: la sonda esiste ma non dichiara il livello | additiva | F0-T12 |
| Esame: 12 incarichi + 7 (A5) = 19 | coerente con `MASTER_PLAN.md` (19) | — | — |

## Voci aperte

| # | Idea | Tipo | Sede | Stato |
|---|---|---|---|---|
| 1 | Transizioni vere: 30 da `xfade` piu' forme Chromium | additiva | F4-T12 | nuova |
| 2 | PiP, split screen, maschere, blend | strutturale | F4-T13 | nuova |
| 3 | LUT 3D con protezione pelle, rimozione sfondo, testo dietro il soggetto, upscaling | additiva | F4-T14 | nuova |
| 4 | Ricalibrare i kill di F17 sulla durata reale | correttiva | F17-T10 | nuova |
| 5 | Livelli L0-L2 e doctor che li dichiara | additiva | F0-T12 | nuova |
| 6 | Allegato F: Atlante 5.000+ voci, Genoma 16 famiglie, strato aperto, 16 moduli, 12 fasi, esame cieco 26-33 | additiva | F32-F43 (AF4/F20-F31) | bloccata: il testo dell'artefatto non e' leggibile da qui |
| 7 | Passo di ripristino per il materiale da telefonino: correzione colore e nitidezza dichiarate, invece di scartare la clip | additiva | F4-T15 | nuova |
