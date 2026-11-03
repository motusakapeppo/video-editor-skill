# Trattamenti: cosa fa il codice

- **Sottotitoli verticali.** `BorderStyle=1`, `Outline=7`, `Shadow=4` a 1920 di
  altezza, scalati su `out_h`; orizzontali `4/2` a 1080 (`subs.py`). Il ponte
  brucia i sottotitoli sulla tela con
  `margin_v = round(out_h*0.08)` clampato a [24, out_h/4] (`bridge.py`), non
  nella safe zone di piattaforma.
- **Font.** La famiglia la sceglie il contesto (`fontproposal`), la conferma
  l'operatore; arriva al `Fontname` dell'ASS e libass riceve `fontsdir` sulla
  cartella del lavoro. Una sostituzione e' un fallimento duro (`QCFailure`),
  non un warning. Dettaglio: `28-font-proposta-e-cancello.md`.
- **Fade audio.** `afade` di 12 ms a ogni giunto interno nel grafo finale, non
  solo nel passaggio di rilevamento dello stadio 01 (`render.py`).
- **Compress e denoise.** `compress` diventa `acompressor`; il fallback del
  denoise e' `highpass`; la stabilizzazione legge `shakiness`, `smoothing` e
  `zoom` dal piano. Il ponte usa `afftdn` col noise floor misurato e `deshake`
  in una passata; l'EditPlan usa vidstab in due passate.
- **Loudness.** `output.loudness` usa le chiavi minuscole `i/tp/lra`; `apple` e
  `broadcast` sono rispettati. Il ponte misura il gain per clip sui tratti
  tenuti (`audio.loudness_spans`), limita a [-60,+24] con `shortfall_lu`. Lo
  stadio 04 esegue `loudnorm` solo se attivo, altrimenti `04_remux`; il QC
  dichiara skip.
- **Hook.** Il testo viene da `brief.hook_text` o dalle prime parole della
  trascrizione; senza testo il trattamento e' disattivato e dichiarato.
- **Guardia trascrizione.** `subs.transcript_quality` richiede almeno 12 parole
  e probabilita' media almeno 0.55; sotto soglia `cli.cmd_run` disattiva i
  sottotitoli.

## Effetti componibili (algebra, non catalogo)

`vx.effects` espone otto primitive tipizzate — `transform`, `grade`, `blur`,
`mask`, `blend`, `time`, `text`, `particles` — con parametri che accettano uno
scalare o uno span `[a, b]`, e curve `linear`/`in`/`out`/`in_out`/`bezier` o a
chiavi multiple. Una transizione è una formula, non una voce: `whip` è
`transform` + `blur` sulla stessa curva, `crossfade` è `blend` 0→1.

* `validate(spec)` → issue in italiano col percorso del campo.
* `cost(spec)` → peso 0–10; un costo dichiarato è un minimo, mai un ribasso.
* `expand(nome)` → i 26 nomi storici diventano la stessa forma di una spec
  scritta a mano, quindi il passato continua a funzionare.

Al render arrivano `transform` (zoompan), `grade` (eq/sendcmd), `blur` (gblur) e
`blend`. Le altre dichiarano `PRIMITIVE_NOT_RENDERED` e non producono file: un
no chiaro, non un'omissione. Le curve non rappresentabili in ffmpeg vengono
rifiutate invece di essere approssimate.

## Coerenza e stima

Prima del render `coherence` misura cinque cose (varietà, ripetizione, frame
inutilizzabili, apertura debole, tagli senza ragione) e scrive
`report["coherence"]`; `--free` le spegne e lo dichiara. `estimate` dichiara
quanto ci vorrà con un intervallo e la scomposizione per stadio, calibrata sui
tempi reali; sopra il budget (`--budget`) si sceglie fra versione completa e
rapida, che cede effetti e risoluzione ma resta dentro gli stessi controlli.
