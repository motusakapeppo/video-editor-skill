# Matrice delle licenze

Regola: nessun modello, libreria o binario nuovo entra senza una riga qui.
Ogni riga dichiara versione, licenza, data di verifica e tipo d'uso. "Verificata"
significa letta dai metadati del pacchetto installato o dal binario stesso, non
ricordata a memoria. Data della verifica: 27/10/2026.

Tipi d'uso ammessi: **libreria** (importata nel processo), **processo esterno**
(lanciata come eseguibile, mai distribuita), **opzionale** (non necessaria al
percorso normale).

## Verificate su questa macchina

| Componente | Versione | Licenza | Verificata da | Uso |
|---|---|---|---|---|
| ffmpeg (build gyan.dev essentials) | 8.1 | **GPL-3.0-or-later** (`--enable-gpl --enable-version3`, x264/x265) | `ffmpeg -version`, riga `configuration` | processo esterno |
| numpy | 2.2.6 | BSD-3-Clause | metadati del pacchetto (classifier BSD) | libreria |
| scipy | 1.15.3 | BSD-3-Clause | metadati del pacchetto (classifier BSD) | libreria |
| librosa | 0.11.0 | ISC | metadati del pacchetto | libreria |
| opencv-contrib-python | 5.0.0.93 | Apache-2.0 | metadati del pacchetto | libreria |
| onnxruntime | 1.23.2 | MIT | metadati del pacchetto | libreria |
| faster-whisper | 1.2.1 | MIT | metadati del pacchetto | libreria |
| Pillow | 11.3.0 | **da confermare** (HPND attesa; i metadati non dichiarano il campo) | metadati vuoti | libreria |
| playwright | 1.64.1 | **da confermare** (Apache-2.0 attesa; i metadati non dichiarano il campo) | metadati vuoti; `licenses/` non leggibile con i permessi correnti | libreria |
| Google Chrome / Microsoft Edge | installati dall'utente | proprietaria | non distribuiti con la skill: usati via `channel` | processo esterno |
| pesi Whisper (via faster-whisper) | variante locale | **da confermare** (MIT per OpenAI Whisper; la variante va nominata) | non ancora ispezionata | opzionale |

## Da verificare prima dell'integrazione

Queste voci sono nominate dal piano o dall'aggiornamento, **non ancora integrate**.
Nessuna può entrare senza una riga verificata in questa tabella.

| Componente | Licenza attesa | Stato | Nota |
|---|---|---|---|
| SFace (modello dentro opencv-contrib) | Apache-2.0 | da verificare sul file del modello | usato solo se il riconoscimento tra clip (E6) viene approvato |
| Piper (TTS) | MIT, ma **le voci hanno licenze proprie** | da verificare voce per voce | la licenza del motore non copre i modelli vocali |
| RVM (rimozione sfondo video) | GPL-3.0 | da verificare | ammesso **solo come processo esterno opzionale**, mai linkato |
| Ultralytics YOLO | **AGPL-3.0** | da evitare | incompatibile con l'uso previsto |
| gl-transitions | MIT | da verificare | F15 |
| OpenTimelineIO (OTIO) | Apache-2.0 | da verificare | F24 / interoperabilità |
| Blender | GPL-2.0-or-later | da verificare | solo processo esterno, opzionale |

## Regole che questa matrice impone

1. **AGPL non si usa**, in nessuna forma: è il caso di Ultralytics.
2. **GPL solo come processo esterno**, mai come libreria linkata: vale per ffmpeg
   (build GPL) e per RVM. Un binario GPL invocato da riga di comando non contagia
   il codice Python; una libreria linkata sì.
3. **Pesi non commerciali non entrano**, anche se il codice che li carica è libero.
4. **La licenza del motore non copre i modelli**: Piper è l'esempio, e vale per
   ogni TTS, ogni modello vocale e ogni LUT scaricata.
5. Ogni riga si riverifica quando cambia la versione: una licenza cambiata nel
   tempo è un rischio, non un dettaglio.
6. `ffmpeg` distribuito con la skill sarebbe GPL: la skill lo **invoca**, non lo
   distribuisce. Se un giorno va impacchettato, serve una build LGPL e la matrice
   va aggiornata prima.
