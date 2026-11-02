# Port della skill su OpenCode 2

**Data:** 2 novembre 2026
**Motore:** OpenCode 2.0.27 (desktop, servizio in background) — sulla stessa
macchina convivono la CLI V1 1.19.5 nel `PATH` e la V2 dentro l'app.
**Esito:** la skill è una skill V2 nativa, scoperta, con il server MCP
registrato e con una corsa reale verde.

Questo file dice cosa è stato misurato, non cosa si spera. Ogni riga viene da un
comando eseguito o da un test verde.

## Dove vive adesso

```text
~/.config/opencode/skills/video-editor/     la skill (forma preferita dalla V2)
~/.config/opencode/skill/video-editor/      giunzione alla stessa cartella, per la V1
```

La cartella è stata spostata intera, non solo `SKILL.md`: i percorsi scritti
dentro la skill sono relativi alla cartella che contiene `SKILL.md`. La
giunzione non duplica la skill nell'elenco: `GET /api/skill` restituisce
`video-editor` una volta sola, con `path` nella cartella `skills/`.

Dopo lo spostamento l'install editable è stata rifatta dalla nuova radice,
perché `__editable__.vx_video_editor-1.5.0.pth` contiene il percorso assoluto del
pacchetto. Serve `wheel` nel venv: senza, `pip install -e . --no-build-isolation`
si ferma con `invalid command 'bdist_wheel'`.

Prove: `vx --version` → `vx 1.5.0`; `vx doctor --json` → `"verdict": "ready"`
anche lanciato da una cartella qualsiasi; `tests/test_packaging.py` 16 PASS.

## Server MCP

Registrato con il comando ufficiale, che riscrive solo la propria voce:

```powershell
opencode mcp add video-editor --global `
  --env PYTHONIOENCODING=utf-8 `
  --env PYTHONPATH="<repo>\scripts" `
  -- "<VENV>\Scripts\python.exe" -m vx.mcp_server
```

La voce scritta è quella nativa V2:

```jsonc
{
  "mcp": { "servers": { "video-editor": {
    "type": "local",
    "command": ["<VENV>/Scripts/python.exe", "-m", "vx.mcp_server"],
    "environment": {
      "PYTHONIOENCODING": "utf-8",
      "PYTHONPATH": "<repo>/scripts"
    }
  } } }
}
```

La forma `-m` è quella provata. L'entry point `vx-mcp.exe` risponde quando lo si
lancia a mano (`initialize` + `tools/list`: sei tool), ma avviato dal servizio
desktop usciva con codice 255 e la connessione non si apriva; con `python -m
vx.mcp_server` la connessione è immediata. Se il workspace ha un proprio
`.opencode/opencode.json` la voce va scritta lì, senza `--global`: è quello che
fa comparire il server nell'elenco di quella cartella.

Prove: `opencode mcp list` → `✓ video-editor connected`; il log del servizio
registra `mcp connected server=video-editor tools=6`; un handshake diretto
risponde `protocolVersion 2025-06-18`, `serverInfo vx-video-editor 1.5.0`,
sei tool (`vx_doctor`, `vx_triage`, `vx_edl_validate`, `vx_auto`,
`vx_job_submit`, `vx_job_status`) e `vx_doctor` → `ready`.

Due differenze V2 che contano, scritte anche in `references/24-api-e-mcp.md`:

- **Code Mode è il default.** I tool non stanno nell'elenco nativo del modello:
  arrivano dentro `execute` come `tools["video-editor"].vx_*`. Con
  `"codemode": false` tornano nativi, con il nome normalizzato
  `<server>_<tool>`.
- **Il timeout di esecuzione è 12 ore** (`mcp.timeout.execution`), non i 30
  secondi del catalogo. Un `vx_auto` sincrono non muore più a metà per il
  timeout del client; per il client che chiude resta il motore di job.

Limite noto: se il processo del server viene ucciso (o l'app lo chiude), la
connessione di quel progetto resta `failed` finché l'app non viene riaperta; la
CLI non ne risente. Non è un difetto della skill: è lo stato della connessione.

## Una corsa vera, dalla cartella nuova

```powershell
vx run --input tests\stock_corpus\real\vertical_916.webm --autonomous --workdir "<WORK_DIR>\v2_port_check6"
```

Esito: **exit 0**, `qc_pass: true`, zero codici di fallimento, 243 frame resi su
243 attesi, quattro deliverable (`master.mp4` 5.811.111 byte, `subtitles.srt`,
`thumbnail.jpg`, `contact_sheet.jpg`) con sha256, loudness misurata **−13,84
LUFS** su target −14,00.

La stessa corsa, prima delle correzioni, si fermava tre volte: piano (exit 2,
testimone oltre la timeline analizzata), pre-render (exit 5, taglio a 14,994 su
materiale lungo 14,9937) e QC (exit 5, cue da 0,26 s e 20,0 cps contati come
20,000000000000004). Le correzioni sono nel commit `eea243a` e riguardano una
sola legge: piano, tagli, sottotitoli e QC devono misurare la stessa timeline,
quella che l'analisi ha davvero percorso.

## Cosa NON è stato toccato

- `SKILL.md` resta sotto le 200 righe (gate): l'unica riga cambiata dice che una
  corsa dura minuti e non va interrotta dal timeout del tool.
- Nessun percorso assoluto nei documenti: `tests/test_portability.py` esce
  `portability: OK`.
- La legge dei testimoni non è stata allentata: un testimone fuori dal materiale
  resta un errore, e c'è un check che lo prova.
