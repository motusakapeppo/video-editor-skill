# 24: API stabile, install editable e server MCP

`vx.api` è la superficie esterna pensata per agenti e per il server MCP. La CLI
resta il protocollo normativo; l'API è il suo specchio tipizzato, e argparse
resta privato. Il contratto è `1.5.0` (`vx.report/1.0`), l'EDL è `2.2.0`.

## Installazione

L'unico modo supportato è l'install editable dalla radice del repo:

```powershell
<venv>\Scripts\python.exe -m pip install -e .
```

`config.py` calcola `SKILL_ROOT = Path(__file__).resolve().parents[2]`: la
configurazione vive accanto a `SKILL.md`, e `assets/fonts/` deve restare nel
repo perché libass la riceve come `fontsdir`. Un'install in `site-packages`
sposterebbe il pacchetto fuori dall'albero: la scoperta di `vx.config.json`
fallirebbe e i font non sarebbero più raggiungibili. Per questo la modalità
non editable non è supportata.

L'install crea due entry point:

| Entry point | Target | Cosa fa |
|---|---|---|
| `vx` | `vx.cli:main` | la CLI del protocollo |
| `vx-mcp` | `vx.mcp_server:main` | il server MCP su stdio |

Da qualsiasi directory:

```powershell
vx --version        # vx 1.5.0
vx doctor --json    # verdict: ready
```

`vx --version` stampa `vx 1.5.0`; `vx doctor --json` restituisce il verdetto
dell'ambiente, e `ready` è la condizione per lavorare.

### Fallback offline

Il comando isolato è quello primario. Se la build isolata non può scaricare
`setuptools` e `wheel`, si aggiunge `--no-build-isolation`, ma il venv deve
avere `wheel`: setuptools 65 non porta `bdist_wheel`. Senza rete si installa
prima:

```powershell
<venv>\Scripts\python.exe -m pip install wheel
<venv>\Scripts\python.exe -m pip install -e . --no-build-isolation
```

## vx.api

`vx.api` è la superficie stabile. Quattro funzioni, nessun flag, nessun
argparse.

```python
from vx.api import AutoRequest, doctor, run_auto, triage, validate_edl
```

`AutoRequest` è un dataclass frozen con nove campi:

| Campo | Tipo | Default |
|---|---|---|
| `prompt` | `str` | obbligatorio |
| `assets` | `str` | obbligatorio |
| `out` | `str` | obbligatorio |
| `workdir` | `Optional[str]` | `None` |
| `fps` | `Optional[float]` | `None` |
| `manifest` | `Optional[str]` | `None` |
| `autonomous` | `bool` | `False` |
| `crf` | `Optional[int]` | `None` |
| `preset` | `Optional[str]` | `None` |

Le quattro funzioni:

```python
run_auto(request: AutoRequest) -> Dict[str, Any]
triage(directory, width, height, *, manifest=None, require_anchor=False) -> Dict[str, Any]
validate_edl(plan) -> Dict[str, Any]
doctor() -> Dict[str, Any]
```

- `run_auto` inoltra i nove campi ad `auto.run` e restituisce il report
  dell'esecuzione. Scrive solo sotto `out` e `workdir`.
- `triage` scopre e classifica il primo livello di `directory` senza scrivere
  nulla. `width` e `height` devono essere interi positivi, altrimenti
  `VxError`. Il risultato è il payload del triage più `total`.
- `validate_edl` accetta un dict, una stringa JSON o un percorso. Restituisce
  sempre `ok`, `errors`, `grammar` e `camera_exemptions`; quando `ok` è vero
  aggiunge `contract_version`, `sources`, `video_tracks`, `audio_tracks`,
  `duration_sec` e `resolution`.
- `doctor` restituisce la scansione dell'ambiente invariata, con `verdict`.

`triage`, `validate_edl` e `doctor` sono di sola lettura. `run_auto` è l'unica
che scrive, e solo sotto `out` e `workdir`.

## MCP

Il server è `vx-mcp`. Il trasporto è JSON-RPC 2.0 delimitato da newline su
stdio: un oggetto JSON per riga, UTF-8. Zero dipendenze: niente asyncio, niente
SDK. Su stdout arrivano solo risposte; la diagnostica va su stderr con il
prefisso `[mcp]`. `vx-mcp` non accetta flag: il trasporto è l'interfaccia.
`vx mcp` esegue lo stesso loop dalla CLI, utile per una prova manuale.

Il protocollo negoziato è uno di `2024-11-05`, `2025-03-26`, `2025-06-18`;
il default è `2025-06-18`. `serverInfo` è `vx-video-editor` alla versione del
contratto.

### Handshake

```
initialize  ->  notifications/initialized  ->  tools/list
```

`initialize` risponde con `protocolVersion`, `capabilities.tools` e
`serverInfo`. Una versione richiesta fuori elenco ricade sul default.
`notifications/initialized` marca la sessione come inizializzata. `tools/list`
e `tools/call` prima dell'inizializzazione restituiscono `-32002`. `ping` è
permesso anche prima e risponde `{}`.

Le notifiche non ricevono mai una risposta. Un errore di dominio non è un
errore JSON-RPC: è un risultato con `isError: true` e il messaggio nel primo
elemento `content`.

### Codici

| Codice | Significato |
|---|---|
| `-32700` | parse error: la riga non è JSON |
| `-32600` | invalid request: manca `jsonrpc: "2.0"` o `method` |
| `-32601` | method not found |
| `-32602` | invalid params o tool sconosciuto |
| `-32002` | server non inizializzato |
| `-32603` | errore interno del gestore: il server resta in ascolto |

Una riga che supera il limite di annidamento del parser riceve `-32700` e il
server continua a servire le righe successive.

### I sei tool

| Tool | Argomenti obbligatori | Argomenti opzionali | Restituisce |
|---|---|---|---|
| `vx_doctor` | nessuno | nessuno | la scansione dell'ambiente con `verdict` |
| `vx_triage` | `path`, `width`, `height` | `manifest`, `require_anchor` | il payload del triage più `total` |
| `vx_edl_validate` | `plan` | nessuno | `ok`, `errors`, `grammar`, `camera_exemptions` e il riepilogo quando `ok` |
| `vx_auto` | `prompt`, `assets`, `out` | `workdir`, `fps`, `manifest`, `autonomous`, `crf`, `preset` | il report di `auto` |
| `vx_job_submit` | `prompt`, `assets`, `out` | `workdir`, `fps`, `manifest`, `autonomous`, `crf`, `preset` | il record `queued` con il `job_id` |
| `vx_job_status` | `job_id` (32 esadecimali minuscoli) | nessuno | il record del job: stato, percentuale, stadio, risultato o errore |

`vx_auto` è sincrono e può durare minuti: il timeout è del client. Per un
client interattivo la CLI è preferibile, e per un lavoro lungo `vx_job_submit`
(vedi `references/25-job-engine.md`). Scrive `plan.json` e
`auto_report.json` sotto `<workdir>`; il master va in `out` e i deliverable in
`<workdir>/out`.

### Configurazione del client: OpenCode 2

OpenCode 2 legge i server sotto `mcp.servers`; il vecchio `mcp.<nome>` della V1
è ancora normalizzato, ma la forma nativa è questa. Il comando ufficiale evita
di riscrivere il resto del file:

```powershell
opencode mcp add video-editor --global `
  --env PYTHONIOENCODING=utf-8 `
  --env PYTHONPATH="<repo>\scripts" `
  -- "<venv>\Scripts\python.exe" -m vx.mcp_server
```

Equivalente in `opencode.json`, con la stessa forma che scrive la CLI:

```jsonc
{
  "mcp": {
    "servers": {
      "video-editor": {
        "type": "local",
        "command": ["<venv>/Scripts/python.exe", "-m", "vx.mcp_server"],
        "environment": {
          "PYTHONIOENCODING": "utf-8",
          "PYTHONPATH": "<repo>/scripts"
        }
      }
    }
  }
}
```

L'entry point `vx-mcp` funziona lanciato a mano (risponde a `initialize` e
`tools/list`), ma avviato dal servizio desktop è uscito con codice 255 e la
connessione non si apriva; la forma `python -m vx.mcp_server` con `PYTHONPATH`
si connette subito. Se si usa l'entry point, `command` è `["<venv>/Scripts/vx-mcp.exe"]`
e `PYTHONPATH` non serve (l'install editable lo fornisce).

In un progetto con un proprio `.opencode/opencode.json` la voce va messa lì
(senza `--global`): quel file è la configurazione di progetto e il server deve
comparire nell'elenco di quella cartella.

Due cose cambiano rispetto alla V1 e vanno sapute:

- **Code Mode è il default.** Con `codemode` assente o `true`, i tool non
  finiscono nell'elenco nativo del modello: arrivano dentro `execute` come
  `tools["video-editor"].vx_doctor(...)`. È la stessa superficie, con un livello
  in più. `"codemode": false` li riporta nell'elenco nativo, dove il nome è
  normalizzato `<server>_<tool>` (`video_editor_vx_auto`).
- **Il timeout di esecuzione è 12 ore** (`mcp.timeout.execution`), non i 30
  secondi del catalogo. Un `vx_auto` sincrono non muore più a metà per il
  timeout del client; resta il caso del client che chiude la sessione, e per
  quello c'è il motore di job.

La skill è scoperta da `~/.config/opencode/skills/video-editor/` (forma
preferita dalla V2) e, per compatibilità, da `~/.config/opencode/skill/video-editor/`.
Il nome del server MCP non dipende da dove vive la cartella.

### Configurazione di un client MCP generico

```json
{
  "mcpServers": {
    "video-editor": {
      "command": "<venv-python>",
      "args": ["-m", "vx.mcp_server"],
      "env": {
        "PYTHONPATH": "<repo>/scripts",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

Con l'install editable, `command` può essere `vx-mcp` e `args` vuoto.

## Limiti dichiarati

- Solo stdio: nessuna rete, nessuna autenticazione.
- L'install non editable non è supportata: `SKILL_ROOT` e `assets/fonts/`
  devono restare nel repo.
- Il motore di job non ha cancellazione e non ha un demone: il worker è un
  processo staccato, e un job avviato arriva a `completed` o `failed`
  (`references/25-job-engine.md`).
- `vx_auto` non esegue whisper: non ci sono ancoraggi da trascrizione e i
  B-roll sono distribuiti uniformemente e dichiarati.
- Senza metadati di camera ogni taglio è un'esenzione dichiarata.

## Test

- `tests/test_api.py`: superficie tipizzata, triage read-only, EDL, doctor.
- `tests/test_mcp_server.py`: envelope, schemi, errori di dominio, purezza di stdout.
- `tests/test_packaging.py`: pyproject, entry point, install editable.
