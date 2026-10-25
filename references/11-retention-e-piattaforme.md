# 11. Piattaforme, retention, sottotitoli, audio, diritti

Dati verificati il **2026-10-25**. La colonna "verificato" dice quando ho letto la fonte, non quando la piattaforma l'ha cambiata.

| Sigla | Significato |
|---|---|
| **[OFF]** | documentazione ufficiale o dichiarazione di un dirigente della piattaforma |
| **[MEAS]** | misurazione indipendente con metodo e campione pubblicati |
| **[PEER]** | accademico peer-reviewed · **[PRE]** preprint |
| **[VENDOR]** | dato di agenzia/tool, metodo spesso sottile |
| **[UNVER]** | folklore o aneddoto, nessuna fonte primaria |

---

## 1. Retention: cosa è dimostrato

**1.1 I primi secondi pesano, ma "3 secondi" non è una soglia ufficiale.**
Classificatori addestrati solo sui **primi 2 secondi** di 120k micro-video (Vine) hanno prestazioni quasi pari a quelli sull'intero clip; il **44%** di 115 intervistati si forma un'opinione nei primi secondi. *Joglekar, Sastry, Redi, "Like at First Sight", SocInfo 2017.* **[PEER]** — caveat: Vine era un formato da 6 s con loop.
**1.2 La continuazione precoce è un buon proxy del watch time totale.**
`ECR = P(watch > 5s)` correla **0,926** con la percentuale di visione normalizzata, su 90k video Snapchat. *Li et al., arXiv:2410.00289, set 2024.* **[PRE/industria]**
**1.3 Curiosity gap e urgenza battono l'apertura didattica.**
46.605 hook TikTok: gli hook emotivi sono solo ~**3,2%** dei post ma generano più commenti e condivisioni; lunghezza ottimale dell'hook da ~9 parole (news) a ~90 (entertainment). *Mishra, OSF, ago 2025.* **[PRE, 0 citazioni]** — direzione plausibile, numeri non confermati.
**1.4 YouTube indica tre famiglie di hook:** una domanda, un fatto sorprendente, un momento visivamente catturante. **[OFF-ADJACENT]** — guida creator riportata, 17 apr 2025.
**1.5 Instagram: i segnali sono watch time, like e *sends*.** I sends pesano più dei like per il contenuto non seguito. *Mosseri, 21 gen 2025.* **[OFF]**
**1.6 YouTube Shorts: "engaged views" esclude esplicitamente i loop.** **[OFF]** — support.google.com/youtube/answer/12220281.
**1.7 TikTok pubblica gli input, non i pesi.** Interazioni, informazioni del video, impostazioni di dispositivo/account. **Nessuna soglia di completamento ufficiale.** **[OFF]**

**1.8 Benchmark 2025–26 (nessuna piattaforma pubblica una soglia di passaggio):**

| Metrica | Valore | Campione | Fonte |
|---|---|---|---|
| TikTok full-watch rate | **10%** | 2,3 M post | Metricool 2026 **[VENDOR]** |
| Reels: skip nei primi 3 s | **60,5–65,5%** | 9,77 M Reel | Socialinsider 2026 **[VENDOR]** |
| Watch ratio mediano (Reels) | **31,4%** (<15 s: 66%; 60 s+: 16,4%) | 346 Reel | Highviz 2026 **[VENDOR]** |
| Watch time medio per Reel | **8,5 s** | 24,3 M post | Metricool 2026 **[VENDOR]** |

**1.9 Non dimostrato** (non metterlo in un playbook come regola): "loopability aumenta la reach" (YouTube esclude i loop dalle engaged views), "pattern interrupt ogni 2–4 s" (nessun test causale), "l'ultimo frame è decisivo", "attenzione di 8 secondi" (**falso**, fonte inesistente), "85% guarda senza audio" (dato Facebook 2016, oggi non aggiornato).

---

## 2. Specifiche tecniche

| Superficie | Aspect | Risoluzione | Durata max | File / bitrate | Verificato |
|---|---|---|---|---|---|
| **IG Reels** (organico) | 1,91:1 … 9:16 (consigliato 9:16) | min 720 px lato corto, min 30 fps | **20 min** in app; **oltre 3 min non consigliati a pubblico nuovo** | MP4/MOV, H.264/HEVC, AAC 128 kbps | 2026-10-25 (help center: pagina bloccata al fetch diretto, testo confermato via indice + citazioni ott. 2026) **[OFF]** |
| **IG feed video** | idem | ≥1080×1920 | dal 2022 i video in feed sono Reels | — | **[OFF]** |
| **IG feed video ads** | 1,91:1 … 9:16 | ≥1080×1920 | 60 min | 4 GB | Meta Business Help **[OFF]** |
| **TikTok** (organico) | 9:16 | 1080×1920 consigliato, 360–4096 px | **3 min** tutti, 5/10 min alcuni; upload consumer variabile | MP4/WebM/MOV, H.264/H.265/VP8/VP9, 23–60 fps, max 4 GB (API) | developers.tiktok.com, agg. 4 ago 2026 **[OFF]** |
| **TikTok ads** | 9:16 | 540×960 min | 5–60 s | 500 MB, ≥516 kbps | **[OFF]** |
| **YouTube Shorts** | quadrato o verticale | **max 1080p in upload** | **≤3 min** | 256 GB; H.264 High 1080p ~8 Mbps SDR 30 fps / 12 Mbps 60 fps; AAC-LC 384 kbps stereo 48 kHz; faststart | 2026-10-25 **[OFF]** |

**Contraddizione aperta:** la guida YouTube dichiara 1080p massimo per gli Shorts, ma una misura su 135 video trova **19% di Shorts serviti in 4K**. Possibili spiegazioni: video lunghi ≤3 min riclassificati, guida non aggiornata, percorsi di upload diversi. **Non risolta.**

---

## 3. Cosa consegnano davvero (evita di sovra-codificare)

Misura su 135 video, 2026-10-12 — **[MEAS, campione piccolo, non ufficiale]**:

| Piattaforma | Risoluzione consegnata | Codec | Bitrate mediano | Audio | Loudness mediana |
|---|---|---|---|---|---|
| **Reels** | 90% 1080p · 4% 1440p · 6% 720p | VP9 | **2,1 Mbps** | AAC 63 kbps | **−14,1 LUFS** (92% entro ±1 LU, nessuno sopra −14,0) |
| **TikTok** | 76% 1080p · 24% 720p | H.265 (88%) | **1,1 Mbps** | AAC | **−21,2 LUFS** (80% fra −27,5 e −15,0) |
| **Shorts** | 19% 4K · 74% 1080p | VP9 | **5,7 Mbps** | AAC | **−19,6 LUFS** |

**Conseguenze operative.**
- **Reels:** normalizza a −14 LUFS (la piattaforma non alza, e chi è più basso suona debole). Esporta 1080×1920, 8–12 Mbps: oltre è peso inutile, perché viene ricodificato a ~2 Mbps.
- **TikTok:** non normalizza in consegna. Se masterizzi a −14 e la piattaforma riproduce a −21, il tuo file suona più forte degli altri in fase di upload ma il risultato finale è nelle sue mani: misura il file **dopo** il primo upload, non assumere.
- **Shorts:** consegna più alta (5,7 Mbps), ma il tetto di upload dichiarato è 1080p. 4K ha senso solo se il tuo flusso di pubblicazione lo consente davvero.
- **Reels nuovi** possono essere serviti a 720p per le prime ore e passare a 1080p con le visualizzazioni: **non giudicare la qualità il primo giorno.**

---

## 4. Sottotitoli e leggibilità

**4.1 I numeri, per formato.**

| Fonte | Caratteri/riga | Righe | Velocità | Note |
|---|---|---|---|---|
| **BBC** (9:16) | **25** | max 3 | **160–180 wpm** | interlinea 3,9–4,5% dell'altezza → 75–86 px su 1920; area centrale 75% verticale / 90% orizzontale |
| **Netflix** (inglese) | 42 | max 2 | **20 cps** | minimo 5/6 s, massimo 7 s per cue |
| **Bruciati social** (prassi) | 18–25 | 2 | 15–20 cps | oltre, il testo si legge a scatti |

**[OFF/MANUAL]** — BBC Subtitle Guidelines; Netflix Timed Text Style Guide.
Per l'italiano: le parole sono più lunghe dell'inglese (~15–20% in più a parità di contenuto, dato di localizzazione, **[UNVER]** per questa piattaforma). In pratica: **max 2 righe, 18–22 caratteri, 15–18 cps**.

**4.2 I sottotitoli servono?** Circa **10%** degli spettatori broadcast usa i sottotitoli con regolarità, fino al **35%** per alcuni contenuti online (BBC R&D, WHP323) **[MEAS]**. Il "+12% di view time" citato da Meta è un dato interno non pubblico **[VENDOR]**. Il "+80% guarda fino in fondo" è citato ovunque ma la fonte primaria non è pubblica **[UNVER]**.

**4.3 Accessibilità (WCAG 2.2).** Vale per il web; un player incorporato in un sito è coperto, l'app nativa no.
`1.2.2` sottotitoli per audio preregistrato (A) · `1.4.3` contrasto 4,5:1 (3:1 per testo grande) · `1.4.11` contrasto non-testuale 3:1 · `2.3.1` max **3 flash al secondo** · `2.2.2` pausa/stop per contenuto in movimento oltre 5 s.

---

## 5. Diritti musicali su Instagram

**5.1 Libreria con licenza:** uso **personale, non commerciale**. Gli account business possono **non** avervi accesso. **5.2 Sound Collection:** royalty-free, uso commerciale consentito. **5.3 Audio originale:** consentito; se pubblico, altri possono riusarlo. **5.4 Esibizioni dal vivo:** esplicitamente ammesse ("filming a live artist or band performing their own music"). **5.5** Clip più corte raccomandate; componente visiva obbligatoria; l'audio può essere **silenziato retroattivamente** se la licenza cambia. **[OFF]** — Instagram Help, Music Guidelines, 2026-10-25.

**Caso specifico: il live dell'artista.** È audio originale → ammesso. Se però il master è già distribuito, può essere riconosciuto e attribuito (o silenziato) automaticamente. Rischio: attribuzione/silenzio, non rimozione, su Instagram.
**YouTube Shorts:** uno Short **oltre 1 minuto** con un claim Content ID viene **bloccato globalmente**. Sotto il minuto, il claim monetizza a favore del titolare ma resta visibile.

---

## 6. Mercato italiano (contesto, non benchmark)

41,2 M identità social (69,7% della popolazione) · Instagram **29,9 M** · TikTok **22,0 M** adulti · YouTube **41,2 M**. Tempo medio: TikTok ~90 min/giorno, YouTube 70, Instagram 67. *DataReportal, Digital 2026 Italy (dati ott. 2025).* **[MEAS]**
**Non esiste** un benchmark documentato di retention o di preferenze di hook specifico per l'italiano: non inventarlo.

---

## 7. Check automatici derivabili

| ID | Regola | Condizione di passaggio | Fonte |
|---|---|---|---|
| `DUR-01` | Reels: oltre 3 min non consigliati a pubblico nuovo | durata ≤ 180 s per un post di reach | §2 **[OFF]** |
| `DUR-02` | Shorts | durata ≤ 180 s | §2 **[OFF]** |
| `ASP-01` | Verticale social | 9:16, 1080×1920 | §2 |
| `LUF-01` | Loudness Reels | −14 LUFS ±1, TP ≤ −1 dBTP | §3 **[MEAS]** |
| `SAF-01` | Testo nella zona sicura | x 120–885, y 288–1248 su 1080×1920 | misure ads/organic |
| `CAP-01` | Leggibilità | ≤ 2 righe, 18–22 caratteri, 15–18 cps | §4 |
| `CAP-02` | Contrasto | ≥ 4,5:1 (3:1 se grande) | WCAG 1.4.3 |
| `CAP-03` | Presenza sottotitoli | traccia presente se c'è parlato | WCAG 1.2.2 |
| `MUS-01` | Diritti | audio originale o Sound Collection; libreria licenziata solo su account personale | §5 **[OFF]** |
| `MUS-02` | Shorts > 60 s | nessun claim Content ID | §5 **[OFF]** |
