// vx — banco di montaggio. Nessuna dipendenza, nessuna rete: solo /api/state
// ogni secondo e /api/approve al clic. Ogni testo mancante lo dice in italiano.
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');

  const el = {
    jobs: $('jobs'),
    jobsList: $('jobsList'),
    jobsCount: $('jobsCount'),
    jobTitle: $('jobTitle'),
    jobClock: $('jobClock'),
    stateWord: $('stateWord'),
    clipChip: $('clipChip'),
    percent: $('percent'),
    heroLabel: $('heroLabel'),
    heroSub: $('heroSub'),
    bar: $('bar'),
    fill: $('fill'),
    barHead: $('barHead'),
    now: $('now'),
    nowText: $('nowText'),
    nowNote: $('nowNote'),
    verdict: $('verdict'),
    verdictMark: $('verdictMark'),
    verdictTitle: $('verdictTitle'),
    verdictText: $('verdictText'),
    verdictMeta: $('verdictMeta'),
    resultMeta: $('resultMeta'),
    resultBody: $('resultBody'),
    fontGate: $('fontGate'),
    fontCards: $('fontCards'),
    fontReq: $('fontReq'),
    fontPool: $('fontPool'),
    gateError: $('gateError'),
    regiaMeta: $('regiaMeta'),
    regiaNow: $('regiaNow'),
    regiaStripWrap: $('regiaStripWrap'),
    lookFrame: $('lookFrame'),
    lookCaption: $('lookCaption'),
    lookNote: $('lookNote'),
    lookStatus: $('lookStatus'),
    estimate: $('estimate'),
    estimateTotal: $('estimateTotal'),
    estimateRows: $('estimateRows'),
    estimateNote: $('estimateNote'),
    estimateNotes: $('estimateNotes'),
    estimateFlash: $('estimateFlash'),
    live: $('live'),
    liveMeta: $('liveMeta'),
    liveBody: $('liveBody'),
    liveCover: $('liveCover'),
    liveStart: $('liveStart'),
    liveCoverNote: $('liveCoverNote'),
    liveNow: $('liveNow'),
    liveStatus: $('liveStatus'),
    liveWait: $('liveWait'),
    liveFill: $('liveFill'),
    liveAudio: $('liveAudio'),
    liveVideoA: $('liveVideoA'),
    liveVideoB: $('liveVideoB'),
    liveEmpty: $('liveEmpty'),
    planGate: $('planGate'),
    planCards: $('planCards'),
    planSub: $('planSub'),
    planError: $('planError'),
    lookImg: $('lookImg'),
    lookEmpty: $('lookEmpty'),
    lookTime: $('lookTime'),
    lookSource: $('lookSource'),
    mods: $('mods'),
    modsEmpty: $('modsEmpty'),
    shotSpecs: $('shotSpecs'),
    specDur: $('specDur'),
    specIn: $('specIn'),
    specOut: $('specOut'),
    reelScroll: $('reelScroll'),
    reelStrip: $('reelStrip'),
    reelHead: $('reelHead'),
    reelSpeed: $('reelSpeed'),
    regiaLine: $('regiaLine'),
    regiaEmpty: $('regiaEmpty'),
    journalList: $('journalList'),
    journalMeta: $('journalMeta'),
    journalEmpty: $('journalEmpty'),
    frameView: $('frameView'),
    frameBackdrop: $('frameBackdrop'),
    frameBig: $('frameBig'),
    frameName: $('frameName'),
    frameJob: $('frameJob'),
    frameClose: $('frameClose'),
    frames: $('frames'),
    framesEmpty: $('framesEmpty'),
    frameCount: $('frameCount'),
    phases: $('phases'),
    phasesEmpty: $('phasesEmpty'),
    railSub: $('railSub'),
    action: $('action'),
    approve: $('approve'),
    approveDone: $('approveDone'),
    approveNote: $('approveNote'),
    approveFlash: $('approveFlash'),
  };

  const body = document.body;

  // Il server serve piu' lavori: `?job=N` tiene insieme stato, fotogrammi,
  // risultato e approvazione. L'indice resta quello che il server dichiara in
  // `selected`, cosi' un indice fuori range non fa divergere la pagina.
  const readJobParam = () => {
    const raw = new URLSearchParams(window.location.search).get('job');
    const value = Number.parseInt(raw, 10);
    return Number.isFinite(value) && value >= 0 ? value : null;
  };

  let jobIndex = readJobParam();

  const scoped = (url) =>
    jobIndex === null
      ? url
      : url + (url.indexOf('?') === -1 ? '?' : '&') + 'job=' + jobIndex;

  const pad2 = (n) => String(n).padStart(2, '0');
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const num = (value, digits) => (Number(value) || 0).toFixed(digits).replace('.', ',');

  function mmss(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    return Math.floor(total / 60) + ':' + pad2(total % 60);
  }

  // La fase arriva con la sua descrizione intera: nella sequenza serve la testa
  // della frase, nel riquadro "Ora" la frase completa.
  function shortLabel(label) {
    const text = String(label || '').trim();
    if (!text) return '';
    return text.split(/[:.;]/)[0].trim() || text;
  }

  function upperFirst(text) {
    const t = String(text || '').trim();
    return t ? t.charAt(0).toUpperCase() + t.slice(1) : '';
  }

  // L'esito è del server; se il campo manca si torna al comportamento di prima.
  const OUTCOME_VALUES = ['running', 'done', 'stopped', 'failed', 'incomplete', 'idle'];
  const OUTCOME_WORD = {
    running: 'In corso',
    done: 'Fatto',
    stopped: 'Fermato',
    failed: 'Non riuscito',
    incomplete: 'Senza master',
    idle: 'In attesa',
  };
  const OUTCOME_MARK = { done: '✓', stopped: '■', failed: '!', incomplete: '–' };
  const OUTCOME_TITLE = {
    done: 'Lavoro concluso',
    stopped: 'Fermato prima della fine',
    failed: 'Non riuscito',
    incomplete: 'Concluso senza master',
  };

  function outcomeOf(state, finished) {
    const raw = String(state.outcome || '').trim().toLowerCase();
    if (OUTCOME_VALUES.indexOf(raw) !== -1) return raw;
    return finished ? 'done' : state.current ? 'running' : 'idle';
  }

  // Il motivo arriva da `note`: si riporta com'è, senza inventarne uno.
  function verdictSentence(kind, note) {
    const reason = String(note || '').trim();
    const quoted = reason ? 'Motivo dichiarato: ' + reason + (/[.!?]$/.test(reason) ? '' : '.') : '';

    if (kind === 'done') {
      const text = upperFirst(reason);
      return text ? text + (/[.!?]$/.test(text) ? '' : '.') : "Il lavoro è arrivato in fondo senza una nota di esito.";
    }
    if (kind === 'stopped') {
      return quoted || 'Il lavoro si è fermato prima della fine, senza un motivo dichiarato.';
    }
    if (kind === 'failed') {
      return quoted || 'Il lavoro non è riuscito, senza un motivo dichiarato.';
    }
    return 'Il master non è stato prodotto.' + (quoted ? ' ' + quoted : '');
  }

  function sizeLabel(bytes) {
    const n = Number(bytes) || 0;
    if (n >= 1048576) return (n / 1048576).toFixed(1).replace('.', ',') + ' MB';
    if (n >= 1024) return Math.round(n / 1024) + ' kB';
    return n + ' B';
  }

  /* ------------------------------------------------------------------ numero */

  let shownPercent = null;

  function setPercent(value) {
    const target = Math.round(clamp(value, 0, 100));
    if (shownPercent === null || reduce.matches) {
      shownPercent = target;
      el.percent.textContent = String(target);
      return;
    }
    if (target === shownPercent) return;
    const from = shownPercent;
    shownPercent = target;
    const started = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - started) / 600);
      const eased = 1 - Math.pow(1 - k, 3);
      el.percent.textContent = String(Math.round(from + (target - from) * eased));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /* ---------------------------------------------------------------- sequenza */

  let phaseNodes = [];

  function buildPhases(phases) {
    el.phases.textContent = '';
    phaseNodes = phases.map((phase, index) => {
      const item = document.createElement('li');
      item.className = 'phase is-todo';
      item.style.setProperty('--i', String(index));

      const node = document.createElement('span');
      node.className = 'phase-node';
      node.setAttribute('aria-hidden', 'true');

      const number = document.createElement('span');
      number.className = 'phase-idx num';
      number.setAttribute('aria-hidden', 'true');
      number.textContent = pad2(index + 1);

      const wrap = document.createElement('span');
      wrap.className = 'phase-body';

      const name = document.createElement('span');
      name.className = 'phase-name';
      name.textContent = shortLabel(phase.label) || phase.key;

      const state = document.createElement('span');
      state.className = 'phase-state';

      wrap.append(name, state);
      item.append(node, number, wrap);
      el.phases.append(item);

      return { item, node, name, state, key: String(phase.key) };
    });
  }

  function paintPhases(phases, activeIndex, done) {
    phaseNodes.forEach((entry, index) => {
      const isDone = done || (activeIndex >= 0 && index < activeIndex);
      const isNow = !done && index === activeIndex;
      entry.item.classList.toggle('is-done', isDone);
      entry.item.classList.toggle('is-now', isNow);
      entry.item.classList.toggle('is-todo', !isDone && !isNow);
      entry.node.textContent = isDone ? '✓' : '';
      entry.state.textContent = isDone ? 'fatta' : isNow ? 'in corso' : 'da fare';
      if (isNow) entry.item.setAttribute('aria-current', 'step');
      else entry.item.removeAttribute('aria-current');
      const label = phases[index] && phases[index].label;
      entry.item.title = label ? String(label) : '';
    });
  }

  /* -------------------------------------------------------------- fotogrammi */

  function cardFor(frame, index, total) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'shot';
    card.setAttribute('aria-haspopup', 'dialog');
    card.style.setProperty('--i', String(index));

    const box = document.createElement('span');
    box.className = 'shot-img';

    const img = document.createElement('img');
    img.alt = 'Fotogramma chiave ' + (index + 1) + ' di ' + total;
    img.decoding = 'async';

    img.addEventListener('load', () => {
      card.classList.add('is-loaded');
      attachTilt(card);
    });

    img.addEventListener('error', () => {
      card.classList.add('is-missing');
      box.textContent = '';
      const fallback = document.createElement('span');
      fallback.className = 'shot-fallback';
      fallback.textContent = "L'immagine non è disponibile";
      box.append(fallback);
    });

    img.src = scoped('/frames/' + encodeURIComponent(frame.name));
    box.append(img);

    const cap = document.createElement('span');
    cap.className = 'shot-cap';

    const idx = document.createElement('span');
    idx.className = 'shot-idx num';
    idx.textContent = pad2(index + 1);

    const name = document.createElement('span');
    name.className = 'shot-name';
    name.textContent = frame.name;
    name.title = frame.name;

    const size = document.createElement('span');
    size.className = 'shot-size num';
    size.textContent = sizeLabel(frame.bytes);

    cap.append(idx, name, size);
    card.append(box, cap);
    card.addEventListener('click', () => openFrame(frame, card));
    return card;
  }

  // Inclinazione reale: il contenitore ha prospettiva, la card si muove su X e Y.
  function attachTilt(card) {
    if (reduce.matches || !finePointer.matches || card.dataset.tilt === '1') return;
    card.dataset.tilt = '1';

    card.addEventListener('pointermove', (event) => {
      const rect = card.getBoundingClientRect();
      const px = (event.clientX - rect.left) / rect.width - 0.5;
      const py = (event.clientY - rect.top) / rect.height - 0.5;
      card.style.setProperty('--ry', (px * 10).toFixed(2) + 'deg');
      card.style.setProperty('--rx', (-py * 8).toFixed(2) + 'deg');
    });

    card.addEventListener('pointerleave', () => {
      card.style.setProperty('--ry', '0deg');
      card.style.setProperty('--rx', '0deg');
    });
  }

  function paintFrames(frames, finished) {
    // Il lavoro fa parte dell'impronta: due lavori possono avere gli stessi nomi
    // di fotogrammi e la griglia deve comunque essere rifatta.
    const signature = jobIndex + '|' + frames.map((f) => f.name + ':' + f.bytes).join('|');
    if (el.frames.dataset.signature !== signature) {
      el.frames.dataset.signature = signature;
      el.frames.textContent = '';
      frames.forEach((frame, index) => {
        el.frames.append(cardFor(frame, index, frames.length));
      });
    }
    el.frameCount.textContent = pad2(frames.length);
    el.framesEmpty.hidden = frames.length > 0;
    el.framesEmpty.textContent = finished
      ? 'Il lavoro è finito senza fotogrammi chiave.'
      : 'Nessun fotogramma: il lavoro non ne ha ancora scritti.';
  }

  /* --------------------------------------------------- fotogramma ingrandito */

  let frameCard = null;
  let frameClosing = false;

  // FLIP: dalla card al grande e ritorno, senza stacchi. Il livello non è un
  // cancello: si chiude con Esc, col fondo o col pulsante, e la pagina resta.
  function openFrame(frame, card) {
    if (el.fontGate.open || !el.frameView.hidden || frameClosing) return;

    const thumb = card.querySelector('.shot-img') || card;
    const from = thumb.getBoundingClientRect();

    frameCard = card;
    el.frameName.textContent = frame.name;
    const prompt = lastState ? String(lastState.prompt || '').trim() : '';
    const jobName = lastState ? String(lastState.job || '').trim() : '';
    el.frameJob.textContent = prompt || jobName || 'Lavoro senza titolo';
    el.frameBig.alt = 'Fotogramma chiave: ' + frame.name;

    el.frameBig.style.transition = 'none';
    el.frameBig.style.transform = 'none';
    el.frameBig.style.visibility = 'hidden';

    el.frameView.hidden = false;
    document.documentElement.classList.add('has-view');
    el.frameBig.src = scoped('/frames/' + encodeURIComponent(frame.name));

    window.requestAnimationFrame(() => el.frameView.classList.add('is-open'));

    const reveal = () => {
      if (el.frameView.hidden) return;
      const to = el.frameBig.getBoundingClientRect();
      el.frameBig.style.visibility = 'visible';

      if (reduce.matches || !to.width || !to.height) {
        el.frameBig.style.transform = 'none';
      } else {
        const scale = from.width / to.width;
        const dx = from.left - to.left;
        const dy = from.top - to.top;
        el.frameBig.style.transform =
          'translate(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px) scale(' + scale.toFixed(4) + ')';
        void el.frameBig.offsetWidth;
        el.frameBig.style.transition = 'transform 320ms cubic-bezier(.2,.8,.2,1)';
        el.frameBig.style.transform = 'none';
      }

      el.frameView.focus({ preventScroll: true });
    };

    if (typeof el.frameBig.decode === 'function') {
      el.frameBig.decode().then(reveal).catch(reveal);
    } else if (el.frameBig.complete) {
      reveal();
    } else {
      el.frameBig.addEventListener('load', reveal, { once: true });
    }
  }

  function closeFrame(options) {
    if (el.frameView.hidden || frameClosing) return;

    const instant = Boolean(options && options.immediate) || reduce.matches;
    const card = frameCard;
    const finish = () => {
      frameClosing = false;
      el.frameView.hidden = true;
      el.frameView.classList.remove('is-open');
      document.documentElement.classList.remove('has-view');
      el.frameBig.style.transition = 'none';
      el.frameBig.style.transform = 'none';
      el.frameBig.style.visibility = 'hidden';
      el.frameBig.removeAttribute('src');
      frameCard = null;
      if (card && card.isConnected) card.focus({ preventScroll: true });
    };

    if (instant || !card || !card.isConnected) {
      finish();
      return;
    }

    const thumb = card.querySelector('.shot-img') || card;
    const to = thumb.getBoundingClientRect();
    const from = el.frameBig.getBoundingClientRect();
    el.frameView.classList.remove('is-open');

    if (!from.width || !to.width) {
      finish();
      return;
    }

    const scale = to.width / from.width;
    const dx = to.left - from.left;
    const dy = to.top - from.top;
    frameClosing = true;
    el.frameBig.style.transition = 'transform 300ms cubic-bezier(.2,.8,.2,1)';
    el.frameBig.style.transform =
      'translate(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px) scale(' + scale.toFixed(4) + ')';
    window.setTimeout(finish, 310);
  }

  el.frameBackdrop.addEventListener('click', () => closeFrame());
  el.frameClose.addEventListener('click', () => closeFrame());

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || el.frameView.hidden) return;
    event.preventDefault();
    closeFrame();
  });

  /* ---------------------------------------------------------------- risultato */

  // Il risultato dipende dal file, non da una parola: `master_ready` lo dice il
  // server leggendo il disco. Se il file c'è, il player è in pagina e il pulsante
  // apre lo stesso flusso a schermo intero; se non c'è, resta una riga sola.
  function paintResult(state, closed) {
    const ready = state.master_ready === true;
    const name = String(state.master || '').trim();
    const bytes = Number(state.master_bytes) || 0;
    const signature =
      (ready ? 'ready|' + name + '|' + bytes : 'empty|' + (closed ? 'closed' : 'open')) +
      '|job:' + jobIndex;

    el.resultMeta.textContent = ready ? 'pronto' : closed ? 'assente' : 'non ancora';
    el.resultMeta.classList.toggle('is-ready', ready);

    if (el.resultBody.dataset.signature === signature) return;
    el.resultBody.dataset.signature = signature;
    el.resultBody.textContent = '';

    if (!ready) {
      const line = document.createElement('p');
      line.className = 'result-empty';
      line.textContent = closed
        ? 'Il master non è stato prodotto: non c’è nessun file da guardare.'
        : "Il risultato non c'è ancora: il master non è stato scritto.";
      el.resultBody.append(line);
      return;
    }

    const card = document.createElement('div');
    card.className = 'result-card';

    const monitor = document.createElement('div');
    monitor.className = 'monitor';

    const video = document.createElement('video');
    video.controls = true;
    video.preload = 'metadata';
    video.playsInline = true;
    video.src = scoped('/result');
    video.setAttribute('aria-label', 'Risultato del montaggio' + (name ? ': ' + name : ''));
    video.addEventListener('loadedmetadata', () => {
      if (video.videoWidth > 0 && video.videoHeight > 0) {
        monitor.style.aspectRatio = video.videoWidth + ' / ' + video.videoHeight;
      }
    });
    video.addEventListener('error', () => {
      monitor.textContent = '';
      const note = document.createElement('p');
      note.className = 'monitor-note';
      note.textContent = 'Il video non si apre: il master non è leggibile.';
      monitor.append(note);
    });

    monitor.append(video);

    const info = document.createElement('div');
    info.className = 'result-info';

    const file = document.createElement('p');
    file.className = 'result-file';
    file.textContent = name || 'master';
    file.title = name;

    const specs = document.createElement('dl');
    specs.className = 'result-specs';
    const spec = (label, value) => {
      const row = document.createElement('div');
      row.className = 'spec-row';
      const term = document.createElement('dt');
      term.textContent = label;
      const detail = document.createElement('dd');
      detail.textContent = value;
      row.append(term, detail);
      specs.append(row);
      return detail;
    };
    spec('peso', bytes > 0 ? sizeLabel(bytes) : 'non dichiarato');
    const format = spec('formato', '—');
    const duration = spec('durata', '—');

    const hint = document.createElement('p');
    hint.className = 'result-hint';
    hint.textContent =
      'Anteprima in pagina. «Guarda il risultato» apre il file a schermo intero.';

    const open = document.createElement('a');
    open.className = 'open-result';
    open.href = scoped('/result');
    open.target = '_blank';
    open.rel = 'noopener';
    open.append(document.createTextNode('Guarda il risultato'));
    const arrow = document.createElement('span');
    arrow.className = 'arrow';
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '↗';
    open.append(arrow);

    // Comando secondario: apre la cartella del master nel gestore di file.
    // Compare solo insieme al player, perche' dipende dallo stesso file.
    const reveal = document.createElement('button');
    reveal.type = 'button';
    reveal.className = 'reveal';
    reveal.textContent = 'Apri la cartella';

    const feedback = document.createElement('p');
    feedback.className = 'reveal-note';
    feedback.setAttribute('role', 'status');

    reveal.addEventListener('click', async () => {
      if (reveal.getAttribute('aria-busy') === 'true') return;
      reveal.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(scoped('/api/reveal'), { method: 'POST' });
        const result = await response.json();
        if (result && result.ok === true) {
          flash(feedback, 'Cartella aperta', 'ok');
        } else {
          flash(
            feedback,
            (result && result.reason) || 'Non ho potuto aprire la cartella.',
            'warn'
          );
        }
      } catch (error) {
        flash(feedback, 'Non ho potuto aprire la cartella: il server non risponde.', 'warn');
      } finally {
        reveal.removeAttribute('aria-busy');
      }
    });

    video.addEventListener('loadedmetadata', () => {
      if (video.videoWidth > 0 && video.videoHeight > 0) {
        monitor.style.aspectRatio = video.videoWidth + ' / ' + video.videoHeight;
        format.textContent = video.videoWidth + ' × ' + video.videoHeight;
      }
      if (Number.isFinite(video.duration) && video.duration > 0) {
        duration.textContent = mmss(video.duration);
      }
    });
    video.addEventListener('error', () => {
      format.textContent = 'non leggibile';
      duration.textContent = 'non leggibile';
    });

    const actions = document.createElement('div');
    actions.className = 'result-actions';
    actions.append(open, reveal, feedback);

    info.append(file, specs, hint, actions);
    card.append(monitor, info);
    el.resultBody.append(card);
  }

  // Riscontro breve, sempre nello stesso posto: si spegne da solo dopo 4 s.
  function flash(note, message, kind, base) {
    window.clearTimeout(note._timer);
    note.textContent = message;
    note.className = (base || 'reveal-note') + (kind ? ' is-' + kind : '');
    note.style.opacity = '1';
    note._timer = window.setTimeout(() => {
      note.style.opacity = '0';
      window.setTimeout(() => {
        if (note.isConnected) note.textContent = '';
      }, 320);
    }, 4000);
  }

  /* ------------------------------------------------------------ approvazione */

  // Il pulsante esiste solo se c'è un piano e un'attesa: `required` accende il
  // blocco, `waiting` lo rende cliccabile, `approved` lo chiude. Mai per ipotesi.
  function paintApproval(state) {
    const approval = state.approval && typeof state.approval === 'object' ? state.approval : null;
    const required = !!(approval && approval.required === true);

    el.action.hidden = !required;
    if (!required) {
      window.clearTimeout(el.approveFlash._timer);
      el.approveFlash.textContent = '';
      el.approve.classList.remove('is-waiting');
      return;
    }

    const approved = approval.approved === true;
    const waiting = approval.waiting === true;

    el.approve.hidden = approved;
    el.approveDone.hidden = !approved;

    if (approved) {
      el.approve.classList.remove('is-waiting');
      el.approve.disabled = true;
      el.approveNote.className = 'action-note';
      el.approveNote.textContent = 'La scelta è registrata per questo lavoro.';
      return;
    }

    el.approve.classList.toggle('is-waiting', waiting);
    el.approve.disabled = !waiting;
    el.approve.textContent = 'Approva questi fotogrammi';
    el.approveNote.className = 'action-note';
    el.approveNote.textContent = waiting
      ? 'Il render è fermo qui finché non approvi.'
      : String(approval.reason || '').trim() || 'Il render non è ancora arrivato ai fotogrammi.';
  }

  /* ------------------------------------------------------------------ racconto */

  // Il journal è la narrazione, non un log: le righe nuove entrano dal basso,
  // quelle già in pagina restano ferme (nessuna rianimazione a ogni poll).
  let journalJob = null;

  function journalRow(entry) {
    const row = document.createElement('li');
    row.className = 'journal-row';
    const time = document.createElement('span');
    time.className = 'journal-time num';
    time.textContent = mmss(Number(entry.t) || 0);
    const line = document.createElement('span');
    line.className = 'journal-line';
    line.textContent = String(entry.line || '').trim();
    row.append(time, line);
    return row;
  }

  function paintJournal(state) {
    const raw = Array.isArray(state.journal) ? state.journal : [];
    const entries = raw
      .map((entry, index) => ({ entry, index }))
      .filter((item) => item.entry && String(item.entry.line || '').trim())
      .sort(
        (a, b) =>
          (Number(b.entry.t) || 0) - (Number(a.entry.t) || 0) || b.index - a.index
      );

    const total = entries.length;
    el.journalMeta.textContent = total > 6 ? 'ultime 6 di ' + total : '';
    el.journalEmpty.hidden = total > 0;
    el.journalList.hidden = total === 0;

    if (!total) {
      if (el.journalList.childElementCount) el.journalList.textContent = '';
      journalJob = jobIndex;
      return;
    }

    if (journalJob !== jobIndex) {
      el.journalList.textContent = '';
      journalJob = jobIndex;
    }

    const existing = new Map();
    el.journalList.querySelectorAll('.journal-row').forEach((node) => {
      existing.set(node.dataset.key, node);
    });

    const visible = entries.slice(0, 6);
    const order = [];
    let fresh = 0;

    visible.forEach((item) => {
      const line = String(item.entry.line || '');
      const key = Number(item.entry.t) + '|' + String(item.entry.phase || '') + '|' + line;
      let node = existing.get(key);
      if (node) {
        existing.delete(key);
      } else {
        node = journalRow(item.entry);
        node.dataset.key = key;
        node.classList.add('is-new');
        node.style.setProperty('--i', String(Math.min(fresh, 5)));
        fresh += 1;
        node.addEventListener(
          'animationend',
          () => node.classList.remove('is-new'),
          { once: true }
        );
      }
      order.push(node);
    });

    existing.forEach((node) => node.remove());

    const current = Array.from(el.journalList.children);
    const same =
      current.length === order.length && current.every((node, i) => node === order[i]);
    if (!same) order.forEach((node) => el.journalList.append(node));
  }

  /* -------------------------------------------------------------------- regia */

  // Tre zone: cosa guarda la testina (un fotogramma vero), cosa fa (la striscia
  // con gli effetti di ogni shot), cosa modifica (i valori dello shot attivo).
  // La testina segue `render.percent`: a percent 0 non c'è posizione inventata.
  let lookAspect = '9 / 16';
  let lastClosed = false;

  function timecode(seconds) {
    const total = Math.max(0, Number(seconds) || 0);
    const m = Math.floor(total / 60);
    const s = Math.floor(total % 60);
    const tenths = Math.floor((total * 10) % 10);
    return pad2(m) + ':' + pad2(s) + '.' + tenths;
  }

  // "zoom 1.15" -> "zoom 1,15×", "lut warm" -> "LUT warm", "fade in 0.3s" -> "fade in 0,3 s"
  function prettyEffect(text) {
    let out = String(text || '').trim();
    if (!out) return '';
    out = out.replace(/^lut\b/i, 'LUT');
    out = out.replace(/(\d+)\.(\d+)/g, '$1,$2');
    out = out.replace(/(\d),(\d+)s\b/g, '$1,$2 s');
    if (/^zoom\s+[\d,]+/.test(out)) out += '×';
    return out;
  }

  function buildShotCells(shots) {
    const signature =
      jobIndex + '|' +
      shots
        .map((s) => s.index + ':' + s.dur + ':' + (s.thumb_ready ? 1 : 0) + ':' + (s.effects || []).join(';'))
        .join(',');
    if (el.reelStrip.dataset.signature === signature) return;

    el.reelStrip.dataset.signature = signature;
    el.reelStrip.textContent = '';

    shots.forEach((shot, position) => {
      const cell = document.createElement('span');
      cell.className = 'shot-cell';
      cell.style.flexGrow = String(Math.max(0.001, Number(shot.dur) || 0));
      cell.title =
        'Shot ' + pad2(position + 1) + ' · ' + timecode(shot.t_in) + '–' + timecode(shot.t_out);

      const thumb = document.createElement('span');
      thumb.className = 'cell-thumb';

      if (shot.thumb_ready && shot.thumb) {
        const img = document.createElement('img');
        img.alt = '';
        img.decoding = 'async';
        img.addEventListener('load', () => thumb.classList.add('is-loaded'));
        img.addEventListener('error', () => img.remove());
        img.src = scoped(String(shot.thumb));
        thumb.append(img);
      } else {
        // Senza immagine la card dice lo stesso cosa contiene: niente rettangolo grigio.
        thumb.classList.add('is-fallback');
        const fb = document.createElement('span');
        fb.className = 'cell-fallback';

        const src = document.createElement('span');
        src.className = 'cell-src';
        src.textContent = shot.source || 'clip ' + (position + 1);

        const dur = document.createElement('span');
        dur.className = 'cell-dur num';
        dur.textContent = num(Number(shot.dur) || 0, 1) + ' s';

        const note = document.createElement('span');
        note.className = 'cell-note';
        note.textContent = 'anteprima non disponibile';

        fb.append(src, dur, note);
        thumb.append(fb);
      }

      const idx = document.createElement('span');
      idx.className = 'cell-idx num';
      idx.textContent = pad2(position + 1);
      thumb.append(idx);

      const tags = document.createElement('span');
      tags.className = 'cell-tags';
      const effects = Array.isArray(shot.effects) ? shot.effects : [];
      const shown = effects.slice(0, 2);
      shown.forEach((effect, i) => {
        const tag = document.createElement('span');
        tag.className = 'cell-tag';
        let text = prettyEffect(effect);
        if (i === shown.length - 1 && effects.length > 2) {
          text += ' +' + (effects.length - 2);
        }
        tag.textContent = text;
        tag.title = effects.map(prettyEffect).join(' · ');
        tags.append(tag);
      });
      if (!effects.length) {
        const none = document.createElement('span');
        none.className = 'cell-tag';
        none.textContent = '—';
        tags.append(none);
      }

      cell.append(thumb, tags);
      el.reelStrip.append(cell);
    });
  }

  function setLookImage(shot) {
    const url = shot.thumb_ready && shot.thumb ? scoped(String(shot.thumb)) : '';
    el.lookEmpty.textContent = '';
    el.lookEmpty.classList.toggle('is-fallback', !url);

    if (!url) {
      el.lookImg.hidden = true;
      el.lookImg.removeAttribute('src');
      el.lookImg.classList.remove('is-loaded');
      el.lookFrame.hidden = false;
      el.lookEmpty.hidden = false;

      const block = document.createElement('span');
      block.className = 'look-fallback';

      const src = document.createElement('span');
      src.className = 'look-fallback-src';
      src.textContent = shot.source || 'clip ' + (Number(shot.index) + 1);

      const dur = document.createElement('span');
      dur.className = 'look-fallback-dur num';
      dur.textContent = num(Number(shot.dur) || 0, 1) + ' s';

      const note = document.createElement('span');
      note.className = 'look-fallback-note';
      note.textContent = 'anteprima non disponibile';

      block.append(src, dur, note);
      el.lookEmpty.append(block);
      return;
    }

    if (el.lookImg.getAttribute('src') === url && el.lookImg.classList.contains('is-loaded')) {
      el.lookEmpty.hidden = true;
      return;
    }
    el.lookEmpty.hidden = true;
    el.lookImg.classList.remove('is-loaded');
    el.lookImg.hidden = false;
    el.lookImg.src = url;
  }

  function paintMods(shot) {
    const effects = (Array.isArray(shot.effects) ? shot.effects : [])
      .map(prettyEffect)
      .filter(Boolean);

    el.mods.textContent = '';

    if (!effects.length) {
      el.modsEmpty.hidden = false;
      el.modsEmpty.textContent = 'Nessuna modifica a questo shot.';
      return;
    }

    el.modsEmpty.hidden = true;
    effects.forEach((effect) => {
      const item = document.createElement('li');
      item.className = 'mod';
      item.textContent = effect;
      el.mods.append(item);
    });
  }

  function paintRegia(state, closed) {
    const shots = Array.isArray(state.shots) ? state.shots : [];
    const render = state.render && typeof state.render === 'object' ? state.render : {};
    const percent = clamp(Number(render.percent) || 0, 0, 100);
    const total = shots.reduce((sum, shot) => sum + (Number(shot.dur) || 0), 0);

    el.regiaEmpty.hidden = shots.length > 0;
    el.regiaNow.hidden = shots.length === 0;
    el.regiaStripWrap.hidden = shots.length === 0;

    if (!shots.length) {
      el.regiaMeta.textContent = '';
      el.reelStrip.textContent = '';
      el.reelStrip.dataset.signature = '';
      el.reelHead.hidden = true;
      el.lookImg.hidden = true;
      el.lookImg.removeAttribute('src');
      el.mods.textContent = '';
      el.shotSpecs.hidden = true;
      return;
    }

    let active = -1;
    let notStarted = false;
    const t = total > 0 ? (percent / 100) * total : 0;
    if (percent > 0 && total > 0) {
      for (let i = 0; i < shots.length; i += 1) {
        active = i;
        if (t >= Number(shots[i].t_in) && t < Number(shots[i].t_out)) break;
      }
    } else if (shots.length) {
      // Encode fermo: si mostra il primo shot del piano (o l'ultimo, se chiuso),
      // dichiarandolo: è un shot reale, non una posizione inventata.
      active = closed ? shots.length - 1 : 0;
      notStarted = !closed;
    }

    el.regiaMeta.textContent =
      active >= 0
        ? 'shot ' + (active + 1) + ' di ' + shots.length +
          (percent > 0 ? '' : closed ? ' · ultimo' : '')
        : shots.length + ' shot';

    buildShotCells(shots);
    el.reelStrip.querySelectorAll('.is-now').forEach((node) => node.classList.remove('is-now'));

    const speed = String(render.speed || '').trim();
    const speedText = speed ? speed.replace('.', ',') : '';

    if (percent > 0 && active >= 0 && total > 0) {
      const shot = shots[active];
      const cell = el.reelStrip.children[active];
      const span = Number(shot.dur) || 0;
      const local = span > 0 ? clamp((t - Number(shot.t_in)) / span, 0, 1) : 0;
      const x = cell ? cell.offsetLeft + cell.offsetWidth * local : 0;

      el.reelHead.hidden = false;
      el.reelHead.style.transform = 'translateX(' + x.toFixed(1) + 'px)';
      if (cell) cell.classList.add('is-now');

      el.reelSpeed.textContent = speedText;
      el.reelSpeed.hidden = !speedText;
      el.reelSpeed.classList.toggle('is-left', x > el.reelStrip.offsetWidth - 60);

      el.regiaLine.textContent =
        'Encode al ' + Math.round(percent) + '% — ' +
        num(Number(render.out_time_sec) || t, 1) + ' s di ' + num(total, 1) + ' s' +
        (speedText ? ' · ' + speedText : '');
    } else {
      el.reelHead.hidden = true;
      el.reelSpeed.hidden = true;
      if (active >= 0) {
        const cell = el.reelStrip.children[active];
        if (cell) cell.classList.add('is-now');
      }
      el.regiaLine.textContent = closed
        ? 'Encode concluso: la testina non è più in movimento.'
        : 'Piano pronto, encode non iniziato.';
    }

    if (active >= 0) {
      const shot = shots[active];
      el.lookCaption.hidden = false;
      el.lookFrame.hidden = false;
      el.lookNote.hidden = true;
      el.lookStatus.hidden = !notStarted;
      el.lookStatus.textContent = notStarted
        ? 'Primo shot del piano (encode non iniziato)'
        : '';
      el.lookTime.textContent = timecode(shot.t_in);
      el.lookSource.textContent = shot.source || 'sorgente non dichiarata';
      el.lookFrame.style.aspectRatio = lookAspect;
      setLookImage(shot);
      paintMods(shot);
      el.shotSpecs.hidden = false;
      el.specDur.textContent = num(Number(shot.dur) || 0, 1) + ' s';
      el.specIn.textContent = timecode(shot.t_in);
      el.specOut.textContent = timecode(shot.t_out);
    } else {
      el.lookCaption.hidden = true;
      el.lookFrame.hidden = true;
      el.lookImg.hidden = true;
      el.lookImg.removeAttribute('src');
      el.lookImg.classList.remove('is-loaded');
      el.lookNote.hidden = false;
      el.lookStatus.hidden = true;
      el.lookNote.textContent = closed
        ? 'Nessuno shot sotto la testina: il lavoro è chiuso.'
        : "Nessuno shot sotto la testina: l'encode non è ancora iniziato.";
      el.mods.textContent = '';
      el.modsEmpty.hidden = false;
      el.modsEmpty.textContent = 'Nessuno shot selezionato.';
      el.shotSpecs.hidden = true;
    }

    el.reelScroll.setAttribute(
      'aria-label',
      'Sequenza di ' + shots.length + ' shot, ' + num(total, 1) + ' secondi' +
      (percent > 0 ? '; encode al ' + Math.round(percent) + '%' : '; encode fermo')
    );
  }

  el.lookImg.addEventListener('load', () => {
    el.lookImg.classList.add('is-loaded');
    if (el.lookImg.naturalWidth > 0 && el.lookImg.naturalHeight > 0) {
      lookAspect = el.lookImg.naturalWidth + ' / ' + el.lookImg.naturalHeight;
      el.lookFrame.style.aspectRatio = lookAspect;
    }
  });

  el.lookImg.addEventListener('error', () => {
    el.lookImg.hidden = true;
    el.lookImg.classList.remove('is-loaded');
    el.lookEmpty.hidden = false;
    el.lookEmpty.textContent = 'Fotogramma non disponibile per questo shot.';
  });

  /* --------------------------------------------------------- stima e piano */

  const STAGE_NAMES = {
    analysis: 'analisi',
    render: 'render',
    ingest: 'acquisizione',
    qc: 'controllo',
    encode: 'encode',
    deliver: 'consegna',
  };

  function paintEstimate(state) {
    const est = state.estimate && typeof state.estimate === 'object' ? state.estimate : null;
    const parts = est && est.parts && typeof est.parts === 'object' ? est.parts : {};
    // Una stima a zero non è una stima: meglio non mostrare il blocco.
    const usable = !!(est && (Number(est.seconds) > 0 || Object.keys(parts).length > 0));
    el.estimate.hidden = !usable;

    if (!usable) {
      el.estimateRows.textContent = '';
      return;
    }

    const seconds = Number(est.seconds) || 0;
    const low = Number(est.low) || seconds;
    const high = Number(est.high) || seconds;
    const plan = state.plan_choice && typeof state.plan_choice === 'object' ? state.plan_choice : {};
    const budget = Number(plan.budget) || 0;

    el.estimateTotal.textContent =
      num(seconds, 0) + ' s (' + num(low, 0) + '–' + num(high, 0) + ')' +
      (budget > 0 ? ' · budget ' + num(budget, 0) + ' s' : '');

    const rows = Object.keys(parts)
      .map((key) => [key, Number(parts[key]) || 0])
      .filter(([, value]) => value > 0);
    const sum = rows.reduce((total, [, value]) => total + value, 0) || 1;

    el.estimateRows.textContent = '';
    rows.forEach(([key, value]) => {
      const row = document.createElement('div');
      row.className = 'estimate-row';

      const name = document.createElement('span');
      name.className = 'estimate-name';
      name.textContent = STAGE_NAMES[key] || key;

      const track = document.createElement('span');
      track.className = 'estimate-track';
      const fill = document.createElement('i');
      fill.className = 'estimate-fill';
      fill.style.width = Math.max(2, (value / sum) * 100).toFixed(1) + '%';
      track.append(fill);

      const val = document.createElement('span');
      val.className = 'estimate-value num';
      val.textContent = num(value, 0) + ' s';

      row.append(name, track, val);
      el.estimateRows.append(row);
    });

    const calibrated = est.calibrated !== false;
    el.estimateNote.hidden = calibrated;
    el.estimateNote.textContent = calibrated ? '' : 'stima non calibrata: nessuna misura precedente';

    const notes = (Array.isArray(est.notes) ? est.notes : []).filter((item) => String(item || '').trim());
    el.estimateNotes.hidden = !notes.length;
    el.estimateNotes.textContent = notes.join(' · ');
  }

  const planPicked = new Set();
  let planPicking = false;
  let planJob = null;

  function planNumbers(pc, est) {
    const seconds = Number(est && est.seconds) || 0;
    const high = Number(est && est.high) || seconds;
    const saved = Math.max(0, Number(pc && pc.saved_seconds_estimate) || 0);
    const fast = saved > 0 ? Math.max(1, seconds - saved) : 0;
    const ratio = seconds > 0 ? saved / seconds : 0;
    const traded = (Array.isArray(pc && pc.traded) ? pc.traded : []).filter(
      (item) => String(item || '').trim()
    );
    return { seconds, high, saved, fast, ratio, traded, recommend: ratio >= 0.35 ? 'fast' : 'full' };
  }

  function planCard(spec) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'plan-card' + (spec.recommend ? ' is-suggested' : '');
    card.dataset.choice = spec.choice;

    if (spec.recommend) {
      const badge = document.createElement('span');
      badge.className = 'plan-badge';
      badge.textContent = 'Consigliata';
      card.append(badge);
    }

    const name = document.createElement('span');
    name.className = 'plan-name';
    name.textContent = spec.name;

    const time = document.createElement('span');
    time.className = 'plan-time num';
    time.textContent = spec.time;

    const label = document.createElement('span');
    label.className = 'plan-keeps';
    label.textContent = spec.label;

    const list = document.createElement('ul');
    list.className = 'plan-list';
    spec.items.forEach((item) => {
      const li = document.createElement('li');
      li.textContent = item;
      list.append(li);
    });

    const line = document.createElement('span');
    line.className = 'plan-line';
    line.textContent = spec.line;

    card.append(name, time, label, list, line);
    card.addEventListener('click', () => pickPlan(spec.choice, card));
    return card;
  }

  function buildPlanCards(pc, est) {
    const n = planNumbers(pc, est);
    const low = Number(est && est.low) || n.seconds;

    el.planSub.textContent =
      'Stima ' + num(n.seconds, 0) + ' s (' + num(low, 0) + '–' + num(n.high, 0) + ')' +
      (Number(pc && pc.budget) > 0 ? ' · budget ' + num(Number(pc.budget), 0) + ' s' : '') +
      (est && est.calibrated === false ? ' · stima non calibrata' : '');

    el.planCards.textContent = '';
    el.planCards.append(
      planCard({
        choice: 'full',
        name: 'Completa',
        time: '~' + num(n.high, 0) + ' s',
        label: 'Cosa mantiene',
        items: n.traded.length ? n.traded : ['tutto il piano'],
        line: 'Nessuna rinuncia: il piano resta intero.',
        recommend: n.recommend === 'full',
      }),
      planCard({
        choice: 'fast',
        name: 'Rapida',
        time: n.fast ? '~' + num(n.fast, 0) + ' s' : 'risparmio non dichiarato',
        label: 'Cosa cede',
        items: n.traded.length ? n.traded : ['niente di dichiarato'],
        line: "Tutto il resto resta com'è: cambia solo ciò che è elencato.",
        recommend: n.recommend === 'fast',
      })
    );
  }

  function openPlanGate(pc, est) {
    if (!el.frameView.hidden) closeFrame({ immediate: true });
    planJob = jobIndex;
    buildPlanCards(pc, est);
    el.planError.textContent = '';

    if (!el.planGate.open) {
      document.documentElement.classList.add('has-gate');
      el.planGate.showModal();
    }

    const suggested =
      el.planCards.querySelector('.plan-card.is-suggested') ||
      el.planCards.querySelector('.plan-card');
    if (suggested) suggested.focus();
  }

  function closePlanGate() {
    planJob = null;
    planPicking = false;
    if (el.planGate.open) el.planGate.close();
    document.documentElement.classList.remove('has-gate');
  }

  function paintPlanChoice(state) {
    const pc = state.plan_choice && typeof state.plan_choice === 'object' ? state.plan_choice : null;
    const pending = !!(pc && pc.pending === true && !pc.chosen);

    if (!pending) {
      if (el.planGate.open) closePlanGate();
      return;
    }
    if (planPicking || planPicked.has(jobIndex)) return;
    if (el.fontGate.open) return; // un cancello alla volta: prima i font
    if (el.planGate.open && planJob === jobIndex) return;

    openPlanGate(pc, state.estimate);
  }

  async function pickPlan(choice, card) {
    if (planPicking) return;
    planPicking = true;
    el.planError.textContent = '';
    card.setAttribute('aria-busy', 'true');
    el.planCards.querySelectorAll('.plan-card').forEach((node) => {
      node.disabled = true;
    });

    try {
      const base = scoped('/api/plan-choice');
      const url =
        base + (base.indexOf('?') === -1 ? '?' : '&') + 'choice=' + encodeURIComponent(choice);
      const response = await fetch(url, { method: 'POST' });
      const result = await response.json();

      if (result && result.ok === true) {
        planPicked.add(jobIndex);
        planPicking = false;
        closePlanGate();
        showPlanFlash(choice, lastState);
        await tick();
        return;
      }
      el.planError.textContent =
        (result && result.reason) || 'La scelta non è stata registrata: riprova.';
    } catch (error) {
      el.planError.textContent = 'La scelta non è stata registrata: il server non risponde.';
    }

    planPicking = false;
    card.removeAttribute('aria-busy');
    el.planCards.querySelectorAll('.plan-card').forEach((node) => {
      node.disabled = false;
    });
    card.focus();
  }

  function showPlanFlash(choice, state) {
    if (!state) return;
    const pc = state.plan_choice || {};
    const est = state.estimate || {};
    const n = planNumbers(pc, est);

    el.estimateFlash.textContent =
      choice === 'fast'
        ? 'Versione rapida: −' + num(n.saved, 0) + ' s stimati' +
          (n.traded.length ? ', ' + n.traded.join(', ') : '') + '.'
        : 'Versione completa: ' + num(n.seconds, 0) + ' s stimati, nessuna rinuncia.';
    el.estimateFlash.hidden = false;
  }

  el.planGate.addEventListener('cancel', (event) => {
    event.preventDefault();
  });

  /* ---------------------------------------------------------------------- live */

  // Il live non è un video che cresce: sono chunk veri, messi in fila. Due
  // elementi si alternano, il prossimo è precaricato, e il cambio avviene solo
  // sulla fine del pezzo in corso — mai a metà.
  const liveVideos = [el.liveVideoA, el.liveVideoB];
  let liveJob = null;
  let liveStarted = false;
  let liveSlot = 0;
  let liveChunk = -1;
  let liveMaster = false;
  let liveWaiting = false;
  let liveChunks = [];
  let liveTotal = 0;
  let liveMuted = true;
  let liveBlocked = false;

  const liveUrl = (chunk) => scoped(String(chunk && chunk.url ? chunk.url : ''));

  function applyMute() {
    liveVideos.forEach((video) => {
      video.muted = liveMuted;
    });
  }

  function onLiveBlocked() {
    // Il browser ha rifiutato la partenza: si dice, e si offre un pulsante.
    liveBlocked = true;
    liveStarted = false;
    el.liveCover.hidden = false;
    el.liveStart.hidden = false;
    el.liveStart.disabled = false;
    el.liveStart.textContent = 'Premi per guardare';
    el.liveCoverNote.textContent = 'Il browser ha bloccato la partenza: premi per guardare.';
    el.liveStatus.textContent = '';
  }

  function liveShow(slot) {
    liveSlot = slot;
    liveVideos.forEach((video, index) => video.classList.toggle('is-on', index === slot));
  }

  function livePlay(video) {
    video.muted = liveMuted;
    const started = video.play();
    if (started && typeof started.catch === 'function') {
      started.catch(() => {
        if (liveStarted && video === liveVideos[liveSlot] && !video.ended) onLiveBlocked();
      });
    }
  }

  function playChunk(index) {
    const chunk = liveChunks[index];
    if (!chunk) return;
    const video = liveVideos[liveSlot];
    const url = liveUrl(chunk);
    if (video.getAttribute('src') !== url) video.src = url;
    try {
      video.currentTime = 0;
    } catch (error) {
      /* il metadata non c'è ancora: si parte da capo comunque */
    }
    liveChunk = index;
    liveMaster = false;
    liveWaiting = false;
    el.liveWait.hidden = true;
    el.liveNow.textContent = 'chunk ' + (index + 1) + ' di ' + liveTotal;
    el.liveStatus.textContent = 'In riproduzione.';
    livePlay(video);
  }

  function nextChunk(index) {
    const chunk = liveChunks[index];
    if (!chunk) return;
    const old = liveVideos[liveSlot];
    const other = liveVideos[1 - liveSlot];
    const url = liveUrl(chunk);
    if (other.getAttribute('src') !== url) other.src = url;
    try {
      other.currentTime = 0;
    } catch (error) {
      /* riparte dall'inizio: nessun salto */
    }
    liveShow(1 - liveSlot);
    liveChunk = index;
    liveMaster = false;
    liveWaiting = false;
    el.liveWait.hidden = true;
    el.liveNow.textContent = 'chunk ' + (index + 1) + ' di ' + liveTotal;
    el.liveStatus.textContent = 'In riproduzione.';
    livePlay(other);
    old.pause();
  }

  function preloadChunk(index) {
    const chunk = liveChunks[index];
    if (!chunk || !chunk.ready) return;
    const other = liveVideos[1 - liveSlot];
    const url = liveUrl(chunk);
    if (other.getAttribute('src') !== url) {
      other.preload = 'auto';
      other.src = url;
    }
  }

  function playMaster() {
    const last = liveChunks[liveChunks.length - 1];
    const old = liveVideos[liveSlot];
    const other = liveVideos[1 - liveSlot];
    const seek = Number(last && last.t_out) || 0;

    other.src = scoped('/result');
    const start = () => {
      try {
        other.currentTime = seek;
      } catch (error) {
        /* parte dall'inizio del master: meglio che fermo */
      }
      livePlay(other);
    };
    if (other.readyState >= 1) start();
    else other.addEventListener('loadedmetadata', start, { once: true });

    liveShow(1 - liveSlot);
    old.pause();
    liveMaster = true;
    liveWaiting = false;
    el.liveWait.hidden = true;
    el.liveNow.textContent = 'master';
    el.liveStatus.textContent = 'Master in riproduzione.';
  }

  function onLiveEnded(event) {
    if (!liveStarted) return;
    if (event.target !== liveVideos[liveSlot]) return;

    const next = liveChunks[liveChunk + 1];
    if (next && next.ready) {
      nextChunk(liveChunk + 1);
      return;
    }
    if (liveChunk >= liveChunks.length - 1 && lastState && lastState.master_ready === true) {
      playMaster();
      return;
    }

    liveWaiting = true;
    el.liveWait.hidden = false;
    el.liveStatus.textContent = next
      ? 'Il prossimo pezzo non è ancora pronto.'
      : 'Il master non è ancora pronto.';
  }

  function stopLive() {
    liveStarted = false;
    liveVideos.forEach((video) => {
      video.pause();
      video.removeAttribute('src');
      video.classList.remove('is-on');
      try {
        video.load();
      } catch (error) {
        /* niente da caricare */
      }
    });
    liveSlot = 0;
    liveChunk = -1;
    liveMaster = false;
    liveWaiting = false;
    el.liveWait.hidden = true;
    el.liveCover.hidden = false;
    el.liveNow.textContent = '';
    el.liveStatus.textContent = '';
  }

  function paintLive(state, closed) {
    const live = state.live && typeof state.live === 'object' ? state.live : null;

    if (!live) {
      el.live.hidden = true;
      return;
    }
    if (liveJob !== jobIndex) {
      stopLive();
      liveJob = jobIndex;
    }

    const chunks = (Array.isArray(live.chunks) ? live.chunks : []).filter(
      (chunk) => chunk && chunk.url
    );
    const total = Number(live.total) || chunks.length;
    const ready = Number(live.ready) || chunks.filter((chunk) => chunk.ready).length;

    el.live.hidden = closed && !liveStarted;
    el.liveEmpty.hidden = chunks.length > 0;
    el.liveBody.hidden = chunks.length === 0;
    el.liveMeta.textContent = chunks.length ? ready + ' di ' + total + ' pronti' : '';
    el.liveFill.style.width = total > 0 ? Math.min(100, (ready / total) * 100) + '%' : '0%';

    if (!chunks.length) {
      if (liveStarted) stopLive();
      el.liveCover.hidden = false;
      return;
    }

    liveChunks = chunks;
    liveTotal = total;

    // Lavoro chiuso e nessuno sta guardando: niente autoplay nascosto.
    if (closed && !liveStarted) {
      el.liveCover.hidden = false;
      el.liveStart.hidden = true;
      el.liveAudio.hidden = true;
      return;
    }

    if (!liveStarted) {
      if (liveBlocked) {
        el.liveCover.hidden = false;
        el.liveStart.hidden = false;
        el.liveStart.disabled = false;
        el.liveStart.textContent = 'Premi per guardare';
        el.liveCoverNote.textContent = 'Il browser ha bloccato la partenza: premi per guardare.';
        el.liveAudio.hidden = true;
        return;
      }
      if (ready > 0) {
        startLive();
        return;
      }
      el.liveCover.hidden = false;
      el.liveStart.hidden = true;
      el.liveCoverNote.textContent = 'Aspetto che il render scriva il primo pezzo.';
      el.liveStatus.textContent = '';
      el.liveAudio.hidden = true;
      return;
    }

    el.liveCover.hidden = true;
    el.liveAudio.hidden = false;
    el.liveAudio.disabled = !liveMuted;
    el.liveAudio.textContent = liveMuted ? 'Attiva audio' : 'Audio attivo';

    if (liveWaiting) {
      const next = liveChunks[liveChunk + 1];
      if (next && next.ready) {
        nextChunk(liveChunk + 1);
        return;
      }
      if (liveChunk >= liveChunks.length - 1 && state.master_ready === true) {
        playMaster();
        return;
      }
      el.liveWait.hidden = false;
      el.liveStatus.textContent = next
        ? 'Il prossimo pezzo non è ancora pronto.'
        : 'Il master non è ancora pronto.';
      return;
    }

    if (!liveMaster) {
      preloadChunk(liveChunk + 1);
      el.liveNow.textContent = 'chunk ' + (liveChunk + 1) + ' di ' + liveTotal;
    }
  }

  function startLive() {
    const first = liveChunks.findIndex((chunk) => chunk.ready);
    if (first < 0) return;
    liveStarted = true;
    liveBlocked = false;
    liveJob = jobIndex;
    liveShow(0);
    applyMute();
    el.liveCover.hidden = true;
    el.liveAudio.hidden = false;
    el.liveAudio.disabled = !liveMuted;
    el.liveAudio.textContent = liveMuted ? 'Attiva audio' : 'Audio attivo';
    playChunk(first);
    preloadChunk(first + 1);
  }

  // Il pulsante serve solo quando il browser blocca la partenza.
  el.liveStart.addEventListener('click', () => {
    liveBlocked = false;
    if (!liveChunks.length) return;
    startLive();
  });

  el.liveAudio.addEventListener('click', () => {
    if (!liveMuted) return;
    liveMuted = false;
    applyMute();
    el.liveAudio.disabled = true;
    el.liveAudio.textContent = 'Audio attivo';
  });

  liveVideos.forEach((video) => {
    video.addEventListener('ended', onLiveEnded);
    video.addEventListener('pause', () => {
      if (!liveStarted || video !== liveVideos[liveSlot] || video.ended) return;
      el.liveStatus.textContent = 'In pausa.';
    });
    video.addEventListener('play', () => {
      if (!liveStarted || video !== liveVideos[liveSlot]) return;
      el.liveStatus.textContent = liveMaster ? 'Master in riproduzione.' : 'In riproduzione.';
    });
  });


  // Il cancello dei font: si apre solo se il lavoro dichiara una scelta da fare,
  // non si chiude con Esc né col clic fuori, e sparisce quando la scelta è presa.
  const pickedJobs = new Set();
  let gateJob = null;
  let gatePicking = false;

  function fontCard(candidate, index, word) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'font-card';
    card.dataset.family = String(candidate.family || '');

    const sample = document.createElement('span');
    sample.className = 'font-sample';

    const img = document.createElement('img');
    img.alt = 'Campione: ' + word + ' in ' + (candidate.family || 'carattere senza nome');
    img.decoding = 'async';
    img.addEventListener('load', () => sample.classList.add('is-loaded'));
    img.addEventListener('error', () => {
      sample.textContent = '';
      const note = document.createElement('span');
      note.className = 'font-sample-note';
      note.textContent = 'Campione non disponibile';
      sample.append(note);
    });
    sample.append(img);
    if (candidate.sample) {
      // Il campione è un percorso servito dal server: va tenuto sul lavoro scelto.
      img.src = scoped(String(candidate.sample));
    } else {
      sample.textContent = '';
      const note = document.createElement('span');
      note.className = 'font-sample-note';
      note.textContent = 'Campione non disponibile';
      sample.append(note);
    }

    const body = document.createElement('span');
    body.className = 'font-body';

    const family = document.createElement('span');
    family.className = 'font-family';
    family.textContent = candidate.family || 'Carattere senza nome';

    const licence = document.createElement('span');
    licence.className = 'font-licence';
    licence.textContent = candidate.licence ? String(candidate.licence) : 'licenza non dichiarata';

    const why = document.createElement('span');
    why.className = 'font-why';
    why.textContent = candidate.because
      ? String(candidate.because)
      : 'Nessun motivo dichiarato per questo carattere.';

    body.append(family, licence, why);
    card.append(sample, body);
    card.addEventListener('click', () => pickFont(String(candidate.family || ''), card));
    return card;
  }

  function buildGate(font) {
    el.fontReq.textContent = String(font.requirement || '').trim() || 'nessuna indicazione dal lavoro';
    el.fontPool.textContent = font.pool === 'local' ? 'Dal computer' : 'Dal catalogo';
    el.gateError.textContent = '';

    const candidates = Array.isArray(font.candidates) ? font.candidates : [];
    const word = String(font.sample || 'prova');
    const signature = candidates.map((c) => c.family + '|' + c.sample).join('~');
    if (el.fontCards.dataset.signature === signature) return;

    el.fontCards.dataset.signature = signature;
    el.fontCards.textContent = '';
    candidates.forEach((candidate, index) => {
      el.fontCards.append(fontCard(candidate, index, word));
    });
  }

  function openGate(font) {
    if (!el.frameView.hidden) closeFrame({ immediate: true });
    if (el.planGate.open) return; // un cancello alla volta
    gateJob = jobIndex;
    buildGate(font);
    if (!el.fontGate.open) {
      document.documentElement.classList.add('has-gate');
      el.fontGate.showModal();
    }
    const first = el.fontCards.querySelector('.font-card');
    if (first) first.focus();
  }

  function closeGate() {
    gateJob = null;
    gatePicking = false;
    if (el.fontGate.open) el.fontGate.close();
    document.documentElement.classList.remove('has-gate');
  }

  function paintFontGate(state) {
    const font = state && state.font;
    const pending = !!(font && font.pending === true && !font.chosen);

    if (!pending) {
      if (el.fontGate.open) closeGate();
      return;
    }
    if (gatePicking || pickedJobs.has(jobIndex)) return;

    if (el.fontGate.open && gateJob === jobIndex) {
      buildGate(font);
      return;
    }
    openGate(font);
  }

  async function pickFont(family, card) {
    if (gatePicking || !family) return;
    gatePicking = true;
    el.gateError.textContent = '';
    card.setAttribute('aria-busy', 'true');
    el.fontCards.querySelectorAll('.font-card').forEach((node) => { node.disabled = true; });

    try {
      const base = scoped('/api/font-pick');
      const url = base + (base.indexOf('?') === -1 ? '?' : '&') + 'family=' + encodeURIComponent(family);
      const response = await fetch(url, { method: 'POST' });
      const result = await response.json();

      if (result && result.ok === true) {
        pickedJobs.add(jobIndex);
        gatePicking = false;
        closeGate();
        await tick();
        return;
      }
      el.gateError.textContent =
        (result && result.reason) || 'La scelta non è stata registrata: riprova.';
    } catch (error) {
      el.gateError.textContent = 'La scelta non è stata registrata: il server non risponde.';
    }

    gatePicking = false;
    card.removeAttribute('aria-busy');
    el.fontCards.querySelectorAll('.font-card').forEach((node) => { node.disabled = false; });
    card.focus();
  }

  // Il cancello non si scavalca: nessuna chiusura con Esc, nessun clic di sfondo.
  el.fontGate.addEventListener('cancel', (event) => {
    event.preventDefault();
  });

  /* ------------------------------------------------------------------ lavori */

  let jobNodes = [];

  function buildJobs(jobs) {
    el.jobsList.textContent = '';
    jobNodes = jobs.map((job) => {
      const item = document.createElement('li');
      const link = document.createElement('a');
      link.className = 'job';
      link.href = '?job=' + job.index;
      link.dataset.index = String(job.index);

      const idx = document.createElement('span');
      idx.className = 'job-idx num';
      idx.textContent = pad2((Number(job.index) || 0) + 1);

      const name = document.createElement('span');
      name.className = 'job-name';

      const pct = document.createElement('span');
      pct.className = 'job-pct num';

      const bar = document.createElement('span');
      bar.className = 'job-bar';
      const fill = document.createElement('i');
      bar.append(fill);

      link.append(idx, name, pct, bar);
      item.append(link);
      el.jobsList.append(item);
      return { link, name, pct, fill };
    });
  }

  function paintJobs(state) {
    const jobs = Array.isArray(state.jobs) ? state.jobs : [];
    const many = jobs.length > 1;
    el.jobs.hidden = !many;

    if (!many) {
      if (jobNodes.length) {
        el.jobsList.textContent = '';
        el.jobs.dataset.signature = '';
        jobNodes = [];
      }
      return;
    }

    const chosen = Number.isFinite(Number(state.selected)) ? Number(state.selected) : 0;
    const signature = jobs.map((job) => job.index + ':' + job.job).join('|');
    if (el.jobs.dataset.signature !== signature) {
      el.jobs.dataset.signature = signature;
      buildJobs(jobs);
    }

    el.jobsCount.textContent = pad2(jobs.length);
    jobNodes.forEach((node, position) => {
      const job = jobs[position];
      if (!job) return;
      const percent = Math.round(clamp(Number(job.percent) || 0, 0, 100));
      const prompt = String(job.prompt || '').trim();
      node.name.textContent = prompt || 'Lavoro senza titolo';
      node.name.title = prompt;
      node.pct.textContent = percent + '%';
      node.fill.style.width = percent + '%';
      node.link.title = (prompt || 'Lavoro senza titolo') + ' — ' + percent + '%';
      if (position === chosen) node.link.setAttribute('aria-current', 'true');
      else node.link.removeAttribute('aria-current');
    });
  }

  function resetForJob() {
    // Cambiare lavoro non deve lasciare in pagina i fotogrammi o il video di prima.
    closeFrame({ immediate: true });
    if (el.planGate.open) closePlanGate();
    stopLive();
    el.estimateFlash.hidden = true;
    el.estimateFlash.textContent = '';
    hasDrawn = false;
    el.frames.dataset.signature = '';
    el.resultBody.dataset.signature = '';
    el.phases.dataset.signature = '';
  }

  function selectJob(next) {
    const index = Number(next);
    if (!Number.isFinite(index) || index < 0 || index === jobIndex) return;
    jobIndex = index;
    const url = new URL(window.location.href);
    url.searchParams.set('job', String(index));
    window.history.pushState({ job: index }, '', url);
    resetForJob();
    tick();
  }

  el.jobsList.addEventListener('click', (event) => {
    const link = event.target.closest('a.job');
    if (!link) return;
    event.preventDefault();
    selectJob(link.dataset.index);
  });

  window.addEventListener('popstate', () => {
    const index = readJobParam();
    if (index === jobIndex) return;
    jobIndex = index;
    resetForJob();
    tick();
  });
  /* ------------------------------------------------------------------ stato */

  function render(state) {
    if (approving) return;
    const phases = Array.isArray(state.phases) ? state.phases : [];
    const frames = Array.isArray(state.frames) ? state.frames : [];
    const percent = clamp(Number(state.percent) || 0, 0, 100);

    // Una lettura transitoria (file scritto a metà, lavoro appena sparito) non
    // deve cancellare quello che si sta già guardando.
    const blank =
      !String(state.prompt || '').trim() &&
      phases.length === 0 &&
      frames.length === 0 &&
      !state.current &&
      percent === 0 &&
      state.master_ready !== true;
    if (blank && hasDrawn) return;
    if (!blank) hasDrawn = true;

    // Il server dice quale lavoro ha davvero servito: la pagina lo segue.
    if (Number.isFinite(Number(state.selected))) jobIndex = Number(state.selected);
    paintJobs(state);
    lastState = state;
    paintJournal(state);

    const finished = !state.current && percent >= 99.9;
    const ready = state.master_ready === true;
    // "Concluso" non si deduce dalla percentuale: un lavoro fermato può restare
    // a 100. L'esito lo dice il server, e senza campo si torna al comportamento
    // di prima (finestra chiusa = concluso).
    const verdict = outcomeOf(state, finished);
    const closed =
      verdict === 'done' ||
      verdict === 'stopped' ||
      verdict === 'failed' ||
      verdict === 'incomplete';
    const approved = state.approved === true;

    body.classList.toggle('is-done', verdict === 'done');
    body.classList.toggle('is-stopped', verdict === 'stopped');
    body.classList.toggle('is-failed', verdict === 'failed');
    body.classList.toggle('is-incomplete', verdict === 'incomplete');
    body.classList.toggle('is-idle', verdict === 'idle');
    body.classList.remove('is-offline');

    lastClosed = closed;
    paintRegia(state, closed);
    paintEstimate(state);
    paintLive(state, closed);

    const prompt = String(state.prompt || '').trim();
    el.jobTitle.textContent = prompt || 'Lavoro senza titolo';
    document.title = 'vx — ' + (prompt ? prompt.slice(0, 70) : 'lavoro in corso');

    el.stateWord.textContent = OUTCOME_WORD[verdict] || 'In corso';
    el.jobClock.textContent = mmss(state.elapsed_sec);

    const clips = Number(state.clips) || 0;
    el.clipChip.textContent = clips === 1 ? '1 clip' : clips > 1 ? clips + ' clip' : 'clip non dichiarate';

    setPercent(percent);
    el.bar.setAttribute('aria-valuenow', String(Math.round(percent)));
    el.fill.style.width = percent + '%';
    el.barHead.style.left = percent + '%';
    el.barHead.classList.toggle('is-on', percent > 0);

    const activeIndex = phases.findIndex((p) => p.key === state.current);
    el.heroLabel.textContent = 'avanzamento';
    if (finished) {
      el.heroSub.textContent = pad2(phases.length) + ' fasi su ' + pad2(phases.length);
    } else if (activeIndex >= 0) {
      el.heroSub.textContent = 'fase ' + pad2(activeIndex + 1) + ' di ' + pad2(phases.length);
    } else {
      el.heroSub.textContent = 'in attesa del lavoro';
    }

    // sequenza
    const phaseSignature = phases.map((p) => p.key).join('|');
    if (el.phases.dataset.signature !== phaseSignature) {
      el.phases.dataset.signature = phaseSignature;
      buildPhases(phases);
    }
    paintPhases(phases, activeIndex, finished);
    el.phasesEmpty.hidden = phases.length > 0;
    el.railSub.textContent =
      phases.length === 0 ? 'nessuna fase' : pad2(phases.length) + ' fasi';

    // ora / esito
    if (closed) {
      el.now.hidden = true;
      el.verdict.hidden = false;
      el.verdict.className = 'verdict is-' + verdict;
      el.verdictMark.textContent = OUTCOME_MARK[verdict] || '–';
      el.verdictTitle.textContent = OUTCOME_TITLE[verdict] || 'Lavoro concluso';
      el.verdictText.textContent = verdictSentence(verdict, state.note);
      el.verdictMeta.textContent =
        frames.length +
        (frames.length === 1 ? ' fotogramma' : ' fotogrammi') +
        ' · ' +
        mmss(state.elapsed_sec) +
        ' di lavoro' +
        (approved ? ' · approvati' : '');
    } else {
      el.verdict.hidden = true;
      el.now.hidden = false;
      const active = phases.find((p) => p.key === state.current);
      if (state.current) {
        el.nowText.textContent = active
          ? String(active.label)
          : 'Fase «' + state.current + '» in corso: la descrizione non è arrivata.';
      } else {
        el.nowText.textContent =
          'In attesa: il lavoro non ha ancora dichiarato la fase in corso.';
      }
      const note = String(state.note || '').trim();
      el.nowNote.textContent = note;
      el.nowNote.hidden = !note;
    }

    // fotogrammi e approvazione
    paintFrames(frames, finished);
    paintResult(state, closed);
    paintFontGate(state);
    paintPlanChoice(state);
    paintApproval(state);
  }

  /* ------------------------------------------------------------------ rete */

  let inflight = false;
  let approving = false;
  let hasDrawn = false;
  let lastState = null;

  // La testina è posizionata in pixel: se cambia la larghezza va ricalcolata.
  window.addEventListener('resize', () => {
    if (lastState) paintRegia(lastState, lastClosed);
  });

  async function tick() {
    if (inflight) return;
    inflight = true;
    try {
      const response = await fetch(scoped('/api/state'), { cache: 'no-store' });
      if (!response.ok) throw new Error('stato ' + response.status);
      render(await response.json());
    } catch (error) {
      body.classList.add('is-offline');
      el.stateWord.textContent = 'Senza collegamento';
      el.verdict.hidden = true;
      el.now.hidden = false;
      el.nowText.textContent =
        'Non riesco a leggere lo stato del lavoro: il server locale non risponde.';
      el.nowNote.hidden = true;
    } finally {
      inflight = false;
    }
  }

  el.approve.addEventListener('click', async () => {
    if (el.approve.disabled || el.approve.getAttribute('aria-busy') === 'true') return;
    approving = true;
    el.approve.setAttribute('aria-busy', 'true');
    el.approve.disabled = true;
    el.approveFlash.className = 'approve-flash';
    el.approveFlash.textContent = "Registro l'approvazione…";

    try {
      const response = await fetch(scoped('/api/approve'), { method: 'POST' });
      const result = await response.json();
      if (result && result.ok === true) {
        flash(el.approveFlash, 'Approvazione registrata.', 'ok', 'approve-flash');
      } else {
        flash(
          el.approveFlash,
          (result && result.reason) || "Il server non ha registrato l'approvazione.",
          'warn',
          'approve-flash'
        );
      }
    } catch (error) {
      flash(el.approveFlash, 'Non ho potuto approvare: il server non risponde.', 'warn', 'approve-flash');
    } finally {
      approving = false;
      el.approve.removeAttribute('aria-busy');
      await tick();
    }
  });

  reduce.addEventListener('change', () => {
    if (reduce.matches) el.frames.querySelectorAll('.shot').forEach((card) => {
      card.style.setProperty('--rx', '0deg');
      card.style.setProperty('--ry', '0deg');
    });
  });

  tick();
  window.setInterval(tick, 1200);
})();
