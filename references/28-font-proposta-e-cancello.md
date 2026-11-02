# Il font: proposta dal contesto, scelta umana, cancello sul render

Prima di renderizzare, `vx auto` costruisce una richiesta dai dati veri del
lavoro (prompt, bpm, look, densita' dei tagli) e chiama `fontproposal.propose`.

Il catalogo ha 2.065 famiglie; l'intenzione viene tradotta in un piccolo insieme
di famiglie note (condensato, horror, editoriale, tecnico, minimal — italiano e
inglese), quelle si scaricano al massimo 12, si misurano sui byte reali e
producono un campione PNG ciascuna con licenza e perche'.

Artefatti, nella workdir del lavoro:

| file | cosa |
|---|---|
| `font_proposal.json` | richiesta, bacino, candidati, punteggi, `chosen` |
| `fonts/<Famiglia>-sample.png` | la parola di prova, 1200 px |
| `fonts/<Famiglia>*.ttf` | il file usato dal render |
| `font_choice.json` | la scelta, con `schema: vx.fontchoice/1` e `from` |

## Il cancello

Il render **non parte** finche' la scelta non c'e', ma non aspetta per sempre:
`--font-wait SECONDS` (default 180). Le uscite sono dichiarate nel report, mai
silenziose:

| `font.selected_by` | quando |
|---|---|
| `user` | scelta arrivata dalla vista, o gia' su disco |
| `auto` | nessuna vista, nessuna attesa (`--autonomous`, `--no-font-gate`) |
| `auto_after_timeout` | attesa scaduta: si prende la prima, **e si scrive** |

Senza rete il bacino e' locale (`pool: "local"`) e lo dichiara. Se la proposta
fallisce del tutto si prosegue col font del preset e il report porta
`font.error`.

## Fino al pixel

La famiglia scelta arriva al `Fontname` dell'ASS anche nel percorso `auto`
(`bridge.caption_ass`), con `fontsdir` sulla cartella `<workdir>/fonts`. Senza
scelta l'ASS resta identico a prima.

## Interruttori

```powershell
--font-network / --no-font-network     cerca e scarica dal catalogo
--font-wait 180                        quanto si aspetta una scelta umana
--no-font-gate                         nessuna attesa: si prende la proposta
```
