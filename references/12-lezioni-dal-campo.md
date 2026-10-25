# 12. Lezioni dal campo

Ogni regola qui nasce da un errore **commesso e misurato** in produzione, non da una teoria.
Le prime sei sono già codice; le ultime sono gate di processo da eseguire a mano finché non
saranno automatizzate.

---

## 1. Video e audio si tagliano nella stessa operazione

**Errore commesso.** Montaggio v2: immagine tagliata in un `ffmpeg`, suono in un altro. Le
durate pianificate sommavano 22,83 s, il file è uscito 19,28 s. Al primo taglio lo scarto era
−0,2 ms (misurato con cross-correlazione), dal secondo in poi le due timeline divergevano.

**Regola.** Un solo filtergraph, un solo input per spezzone, stesso `-ss`, e
`concat=n=N:v=1:a=1`. La sincronizzazione non va *aggiustata*, va resa **impossibile da
sbagliare**.

```python
# corretto: un grafo, due flussi, una concatenazione
[i:v]trim=start_frame=A:end_frame=B,setpts=PTS-STARTPTS[vN];
[i:a]atrim=start=S:end=E,asetpts=PTS-STARTPTS[aN];
[v0][a0][v1][a1]...concat=n=N:v=1:a=1[v][a]
```

`render.build_trim_graph(..., with_audio=True)` fa già questo. **Vietato** costruire il video
da una parte e il WAV dall'altra e unirli dopo.

---

## 2. Verifica la durata **per spezzone**, non sul totale

**Errore commesso.** Ho controllato il totale del montaggio invece dei singoli spezzoni. Un
render da 3,00 s richiesti produceva meno frame, e me ne sono accorto solo alla fine.

**Regola.** Dopo aver reso uno spezzone, prima di concatenare:

```bash
ffprobe -v error -count_frames -select_streams v:0 \
  -show_entries stream=nb_read_frames,r_frame_rate -of csv=p=0 shot.mp4
```

Il numero di frame deve essere `durata × fps` **±1**. Se non lo è, il filtro ha mangiato o
duplicato frame: si ferma lì, non si concatena.

---

## 3. `zoompan` gonfia la durata se il timebase d'ingresso non è quello di uscita

**Errore commesso.** Push-in con `zoompan=...:d=1:fps=25` su spezzoni da 3,5 s: il contenitore
è uscito da ~86 s per clip. Montaggio finale: 620 s invece di 26.

**Regola.** Prima di `zoompan` normalizza il rate (`fps=25` **prima** del filtro, non dopo) e
verifica la durata del singolo spezzone (§2). In alternativa, il movimento si fa con
`crop` animato su `x`/`y` (che accetta espressioni temporali), non su `w`/`h` (valutati una
sola volta). Se `zoompan` non è verificato, **niente push-in**: meglio fermo che rotto.

---

## 4. Un filtro che "dovrebbe" funzionare va provato su **un** clip

**Errore commesso.** Ho debuggato il montaggio intero invece del singolo spezzone: due render
da minuti per scoprire un errore che un test da 3 secondi avrebbe mostrato.

**Regola.** Ogni nuovo filtro o catena si prova su 3 secondi di un clip, e si controllano
durata, frame, decodifica. Solo dopo si scala. Costo: 10 secondi. Risparmio: un pomeriggio.

---

## 5. Non bruciare sottotitoli che non sono stati misurati

**Regola.** La trascrizione si valuta prima di incidere: parole ≥ 12 e probabilità media
≥ 0,55. Sotto soglia **non si brucia** — meglio nessun sottotitolo che parole sbagliate
indelebilmente incise nel video.

```python
probs = [w.get("probability", 0) for w in tr.words]
mean_p = sum(probs) / len(probs) if probs else 0.0
burn = len(tr.words) >= 12 and mean_p >= 0.55
```

È la stessa logica della regola dell'inazione: **misura incerta → non agisco**.

---

## 6. L'audio originale non si toglie per iniziativa

**Errore commesso.** Ho rimosso l'audio del live giustificandolo con `snr_db` = 0,4 dB. Ma lo
SNR confronta parlato e non-parlato: su un pezzo con la base, il "non-parlato" **è la
musica**. La metrica non era applicabile, e la mia stessa regola (detector incerto → non
toccare) diceva di fermarmi.

**Regola.** L'audio fa parte del materiale. Si può **segnalare** che è tecnicamente critico;
**non** si può decidere di eliminarlo. Al massimo si propone, e si aspetta.

---

## 7. Il QC si esegue su **ogni** file prima che entri in pubblicazione

**Errore commesso.** Il v2 difettoso è finito nella cartella `PUBBLICAZIONI/` senza passare
dal QC. Il gate esisteva (`qc.check_render`), semplicemente non l'ho lanciato.

**Regola.** Nessun file entra in `PUBBLICAZIONI/` senza questi tre comandi, nell'ordine:

```powershell
& $PY $VX qc --plan plan.json --render out.mp4 --out qc.json
# deve dire "pass": true
ffmpeg -v error -i out.mp4 -f null -          # nessun errore di decodifica
ffprobe -v error -show_entries stream=codec_type,duration -of csv=p=0 out.mp4
# le due durate (video, audio) devono coincidere entro 40 ms
```

Se una delle tre fallisce, il file va in quarantena (`DIFETTOSO_*`), non in cartella.

---

## 8. Non dichiarare un risultato senza averlo guardato

**Errore commesso.** Ho consegnato un file dicendo "fatto" senza aprire un frame; il difetto
(duplicazione, durata gonfiata, desync) era visibile subito.

**Regola.** Un frame per ogni spezzone, estratto e **guardato**, più un controllo della
sequenza. Il numero non sostituisce l'occhio: `ffprobe` dice che il file è valido, non che il
montaggio funziona.

---

## 9. Gli errori d'ambiente che tornano sempre

| Sintomo | Causa | Rimedio |
|---|---|---|
| `ffmpeg` riceve `1,5` e fallisce | locale italiano: PowerShell formatta i decimali con la virgola | `$t.ToString('0.0', [CultureInfo]::InvariantCulture)` |
| File `.py` con em-dash e accenti corrotti | `Set-Content -Encoding UTF8` in PS 5.1 rilegge in cp1252 | mai `Set-Content` su UTF-8: usare Python con `newline=''` |
| `cublas64_12.dll is not found` con le DLL installate | `site.getsitepackages()[0]` è la radice del venv, non `lib\site-packages` | `sysconfig.get_paths()['purelib']` + `os.add_dll_directory` |
| `silencedetect` non stampa nulla | `-loglevel error` nasconde il livello info | tenere `-loglevel info` su quel passaggio |
| Il path nei sottotitoli non viene letto | i due punti vengono interpretati come separatore | `subtitles='D\:/path/x.ass'` |

---

## 10. Cosa manca ancora (dichiarato, non nascosto)

| Capacità | Stato | Blocco |
|---|---|---|
| Taglio verificato sul beat | parziale | `bpm.py` non converge su live con PA: 88 / 60 / 67 BPM, contrasto < 1,35 |
| Push-in per spezzone | non attivo | `zoompan` non verificato (§3) |
| Piano ritmico nel renderer | non cablato | `grammar.plan_multisource` produce il piano, `render.py` non lo consuma |
| Check di §7 in `qc.py` | parziale | `duration_drift_ms` esiste; manca il confronto *pianificato vs reso* |

Queste quattro righe sono il backlog vero. Finché restano qui, un montaggio esce con tagli a
durata fissa: è onesto dirlo prima, non dopo.
