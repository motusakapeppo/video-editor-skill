# 10. Grammatica del montaggio e ritmo

Ogni regola qui ha una fonte. La legenda dice quanto fidarsi.

| Sigla | Significato |
|---|---|
| **[DOC]** | scritto in un libro/manuale pubblicato, con pagina |
| **[MEAS]** | misurato in uno studio, con numeri |
| **[CONS]** | consenso professionale su più fonti, non testato sperimentalmente |
| **[DERIV]** | mia derivazione ingegneristica — **non** è prassi documentata |

---

## 1. La gerarchia di Murch

**Regola.** Sei criteri, in ordine di priorità. Quando devi sacrificare, sacrifica **dal basso verso l'alto**, mai il contrario.

| # | Criterio | Peso |
|---|---|---|
| 1 | Emozione | **51%** |
| 2 | Storia | **23%** |
| 3 | Ritmo | **10%** |
| 4 | Eye-trace (dove sta l'occhio) | **7%** |
| 5 | Piano bidimensionale dello schermo | **5%** |
| 6 | Spazio tridimensionale dell'azione | **4%** |

*Fonte: Walter Murch, "In the Blink of an Eye", 2ª ed. 2001, §"The Rule of Six", pp. 17–20 (paginazione variabile).* **[DOC]**
Murch stesso avverte che i numeri sono "in parte provocatori": conta **l'ordine**, non l'aritmetica.

**Implementazione.** Confronto **lessicografico**, non somma pesata. Una somma permette al 4% di spazialità di battere il 51% di emozione, che è l'esatto contrario della regola.

```python
def murch_score(cut, ctx):
    return (ctx.emotion(cut), ctx.story(cut), -abs(ctx.beat_offset(cut)),
            ctx.eye_penalty(cut), ctx.plane_penalty(cut), ctx.space_penalty(cut))
best = max(candidates, key=murch_score)   # lessicografico
```

**Conseguenza pratica.** Emozione + storia = 74%. Un taglio emotivamente giusto ma che attraversa la linea (spazialmente "sbagliato") è **preferibile** a un taglio spazialmente perfetto ma morto. È la licenza che rende legittima la discontinuità nel videoclip.

---

## 2. Grammatica della continuità: cosa vietano davvero

**2.1 Match on action.** Si taglia **durante** un movimento, alla stessa fase del gesto nei due piani. Il movimento maschera la giunzione. Non richiede continuità spaziale, solo continuità di traiettoria. *Bordwell & Thompson, Film Art 12ª ed., cap. 6, "Continuity Editing: Some Fine Points", p. 237.* **[DOC]**
Verifica: il taglio deve cadere dove il moto è alto e crescente, non in un minimo di moto (taglio "morto", si legge come esitazione).

**2.2 Regola dei 30°.** Fra due piani dello **stesso soggetto** la macchina deve cambiare angolo di almeno 30°. Meno, e le due inquadrature sono troppo simili: si legge come **jump cut**, cioè errore. *Bordwell & Thompson, p. 237; Arijon, "Grammar of the Film Language", 1976.* **[DOC]**
Verifica automatica: omografia ORB fra ultimo frame di A e primo di B; se la rotazione stimata è < 30°, rischio jump cut.

**2.3 Regola dei 180°.** Stabilita la linea d'azione, tutte le macchine restano da un lato. Attraversarla inverte la direzione di schermo e gli sguardi. *Bordwell & Thompson, cap. 6, "Spatial Continuity: The 180° System", p. 231.* **[DOC]**
Verifica: segno della velocità del baricentro del soggetto; due piani consecutivi con segno opposto sullo stesso soggetto = violazione.
**Deroghe legittime** [DOC]: piano neutro/frontale di ristabilimento; disorientamento voluto; montaggio (vedi §5).

**2.4 Eyeline match.** Se A guarda fuori campo a sinistra, B deve mostrare l'oggetto a destra. *Bordwell & Thompson, glossario + cap. 6.* **[DOC]**
Verifica: `segno_sguardo × segno_oggetto < 0`. È anche il prerequisito dell'effetto Kuleshov (§4).

---

## 3. Ritmo: la matematica del BPM

```
frame_per_battuta = fps × 60 / BPM
frame_per_battuta_intera (4/4) = frame_per_battuta × 4
```

| BPM | 24 fps | 25 fps | 30 fps | battuta (s) | battuta 4/4 (s) |
|---|---|---|---|---|---|
| 80 | 18.00 | 18.75 | 22.50 | 0.750 | 3.00 |
| 90 | 16.00 | 16.67 | 20.00 | 0.667 | 2.67 |
| 100 | 14.40 | 15.00 | 18.00 | 0.600 | 2.40 |
| 120 | 12.00 | 12.50 | 15.00 | 0.500 | 2.00 |
| 129 | 11.16 | 11.63 | 13.95 | 0.465 | 1.86 |
| 140 | 10.29 | 10.71 | 12.86 | 0.429 | 1.71 |

**[DERIV]** per la tabella; il *principio* è documentato: il "montaggio metrico" di Eisenstein è costruito su "lunghezze assolute dei pezzi… secondo uno schema che corrisponde a una misura musicale" (*Film Form*, "Methods of Montage", p. 72). **[DOC]**

**3.1 Tolleranza: ±1 frame.** A 25 fps un frame è 40 ms. L'asincronia percettibile sta nell'ordine dei 20–30 ms, quindi **±1 frame è il limite pratico; ±2 frame si sente come "in ritardo"**. **[CONS]**
Conseguenza: `snap_cuts` con tolleranza 0,30 s (7 frame) non sta "sul beat", sta **vicino** al beat. Va usata la tolleranza di un frame.

**3.2 Taglia sull'"uno", accelera sulla frase.** I cambi di sezione (strofa→ritornello, drop) cadono sulla **battuta 1**. La densità dei tagli segue la frase musicale: rada nelle strofe, densa verso il ritornello, massima al culmine.
*Misurato su 548 videoclip ufficiali: sincronia a livello di battuta nel 60% dei clip (329/548), a livello di staccato nel 17% (95/548); score di coincidenza sul downbeat più alto per R&B 0,31 e reggaeton 0,28, più basso per rock 0,19.* *"Is there a 'language of music-video clips'?", ISMIR 2021, arXiv:2108.00970, §4.3.* **[MEAS]**
*Editor Maxime Pozzi, nella stessa ricerca: "Per ritornelli e strofe il montaggio segue il ritmo e tipicamente accelera verso i climax; nei bridge è più lento e poetico".* **[DOC]**
*Pearlman, "Cutting Rhythms" 2ª ed., cap. 2 e 4: il ritmo è cicli di tensione e rilascio, non un metronomo.* **[DOC]**

Verifica: densità (tagli per battuta) non decrescente verso il ritornello, con massimo locale al drop. Una densità **piatta** su tutto il brano è il tell numero uno del montaggio automatico.

**3.3 Lunghezza dei piani.** Non esiste un valore "giusto", esiste una **distribuzione**.

| Contesto | ASL | Fonte |
|---|---|---|
| Film narrativo contemporaneo | 3–5 s | Cutting et al., i-Perception 2011 (ASL 10 s negli anni '30 → <4 s dopo il 2000, r = −.75) **[MEAS]** |
| Videoclip ufficiale | media 4,76 s, ma **modale sulla battuta/staccato** (distribuzione bimodale) | arXiv:2108.00970 §4.2.1 **[MEAS]** |
| TikTok talking-head top | 0,37 tagli/s (SD 0,17) → **ASL ≈ 2,7 s** | Dost & Huang, Marketing Trends 2026, Studio 1 **[MEAS]** |

Verifica: l'istogramma delle durate deve avere un picco su un multiplo della battuta; una campana liscia significa montaggio non musicato.

**3.4 Tre strumenti diversi.** Taglio netto (punteggiatura ritmica, default nel music-driven); taglio sul movimento (§2.1, giunzione invisibile); **J-cut** (l'audio del piano successivo entra prima della sua immagine) e **L-cut** (l'audio del piano precedente continua sopra la nuova immagine). *Bordwell & Thompson, cap. 7, "A Closer Look: Editing Dialogue", p. 273: J/L sono decisioni di suono, non di immagine.* **[DOC]**
Offset tipico: 4–12 frame. Un offset casuale è un bug, non una scelta.

---

## 4. Kuleshov: vero, ma condizionato

**Regola.** Volto neutro → oggetto → stesso volto: il volto si legge come *reazione all'oggetto*. Il significato sta nella **sequenza**, non nel piano. Ma funziona **solo se**: (a) lo sguardo è fuori campo, (b) il rapporto spaziale è inferibile, (c) lo spettatore è alfabetizzato al montaggio.

**Le prove, senza la leggenda.** Il filmato originale è perduto e i resoconti si contraddicono. Le repliche:

| Studio | Esito |
|---|---|
| Prince & Hensley, *Cinema Journal* 31(2), 1992 | **non replica**: la maggior parte dei soggetti vede un volto neutro |
| Barratt et al., *Projections* 10(2), 2016 | **conferma** l'effetto su cinque condizioni emotive, massimo sulla tristezza |
| Ildirar & Ewing, *Projections* 12(1), 2018 | effetto presente **solo in spettatori esperti** e solo nella condizione tristezza; **assente** nei primi spettatori |
| Cao et al., *PLOS ONE* 19(8): e0308295, 2024 | conferma comportamentale + fMRI su clip autentiche |

**[MEAS]** — L'effetto è reale ma **non automatico**: è un'inferenza appresa e dipendente dagli indizi.

**Applicazione concreta (gratis).**
```
A  volto dell'artista, neutro, sguardo fuori campo a sinistra   (2 battute)
B  l'oggetto dello sguardo, a destra                            (1 battuta)
A' stesso volto, stessa inquadratura                            (2 battute)
```
Nel rap: `volto → pubblico/sala → volto` fa leggere il volto come *sta sentendo il pubblico*. `volto → palco vuoto → volto` fa leggere *mancanza/attesa*. Stesso materiale, significato diverso.
Verifica: il controllo eyeline di §2.4 deve passare, e **A e A' devono essere identici**: cambiare angolo trasforma la reazione in un piano nuovo e uccide l'inferenza.

---

## 5. Continuità contro montaggio

**Linkage (Pudovkin):** i piani si sommano, il significato si accumula, la giunzione deve essere invisibile.
**Collisione (Eisenstein):** il significato nasce dal **conflitto** fra piani — grafico, di scala, di volume, di profondità, di tempo. Cinque metodi in ordine crescente di astrazione: metrico, ritmico, tonale, overtonale, intellettuale. *Film Form, "Methods of Montage", pp. 72–83.* **[DOC]**

**Quando la discontinuità è più forte** [DOC + CONS]:
1. Quando l'idea vale più dello spazio.
2. **Quando la musica porta la continuità.** È la licenza del videoclip: la traccia audio fornisce il filo temporale, quindi l'immagine può rifiutarlo.
3. Quando vuoi far sentire il vuoto (ellissi, memoria, violenza).
4. Quando la continuità richiederebbe un piano che non hai — e la gerarchia di Murch dice di sacrificare la spazialità (4%) prima del ritmo (10%).

Verifica: ogni giunzione etichettata `LINK` o `COLLIDE`; il rapporto deve essere **coerente**. 95% linkage con tre collisioni casuali si legge come rotto; 100% collisione si legge come trailer.

---

## 6. Transizioni

**Non esiste un "canone delle sei regole delle transizioni"** nella letteratura primaria. Esistono sei **test decisionali**; li do come sintesi, non come citazione. **[CONS]**

1. **Passa il tempo?** La dissolvenza afferma ellissi temporale. Se il tempo non passa: taglio. **[DOC]**
2. **Cambia il luogo?** Se sì, la dissolvenza è motivata; se il luogo è lo stesso, taglio. **[DOC]**
3. **C'è un'associazione da affermare?** La sovrapposizione significa "questi due appartengono insieme". Senza associazione è decorazione.
4. **Le due composizioni fanno rima?** Orizzonte, forma, posizione della figura: se non condividono nulla, il centro della dissolvenza è rumore.
5. **Il moto è abbastanza basso?** Due piani in movimento sovrapposti producono ghosting.
6. **Hai i handle?** Una dissolvenza di *d* consuma *d* di sovrapposizione: servono *d* frame di materiale in più su entrambi i lati.

**Durate** [CONS] — convenzioni professionistiche, non derivate sperimentalmente:

| Transizione | Durata | @24 | @25 | @30 |
|---|---|---|---|---|
| Taglio netto | 0 | 0 | 0 | 0 |
| Dissolvenza corta | 0,25–0,5 s | 6–12 | 6–13 | 8–15 |
| Dissolvenza standard (passa il tempo) | ~1 s | 24 | 25 | 30 |
| Dissolvenza lunga (dichiarazione) | 2 s+ | 48+ | 50+ | 60+ |
| Fade a nero (a metà) | 1–2 s | 24–48 | 25–50 | 30–60 |
| Fade a nero (fine pezzo) | 1,5–3 s | 36–72 | 38–75 | 45–90 |

**Regola derivata** [DERIV]: `durata_dissolvenza ≤ 0,25 × min(len(A), len(B))`. Una dissolvenza da 1 s fra due piani da 1,5 s mangia un terzo di ciascuno: i piani smettono di esistere come piani.

---

## 7. I tell dell'amatore

1. Taglio a metà gesto o **durante** il blink (si taglia 1–2 frame **prima**). *Murch, §"Blinking".* **[DOC]**
2. **Dissolvenza su ogni giunzione**, non motivata: il tell più riconoscibile.
3. Jump cut da cambio d'angolo < 30° (§2.2).
4. Attraversamento della linea senza piano di ristabilimento (§2.3).
5. **Metronomo**: tagliare su ogni battuta con densità uniforme, senza accelerare sulla frase (§3.2).
6. Frame flash / anomalie di un frame singolo.
7. Dissolvenza più lunga del piano che esce (§6).
8. Musica troppo alta o non duckata sotto la voce.
9. Tagliare a un piano prima che il precedente sia stato "ricevuto" (il problema del "tour guide" di Murch). **[DOC]**
10. Due piani di taglia simile attaccati senza cambio d'angolo.

---

## 8. Cosa NON credere (pur restando nei manuali)

| Affermazione | Stato |
|---|---|
| "Il primo secondo e 7 decimi decide tutto" | numero preciso non documentato; la finestra utile è **2–5 s** (R1/R2 in `11-retention-e-piattaforme.md`) |
| "Il 67% swipa via in 3 secondi" | folklore, nessuna fonte primaria |
| "Attenzione di 8 secondi, come un pesce rosso" | **falso**, fonte inesistente |
| "I loop aumentano la reach" | nessuna piattaforma lo dichiara; YouTube **esclude** i loop dalle engaged views |
| "Pattern interrupt ogni 2–4 s aumenta la retention" | convenzione, nessun test causale pubblicato |
| "L'ultimo frame è il più importante" | principio ragionevole, nessuna prova diretta |

Le fonti e i numeri delle affermazioni di piattaforma stanno nel file 11.
