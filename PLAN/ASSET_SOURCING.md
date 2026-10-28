# Sourcing di asset esterni — verifica licenze (28/10/2026)

Regola: nessun asset entra senza licenza letta alla fonte, hash fissato e una riga in
`LICENSES_MATRIX.md`. Le licenze cambiano: questa pagina è una fotografia datata, non
una verità permanente. Rileggere prima di ogni acquisto o import.

| Fonte | Licenza | Ridistribuzione dentro uno strumento | Attribuzione | Fonte | Quantità | Trappola |
|---|---|---|---|---|---|---|
| gl-transitions | MIT | sì, senza restrizioni | copyright + testo della licenza nelle copie | github.com/gl-transitions/gl-transitions | 125 `.glsl` | la licenza avverte che singole transizioni possono avere un'intestazione diversa: **l'intestazione vince** |
| Google Fonts | OFL-1.1 (2.016 famiglie), Apache-2.0 (44), Ubuntu-1.0 (5) | sì: "may be bundled, redistributed and/or sold with any software" | sì: avviso di copyright + licenza completa; il nome riservato (RFN) vieta di chiamare col nome originale una versione **modificata** (anche un subset) | openfontlicense.org, github.com/google/fonts | ~2.065 famiglie | licenza per famiglia, non una sola; un subset è un'opera derivata |
| Lucide | ISC (+ MIT per la parte derivata da Feather) | sì | sì, avviso | lucide.dev/license | 1.999 SVG | due licenze nello stesso albero |
| Tabler | MIT | sì | sì | github.com/tabler/tabler-icons | 6.257 (5.203 outline + 1.054 filled) | stroke con `currentColor`: va risolto prima del render |
| lottie-web | MIT | sì (runtime) | sì | github.com/airbnb/lottie-web | ~250-300 KB | — |
| LottieFiles (animazioni) | "Lottie Simple License" (nessun identificatore SPDX) | **no in blocco**: vieta di raccogliere o compilare i file per replicare o sviluppare un servizio simile o concorrente; licenza per singolo asset | non obbligatoria, ma se data deve essere visibile all'utente finale | lottiefiles.com/page/license | centinaia di migliaia (non verificato) | la clausola anti-compilazione esclude una libreria di animazioni dentro lo strumento |
| Shadertoy | CC-BY-NC-SA-3.0 di default; opt-in per shader (es. MIT) | no col default (non commerciale + share-alike) | sì BY + SA | shadertoy.com/terms | decine di migliaia (non verificato) | la licenza sta nell'intestazione del singolo shader, non nei metadati: va letta file per file |
| Freesound | per suono: CC0-1.0 / CC-BY-4.0 / CC-BY-NC-4.0 | CC0 e CC-BY sì, NC no | CC-BY: autore + licenza + link | freesound.org/help/tos_web/ | oltre 500.000 (non verificato) | nessuna licenza unica; nessun diritto di scaricamento in blocco nei termini: si cura a mano |
| Pixabay / Pexels | licenze proprietarie ("Pixabay Content License", "Pexels License") | **no**: vietata la distribuzione "su base autonoma", incluso dentro un altro file digitale; vietata la copia massiva | non richiesta | pixabay.com/service/terms/, pexels.com/terms-of-service/ | milioni | spedire il file grezzo nel pacchetto **è** distribuzione autonoma: salvo solo l'output renderizzato |

## Da usare

gl-transitions (MIT, 125 transizioni): la singola aggiunta più forte per il Motion Studio.
Famiglie Google Fonts OFL: raggruppabili, con l'obbligo di rinominare se si fa un subset di un font RFN.
Lucide e Tabler: SVG semplici, ideali per Chromium headless.
Runtime lottie-web (MIT), se serve un formato Lottie.
Solo gli shader Shadertoy la cui intestazione dice MIT/CC-BY, verificati uno per uno.
Solo suoni Freesound CC0/CC-BY, catalogati per file.

## Da evitare

Shadertoy col default CC-BY-NC-SA: non commerciale e share-alike avvelenano il prodotto.
Il catalogo LottieFiles in blocco: la clausola anti-compilazione è esplicita.
File grezzi Pixabay/Pexels dentro il pacchetto: vietata la distribuzione autonoma.
Suoni Freesound NC.
Font OFL rinominati o ridotti senza controllare il nome riservato.
