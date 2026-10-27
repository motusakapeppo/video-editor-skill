# DECISIONS PATCH

Voci nuove per `PLAN/DECISIONS.md`. La numerazione continua da **D016**, il più
alto esistente. **D017 è già impegnato**: è la Strada A approvata dall'utente il 27
ottobre 2026 (`--edl3`), ancora non scritta nel file. Le voci D018-D026 sono
proposte da questo incarico e si applicano quando vengono accettate.

Ogni voce è revocabile: si sostituisce con una voce nuova, non si riscrive la
storia. Ogni voce dice cosa emenda o restringe.

---

## D017 — `vx auto` resta 2.2 e guadagna uno stadio `--edl3` (2026-10-27)

**Stato: approvata dall'utente.**

Contesto. `scripts/vx/auto.py` produce un piano `vx.edl/2.2`
(`scripts/vx/edl_plan.py:21-22`) e lo rende con il renderer 2.2. EDL 3, le clip
`generator` di F3 e i keyframe di F2 vivono in `edl3_render` e sono raggiungibili
solo dall'API. Una capacità inarrivabile dal comando che l'utente usa non è una
capacità di prodotto.

Si decide:

1. `cmd_auto` accetta `--edl3`. Senza il flag, il percorso è quello di oggi e
   l'output resta **byte-identico**: è l'accettazione che protegge le regressioni.
2. Con il flag, l'orchestratore compila il 2.2, lo migra in memoria con
   `edl3_migrate` (già provato a MAD 0.0000), inietta la clip generatrice del
   Motion Studio e rende con `edl3_render`.
3. L'iniezione non è un secondo costruttore: usa `Clip3.generator` e
   `_materialize_generators`, cioè il contratto di F3-T07.

Perché. Il migratore è già scritto e misurato; usarlo come ponte non crea logica
parallela. Riscrivere il bridge in 3.0 avrebbe rotto la promessa «2.2 resta 2.2»
di D004 e il collaudo su materiale reale.

Emenda: nulla. Attua D004. Revocabile con una voce che la sostituisce.

---

## D018 — Il segnale esterno è un artefatto, non un'opinione (2026-10-27)

**Stato: proposta.**

Contesto. `retention`, `blind` e `curva` hanno zero occorrenze in
`PLAN/MASTER_PLAN.md` e `PLAN/MASTER_LEDGER.md`. L'unico criterio esterno del
piano è l'accordo con l'utente 7/10 (`MASTER_PLAN.md:264`); F19 promuove le
ricette su un benchmark interno (`MASTER_PLAN.md:270`) e il ledger ha già perso
la «valutazione dell'utente» che il piano chiedeva (`MASTER_LEDGER.md:219`).

Si decide:

1. Il blind test è un artefatto su disco: coppie con ordine scambiato, stesso
   seed, stessa durata, stessa risoluzione, stesso LUFS, nessun branding. I voti
   finiscono in un JSON immutabile con il tempo di risposta e lo sha256 dei due
   video (D005 applicato al giudizio, non al frame).
2. I numeri sono tre: tasso di preferenza, tasso di indistinguibilità, intervallo
   di Wilson al 95%. Le soglie si **calibrano** sul corpus; il punto di partenza
   proposto è 40% di preferenza e 50% di indistinguibilità, e non è una verità.
3. La retention entra come export CSV manuale, non come provider di rete (D002):
   niente rete accesa per default. Un calo senza decisione associata si dichiara
   `unattributed`.
4. **Il Critico propone, solo il segnale esterno promuove.** Una ricetta senza
   osservazione esterna resta `candidate`; una ricetta senza dati dice `no_data`.
5. L'esame non si certifica senza un blind test registrato sopra le soglie.

Perché. Un sistema che si giudica da solo converge sul proprio gusto. Il numero
esterno è l'unica cosa che può dire «questo montaggio è buono» senza chiedere al
sistema che l'ha fatto.

Emenda: F18, F19, F9 (accettazione) e la definizione dell'esame. Non sostituisce
nessuna decisione. Revocabile con una voce che la sostituisce.

---

## D019 — La legge delle prove si estende alle opportunità (2026-10-27)

**Stato: proposta.**

Contesto. La legge delle prove conosce solo i difetti: `ALLOWED_EVIDENCE_MAP`
(`scripts/vx/contract.py:246`) mappa un trattamento su un finding, e i tre punti
di controllo sono `contract.py:630`, `gates.py:153`, `render.py:1882`. Un sistema
che agisce solo sui difetti produce, per definizione, un video medio.

Si decide:

1. `contract.py` guadagna lo schema `opportunity`:
   `{opportunity_id, tipo, t0, t1, intensita, fonte}`. I `tipo` sono un insieme
   chiuso: energia, emozione, rivelazione, gesto, pausa, rottura di pattern.
2. La legge **si estende, non si sostituisce**: un trattamento creativo cita un
   finding **o** un'opportunità. Il controllo vive negli stessi tre punti, non in
   un quarto.
3. Ogni rilevatore dichiara la misura che ha prodotto l'opportunità. Un'opportunità
   senza fonte è rifiutata.
4. Il referto elenca trovate, usate e scartate con motivo.

Perché. Un'opportunità è un momento di valore con la stessa dignità probatoria di
un difetto. Senza, il tetto anti-arlecchino diventa l'unica regola e il sistema
ottimizza l'assenza di errori.

Emenda: estende D005 (la prova è un artefatto) al positivo. **Non allenta la legge
esistente**: nessun controllo viene rimosso o ridotto. Revocabile con una voce che
la sostituisce.

---

## D020 — `PROVISIONAL`: chiusa non è certificata (2026-10-27)

**Stato: proposta. Emenda D010.**

Contesto. T09 blocca F5, F6, F9, F18 e l'esame (`MASTER_PLAN.md:78`, `:84`, `:102`,
`:264`), e un solo dato umano mancante paralizza la costruzione. Peggio: lo stato
attuale non è coerente. `scripts/vx/registry.py:347` restituisce `provisional` sia
quando T09 è aperto sia quando un asse non ha driver; `composition` e `typography`
non hanno driver, quindi restano `provisional` per sempre, e il ramo «tutte non
provvisorie» di `tests/test_threshold_provenance.py:136-141` fallirebbe il giorno
in cui T09 diventa `DONE`. La riga di ledger `F0-T10` diceva `TODO` con il cancello
già verde.

Si decide:

1. Lo stato di fase ammesso diventa tre valori: `TODO`, `PROVISIONAL`, `DONE`.
   `PROVISIONAL` significa: il codice è chiuso, le prove esistono, la
   certificazione no.
2. La certificazione dell'esame fallisce se una fase è `PROVISIONAL` o se un asse
   è `unmeasured`. Un test che non può esistere altrimenti.
3. Le due cause di provvisorietà si separano: `provisional` = T09 aperto;
   `unmeasured` = nessun driver, indipendente da T09. `composition` e `typography`
   sono `unmeasured` finché non hanno un driver, e non fingono di essere in attesa
   del corpus.
4. `test_threshold_provenance.py` non viene indebolito: i 22 assert restano, e il
   nuovo ramo si aggiunge.

Perché. La disciplina non è «non costruire senza il dato»; è «non certificare
senza il dato». Confondere le due cose trasforma un blocco umano in un blocco
dell'intero programma.

Emenda D010, che resta nella storia. Revocabile con una voce che la sostituisce.

---

## D021 — La licenza musicale è un regime, non un divieto (2026-10-27)

**Stato: proposta.**

Contesto. `MASTER_PLAN.md:177` scrive la regola in modo incondizionato: «musica e
SFX (solo CC0 o CC-BY con attribuzione registrata)». Applicata a un esperimento
privato è attrito puro: nessuno pubblica, nessuno rischia nulla, e la verifica
costa tempo per un file che si chiama `music.wav`. La regola è giusta dove serve,
sbagliata dove non serve.

Si decide:

1. Ogni lavoro dichiara `publication: private | public`. Default `private`.
2. In `private`: nessun cancello di licenza. Il record porta `sha256` e
   `licence: unverified`. Nessuna domanda all'utente, nessun blocco.
3. In `public`: il record deve portare `licence_type`, `licence_url`,
   `retrieved_at`. Senza, il lavoro non passa. Il cancello si accende solo qui.
4. Lo `sha256` resta sempre: non è una limitazione, è memoria. Serve il giorno in
   cui quella traccia finisce in un lavoro pubblico.

Perché. Un divieto che nessuno può rispettare nel proprio flusso viene aggirato o
ignorato. Un regime che si accende solo al momento irreversibile — la
pubblicazione — costa zero oggi e protegge dove conta.

Emenda: restringe `MASTER_PLAN.md:177` al regime `public`. **Non tocca D001**: i
font sono una scelta autonoma in un lavoro di committenza, non una traccia in un
esperimento. Revocabile con una voce che la sostituisce.

---

## D022 — Il riempimento predefinito non cambia fino a F12 (2026-10-27)

**Stato: proposta.**

Contesto. La scelta fra `crop` 1080, `crop` 720 e `blur` è aperta da giorni. Il
valore attuale è in `scripts/vx/render.py` (`FILL_POLICIES`). Cambiarlo oggi rompe
i golden di F2 e il collaudo su materiale reale.

Si decide:

1. Il default resta quello attuale fino a F12.
2. Si introduce `--fill auto` come proposta, non come default: `crop` con
   confidenza di tracking alta, `blur` quando il volto non è tracciato, `fit` solo
   per orizzontale o su richiesta esplicita.
3. Il cambio di default avviene una volta sola, in F12, con i golden rigenerati
   nello stesso commit e la decisione registrata.

Perché. Un default che cambia due volte costa due giri di golden e due collaudi.
Il momento giusto per cambiarlo è quando il sistema sa tracciare il volto in modo
affidabile, cioè dopo F17/F13, non prima.

Emenda: nessuna. Revocabile con una voce che la sostituisce.

---

## D023 — La documentazione nuova apre l'indice con una decisione (2026-10-27)

**Stato: proposta.**

Contesto. `SKILL.md` è a 200 righe esatte e `tests/test_docs_consistency.py:57`
impone `<= 200`; l'indice delle reference è congelato a 01..26 (`:62-67`) e ogni
numero deve esistere. Le fasi F20-F24 e le decisioni nuove hanno bisogno di
documentazione lunga, che non entra in 200 righe.

Si decide:

1. La documentazione lunga va in reference nuove (`27-...`), mai in appendice
   dentro la 26.
2. L'indice si apre solo con una decisione registrata, e il test si aggiorna nello
   **stesso commit** della decisione. Mai un aggiramento.
3. `SKILL.md` resta a 200 righe: si sostituisce una riga, non si aggiunge.

Perché. Il vincolo delle 200 righe è ciò che rende la skill leggibile da un
agente. Aggirarlo con un'appendice è il modo gentile di perderlo.

Emenda: nessuna. Revocabile con una voce che la sostituisce.

---

## D024 — L'interoperabilità è posizionamento, non export (2026-10-27)

**Stato: proposta.**

Contesto. F10 elenca OTIO e FCPXML fra gli export (`MASTER_PLAN.md:106`) e li
valida (`:108`). `references/26-edl3-e-ponte.md:562` dice «non un sostituto di
DaVinci o Premiere», ma non dice cosa il sistema è.

Si decide:

1. OTIO e FCPXML sono il formato in cui il sistema consegna a un NLE
   professionale: «il reparto montaggio che lavora mentre l'NLE è chiuso».
2. F24 esiste per questo: round-trip senza perdita sul sottoinsieme dichiarato,
   casi fuori sottoinsieme dichiarati, un NLE reale che apre il file.
3. F10 resta com'è: l'export multi-formato non viene toccato.

Perché. Un export in più è una funzione; un formato di consegna è una posizione.
La differenza decide se il prodotto è un giocattolo o un reparto.

Emenda: nessuna, aggiunge F24. Revocabile con una voce che la sostituisce.

---

## D025 — Il golden si estende all'edit (2026-10-27)

**Stato: proposta.**

Contesto. I golden esistenti coprono i frame: F2-T09 (15 effetti, 34 frame) e
F3-T08 (54 template, 162 frame). Una modifica creativa che non cambia i frame
campionati — un taglio spostato, una curva di loudness, un ordine — non viene
vista.

Si decide:

1. Il golden di un EDL creativo è composto da tre cose: hash del documento, curva
   di loudness, tre frame campionati.
2. Un EDL con hash o curva diversi dal golden fallisce nominando il campo.
3. Il sigillo si rigenera solo con un comando esplicito, come per la Motion
   Studio (D016): il test non scrive mai.

Perché. Un golden sui soli frame protegge il rendering, non il montaggio. La cosa
che l'utente guarda è il montaggio.

Emenda: F2-T09, con la riga `F2-T10`. Revocabile con una voce che la sostituisce.

---

## D026 — Il cancello ha un budget di durata (2026-10-27)

**Stato: proposta.**

Contesto. La suite completa è a 757 secondi e cresce a ogni fase. Nessuna fase
dichiara un tetto: il cancello può diventare così lento che nessuno lo esegue
prima di un commit, e un cancello che non si esegue non è un cancello.

Si decide:

1. Ogni fase dichiara, alla chiusura, il tetto di durata della suite.
2. Superarlo è un fallimento, non un avviso.
3. Il tetto si alza solo con una decisione registrata.
4. F16 (prestazioni) viene anticipato: è ciò che tiene usabile il cancello.

Perché. La disciplina si paga in secondi. Se il prezzo diventa intollerabile, la
disciplina sparisce per prima.

Emenda: F12 e l'ordine di F16. Revocabile con una voce che la sostituisce.

---

## D027 — Il secondo letto musicale si abbassa di più (2026-10-27)

**Stato: attuata in F1-T04.**

Contesto. Il ducking era un `DuckingSpec` unico, indicizzato per ruolo: con due
letti musicali non esisteva modo di darne uno diverso a ciascuno. La scelta di
rappresentazione doveva restare additiva: `edl_plan.py` è il contratto 2.2.0 e
D004 lo dichiara congelato.

Si decide:

1. `EDLPlan.ducking_tracks` è una tupla opzionale di `DuckingSpec`, ognuna con il
   proprio `target_role`. Additiva: un documento senza la chiave è identico a
   prima, e un piano a un letto produce **lo stesso grafo** di prima, character
   for character.
2. Il letto principale usa la politica di default (soglia 0.05, rapporto 8). Un
   secondo letto si abbassa di più: soglia ×1.6 e rapporto ×1.5, con i due valori
   dichiarati per traccia nel referto. È una politica, non una misura calibrata, e
   come tale si registra: non ha provenienza di corpus e non deve fingerla.
3. Il cap di triage passa da 1 a 2. La terza traccia resta `REDUNDANT_MUSIC`: il
   limite è una politica, non un numero magico.

Perché. Due letti con lo stesso ducking si contendono lo stesso spazio e il
dialogo diventa il campo di battaglia. Il secondo deve cedere prima e più in
profondità; dirlo nel referto, per traccia, rende la scelta verificabile invece
che sotterranea.

Emenda: nessuna. Estende D004 senza riscriverlo. Revocabile con una voce che la
sostituisce.
