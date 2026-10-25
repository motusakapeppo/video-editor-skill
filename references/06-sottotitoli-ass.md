# 06. Sottotitoli e ASS

Dal parlato alla cue, dalla cue all'ASS, dall'ASS al burn-in. Ogni passaggio ha
un limite misurabile.

## Trascrizione

| Voce | Valore |
|------|--------|
| Motore | faster-whisper 1.2.1, backend CTranslate2 4.8.3 |
| Modello predefinito | `small` |
| Device | `cpu` |
| Compute type | `int8` |
| Beam size | 5 |
| Word timestamps | attivi |
| VAD | attivo per default |
| `condition_on_previous_text` | `False` |

Il modello è fissato a CPU int8 nel codice: la GPU esiste ma non viene usata,
perché riproducibilità e parità proxy/full valgono più della velocità.

`faster_whisper.Word` porta `start`, `end`, `word`, `probability`. Il campo
`word` ha quasi sempre uno spazio iniziale e viene ripulito prima di entrare in
`Transcript.words`. La timeline è forzata monotona: se il modello oscilla,
`start` non torna mai indietro. La trascrizione non solleva eccezioni: un
pacchetto mancante o un decoder che fallisce tornano come `Transcript` con
`skipped_reason` valorizzato.

## Limiti delle cue

| Costante | Valore | Significato |
|----------|--------|-------------|
| `sub_max_chars_916` | 18 | caratteri massimi per cue verticale |
| `sub_max_chars_169` | 42 | caratteri massimi per cue orizzontale |
| `sub_max_lines` | 2 | righe massime |
| `sub_max_cps` | 25 | caratteri al secondo massimi |
| `GAP_BREAK` | 0.45 s | una pausa più lunga chiude la cue |
| `MIN_CUE` | 0.4 s | nessuna cue più corta, si allunga la fine |

La cue si chiude quando scatta una delle quattro condizioni: gap oltre 0.45 s,
testo oltre il budget di caratteri, righe oltre il limite, cps oltre 25. Il
controllo sui cps si applica solo da due parole in su: spezzare una parola sola
non abbassa i suoi cps. Con 18 caratteri una cue verticale è sempre una riga
sola; il limite di 2 righe resta come rete di sicurezza se qualcuno alza
`max_chars`. `_finalize` evita le sovrapposizioni: se una cue avrebbe bisogno di
più tempo del gap disponibile, viene fusa con la successiva quando il testo
combinato ci sta, altrimenti viene troncata all'inizio della successiva.

## Struttura ASS

| Campo | Valore |
|-------|--------|
| `ScriptType` | `v4.00+` |
| `PlayResX` / `PlayResY` | `out_w` / `out_h` del render |
| `WrapStyle` | 2 (nessun wrap automatico) |
| `ScaledBorderAndShadow` | `yes` |
| Stile / Font | `Vx` / `Arial Black` |
| `PrimaryColour` | `&H00FFFFFF` (bianco) |
| `SecondaryColour` | `&H0000D7FF` (arancione, usato dal karaoke) |
| `OutlineColour` / `BackColour` | `&H00000000` / `&H80000000` |
| `Bold` / `Alignment` / `Encoding` | `-1` / 2 (basso, centrato) / 1 |

Il file è UTF-8 senza BOM, con newline `\n`: un BOM rompe il parsing della prima riga in libass.

## Karaoke

Ogni parola diventa `{\kNN}PAROLA`, dove `NN` è la durata in centesimi di
secondo. I valori sono calcolati perché la loro somma coincida **esattamente**
con la durata della cue: se la somma è inferiore il residuo va sull'ultima
sillaba, se è superiore viene tolto dalla coda all'indietro. Senza questa
correzione l'ultima parola resta grigia o si accende troppo presto.
`SecondaryColour` è il colore prima che la parola venga cantata, `PrimaryColour`
quello dopo. Il testo è in maiuscolo, con un pop di 160 ms (100 -> 108 in 80 ms,
ritorno a 100 in altri 80 ms, sotto la soglia di percezione del ritardo):

```
{\fscx100\fscy100\t(0,80,\fscx108\fscy108)\t(80,160,\fscx100\fscy100)}
```

## Escaping del testo

Tre caratteri sono markup per libass e vanno neutralizzati: `\` diventa `/`,
`{` diventa `(`, `}` diventa `)`. Una graffa non escapata apre un blocco di
override e inghiotte il testo successivo; un backslash non escapato apre un tag
e produce output casuale.

## La regola di escaping del path (verificata)

Questa è l'unica forma che funziona su ffmpeg 8.1 / Windows:

| Forma | Esito |
|-------|-------|
| `ass='C\:/work/clip.ass'` | FUNZIONA |
| `ass=C\:/work/clip.ass` | FALLISCE, i due punti vengono letti come separatore di opzione |
| `ass='C:/work/clip.ass'` | FALLISCE, i due punti non sono escapati |
| spazi dentro gli apici singoli | FUNZIONA |
| spazi escapati con backslash | FALLISCE |

La produce `vx.winpath.ass_filter_path`: path in forma POSIX, due punti escapati, tutto fra apici singoli. Output: `'C\:/work/clip-a1b2c3d4/subs/clip.ass'`

```powershell
$PY = "<VENV_DIR>\Scripts\python.exe"
$env:PYTHONPATH = "<SKILL_DIR>\scripts"
& $PY -c "from vx.winpath import ass_filter_path; print(ass_filter_path(r'C:\work\clip-a1b2c3d4\subs\clip.ass'))"
```

## Verifica del font

libass sostituisce un font mancante **in silenzio**: nessun errore, nessun
warning nel log normale, il render esce con un altro font e nessuno se ne
accorge. `verify_fonts` brucia un frame su un canvas nero e legge le righe
`fontselect:` dal log a `loglevel=verbose`, confrontando richiesta e risolta.

| Campo | Significato |
|-------|-------------|
| `fallback_count` | numero di sostituzioni |
| `fonts_used` | famiglie effettivamente usate |
| `requested` | famiglie richieste |
| `ok` | `rc == 0` e `fallback_count == 0` e nessun errore hard |

Il gate è bloccante: `font_fallback_count` in QC ha limite 0. Se Arial Black è
installato (`%WINDIR%\Fonts\ariblk.ttf`), il fallback non scatta.

## SRT e statistiche

`write_srt` produce un SRT standard con timestamp `HH:MM:SS,mmm`, testo senza
tag karaoke. Serve per la consegna: le piattaforme caricano l'SRT, non l'ASS.

| Campo | Significato |
|-------|-------------|
| `word_count` | parole trascritte |
| `wpm` | parole al minuto |
| `fillers` | intercalari riconosciuti |
| `fastest_cue_wps` | caratteri al secondo della cue più veloce |

Gli intercalari riconosciuti includono `ehm`, `eh`, `uh`, `um`, `mmm`, `mm`,
`ah`, `ahm`, `hmm`, `cioe`, `cioè`, `tipo`, `praticamente`, `insomma`,
`diciamo`, `ecco`, `beh`, `boh`, `mah`, `ok`, `okay`. Sono un'informazione, non
un trattamento automatico: toglierli è una scelta editoriale.

## Comandi

```powershell
$PY = "<VENV_DIR>\Scripts\python.exe"
$env:PYTHONPATH = "<SKILL_DIR>\scripts"
& $PY -m vx.subs --input "%USERPROFILE%\clip.mp4" --out "<WORK_DIR>\clip-a1b2c3d4\subs" --style karaoke --lang it
& $PY -m vx.subs --input "%USERPROFILE%\clip.mp4" --out "<WORK_DIR>\clip-a1b2c3d4\subs" --style clean --model medium
```

Output: `<stem>.ass`, `<stem>.srt`, `fontselect.log` e un riepilogo JSON con `cues`, `stats`, `fonts`, `timings_s`. Se `fonts.ok` è `false` non si brucia: si installa il font o si cambia `--font`, poi si ripete la verifica.
