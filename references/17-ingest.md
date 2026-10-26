# 17 — Stage 00: normalizzazione dell'ingresso

Ogni misura a valle (luma, saturazione, box dei volti, densità dei tagli) assume
un timeline CFR e uno spazio BT.709. Se la sorgente è VFR o HDR, le soglie
misurano un'altra cosa. Questo stage produce il timeline giusto una volta sola,
oppure restituisce il percorso originale se la clip è già conforme.

## Rilevazione

- **VFR**: frazione di delta PTS che deviano dal mediano di oltre il 2% per più
  del 5% dei frame, su una finestra di 10 s. Fallback su `r_frame_rate` vs
  `avg_frame_rate` quando i frame non sono leggibili.
- **HDR**: `color_transfer` in `smpte2084`/`arib-std-b67`, oppure `color_space`
  in `bt2020*`, oppure 10-bit con transfer non BT.709.
- **SAR**: `sample_aspect_ratio` diverso da 1:1.
- **Rotazione**: `side_data_list.rotation` o tag `rotate`.
- **Risoluzione**: area oltre `1920*1080*1.5` **e** scala utile `< 1`. La scala
  utile è `max(1080/crop_w0, 1920/crop_h0)`, dove `crop_w0`/`crop_h0` sono il crop
  9:16 della sorgente già corretta per rotazione e SAR. Sotto 1 la sorgente può
  ancora alimentare un crop 1080×1920 senza upscale: si ridimensiona; a 1 o
  sopra non si tocca, perché ridurre peggiorerebbe l'output.

## Staging

Solo se una delle condizioni è vera: `stage_00_ingest_<stem>.mp4`, scritto con
`-filter_complex_script`, libx264 CRF 17, audio AAC 192k.

- SAR: `scale=trunc(iw*sar/2)*2:ih,setsar=1` (de-squeeze, poi pixel quadrati).
- HDR: `zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=limited`.
- VFR: `fps=fps=<mediana dei delta>`.
- Rotazione: ffmpeg autoruota in input; il file di stage non porta più la
  matrice di visualizzazione.
- Risoluzione: `scale=<w>:<h>:flags=lanczos`, con dimensioni pari e aspect
  conservato. Ordine: SAR, downscale, tonemap, fps, `format=yuv420p`. Con SAR e
  downscale insieme si emette un solo `setsar=1` prima e uno dopo lo scale: il
  filtro `scale` ricalcola il SAR per conservare il DAR, quindi senza il secondo
  `setsar=1` il file di stage non sarebbe 1:1. Il target è calcolato sulle
  dimensioni **display** (dopo rotazione e SAR): un 4K landscape diventa
  3412×1920, un 4K verticale 1080×1920, un 8K landscape 3412×1920. Il tetto
  1920×1080 cieco non è usato perché trasformerebbe il crop 9:16 di un 4K in un
  upscale 1,78×, che il QC segnala come `upscale_ratio`.

## Integrazione

`_ingest` fa il probe del **sorgente** per l'identità, normalizza, poi esegue
proxy, analisi video, audio, volti e trascrizione sul **file di stage**. Il
render usa il media dello stage. `report.source` resta il sorgente originale
(sha256), con `staged` e `normalized`. Il QC verifica lo sha del **sorgente**,
non dello stage: il gate `source_sha_unchanged` preferisce il percorso del
fingerprint.

`validate_plan` usa `report.source.analysis_duration` per i limiti temporali:
le evidenze sono sul timeline normalizzato, non su quello originale.

## Limiti dichiarati

- HDR senza metadata di transfer/primari: `zscale` non ha un percorso e il
  modulo fallisce con `IngestError`; non copia l'HDR fingendo una conversione.
- `zscale` e `tonemap` devono esistere nella build (`ff.require` lo impone).
- La finestra VFR è di 10 s: un cambio di rate dopo la finestra non è rilevato.
- Il pavimento 1080×1920 è calcolato sul crop 9:16. Un output 1:1 da una
  sorgente verticale larga 1080 non può che upscalare: la sorgente non contiene
  i pixel, e nessuna politica di downscale può inventarli.
- Il file di stage è anche la sorgente del render, quindi il downscale riduce
  anche i pixel disponibili al compositor. Per questo il target non scende mai
  sotto il crop dichiarato.

## Test

`tests/test_ingest.py`: unit VFR/HDR/SAR/rotazione, passthrough su CFR SDR,
VFR reale, HDR reale con metadata, SAR reale, e `vx run` su VFR con QC pass e
sha del sorgente intatto.

`tests/test_ingest_downscale.py`: dieci casi di geometria (4K, 8K, verticale,
4K ruotato, DCI, quadrato, 2.39:1, Full HD, SAR), ordine dei filtri, e staging
reale con probe del file prodotto, SAR 1:1, rotazione azzerata e sha del
sorgente invariato.
