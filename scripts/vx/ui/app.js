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
    jobStamp: $('jobStamp'),
    jobStampFile: $('jobStampFile'),
    jobClock: $('jobClock'),
    sessionLine: $('sessionLine'),
    sessionChip: $('sessionChip'),
    sessionSay: $('sessionSay'),
    stateWord: $('stateWord'),
    clipChip: $('clipChip'),
    chipWrap: $('chipWrap'),
    stageHead: $('stageHead'),
    emptyWork: $('emptyWork'),
    emptyReason: $('emptyReason'),
    hero: $('hero'),
    percent: $('percent'),
    heroUnit: $('heroUnit'),
    heroLabel: $('heroLabel'),
    heroSub: $('heroSub'),
    bar: $('bar'),
    barTrack: $('barTrack'),
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
    contract: $('contract'),
    contractSub: $('contractSub'),
    contractScore: $('contractScore'),
    contractScoreWord: $('contractScoreWord'),
    contractStrip: $('contractStrip'),
    contractLog: $('contractLog'),
    contractNote: $('contractNote'),
    contractCorrupt: $('contractCorrupt'),
    contractList: $('contractList'),
    scaletta: $('scaletta'),
    scalettaMeta: $('scalettaMeta'),
    scalettaWarn: $('scalettaWarn'),
    scalettaWarnText: $('scalettaWarnText'),
    scalettaList: $('scalettaList'),
    scalettaDeclined: $('scalettaDeclined'),
    scalettaDeclinedList: $('scalettaDeclinedList'),
    motore: $('motore'),
    motoreSentinel: $('motoreSentinel'),
    motoreImg: $('motoreImg'),
    motoreState: $('motoreState'),
    motoreHash: $('motoreHash'),
    motoreBadge: $('motoreBadge'),
    motoreFill: $('motoreFill'),
    motoreStrip: $('motoreStrip'),
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
    estimateActual: $('estimateActual'),
    estimateMape: $('estimateMape'),
    estimateFlash: $('estimateFlash'),
    voteCard: $('voteCard'),
    voteAsk: $('voteAsk'),
    voteMeta: $('voteMeta'),
    voteSub: $('voteSub'),
    voteTitle: $('voteTitle'),
    voteScale: $('voteScale'),
    voteHint: $('voteHint'),
    voteChips: $('voteChips'),
    voteNote: $('voteNote'),
    voteSend: $('voteSend'),
    voteClose: $('voteClose'),
    voteFlash: $('voteFlash'),
    voteError: $('voteError'),
    voteErrorText: $('voteErrorText'),
    voteDone: $('voteDone'),
    voteDoneNum: $('voteDoneNum'),
    voteDoneSay: $('voteDoneSay'),
    voteDoneTicks: $('voteDoneTicks'),
    voteDoneNote: $('voteDoneNote'),
    gateStop: $('gateStop'),
    gateStopQ: $('gateStopQ'),
    gateStopOpts: $('gateStopOpts'),
    gateStopMsg: $('gateStopMsg'),
    nowHead: $('nowHead'),
    nowLabel: $('nowLabel'),
    nowClock: $('nowClock'),
    life: $('life'),
    lifeTicks: $('lifeTicks'),
    lifeSay: $('lifeSay'),
    heroStall: $('heroStall'),
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
    decisionsList: $('decisionsList'),
    decisionsMeta: $('decisionsMeta'),
    decisionsEmpty: $('decisionsEmpty'),
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

  // `?session=<id>` sceglie quale sessione guardare; senza, il server serve
  // l'ultima. Va tenuto su ogni richiesta, come l'indice del lavoro.
  const sessionParam = new URLSearchParams(window.location.search).get('session');

  const scoped = (url) => {
    let out = url;
    const glue = () => (out.indexOf('?') === -1 ? '?' : '&');
    if (jobIndex !== null) out += glue() + 'job=' + jobIndex;
    if (sessionParam) out += glue() + 'session=' + encodeURIComponent(sessionParam);
    return out;
  };

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

  // Un cancello è una domanda **al lavoro che sta girando**: solo un lavoro in
  // corso ha qualcuno che aspetta una risposta. La regola è questa, una sola per
  // font, fotogrammi, piano e domande: `active` vero (lock o stato fresco),
  // oppure nessun esito dichiarato e processo ancora vivo nel registro.
  function jobRunning(state) {
    if (!state || typeof state !== 'object') return false;
    if (state.active === true) return true;
    // "running" è un lavoro in corso dichiarato: la testina si muove, e una
    // domanda può ancora cambiare qualcosa.
    const outcome = String(state.outcome || '').trim().toLowerCase();
    if (outcome === 'running') return true;
    const progress =
      state.progress && typeof state.progress === 'object' ? state.progress : null;
    return !outcome && Boolean(progress && progress.alive === true);
  }

  // Il motivo arriva da `note`: si riporta com'è, senza inventarne uno.
  function verdictSentence(kind, note, hasMaster = false) {
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
    return (
      hasMaster
        ? 'Il master c’è, ma il contratto non è tutto firmato.'
        : 'Il master non è stato prodotto.'
    ) + (quoted ? ' ' + quoted : '');
  }

  function sizeLabel(bytes) {
    const n = Number(bytes) || 0;
    if (n >= 1048576) return (n / 1048576).toFixed(1).replace('.', ',') + ' MB';
    if (n >= 1024) return Math.round(n / 1024) + ' kB';
    return n + ' B';
  }

  /* ------------------------------------------------------- esito di un chiuso */

  // Un lavoro chiuso male non si racconta con «Lavoro concluso»: si racconta con
  // la ragione più utile che lo stato porta già — una verifica fallita (dice
  // *perché*), una guardia (dice *cosa*), un cancello rimasto aperto (dice *cosa
  // manca*) — e solo alla fine con la nota del lavoro.

  const CHECK_LABEL = {
    contrast: 'contrasto',
    duration: 'durata',
    variety: 'varietà',
    loudness: 'volume',
    silence: 'silenzio',
    sharpness: 'nitidezza',
    coverage: 'copertura',
    sync: 'sincronia',
  };

  function checkLabel(name) {
    const key = String(name || '').trim().toLowerCase();
    return CHECK_LABEL[key] || key.replace(/_/g, ' ') || 'controllo';
  }

  // La verifica che smentisce una scelta, se il report ne ha dichiarata una.
  function failedVerification(state) {
    const rows = Array.isArray(state.decisions) ? state.decisions : [];
    for (let i = 0; i < rows.length; i += 1) {
      const check = rows[i] && rows[i].verification;
      if (!check || typeof check !== 'object') continue;
      if (check.passed === false && String(check.why || '').trim()) return check;
    }
    return null;
  }

  // Il cancello rimasto aperto quando il lavoro si è chiuso.
  function gateLeftOpen(state) {
    const font = state.font && state.font.pending === true && !state.font.chosen;
    if (font) return 'carattere';
    const approval = state.approval && typeof state.approval === 'object' ? state.approval : {};
    if (approval.waiting === true) return 'fotogrammi';
    const questions = Array.isArray(state.questions) ? state.questions : [];
    if (questions.some((q) => q && q.answered !== true)) return 'domande';
    const plan = state.plan_choice && typeof state.plan_choice === 'object' ? state.plan_choice : {};
    if (plan.pending === true && !plan.chosen) return 'versione del lavoro';
    return '';
  }

  // Il nome da mostrare è quello **vero**: se il contratto ha timbrato il file,
  // la pagina non deve chiamarlo ancora "master.mp4".
  function displayMasterName(state) {
    const run = state && state.run && typeof state.run === 'object' ? state.run : null;
    const master = run && run.master && typeof run.master === 'object' ? run.master : null;
    if (master && master.stamped === true && master.name) return String(master.name);
    return String((state && state.master) || '').trim();
  }

  function closeReport(state, verdict) {
    const ready = state.master_ready === true;
    const name = displayMasterName(state) || 'master.mp4';
    const bytes = Number(state.master_bytes) || 0;
    const masterBit = ready
      ? "Il master c'è (" + name + (bytes > 0 ? ', ' + sizeLabel(bytes) : '') +
        '): si può aprire e guardare.'
      : "Il master non esiste: non c'è un file da aprire.";
    const gate = verdict === 'done' ? '' : gateLeftOpen(state);
    const frames = Array.isArray(state.frames) ? state.frames.length : 0;
    const shots = Array.isArray(state.shots) ? state.shots.length : 0;
    const counted = frames
      ? frames + (frames === 1 ? ' fotogramma' : ' fotogrammi')
      : shots + ' shot';
    const bits = [counted, mmss(state.elapsed_sec) + ' di lavoro'];
    if (gate) bits.push('cancello aperto: ' + gate);
    if (state.approved === true) bits.push('approvati');
    const meta = bits.join(' · ');

    if (verdict === 'done') {
      return {
        word: OUTCOME_WORD.done,
        title: OUTCOME_TITLE.done,
        text: verdictSentence('done', state.note),
        meta,
      };
    }

    if (verdict === 'failed') {
      const bad = failedVerification(state);
      if (bad) {
        const label = checkLabel(bad.check);
        const why = String(bad.why || '').trim();
        return {
          word: 'Non riuscito · ' + label,
          title: 'Non riuscito: la verifica «' + label + '» non passa',
          text: upperFirst(why) + (/[.!?]$/.test(why) ? '' : '.') + ' ' + masterBit,
          meta,
        };
      }
      const guard = state.guard && typeof state.guard === 'object' ? state.guard : {};
      const violations = Array.isArray(guard.violations) ? guard.violations : [];
      if (guard.ok === false || violations.length) {
        const first = String(violations[0] || guard.why || '').trim();
        return {
          word: 'Non riuscito · guardia',
          title: 'Non riuscito: non passa la guardia di consegna',
          text:
            (first ? upperFirst(first) + (/[.!?]$/.test(first) ? '' : '.') + ' ' : '') + masterBit,
          meta,
        };
      }
      if (gate) {
        return {
          word: 'Non riuscito · ' + gate,
          title: 'Non riuscito: il cancello «' + gate + '» è rimasto aperto',
          text: 'Nessuno ha risposto e il lavoro è finito lo stesso. ' + masterBit,
          meta,
        };
      }
      return {
        word: OUTCOME_WORD.failed,
        title: OUTCOME_TITLE.failed,
        text: verdictSentence('failed', state.note) + ' ' + masterBit,
        meta,
      };
    }

    if (verdict === 'stopped') {
      return {
        word: OUTCOME_WORD.stopped,
        title: OUTCOME_TITLE.stopped,
        text: verdictSentence('stopped', state.note) + ' ' + masterBit,
        meta,
      };
    }

    return {
      word: ready ? 'Contratto incompleto' : OUTCOME_WORD.incomplete,
      title: ready ? 'Chiuso senza il contratto completo' : OUTCOME_TITLE.incomplete,
      text: ready
        ? verdictSentence('incomplete', state.note, true)
        : verdictSentence('incomplete', state.note) + ' ' + masterBit,
      meta,
    };
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

  function paintFrames(frames, finished, reason) {
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
    const why = String(reason || '').trim();
    el.framesEmpty.textContent = frames.length
      ? ''
      : finished
        ? 'Il lavoro è finito senza fotogrammi chiave.'
        : why || 'Nessun fotogramma: il lavoro non ne ha ancora scritti.';
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
  /* --------------------------------------------------------------- contratto */

  // Gli otto stadi del lavoro, nell'ordine in cui `vx reel` li firma. La
  // promessa e la gloss sono scritte anche qui: il log non ripete una frase per
  // lo stadio che non è mai arrivato, e la riga deve restare leggibile lo
  // stesso. Il verdetto, però, viene solo dal log: senza, tutto è "mancante".
  const CONTRACT_STAGES = [
    'doctor',
    'ingest',
    'analyze',
    'decide',
    'gates',
    'render',
    'qc',
    'deliver',
  ];

  const STAGE_GLOSS = {
    doctor: 'ambiente',
    ingest: 'materiale',
    analyze: 'analisi',
    decide: 'montaggio',
    gates: 'cancelli',
    render: 'render',
    qc: 'qualità',
    deliver: 'consegna',
  };

  const STAGE_PROMISE = {
    doctor: "l'ambiente non è rotto: ffmpeg e libx264 rispondono",
    ingest: 'almeno un asset entra nella timeline',
    analyze: 'almeno una sorgente misurata',
    decide: 'densità di taglio ≥ 1,0 tagli/10s',
    gates: 'attesa totale ≤ 5,0 s',
    render: 'durata del master = richiesta ± 1 frame',
    qc: 'LUFS = target ± 1,5 LU',
    deliver: 'contrasto del titolo ≥ 4,5',
  };

  // Cosa fare quando lo stadio fallisce: due scelte vere, non un "riprova".
  const STAGE_DO = {
    doctor: {
      what: "L'ambiente non è utilizzabile: senza ffmpeg non parte nessun lavoro.",
      opts: [
        'Reinstalla ffmpeg e libx264, poi rilancia il lavoro',
        "Sposta il lavoro su una macchina con ffmpeg funzionante",
      ],
    },
    ingest: {
      what: 'Nessun asset è entrato nella timeline.',
      opts: [
        'Aggiungi almeno un file alla cartella degli asset',
        'Togli i file che non si aprono e rilancia import',
      ],
    },
    analyze: {
      what: "L'analisi non ha misurato nessuna sorgente.",
      opts: [
        'Rilancia l’analisi sui file importati',
        'Escludi le sorgenti illeggibili e riprova',
      ],
    },
    decide: {
      what: 'Il montaggio è troppo lento: pochi tagli per la durata.',
      opts: [
        'Accorcia i clip: più tagli nella stessa durata',
        'Accetta un ritmo più lento e abbassa la soglia',
      ],
    },
    gates: {
      what: 'Il lavoro ha aspettato troppo: i cancelli sono rimasti aperti.',
      opts: [
        'Rispondi ai cancelli appena la pagina li chiede',
        'Rilancia senza cancelli, in autonomia',
      ],
    },
    render: {
      what: 'La durata del master non è quella richiesta.',
      opts: [
        'Taglia o allunga la timeline fino alla durata richiesta',
        'Tieni la durata prodotta e aggiorna la richiesta',
      ],
    },
    qc: {
      what: 'Il loudness del master è fuori target.',
      opts: [
        'Normalizza il master al LUFS del target e rifai il QC',
        'Sposta il target sul livello che il master ha davvero',
      ],
    },
    deliver: {
      what: 'Il titolo non è leggibile sul fotogramma campionato.',
      opts: ['Contorno nero dietro al titolo', 'Sposta il titolo in safe zone'],
    },
  };

  // Un numero del log, leggibile: la virgola è italiana, i decimali sono quelli
  // misurati e non un arrotondamento che fa sembrare il valore un altro.
  function measureValue(value) {
    if (value === null || value === undefined || value === '') return 'n/d';
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) return 'n/d';
      if (Number.isInteger(value)) return String(value);
      return value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '').replace('.', ',');
    }
    return String(value);
  }

  // Il server dice lo stato in italiano, il foglio di stile ha una classe per
  // ognuno: questa tabella è l'unico punto in cui le due lingue si toccano.
  const STATE_CLASS = { firmato: 'signed', fallito: 'failed', mancante: 'missing' };

  function stateClass(state) {
    return STATE_CLASS[String(state || 'mancante')] || 'missing';
  }

  function contractRow(name, index, stage) {
    const state = String(stage.state || 'mancante');
    const item = document.createElement('li');
    item.className = 'cstage is-' + stateClass(state);

    const idx = document.createElement('span');
    idx.className = 'cstage-idx num';
    idx.textContent = pad2(index + 1);

    const label = document.createElement('span');
    label.className = 'cstage-name';
    label.textContent = name;
    const gloss = document.createElement('span');
    gloss.className = 'cstage-gloss';
    gloss.textContent = STAGE_GLOSS[name] || '';
    label.append(gloss);

    const mid = document.createElement('div');
    mid.className = 'cstage-mid';
    const promise = document.createElement('p');
    promise.className = 'cstage-contract';
    promise.textContent =
      String(stage.contract || '').trim() || STAGE_PROMISE[name] || '';
    const measure = document.createElement('p');
    measure.className = 'cstage-measure num';
    if (state === 'mancante') {
      measure.textContent = stage.event
        ? 'in corso · nessuna firma'
        : 'non misurato · nessuna firma';
    } else {
      measure.append(document.createTextNode('misurato '));
      const value = document.createElement('b');
      value.textContent = measureValue(stage.value);
      measure.append(value);
      if (stage.threshold !== null && stage.threshold !== undefined) {
        measure.append(document.createTextNode(' · soglia '));
        const threshold = document.createElement('b');
        threshold.textContent = measureValue(stage.threshold);
        measure.append(threshold);
      } else {
        measure.append(document.createTextNode(' · nessuna soglia'));
      }
    }
    mid.append(promise, measure);

    const pill = document.createElement('span');
    pill.className = 'cstate';
    pill.textContent = state;

    item.append(idx, label, mid, pill);

    if (state === 'fallito') {
      const advice = STAGE_DO[name] || { what: '', opts: [] };
      const say = document.createElement('p');
      say.className = 'cstage-say';
      say.textContent =
        String(stage.why || '').trim() ||
        advice.what ||
        'Lo stadio non ha passato la sua soglia.';
      item.append(say);

      const opts = Array.isArray(advice.opts) ? advice.opts.slice(0, 2) : [];
      if (opts.length) {
        const wrap = document.createElement('div');
        wrap.className = 'cstage-do';
        const caption = document.createElement('p');
        caption.className = 'cdo-label';
        caption.textContent = 'Cosa fare';
        const list = document.createElement('ul');
        list.className = 'cdo-opts';
        opts.forEach((text, position) => {
          const choice = document.createElement('li');
          choice.className = 'cdo-opt';
          const key = document.createElement('span');
          key.className = 'cdo-key';
          key.textContent = position === 0 ? 'A' : 'B';
          const body = document.createElement('span');
          body.textContent = text;
          choice.append(key, body);
          list.append(choice);
        });
        wrap.append(caption, list);
        item.append(wrap);
      }
    }
    return item;
  }

  function paintContract(state) {
    const run = state && state.run && typeof state.run === 'object' ? state.run : null;
    if (!run) {
      el.contract.hidden = true;
      return;
    }
    el.contract.hidden = false;

    const stages = run.stages && typeof run.stages === 'object' ? run.stages : {};
    const corrupt =
      run.corrupt && typeof run.corrupt === 'object' ? run.corrupt : null;
    const total = Number(run.total) || CONTRACT_STAGES.length;
    const signed = Number(run.count) || 0;
    const failed = Array.isArray(run.failed) ? run.failed : [];
    const present = run.present === true;

    el.contract.classList.toggle('is-blocked', Boolean(corrupt) || failed.length > 0);
    el.contract.classList.toggle('is-complete', run.complete === true);
    el.contractScore.textContent = corrupt ? '—' : signed + '/' + total;
    el.contractScoreWord.textContent = corrupt
      ? 'ignoto'
      : run.complete === true
        ? 'firmati · completo'
        : 'firmati';
    el.contractSub.textContent = corrupt
      ? 'il registro non si legge: nessuno stadio è verificabile'
      : present
        ? 'otto stadi, ognuno con la misura e la soglia'
        : 'questo lavoro non ha un registro del contratto';

    const log = String(run.log || '');
    el.contractLog.textContent = log ? 'log · ' + log : '';
    el.contractLog.hidden = !log;

    const notes = [];
    if (!present) {
      notes.push(
        'Nessun .vx/run.jsonl per questo lavoro: non è passato da vx reel, ' +
          'quindi non c’è nessuno stadio firmato da mostrare.'
      );
    }
    if (run.tail_truncated === true) {
      notes.push(
        'Il log finisce a metà riga: l’ultimo evento è stato perso in una ' +
          'scrittura interrotta, non è una firma.'
      );
    }
    if (corrupt) {
      notes.push(
        'Con una riga rotta non posso fidarmi di quelle dopo: nessuno stadio ' +
          'qui è dichiarato firmato.'
      );
    }
    el.contractNote.textContent = notes.join(' ');
    el.contractNote.hidden = notes.length === 0;

    // Il segno di quello che cambia: otto stati, la riga rotta, il log. Finché
    // non cambia niente, il disegno resta — la pagina chiede ogni secondo.
    const signature =
      CONTRACT_STAGES.map((name) => (stages[name] && stages[name].state) || 'mancante').join('|') +
      '|' + (corrupt ? 'c' + String(corrupt.line || '') : '') +
      '|' + log +
      '|' + String(run.tail_truncated === true);
    if (el.contract.dataset.signature === signature) return;
    el.contract.dataset.signature = signature;

    el.contractStrip.textContent = '';
    el.contractStrip.hidden = Boolean(corrupt);
    el.contractList.textContent = '';
    el.contractList.hidden = Boolean(corrupt);

    if (!corrupt) {
      CONTRACT_STAGES.forEach((name) => {
        const stage = stages[name] && typeof stages[name] === 'object' ? stages[name] : {};
        const seg = document.createElement('i');
        seg.className = 'is-' + stateClass(stage.state);
        el.contractStrip.append(seg);
      });
      CONTRACT_STAGES.forEach((name, index) => {
        const stage = stages[name] && typeof stages[name] === 'object' ? stages[name] : {};
        el.contractList.append(contractRow(name, index, stage));
      });
    }

    el.contractCorrupt.textContent = '';
    el.contractCorrupt.hidden = !corrupt;
    if (corrupt) {
      const title = document.createElement('p');
      title.className = 'corrupt-title';
      title.textContent = 'Il registro del contratto è illeggibile';

      const line = document.createElement('p');
      line.className = 'corrupt-line';
      line.textContent =
        'riga ' + String(Number(corrupt.line) || 1) + ' · ' +
        (String(corrupt.why || '').trim() || 'contenuto non valido');

      const say = document.createElement('p');
      say.className = 'corrupt-say';
      say.textContent =
        'Il log è la prova delle firme: con una riga rotta non posso dire ' +
        'quali stadi siano passati. Nessuno è verde per gentilezza.';

      const hint = document.createElement('p');
      hint.className = 'corrupt-do';
      hint.textContent =
        'Cosa fare: ripristina .vx/run.jsonl da una copia integra, oppure ' +
        'rilancia il lavoro da capo in una cartella nuova.';

      el.contractCorrupt.append(title, line, say, hint);
    }
  }

  /* ------------------------------------------------------- scaletta del montaggio */

  // Le tre leve della composizione. La proposta e il fatto non sono la stessa
  // riga: il numero grande è quello che il piano ha montato davvero, la riga
  // piccola dice da dove si veniva o cosa la misura voleva. Tutto arriva dal
  // blocco `composition` del report: se il blocco non c'è, la sezione non c'è.
  const SCAL_LEVERS = [
    { id: 'hook', label: 'Gancio' },
    { id: 'shots', label: 'Clip' },
    { id: 'broll', label: 'B-roll' },
  ];
  const SCAL_DECLINED_LABEL = {
    hook: 'Gancio',
    shots: 'Clip',
    beat: 'Clip',
    broll: 'B-roll',
    payoff: 'Chiusura',
    cta: 'Chiusura',
    proof: 'Prova',
  };

  let scalJob = null;
  const scalRows = new Map();

  const objectOf = (value) =>
    value && typeof value === 'object' && !Array.isArray(value) ? value : {};

  // Un campo assente non è uno zero: torna `null` e la pagina scrive «—».
  function numberOrNull(value) {
    if (value === null || value === undefined || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function secText(value) {
    const n = numberOrNull(value);
    return n === null ? '—' : num(n, 2) + ' s';
  }

  function compositionOf(state) {
    const direct = objectOf(state && state.composition);
    if (Object.keys(direct).length) return direct;
    const nested = objectOf(objectOf(state && state.report).composition);
    return Object.keys(nested).length ? nested : null;
  }

  function scalettaModel(state) {
    const composition = compositionOf(state);
    if (!composition) return null;

    const proposed = objectOf(composition.proposed);
    const applied = objectOf(composition.applied);
    const declined = (Array.isArray(composition.declined) ? composition.declined : [])
      .filter((item) => item && typeof item === 'object')
      .map((item) => ({
        id: String(item.id || '').trim(),
        why: String(item.why || '').trim(),
      }))
      .filter((item) => item.id || item.why);

    const appliedFlag =
      applied.hook_applied === true ? true : applied.hook_applied === false ? false : null;
    const hookSeconds = numberOrNull(applied.hook_seconds);
    const hookProposed = numberOrNull(proposed.hook_seconds);
    const hookBase = numberOrNull(applied.hook_baseline_seconds);
    const hookSrcIn = numberOrNull(applied.hook_src_in);
    const hookMoved = applied.hook_take_moved === true;
    const hookHonored = applied.hook_take_honored === true;
    const hookKnown =
      hookSeconds !== null || hookProposed !== null || hookBase !== null || appliedFlag !== null;
    const hook = hookKnown
      ? {
          // Con la scelta non applicata il numero resta quello composto: il
          // fatto che non sia entrato nel piano lo dice lo stato, non un altro
          // numero.
          value: hookSeconds !== null ? hookSeconds : hookProposed,
          proposal: hookProposed,
          baseline: hookBase,
          applied: appliedFlag,
          note:
            hookMoved && !hookHonored
              ? 'il piano usa la corsa intera' +
                (hookSrcIn !== null ? ': si entra da ' + num(hookSrcIn, 2) + ' s' : '')
              : '',
        }
      : null;

    const shotsBefore = numberOrNull(applied.shots_before);
    const shotsAfter = numberOrNull(applied.shots_after);
    const shotsDelta = numberOrNull(applied.delta);
    const shotsKnown =
      shotsBefore !== null || shotsAfter !== null || shotsDelta !== null;
    const shots = shotsKnown
      ? { before: shotsBefore, after: shotsAfter, delta: shotsDelta }
      : null;

    const order = (Array.isArray(proposed.broll_order) ? proposed.broll_order : [])
      .map((item) => String(item || '').trim())
      .filter(Boolean);
    const brollMoved =
      applied.broll_moved === true ? true : applied.broll_moved === false ? false : null;
    const brollSlots = Array.isArray(applied.broll_slots_after)
      ? applied.broll_slots_after.length
      : null;
    const brollKnown = order.length > 0 || brollMoved !== null || brollSlots !== null;
    const broll = brollKnown ? { order, moved: brollMoved } : null;

    return {
      hook,
      shots,
      broll,
      declined,
      // La creatività può spostare il blocco: è un fatto da dire, non un guasto.
      warn: applied.survived === false && appliedFlag === true,
    };
  }

  function scalRowNode(lever) {
    const row = document.createElement('li');
    row.className = 'scal-row';
    row.dataset.lever = lever.id;

    const kicker = document.createElement('p');
    kicker.className = 'scal-kicker';
    const name = document.createElement('span');
    name.textContent = lever.label;
    const status = document.createElement('span');
    status.className = 'scal-status';
    status.hidden = true;
    kicker.append(name, status);

    // La riga che cambia: è questa la zona che uno screen reader annuncia.
    const value = document.createElement('p');
    value.className = 'scal-value';
    value.setAttribute('aria-live', 'polite');

    const sub = document.createElement('p');
    sub.className = 'scal-sub';
    sub.hidden = true;

    const note = document.createElement('p');
    note.className = 'scal-note';
    note.hidden = true;

    row.append(kicker, value, sub, note);
    return { row, status, value, sub, note };
  }

  function setScalStatus(node, text, tone) {
    const label = String(text || '').trim();
    node.textContent = label;
    node.hidden = !label;
    node.className = 'scal-status' + (tone ? ' is-' + tone : '');
  }

  function setScalLine(node, text) {
    const label = String(text || '').trim();
    node.textContent = label;
    node.hidden = !label;
  }

  function scalEmpty(node) {
    node.textContent = '—';
    node.classList.add('is-empty');
  }

  function scalSeconds(node, value) {
    const n = numberOrNull(value);
    node.textContent = '';
    node.classList.toggle('is-empty', n === null);
    if (n === null) {
      scalEmpty(node);
      return;
    }
    const amount = document.createElement('span');
    amount.textContent = num(n, 2);
    const unit = document.createElement('span');
    unit.className = 'scal-unit';
    unit.textContent = 's';
    node.append(amount, unit);
  }

  function scalDelta(delta) {
    const value = Math.round(Number(delta) || 0);
    const badge = document.createElement('span');
    badge.className = 'scal-delta' + (value === 0 ? ' is-zero' : '');
    badge.textContent = value > 0 ? '+' + value : String(value);
    return badge;
  }

  function scalShots(node, shots) {
    node.textContent = '';
    node.classList.remove('is-empty');
    const before = shots.before;
    const after = shots.after;

    // Senza le due misure non c'è una freccia da disegnare: il delta da solo
    // non è un numero di clip.
    if (before === null && after === null) {
      scalEmpty(node);
      return;
    }

    // Delta zero: niente freccia, la parola basta.
    if (shots.delta === 0) {
      const amount = document.createElement('span');
      amount.textContent = String(Math.round(before !== null ? before : after));
      const hold = document.createElement('span');
      hold.className = 'scal-hold';
      hold.textContent = '(invariate)';
      node.append(amount, hold);
      return;
    }

    const from = document.createElement('span');
    from.textContent = before === null ? '—' : String(Math.round(before));
    const to = document.createElement('span');
    to.textContent = after === null ? '—' : String(Math.round(after));
    if (after !== null) {
      const arrow = document.createElement('span');
      arrow.className = 'scal-arrow';
      arrow.setAttribute('aria-hidden', 'true');
      arrow.textContent = '→';
      node.append(from, arrow, to);
    } else {
      node.append(from);
    }

    if (shots.delta !== null) node.append(scalDelta(shots.delta));
  }

  function scalBroll(node, broll) {
    node.textContent = '';
    node.classList.toggle('is-empty', broll.order.length === 0);
    if (!broll.order.length) {
      scalEmpty(node);
      return;
    }
    const chain = document.createElement('span');
    chain.className = 'scal-chain';
    broll.order.forEach((name, index) => {
      if (index) {
        const arrow = document.createElement('span');
        arrow.className = 'scal-chain-arrow';
        arrow.setAttribute('aria-hidden', 'true');
        arrow.textContent = '→';
        chain.append(arrow);
      }
      const chip = document.createElement('span');
      chip.className = 'scal-chain-item';
      chip.textContent = name;
      chip.title = name;
      chain.append(chip);
    });
    node.append(chain);
  }

  function paintScaletta(state) {
    if (!el.scaletta) return;
    const model = scalettaModel(state);

    if (!model) {
      // Nessun guscio vuoto: senza il blocco `composition` la sezione sparisce.
      el.scaletta.hidden = true;
      el.scalettaList.textContent = '';
      el.scalettaList.dataset.levers = '';
      el.scalettaDeclined.hidden = true;
      el.scalettaDeclinedList.textContent = '';
      el.scalettaDeclined.dataset.signature = '';
      el.scalettaWarn.hidden = true;
      scalRows.clear();
      scalJob = null;
      return;
    }

    const present = [];
    if (model.hook) present.push('hook');
    if (model.shots) present.push('shots');
    if (model.broll) present.push('broll');
    if (!present.length && !model.declined.length) {
      el.scaletta.hidden = true;
      return;
    }

    el.scaletta.hidden = false;

    if (scalJob !== jobIndex) {
      scalRows.clear();
      el.scalettaList.textContent = '';
      el.scalettaList.dataset.levers = '';
      el.scalettaDeclinedList.textContent = '';
      el.scalettaDeclined.dataset.signature = '';
      scalJob = jobIndex;
    }

    el.scalettaMeta.textContent =
      present.length === 0 ? '' : present.length === 1 ? '1 leva' : present.length + ' leve';

    const levers = present.join(',');
    if (el.scalettaList.dataset.levers !== levers) {
      el.scalettaList.dataset.levers = levers;
      el.scalettaList.textContent = '';
      present.forEach((id) => {
        let nodes = scalRows.get(id);
        if (!nodes) {
          nodes = scalRowNode(SCAL_LEVERS.find((lever) => lever.id === id));
          scalRows.set(id, nodes);
        }
        el.scalettaList.append(nodes.row);
      });
    }

    const declinedIds = new Set(model.declined.map((item) => item.id));

    if (model.hook) {
      const nodes = scalRows.get('hook');
      const hook = model.hook;
      scalSeconds(nodes.value, hook.value);
      const sub = [];
      if (hook.baseline !== null) sub.push('prima ' + secText(hook.baseline));
      if (
        hook.proposal !== null &&
        hook.value !== null &&
        Math.abs(hook.proposal - hook.value) >= 0.005
      ) {
        sub.push('proposta ' + secText(hook.proposal));
      }
      setScalLine(nodes.sub, sub.join(' · '));
      setScalLine(nodes.note, hook.note);
      setScalStatus(
        nodes.status,
        hook.applied === false ? 'non applicato' : '',
        hook.applied === false ? 'warn' : ''
      );
    }

    if (model.shots) {
      const nodes = scalRows.get('shots');
      scalShots(nodes.value, model.shots);
      // Il delta è già nel distintivo accanto al numero: ripeterlo sarebbe rumore.
      setScalLine(nodes.sub, '');
      setScalLine(nodes.note, '');
      setScalStatus(nodes.status, '', '');
    }

    if (model.broll) {
      const nodes = scalRows.get('broll');
      scalBroll(nodes.value, model.broll);
      setScalLine(
        nodes.sub,
        model.broll.moved === false
          ? 'slot invariati'
          : model.broll.moved === true
            ? 'slot spostati'
            : ''
      );
      setScalLine(nodes.note, '');
      setScalStatus(nodes.status, '', '');
      nodes.row.classList.toggle('is-declined', declinedIds.has('broll'));
    }

    el.scalettaWarn.hidden = !model.warn;

    el.scalettaDeclined.hidden = model.declined.length === 0;
    const declinedSignature = JSON.stringify(model.declined);
    if (el.scalettaDeclined.dataset.signature !== declinedSignature) {
      el.scalettaDeclined.dataset.signature = declinedSignature;
      el.scalettaDeclinedList.textContent = '';
      model.declined.forEach((item) => {
        const row = document.createElement('li');
        row.className = 'scal-declined-row';

        const label = document.createElement('p');
        label.className = 'scal-declined-label';
        label.textContent =
          SCAL_DECLINED_LABEL[item.id] ||
          (item.id ? item.id.replace(/_/g, ' ') : 'voce');

        const why = document.createElement('p');
        why.className = 'scal-declined-why';
        why.textContent = item.why || '—';

        row.append(label, why);
        el.scalettaDeclinedList.append(row);
      });
    }
  }

  // Il timbro in testata: se il master si chiama NON-PUBBLICABILE-* la pagina
  // lo dice subito, senza aprire il report. Il pulsante resta attivo perché il
  // file esiste: è timbrato, non invisibile.
  function paintStamp(state) {
    const run = state && state.run && typeof state.run === 'object' ? state.run : null;
    const master = run && run.master && typeof run.master === 'object' ? run.master : null;
    const stamped = Boolean(master && master.stamped);
    body.classList.toggle('is-stamped', stamped);
    el.jobStamp.hidden = !stamped;
    if (!stamped) {
      el.jobStampFile.textContent = '';
      el.jobStamp.removeAttribute('title');
      return;
    }
    el.jobStampFile.textContent = String(master.name || 'master');
    const failed = Array.isArray(run.failed) ? run.failed.join(', ') : '';
    el.jobStamp.title = failed
      ? 'Il contratto non passa (' + failed + '): il file è timbrato e non va consegnato.'
      : 'Il file è timbrato: non va consegnato.';
  }

  function paintResult(state, closed) {
    const ready = state.master_ready === true;
    // Il nome del file è quello vero: se il contratto l'ha timbrato, il nome
    // che si legge è il timbro, non il "master.mp4" della cartella.
    const name = displayMasterName(state);
    const bytes = Number(state.master_bytes) || 0;
    const signature =
      (ready ? 'ready|' + name + '|' + bytes : 'empty|' + (closed ? 'closed' : 'open')) +
      '|job:' + jobIndex;

    el.resultMeta.textContent = ready ? 'pronto' : closed ? 'assente' : 'non ancora';
    el.resultMeta.classList.toggle('is-ready', ready);
    // Con un master pronto la zona del risultato sale sopra la Regia: è il file
    // che si vuole aprire, e non deve stare in fondo alla pagina.
    body.classList.toggle('has-master', ready);

    if (el.resultBody.dataset.signature === signature) return;
    el.resultBody.dataset.signature = signature;
    el.resultBody.textContent = '';

    if (!ready) {
      const line = document.createElement('p');
      line.className = 'result-empty';
      line.textContent = closed
        ? String(state.result_note || '').trim() ||
          'Il master non è stato prodotto: non c’è nessun file da guardare.'
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

  // Il pulsante esiste solo se c'è un piano e un'attesa **di un lavoro in corso**:
  // `required` accende il blocco, `waiting` lo rende cliccabile, `approved` lo
  // chiude. Mai per ipotesi, e mai su un lavoro che non sta più aspettando.
  function paintApproval(state, frameCount) {
    const approval = state.approval && typeof state.approval === 'object' ? state.approval : null;
    const required = !!(approval && approval.required === true) && jobRunning(state);

    el.action.hidden = !required;
    if (!required) {
      window.clearTimeout(el.approveFlash._timer);
      el.approveFlash.textContent = '';
      el.approve.classList.remove('is-waiting');
      return;
    }

    const approved = approval.approved === true;
    const waiting = approval.waiting === true;
    const frames = Number.isFinite(frameCount) ? frameCount : 0;

    // Con zero immagini non c'e' niente da approvare: niente pulsante, solo il
    // motivo. La riga sta dove stava il pulsante, cosi' il silenzio non si
    // confonde con un'attesa.
    if (!approved && frames === 0) {
      window.clearTimeout(el.approveFlash._timer);
      el.approveFlash.textContent = '';
      el.approve.hidden = true;
      el.approveDone.hidden = true;
      el.approve.classList.remove('is-waiting');
      el.approveNote.className = 'action-note';
      el.approveNote.textContent =
        String(approval.reason || '').trim() ||
        'Nessun fotogramma da approvare: l\'estrazione non ha prodotto immagini.';
      return;
    }

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

  const JOURNAL_ICONS = {
    misura: '<path d="M2 4v4M5 2v8M8 4v4M11 2v8"/>',
    taglio: '<path d="M2.5 3 9.5 9M9.5 3 2.5 9"/>',
    audio: '<path d="M3 6v1M5 4v4M7 2v8M9 4v4M11 6v1"/>',
    testo: '<path d="M3 3h6M6 3v7M4.5 10h3"/>',
    render: '<rect x="2" y="3" width="8" height="6" rx="1"/><path d="M5.2 5.4 7.6 6l-2.4.6z"/>',
    consegna: '<path d="M6 8V2M4 4l2-2 2 2M2.5 7.5V10h7V7.5"/>',
    altro: '<circle cx="6" cy="6" r="1.4"/>',
  };

  // L'icona viene dal tipo di operazione, non da un'icona generica ripetuta.
  function journalKind(entry) {
    // Una riga costruita da una sessione porta gia' il suo tipo: si rispetta.
    const declared = String(entry.kind || '').toLowerCase();
    if (JOURNAL_ICONS[declared]) return declared;
    const phase = String(entry.phase || '').toLowerCase();
    const line = String(entry.line || '').toLowerCase();
    // Prima il tipo dichiarato dalla riga, poi la fase: "sottotitoli" resta testo
    // anche se arriva durante il render.
    if (phase === 'deliverables' || phase === 'done' || /consegn|deliver/.test(line)) {
      return 'consegna';
    }
    if (phase === 'bridge' || /tagli|taglio|pezzi|montagg|transizion/.test(line)) {
      return 'taglio';
    }
    if (/audio|voce|parlato|silenz|musica|loudness/.test(line)) return 'audio';
    if (/testo|sottotitol|frase|titolo|didascal|font/.test(line)) return 'testo';
    if (phase === 'render' || phase === 'render_done' || /encode|render|fotogrammi/.test(line)) {
      return 'render';
    }
    if (phase === 'triage' || phase === 'ingest' || phase === 'qc' || /misur|clip|durata|controll/.test(line)) {
      return 'misura';
    }
    return 'altro';
  }

  function journalRow(entry) {
    const kind = journalKind(entry);
    const row = document.createElement('li');
    row.className = 'journal-row';
    row.dataset.kind = kind;

    const icon = document.createElement('span');
    icon.className = 'journal-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML =
      '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2" ' +
      'stroke-linecap="round" stroke-linejoin="round">' +
      (JOURNAL_ICONS[kind] || JOURNAL_ICONS.altro) +
      '</svg>';

    const time = document.createElement('span');
    time.className = 'journal-time num';
    time.textContent = mmss(Number(entry.t) || 0);
    const line = document.createElement('span');
    line.className = 'journal-line';
    line.textContent = String(entry.line || '').trim();
    row.append(icon, time, line);
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

  /* ---------------------------------------------------------------- decisioni */

  let decisionsJob = null;
  const decisionOpen = new Map();

  function decisionLabel(text) {
    const label = document.createElement('p');
    label.className = 'decision-label';
    label.textContent = text;
    return label;
  }

  function paintDecisions(state) {
    const items = (Array.isArray(state.decisions) ? state.decisions : []).filter(
      (item) => item && (item.question || item.chosen)
    );

    if (decisionsJob !== jobIndex) {
      decisionsJob = jobIndex;
      decisionOpen.clear();
      el.decisionsList.textContent = '';
      el.decisionsList.dataset.signature = '';
    }

    el.decisionsEmpty.hidden = items.length > 0;
    el.decisionsList.hidden = items.length === 0;
    el.decisionsMeta.textContent = items.length
      ? items.length + (items.length === 1 ? ' voce' : ' voci')
      : '';

    if (!items.length) {
      el.decisionsList.textContent = '';
      el.decisionsList.dataset.signature = '';
      return;
    }

    // La firma evita di ricostruire (e richiudere) tutto a ogni poll.
    const signature = JSON.stringify(items);
    if (el.decisionsList.dataset.signature === signature) return;
    el.decisionsList.dataset.signature = signature;
    el.decisionsList.textContent = '';

    items.forEach((item, position) => {
      const id = String(item.id || 'd' + position);
      const bodyId = 'dec-' + jobIndex + '-' + position;
      const confidence = String(item.confidence || '').toLowerCase();
      const by = String(item.by || 'auto').toLowerCase();
      const open = decisionOpen.has(id) ? decisionOpen.get(id) : position === 0;

      const row = document.createElement('li');
      row.className = 'decision' + (confidence === 'low' ? ' is-low' : '');

      const head = document.createElement('button');
      head.type = 'button';
      head.className = 'decision-head';
      head.setAttribute('aria-expanded', open ? 'true' : 'false');
      head.setAttribute('aria-controls', bodyId);

      const caret = document.createElement('span');
      caret.className = 'decision-caret';
      caret.setAttribute('aria-hidden', 'true');
      caret.textContent = '▸';

      const question = document.createElement('span');
      question.className = 'decision-q';
      question.textContent = String(item.question || 'Domanda non dichiarata');

      const answer = document.createElement('span');
      answer.className = 'decision-a';
      answer.textContent = String(item.chosen || 'nessuna scelta');

      const tags = document.createElement('span');
      tags.className = 'decision-tags';

      const byTag = document.createElement('span');
      byTag.className =
        'dtag is-by' +
        (by === 'user' ? ' is-user' : by === 'timeout' ? ' is-timeout' : '');
      byTag.textContent = by === 'user' ? 'tu' : by === 'timeout' ? 'timeout' : 'auto';
      tags.append(byTag);

      if (confidence === 'high' || confidence === 'medium' || confidence === 'low') {
        const confTag = document.createElement('span');
        confTag.className =
          'dtag is-conf' +
          (confidence === 'low' ? ' is-low' : confidence === 'medium' ? ' is-medium' : '');
        confTag.textContent =
          'fiducia ' + (confidence === 'high' ? 'alta' : confidence === 'medium' ? 'media' : 'bassa');
        tags.append(confTag);
      }

      // La verifica è il controllo che tiene o smentisce la scelta: se è
      // fallita si vede già dalla riga chiusa, non solo aprendola.
      const verification =
        item.verification && typeof item.verification === 'object' ? item.verification : null;
      if (verification) {
        const failed = verification.passed === false;
        const verifyTag = document.createElement('span');
        verifyTag.className =
          'dtag is-verify' + (failed ? ' is-bad' : verification.passed === true ? ' is-ok' : '');
        verifyTag.textContent = failed
          ? 'verifica fallita'
          : verification.passed === true
            ? 'verifica ok'
            : 'non verificata';
        verifyTag.title = String(verification.why || '').trim();
        tags.append(verifyTag);
      }

      head.append(caret, question, answer, tags);
      row.append(head);

      const body = document.createElement('div');
      body.className = 'decision-body';
      body.id = bodyId;
      body.hidden = !open;

      const evidence = (Array.isArray(item.evidence) ? item.evidence : []).filter((value) =>
        String(value || '').trim()
      );
      if (evidence.length) {
        const block = document.createElement('div');
        const list = document.createElement('ul');
        list.className = 'decision-evidence';
        evidence.forEach((value) => {
          const li = document.createElement('li');
          li.textContent = String(value);
          list.append(li);
        });
        block.append(decisionLabel('Prove'), list);
        body.append(block);
      }

      // La verifica, per esteso: cosa è stato misurato e cosa serviva.
      if (verification) {
        const why = String(verification.why || '').trim();
        const check = String(verification.check || '').trim();
        const block = document.createElement('div');
        const line = document.createElement('p');
        line.className =
          'decision-verify' +
          (verification.passed === false ? ' is-failed' : verification.passed === true ? ' is-passed' : '');
        const lead = document.createElement('span');
        lead.className = 'decision-verify-head';
        lead.textContent =
          verification.passed === false
            ? 'Verifica fallita'
            : verification.passed === true
              ? 'Verifica passata'
              : 'Verifica non automatica';
        line.append(lead);
        if (check) {
          const tag = document.createElement('span');
          tag.className = 'decision-verify-check num';
          tag.textContent = check;
          line.append(tag);
        }
        if (why) {
          const say = document.createElement('span');
          say.textContent = ' — ' + why;
          line.append(say);
        }
        block.append(decisionLabel('Verifica'), line);
        body.append(block);
      }

      const alts = (Array.isArray(item.alternatives) ? item.alternatives : []).filter(
        (alt) => alt && String(alt.option || '').trim()
      );
      const note = String(item.alternatives_note || '').trim();
      if (alts.length || note) {
        const block = document.createElement('div');
        block.append(decisionLabel('Alternative scartate'));
        if (alts.length) {
          const list = document.createElement('ul');
          list.className = 'decision-alts';
          alts.forEach((alt) => {
            const li = document.createElement('li');
            const option = document.createElement('span');
            option.className = 'decision-alt-option';
            option.textContent = String(alt.option);
            const why = document.createElement('span');
            why.className = 'decision-alt-why';
            why.textContent = ' — ' + (String(alt.why_not || '').trim() || 'motivo non dichiarato');
            li.append(option, why);
            list.append(li);
          });
          block.append(list);
        } else {
          const line = document.createElement('p');
          line.className = 'decision-note';
          line.textContent = note;
          block.append(line);
        }
        body.append(block);
      }

      const falsified = String(item.falsified_if || '').trim();
      if (falsified) {
        const block = document.createElement('div');
        const line = document.createElement('p');
        line.className = 'decision-falsified';
        line.textContent = falsified;
        block.append(decisionLabel('Mi smentisci se'), line);
        body.append(block);
      }

      if (!body.childElementCount) {
        const line = document.createElement('p');
        line.className = 'decision-note';
        line.textContent = 'Nessun dettaglio pubblicato per questa voce.';
        body.append(line);
      }

      row.append(body);
      el.decisionsList.append(row);

      head.addEventListener('click', () => {
        const next = head.getAttribute('aria-expanded') !== 'true';
        head.setAttribute('aria-expanded', next ? 'true' : 'false');
        body.hidden = !next;
        decisionOpen.set(id, next);
      });
    });
  }

  /* -------------------------------------------------------------------- regia */

  // Tre zone: cosa guarda la testina (un fotogramma vero), cosa fa (la striscia
  // con gli effetti di ogni shot), cosa modifica (i valori dello shot attivo).
  // La testina segue `render.percent`: a percent 0 non c'è posizione inventata.
  let lookAspect = '9 / 16';
  let lastClosed = false;
  let shotJob = -1;
  let shotCount = 0;
  let gradeKey = null;

  // Fasi che "guardano" davvero un'immagine: lì ha senso la scansione.
  const SCAN_PHASES = ['analysis', 'analisi', 'triage', 'ingest', 'misura'];
  // Fasi in cui il colore si tocca: lì ha senso il velo di grado.
  const GRADE_PHASES = ['grade', 'grading', 'color', 'colore', 'lut'];

  function lutColor(effects) {
    const effect = (Array.isArray(effects) ? effects : []).find((item) =>
      /^\s*lut\b/i.test(String(item || ''))
    );
    if (!effect) return '';
    const name = String(effect).replace(/^\s*lut\s*/i, '').trim().toLowerCase();
    if (/warm|caldo|gold|golden|orange|amber/.test(name)) return 'rgba(255, 154, 77, .5)';
    if (/cool|freddo|teal|blue|cyan/.test(name)) return 'rgba(77, 168, 255, .45)';
    return 'rgba(255, 255, 255, .28)';
  }

  function fireGrade(shot) {
    const color = lutColor(shot && shot.effects);
    if (!color) return; // nessun LUT dichiarato: nessun colore inventato
    el.lookFrame.style.setProperty('--grade-color', color);
    el.lookFrame.classList.remove('is-grading');
    void el.lookFrame.offsetWidth;
    el.lookFrame.classList.add('is-grading');
    el.lookFrame.addEventListener(
      'animationend',
      () => el.lookFrame.classList.remove('is-grading'),
      { once: true }
    );
  }

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

    // Tacche nuove: cadono solo se il piano è cresciuto mentre guardavamo.
    const previous = shotJob === jobIndex ? shotCount : -1;

    el.reelStrip.dataset.signature = signature;
    el.reelStrip.textContent = '';

    shots.forEach((shot, position) => {
      const cell = document.createElement('span');
      cell.className = 'shot-cell';
      if (previous >= 0 && position >= previous) {
        cell.classList.add('is-fresh');
        cell.style.setProperty('--i', String(position - previous));
      }
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

    shotJob = jobIndex;
    shotCount = shots.length;
  }

  function setLookImage(shot, closed) {
    // A lavoro finito il fotogramma si chiede anche quando la cache del lavoro
    // non c'è ancora: `/shots/<i>` lo estrae dal sorgente al primo accesso, e il
    // file resta nella workdir. Mentre il render gira non si chiede niente che
    // non sia già pronto: l'encoder sta lavorando, non la vista.
    const url = shot.thumb && (shot.thumb_ready || closed) ? scoped(String(shot.thumb)) : '';
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
      el.lookFrame.classList.remove('is-scanning');
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
        : 'Nessun encode in corso: la testina è ferma.';
    }

    if (active >= 0) {
      const shot = shots[active];
      el.lookCaption.hidden = false;
      el.lookFrame.hidden = false;
      el.lookNote.hidden = true;

      // Scansione solo dove il dato la giustifica: la fase di analisi.
      const phaseKey = String(state.current || '').toLowerCase();
      el.lookFrame.classList.toggle('is-scanning', SCAN_PHASES.indexOf(phaseKey) !== -1);

      // Velo di grado: una volta per fase, e solo con un LUT dichiarato.
      if (GRADE_PHASES.indexOf(phaseKey) !== -1 && gradeKey !== jobIndex + ':' + phaseKey) {
        gradeKey = jobIndex + ':' + phaseKey;
        fireGrade(shot);
      }

      el.lookStatus.hidden = !notStarted;
      el.lookStatus.textContent = notStarted
        ? 'Primo shot del piano (encode non iniziato)'
        : '';
      el.lookTime.textContent = timecode(shot.t_in);
      el.lookSource.textContent = shot.source || 'sorgente non dichiarata';
      el.lookFrame.style.aspectRatio = lookAspect;
      setLookImage(shot, closed);
      paintMods(shot);
      el.shotSpecs.hidden = false;
      el.specDur.textContent = num(Number(shot.dur) || 0, 1) + ' s';
      el.specIn.textContent = timecode(shot.t_in);
      el.specOut.textContent = timecode(shot.t_out);
    } else {
      el.lookCaption.hidden = true;
      el.lookFrame.hidden = true;
      el.lookFrame.classList.remove('is-scanning');
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

  function paintEstimate(state, verdict) {
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

    // Consuntivo: solo a lavoro finito, e solo se c'è davvero un tempo misurato.
    const actual = Number(state.elapsed_sec) || 0;
    const showActual = verdict === 'done' && seconds > 0 && actual > 0;
    el.estimateActual.hidden = !showActual;
    if (showActual) {
      const delta = ((actual - seconds) / seconds) * 100;
      const rounded = Math.round(delta);
      el.estimateActual.textContent =
        num(seconds, 0) + ' s stimati · ' + num(actual, 0) + ' s reali · ' +
        (rounded >= 0 ? '+' : '−') + Math.abs(rounded) + '%';
      el.estimateActual.className = 'estimate-actual num' + (Math.abs(delta) > 20 ? ' is-off' : '');
    }

    // Errore medio del modello, se il registro ne ha uno (numero o blocco).
    const summary = state.feedback && state.feedback.summary ? state.feedback.summary : {};
    let mapeValue = null;
    let mapeN = 0;
    if (typeof summary.mape === 'number') {
      mapeValue = summary.mape;
      mapeN = Number(summary.n || summary.count) || 0;
    } else if (summary.mape && typeof summary.mape === 'object') {
      mapeN = Number(summary.mape.n) || 0;
      const percent = Number(summary.mape.mean_percent);
      if (Number.isFinite(percent)) mapeValue = percent / 100;
    }
    const showMape = mapeN > 0 && mapeValue !== null && Number.isFinite(mapeValue);
    el.estimateMape.hidden = !showMape;
    if (showMape) {
      el.estimateMape.textContent =
        'errore medio sugli ultimi ' + mapeN +
        (mapeN === 1 ? ' lavoro: ' : ' lavori: ') +
        Math.round(mapeValue * 100) + '%';
    }
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

    // Come i font: la scelta della versione si chiede solo a un lavoro che sta
    // girando. A lavoro chiuso il dialogo sparisce e la scelta resta una decisione.
    if (!pending || !jobRunning(state)) {
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

  /* -------------------------------------------------------------- voto finale */

  // Una scheda sola, sotto l'esito: dice cosa si sta valutando e chiede un
  // numero. Il clic su una tacca sceglie soltanto: si vede la scelta, poi
  // «Registra». Nessun dialogo: il master resta guardabile mentre decidi.

  const VOTE_WORD = { 1: 'sbagliata', 2: 'debole', 3: 'passabile', 4: 'buona', 5: 'giusta' };
  const VOTE_OUTCOME = {
    done: 'consegnato',
    stopped: 'fermato',
    failed: 'non riuscito',
    incomplete: 'senza master',
  };
  const VOTE_HINT = '1–5 per scegliere, Invio per registrare, Esc per chiudere senza votare.';

  let voteJob = null;
  let voteRating = null;
  let voteSending = false;
  let voteDismissed = false;
  let voteGivenLocal = null;

  const chipWords = () =>
    Array.from(el.voteChips.querySelectorAll('.vote-chip'))
      .filter((chip) => chip.getAttribute('aria-pressed') === 'true')
      .map((chip) => String(chip.dataset.note || ''));

  // La durata che si sta giudicando, presa dal piano: mai un numero inventato.
  function jobSpan(state) {
    let end = 0;
    (Array.isArray(state.timeline) ? state.timeline : []).forEach((row) => {
      end = Math.max(end, Number(row && row.t_out) || 0);
    });
    if (!end) {
      (Array.isArray(state.shots) ? state.shots : []).forEach((shot) => {
        end = Math.max(end, Number(shot && shot.t_out) || 0);
      });
    }
    return end;
  }

  function voteMetaLine(state) {
    const bits = [];
    const shots = Array.isArray(state.shots) ? state.shots.length : 0;
    const span = jobSpan(state);
    if (shots > 0) bits.push(shots + ' shot');
    if (span > 0) bits.push(mmss(span));
    const kind = outcomeOf(state, false);
    const word = VOTE_OUTCOME[kind];
    if (word) bits.push(word);
    return bits.join(' · ');
  }

  function voteSubLine(verdict, hasMaster) {
    if (verdict === 'done') return 'Puoi guardarlo con calma: il voto non blocca niente.';
    if (verdict === 'failed') {
      return hasMaster
        ? "Il master c'è, ma il lavoro non è passato: com'è andata lo stesso?"
        : "Non è arrivato al master. Dì com'è andata lo stesso.";
    }
    if (verdict === 'incomplete') return "Il master non c'è: il voto resta utile lo stesso.";
    if (verdict === 'stopped') return "Si è fermato prima della fine: com'è andata fino a lì?";
    return "Dimmi com'è andata.";
  }

  function paintVoteSegments() {
    el.voteScale.querySelectorAll('.vote-seg').forEach((seg) => {
      const value = Number(seg.dataset.rating);
      const on = voteRating === value;
      seg.classList.toggle('is-on', on);
      seg.classList.toggle('is-under', voteRating !== null && value < voteRating);
      seg.setAttribute('aria-checked', on ? 'true' : 'false');
    });
  }

  function resetVoteForm() {
    voteRating = null;
    voteSending = false;
    el.voteNote.value = '';
    el.voteChips.querySelectorAll('.vote-chip').forEach((chip) => {
      chip.setAttribute('aria-pressed', 'false');
    });
    el.voteSend.disabled = true;
    el.voteSend.removeAttribute('aria-busy');
    el.voteError.hidden = true;
    el.voteErrorText.textContent = '';
    el.voteFlash.textContent = '';
    el.voteFlash.style.opacity = '1';
    el.voteHint.textContent = VOTE_HINT;
    paintVoteSegments();
  }

  function chooseVote(value) {
    if (!Number.isFinite(value) || value < 1 || value > 5) return;
    voteRating = value;
    paintVoteSegments();
    el.voteSend.disabled = voteSending;
    el.voteHint.textContent =
      value + ' su 5 · ' + VOTE_WORD[value] + ' — Invio per registrare, Esc per chiudere.';
  }

  function noteForVote() {
    const parts = chipWords();
    const free = String(el.voteNote.value || '').trim();
    if (free) parts.push(free);
    return parts.join(' · ').slice(0, 240);
  }

  function showVoteGiven(rating, tail) {
    el.voteAsk.hidden = true;
    el.voteDone.hidden = false;
    el.voteDoneNum.textContent = rating + '/5';
    el.voteDoneSay.textContent = VOTE_WORD[rating] || '';
    el.voteDoneTicks.textContent = '';
    for (let step = 1; step <= 5; step += 1) {
      const tick = document.createElement('i');
      if (step <= rating) tick.className = 'is-on';
      el.voteDoneTicks.append(tick);
    }
    el.voteDoneNote.textContent = tail || 'registrato';
  }

  function dismissVote() {
    voteDismissed = true;
    el.voteCard.hidden = true;
  }

  async function sendVote() {
    if (voteSending || !voteRating) return;
    const rating = voteRating;
    voteSending = true;
    el.voteSend.disabled = true;
    el.voteSend.setAttribute('aria-busy', 'true');
    el.voteError.hidden = true;
    el.voteErrorText.textContent = '';
    flash(el.voteFlash, 'Registro il voto…', '', 'vote-flash num');

    try {
      let url = scoped('/api/feedback');
      url += (url.indexOf('?') === -1 ? '?' : '&') + 'rating=' + rating;
      const note = noteForVote();
      if (note) url += '&note=' + encodeURIComponent(note);
      const response = await fetch(url, { method: 'POST' });
      const result = await response.json();

      if (result && result.ok === true) {
        voteGivenLocal = rating;
        el.voteNote.value = '';
        el.voteChips.querySelectorAll('.vote-chip').forEach((chip) => {
          chip.setAttribute('aria-pressed', 'false');
        });
        showVoteGiven(rating, 'registrato ora');
        el.voteCard.classList.remove('is-thanked');
        void el.voteCard.offsetWidth; // riavvia il velo di conferma
        el.voteCard.classList.add('is-thanked');
        voteSending = false;
        el.voteSend.removeAttribute('aria-busy');
        await tick();
        return;
      }

      // Il server ha detto di no: il motivo si mostra com'è, la scheda resta.
      el.voteErrorText.textContent = String(
        (result && result.reason) || 'il server non ha risposto con un motivo'
      );
      el.voteError.hidden = false;
      window.clearTimeout(el.voteFlash._timer);
      el.voteFlash.textContent = '';
    } catch (error) {
      el.voteErrorText.textContent = 'il server non risponde';
      el.voteError.hidden = false;
      window.clearTimeout(el.voteFlash._timer);
      el.voteFlash.textContent = '';
    }

    voteSending = false;
    el.voteSend.removeAttribute('aria-busy');
    el.voteSend.disabled = !voteRating;
  }

  function paintVote(state, verdict) {
    if (voteJob !== jobIndex) {
      voteJob = jobIndex;
      voteGivenLocal = null;
      voteDismissed = false;
      el.voteCard.classList.remove('is-thanked');
      resetVoteForm();
    }

    const fb = state.feedback && typeof state.feedback === 'object' ? state.feedback : {};
    const asked = fb.asked === true;
    const stored = fb.given === true ? Number(fb.rating) || null : null;
    const running = verdict === 'running' || verdict === 'idle';
    // `auto` dice `asked` a fine lavoro; `given` resta vero anche dopo un
    // ricarico, così il voto dato continua a leggersi. In corso non si chiede.
    const show = !voteDismissed && !running && (asked || stored !== null || voteGivenLocal !== null);

    el.voteCard.hidden = !show;
    if (!show) return;

    el.voteMeta.textContent = voteMetaLine(state);
    el.voteSub.textContent = voteSubLine(verdict, state.master_ready === true);

    const answer = stored || voteGivenLocal;
    if (answer) {
      showVoteGiven(answer, stored ? 'registrato' : 'registrato ora');
      return;
    }
    el.voteAsk.hidden = false;
    el.voteDone.hidden = true;
  }

  el.voteScale.querySelectorAll('.vote-seg').forEach((seg) => {
    seg.addEventListener('click', () => chooseVote(Number(seg.dataset.rating)));
  });

  el.voteChips.querySelectorAll('.vote-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      const on = chip.getAttribute('aria-pressed') === 'true';
      chip.setAttribute('aria-pressed', on ? 'false' : 'true');
    });
  });

  el.voteSend.addEventListener('click', () => sendVote());
  el.voteClose.addEventListener('click', () => dismissVote());

  // Tastiera: 1–5 sceglie, Invio registra, Esc chiude senza votare.
  document.addEventListener('keydown', (event) => {
    if (el.voteCard.hidden || el.voteAsk.hidden) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (el.fontGate.open || el.planGate.open || !el.frameView.hidden) return;

    if (event.key === 'Escape') {
      event.preventDefault();
      dismissVote();
      return;
    }

    const focused = document.activeElement;
    const onButton =
      focused && focused.tagName === 'BUTTON' && el.voteCard.contains(focused);

    if (!onButton && event.key.length === 1 && event.key >= '1' && event.key <= '5') {
      event.preventDefault();
      chooseVote(Number(event.key));
      return;
    }

    if (event.key === 'Enter') {
      if (onButton) return; // il clic del pulsante fa già il suo lavoro
      event.preventDefault();
      sendVote();
    }
  });

  /* ------------------------------------------------------------ segno di vita */

  // La percentuale può non muoversi per minuti: accanto al numero ci sono il
  // dettaglio vero, da quanto dura la fase, quanto manca — e un battito. Il
  // battito sono le tacche degli ultimi secondi: se la sessione tace, si vede.
  const LIFE_SLOTS = 14;

  let beatKey = null;
  let beatAt = 0;
  let beatTrail = [];
  let percentSeen = null;

  function paintLifeTicks() {
    if (!el.lifeTicks.children.length) {
      for (let slot = 0; slot < LIFE_SLOTS; slot += 1) {
        el.lifeTicks.append(document.createElement('i'));
      }
    }
    const ticks = el.lifeTicks.children;
    const tail = beatTrail.slice(-LIFE_SLOTS);
    const offset = ticks.length - tail.length;
    for (let index = 0; index < ticks.length; index += 1) {
      const position = index - offset;
      const on = position >= 0 && tail[position] === true;
      ticks[index].classList.toggle('is-on', on);
      ticks[index].style.opacity = on
        ? String(0.3 + (0.7 * (position + 1)) / Math.max(1, tail.length))
        : '0.09';
    }
  }

  function paintNow(state, closed) {
    const percent = clamp(Number(state.percent) || 0, 0, 100);
    const moment = performance.now();
    const phases = Array.isArray(state.phases) ? state.phases : [];

    // Un numero fermo lo dice: «fermo al 42% da 3:12». Mai una percentuale che
    // resta identica per minuti senza una parola accanto.
    if (!percentSeen || percentSeen.value !== Math.round(percent)) {
      percentSeen = { value: Math.round(percent), at: moment };
    }
    const still = (moment - percentSeen.at) / 1000;
    const frozen = !closed && percent > 0 && still >= 40;
    el.heroStall.hidden = !frozen;
    if (frozen) {
      el.heroStall.textContent = 'fermo al ' + Math.round(percent) + '% da ' + mmss(still);
    }

    // `beat` è l'ultimo heartbeat che la sessione ha scritto per questo lavoro.
    const beat = state.beat && typeof state.beat === 'object' ? state.beat : {};
    const key =
      beat.at_sec === undefined || beat.at_sec === null
        ? ''
        : [beat.at_sec, beat.stage, beat.detail].join('|');
    const fresh = Boolean(key) && key !== beatKey;
    if (fresh) {
      beatKey = key;
      beatAt = moment;
    }
    beatTrail.push(fresh);
    if (beatTrail.length > LIFE_SLOTS) beatTrail = beatTrail.slice(-LIFE_SLOTS);
    paintLifeTicks();

    const age = fresh ? 0 : Math.round((moment - beatAt) / 1000);
    const alive = Boolean(key) && !closed;
    const quiet = alive && age >= 4;
    el.life.hidden = !alive;
    el.life.classList.toggle('is-quiet', quiet);
    if (alive) {
      el.lifeSay.textContent = quiet
        ? 'in silenzio da ' + age + ' s'
        : age < 1
          ? 'battito'
          : 'battito ' + age + ' s fa';
    }

    el.nowClock.hidden = !alive;
    if (alive) {
      const bits = ['qui da ' + mmss(beat.elapsed_sec)];
      const eta = Number(beat.eta_sec) || 0;
      if (eta > 0) bits.push('mancano ~' + mmss(eta));
      el.nowClock.textContent = bits.join(' · ');
    }

    el.nowLabel.textContent = beat.stage ? 'Ora · ' + beat.stage : 'Ora';

    const detail = String(beat.detail || '').trim();
    const active = phases.find((phase) => phase.key === state.current);
    if (detail && detail !== 'lavoro in corso') {
      el.nowText.textContent = detail;
    } else if (state.current) {
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

  function hideNow() {
    el.heroStall.hidden = true;
    el.life.hidden = true;
    el.nowClock.hidden = true;
    beatTrail = [];
    beatKey = null;
    paintLifeTicks();
  }

  /* ------------------------------------------------------------------ cancelli */

  // Un cancello aperto è l'unico caso in cui il lavoro *deve* sembrare fermo:
  // la pagina lo dice e offre la risposta, invece di lasciare la barra immobile.

  let gateKey = null;
  let gateBusy = false;

  function openGates(state) {
    const gates = [];
    (Array.isArray(state.questions) ? state.questions : []).forEach((question) => {
      if (!question || question.answered === true) return;
      gates.push({
        kind: 'ask',
        id: String(question.id || ''),
        text: String(question.question || ''),
        options: Array.isArray(question.options) ? question.options : [],
        waiting_for: Number(question.waiting_for) || 0,
      });
    });
    const approval = state.approval && typeof state.approval === 'object' ? state.approval : {};
    if (approval.waiting === true && gates.length === 0) {
      gates.push({
        kind: 'keyframes',
        id: 'keyframes',
        text: 'Approva i fotogrammi chiave: il render è fermo qui.',
        options: [],
        waiting_for: 0,
      });
    }
    return gates;
  }

  async function answerGate(id, value, button) {
    if (!id || gateBusy) return;
    gateBusy = true;
    button.disabled = true;
    flash(el.gateStopMsg, 'Registro la risposta…', '', 'gate-stop-msg num');
    try {
      let url = scoped('/api/answer');
      url +=
        (url.indexOf('?') === -1 ? '?' : '&') +
        'id=' + encodeURIComponent(id) +
        '&value=' + encodeURIComponent(value);
      const response = await fetch(url, { method: 'POST' });
      const result = await response.json();
      if (result && result.ok === true) {
        flash(el.gateStopMsg, 'Risposta registrata: il lavoro riparte.', 'ok', 'gate-stop-msg num');
        gateKey = null;
        await tick();
      } else {
        flash(
          el.gateStopMsg,
          String((result && result.reason) || 'la risposta non è stata registrata'),
          'warn',
          'gate-stop-msg num'
        );
        button.disabled = false;
      }
    } catch (error) {
      flash(
        el.gateStopMsg,
        'Il server non risponde: la risposta non è registrata.',
        'warn',
        'gate-stop-msg num'
      );
      button.disabled = false;
    }
    gateBusy = false;
  }

  function paintGates(state, closed) {
    const gates = closed ? [] : openGates(state);
    if (!gates.length) {
      if (!el.gateStop.hidden) {
        el.gateStop.hidden = true;
        el.gateStopOpts.textContent = '';
        el.gateStopMsg.textContent = '';
      }
      gateKey = null;
      return;
    }

    el.gateStop.hidden = false;
    const gate = gates[0];
    const key = gate.kind + '|' + gate.id + '|' + gate.text;
    if (key === gateKey) return;
    gateKey = key;

    el.gateStopQ.textContent = gate.text || 'Il lavoro aspetta una tua risposta.';
    el.gateStopOpts.textContent = '';

    if (gate.kind === 'keyframes') {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'gate-opt';
      button.textContent = 'Approva i fotogrammi';
      button.addEventListener('click', () => {
        if (el.approve.disabled) return;
        el.approve.click();
        flash(el.gateStopMsg, 'Approvazione in corso…', '', 'gate-stop-msg num');
      });
      el.gateStopOpts.append(button);
    } else {
      gate.options.forEach((option) => {
        const value = String(option && option.value !== undefined ? option.value : '');
        const label = String((option && (option.label || option.value)) || '') || value;
        if (!value) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'gate-opt';
        button.textContent = label;
        button.addEventListener('click', () => answerGate(gate.id, value, button));
        el.gateStopOpts.append(button);
      });
      if (!el.gateStopOpts.children.length) {
        el.gateStopOpts.append(
          document.createTextNode('La domanda non porta opzioni: si risponde da terminale.')
        );
      }
    }

    const waited = gate.waiting_for > 0 ? 'Aspetta da ' + mmss(gate.waiting_for) + '.' : '';
    const more = gates.length > 1 ? ' Altre ' + (gates.length - 1) + ' in coda.' : '';
    el.gateStopMsg.className = 'gate-stop-msg num';
    el.gateStopMsg.textContent = waited + more;
  }

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
    el.liveFill.style.setProperty('--p', total > 0 ? String(Math.min(1, ready / total)) : '0');

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

    // Il dialogo è una domanda al lavoro in corso: se il lavoro è chiuso non c'è
    // più nessuno che aspetta, e chiedere la scelta sarebbe una richiesta a
    // vuoto. La scelta registrata resta leggibile nella sua decisione.
    if (!pending || !jobRunning(state)) {
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
      node.fill.style.setProperty('--p', String(percent / 100));
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
    hideNow();
    percentSeen = null;
    el.gateStop.hidden = true;
    el.gateStopOpts.textContent = '';
    el.gateStopMsg.textContent = '';
    gateKey = null;
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

  /* ---------------------------------------------------------------- sessione */

  // Aprendo `/` sul daemon il server manda una sessione: `session, events,
  // artifacts, gates, questions, live, journal` e — quando la cartella del
  // lavoro esiste ancora — anche `job`, lo stesso stato che darebbe `?job=`.
  // La pagina sa disegnare un lavoro: qui la sessione prende quella forma, e
  // quello che manca lo dice invece di restare sul guscio vuoto con lo 0%.

  const SESSION_STATUS_WORD = {
    running: 'in corso',
    done: 'finita',
    stopped: 'fermata',
    failed: 'non riuscita',
    incomplete: 'senza esito',
    absent: 'assente',
  };

  const SESSION_OUTCOME = {
    done: 'done',
    stopped: 'stopped',
    failed: 'failed',
    incomplete: 'incomplete',
    running: 'running',
  };

  // La riga di chiusura del racconto: il lavoro, non la sessione.
  const END_WORD = {
    done: 'lavoro concluso',
    stopped: 'lavoro fermato',
    failed: 'lavoro non riuscito',
    incomplete: 'lavoro senza master',
  };

  // I nomi dei passaggi, in italiano: la sessione li scrive in inglese.
  const STAGE_LABEL = {
    triage: 'scelta del materiale',
    ingest: 'acquisizione',
    bridge: 'montaggio',
    render: 'render',
    render_done: 'render concluso',
    qc: 'controllo',
    deliverables: 'consegna',
    questions: 'domande',
    probe: 'sonda dei file',
    hash: 'impronta dei file',
    proxy: 'proxy',
    audio: 'audio',
    analysis: 'analisi',
    faces: 'volti',
    speech: 'parlato',
    measure_wall: 'misure',
    plan: 'piano',
    coherence: 'coerenza',
    plan_choice: 'versione del lavoro',
    creative: 'scelta creativa',
    font: 'carattere',
    typography: 'tipografia',
    keyframes: 'fotogrammi chiave',
    recipe: 'ricetta',
    effects: 'effetti',
    guard: 'guardia',
    verify: 'verifica',
    unaccounted: 'tempo non contato',
  };

  const GATE_LABEL = { font: 'carattere', keyframes: 'fotogrammi', plan: 'piano' };

  const DECISION_QUESTION = {
    font: 'Quale carattere',
    typography: 'Come comporre il testo',
    subtitles: 'Sottotitoli',
    transition_kinds: 'Transizioni',
    plan_choice: 'Versione del lavoro',
    keyframes: 'Fotogrammi chiave',
    music: 'Musica',
  };

  const stageLabel = (key) => {
    const name = String(key || '');
    return STAGE_LABEL[name] || name.replace(/_/g, ' ');
  };

  function basename(path) {
    const text = String(path || '').trim();
    if (!text) return '';
    const parts = text.split(/[\\/]/);
    return parts[parts.length - 1] || text;
  }

  // Il titolo del lavoro è il prompt del comando, se il comando ne aveva uno.
  function promptOfArgv(argv) {
    const list = Array.isArray(argv) ? argv.map(String) : [];
    const at = list.indexOf('--prompt');
    if (at >= 0 && list[at + 1]) return list[at + 1].trim();
    const out = list.indexOf('--out');
    if (out >= 0 && list[out + 1]) return basename(list[out + 1]);
    return '';
  }

  function stageRows(session) {
    const rows = Array.isArray(session && session.stages) ? session.stages : [];
    return rows.filter((row) => row && String(row.stage || ''));
  }

  function lastStageName(session) {
    const rows = stageRows(session);
    return rows.length ? String(rows[rows.length - 1].stage) : '';
  }

  // L'ultima percentuale che la sessione ha dichiarato: è lei la verità quando
  // il lavoro non pubblica un panel suo.
  function lastPercent(session) {
    const rows = stageRows(session);
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      const value = rows[i].percent;
      if (value === null || value === undefined) continue;
      const number = Number(value);
      if (Number.isFinite(number)) return clamp(number, 0, 100);
    }
    return null;
  }

  // La sequenza: i passaggi che hanno dichiarato un avanzamento, nell'ordine in
  // cui sono arrivati. Le righe di solo tempo (probe, hash, audio…) sono il
  // conto delle misure, non fasi: restano fuori dalla spina.
  function sessionPhases(session, running) {
    const phases = [];
    const seen = new Set();
    stageRows(session).forEach((row) => {
      const key = String(row.stage);
      const percent = row.percent;
      // `null` non è zero: uno stadio senza percentuale non è una fase.
      if (seen.has(key) || percent === null || percent === undefined) return;
      if (!Number.isFinite(Number(percent))) return;
      seen.add(key);
      phases.push({ key, label: stageLabel(key) });
    });
    if (running) {
      const key = lastStageName(session);
      if (key && !seen.has(key)) phases.push({ key, label: stageLabel(key) });
    }
    return phases;
  }

  function eventsToJournal(events) {
    const rows = [];
    const stages = new Set();
    const keys = new Set();
    (Array.isArray(events) ? events : []).forEach((event) => {
      const type = String(event.type || '');
      const t = Number(event.t) || 0;
      const phase = String(event.stage || '');
      let line = '';
      let kind = 'altro';

      if (type === 'start') {
        const prompt = promptOfArgv(event.argv);
        const what = String(event.kind || 'lavoro');
        line = 'Inizio: ' + what + (prompt ? ' — ' + prompt : '');
        kind = 'misura';
      } else if (type === 'stage') {
        const stage = String(event.stage || '');
        const detail = String(event.detail || '').trim();
        const percent = Number(event.percent);
        const timing = /^[\d.,]+\s*s$/.test(detail);
        if (detail && !timing) {
          line = stageLabel(stage) + ': ' + detail;
        } else if (detail) {
          line = stageLabel(stage) + ' in ' + detail.replace('.', ',');
        } else if (!stages.has(stage)) {
          line = 'Fase «' + stageLabel(stage) + '»' +
            (Number.isFinite(percent) ? ' al ' + Math.round(percent) + '%' : '');
        }
        stages.add(stage);
        kind = /deliver|qc/.test(stage) ? 'consegna'
          : /render/.test(stage) ? 'render'
            : /bridge/.test(stage) ? 'taglio'
              : /audio/.test(stage) ? 'audio'
                : 'misura';
      } else if (type === 'decision') {
        const id = String(event.id || '');
        const question = DECISION_QUESTION[id] || ('Scelta «' + id.replace(/_/g, ' ') + '»');
        line = question + ': ' + (String(event.choice || '') || 'nessuna scelta') +
          (String(event.by || '') === 'user' ? ' (tua)' : '');
        kind = /font|text|subtitle/.test(id) ? 'testo' : 'taglio';
      } else if (type === 'gate') {
        const gate = GATE_LABEL[String(event.gate || '')] || String(event.gate || '');
        if (event.open === true) {
          line = 'Cancello ' + gate + ' aperto' +
            (String(event.detail || '').trim() ? ': ' + String(event.detail).trim() : '');
          kind = 'altro';
        } else {
          line = 'Cancello ' + gate + ' chiuso' +
            (String(event.chosen || '').trim() ? ' · ' + String(event.chosen).trim() : '') +
            (String(event.by || '').trim() ? ' da ' + String(event.by).trim() : '');
          kind = 'consegna';
        }
      } else if (type === 'artifact') {
        const name = basename(event.path) || String(event.kind || 'file');
        const bytes = Number(event.bytes) || 0;
        line = 'Scritto ' + name + (bytes > 0 ? ' · ' + sizeLabel(bytes) : '');
        kind = 'consegna';
      } else if (type === 'end') {
        line = 'Fine: ' +
          (END_WORD[String(event.status || '')] || 'sessione chiusa');
        kind = 'consegna';
      } else if (type === 'error') {
        line = 'Errore: ' + (String(event.message || '').trim() || 'non dichiarato');
        kind = 'altro';
      }

      const text = line.trim();
      if (!text) return;
      const key = t + '|' + text;
      if (keys.has(key)) return;
      keys.add(key);
      rows.push({ t, phase, kind, line: text });
    });
    return rows;
  }

  // Il battito: l'ultimo heartbeat della sessione, con il suo stadio e il tempo
  // che dichiara. Senza heartbeat vale l'ultimo stadio scritto.
  function eventsToBeat(session, events) {
    const rows = Array.isArray(events) ? events : [];
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      const row = rows[i];
      if (String(row.type || '') !== 'heartbeat') continue;
      return {
        stage: stageLabel(row.stage),
        detail: String(row.detail || ''),
        elapsed_sec: Number(row.elapsed_sec) || 0,
        eta_sec: Number(row.eta_sec) || 0,
        percent: Number(row.percent),
        at_sec: Number(row.t) || 0,
      };
    }
    const stage = lastStageName(session);
    if (!stage) return {};
    const written = stageRows(session);
    const last = written[written.length - 1] || {};
    return {
      stage: stageLabel(stage),
      detail: String(last.detail || ''),
      elapsed_sec: Number(session.t) || 0,
      eta_sec: 0,
      percent: Number(last.percent),
      at_sec: Number(last.t) || 0,
    };
  }

  function emptyJobView() {
    return {
      job: '',
      path: '',
      active: false,
      locked: false,
      updated_sec: null,
      updated_at: '',
      state: '',
      prompt: '',
      clips: 0,
      percent: 0,
      elapsed_sec: 0,
      current: null,
      note: '',
      estimate: {},
      plan_choice: {},
      decisions: [],
      outcome: 'idle',
      guard: { ok: true, skipped: true, why: '', violations: [], warnings: [] },
      phases: [],
      journal: [],
      frames: [],
      approved: false,
      approval: { required: false, approved: false, waiting: false, reason: 'nessun piano di fotogrammi' },
      master: '',
      master_ready: false,
      master_bytes: 0,
      render: { out_time_sec: 0, duration_sec: 0, percent: 0, speed: '' },
      timeline: [],
      shots: [],
      live: { ready: 0, total: 0, chunks: [] },
      font: { pending: false, sample: '', requirement: '', pool: '', chosen: '', candidates: [] },
      questions: [],
      feedback: { asked: false, given: false, rating: null, summary: {} },
      beat: {},
      progress: { value: null, source: '', age_sec: null, alive: false, why: '' },
      progress_source: '',
      run: null,
    };
  }

  function sessionView(payload) {
    const session = payload.session;
    const job = payload.job && typeof payload.job === 'object' ? payload.job : null;
    const artifacts = Array.isArray(payload.artifacts) ? payload.artifacts : [];
    const events = Array.isArray(payload.events) ? payload.events : [];
    const found = Array.isArray(payload.jobs) ? payload.jobs.length : 0;
    const view = Object.assign(emptyJobView(), job || {});

    const status = String(session.status || '');
    const running = status === 'running';
    const elapsed = Number(session.t) || 0;

    // Niente sessione, niente lavoro, niente eventi: non c'è nulla da
    // raccontare. Non è uno stato «idle» con lo 0%: è un'assenza, e la pagina
    // la mostra come tale invece di promettere un'attesa che non esiste.
    if (!String(session.id || '').trim() && !job && !found && !events.length) {
      view.session = session;
      view.jobs = [];
      view.empty = {
        reason:
          String(payload.reason || session.reason || '').trim() ||
          'nessun lavoro è stato ancora eseguito su questa macchina',
      };
      return view;
    }

    view.session = session;
    view.jobs = [];
    view.sessionMissing = !job;

    // Il master è un fatto della sessione: se il file è stato scritto, la zona
    // «Risultato» si apre anche quando il lavoro non pubblica un panel suo.
    const masters = artifacts.filter(
      (item) => item && String(item.kind) === 'master' && Number(item.bytes) > 0
    );
    const master = masters.length ? masters[masters.length - 1] : null;
    if (master && view.master_ready !== true) {
      view.master_ready = true;
      view.master = basename(master.path) || 'master.mp4';
      view.master_bytes = Number(master.bytes) || 0;
    }

    // Il racconto è la storia della sessione, non un log: solo i fatti.
    const journal = eventsToJournal(events);
    if (journal.length) view.journal = journal;
    view.beat = eventsToBeat(payload.session, events);

    if (!String(view.prompt || '').trim()) view.prompt = promptOfArgv(session.argv);
    if (!Number(view.elapsed_sec)) view.elapsed_sec = elapsed;
    if (!Number(view.clips) && Array.isArray(view.shots)) view.clips = view.shots.length;

    // In corso: la testina sta sull'ultimo stadio. Finita: nessuna testina.
    const phases = sessionPhases(session, running);
    if (phases.length) view.phases = phases;
    view.current = running ? (view.current || lastStageName(session)) : null;

    const fromSession = lastPercent(session);
    if ((Number(view.percent) || 0) <= 0 && fromSession !== null) view.percent = fromSession;

    // In corso la percentuale è quella che la sessione dichiara: dirlo evita
    // che un 25% fermo per due minuti sembri un lavoro impuntato. Se invece il
    // lavoro ha un panel suo, la percentuale è la sua e la nota resta la sua.
    if (running && job && (Number(job.percent) || 0) <= 0) {
      view.note = 'La percentuale è quella delle fasi della sessione: ' +
        'questo lavoro non ne pubblica una sua.';
    }

    if (!job) {
      // La cartella del lavoro non c'è più: la sessione resta leggibile, il
      // lavoro no, e la pagina lo scrive invece di mostrare uno stato vuoto.
      view.outcome = SESSION_OUTCOME[status] || (running ? 'running' : 'idle');
      view.note = String(payload.reason || '').trim() ||
        'La sessione non ha lasciato una cartella di lavoro da rileggere.';
      view.result_note =
        "La cartella del lavoro non è più sul disco: qui non c'è un master da aprire.";
      view.master_ready = false;
      view.master = '';
      view.master_bytes = 0;
    } else if (view.outcome === 'done' && view.master_ready) {
      view.note =
        'sessione ' + (String(session.kind || 'vx')) + ' finita in ' + mmss(elapsed) +
        ' · ' + view.master + ' (' + sizeLabel(view.master_bytes) + ')';
    }

    return view;
  }

  // Cos'è la sessione che si sta guardando, in testa: non un lavoro appena
  // partito, ma l'ultima cosa che il daemon ha eseguito.
  function paintSession(view) {
    const session = view && view.session;
    if (!session) {
      el.sessionLine.hidden = true;
      return;
    }
    const status = String(session.status || '');
    const running = status === 'running';
    const kind = String(session.kind || '').trim();

    // Nessuna sessione nel registro: la riga non sparisce, dice cosa manca.
    if (!String(session.id || '').trim()) {
      el.sessionLine.hidden = false;
      el.sessionLine.classList.remove('is-live', 'is-done');
      el.sessionLine.classList.add('is-missing');
      el.sessionChip.textContent = 'nessuna sessione';
      el.sessionSay.textContent = tidyReason(session.reason) ||
        'nessun comando vx è stato eseguito su questa macchina';
      el.sessionLine.title = el.sessionSay.textContent;
      return;
    }

    const bits = [];
    if (kind) bits.push('vx ' + kind);
    bits.push(SESSION_STATUS_WORD[status] || status || 'senza stato');
    if (Number(session.t) > 0) bits.push(mmss(session.t));
    const phases = Array.isArray(view.phases) ? view.phases.length : 0;
    if (phases) bits.push(phases + (phases === 1 ? ' fase' : ' fasi'));
    const decisions = Array.isArray(view.decisions) ? view.decisions.length : 0;
    if (decisions) bits.push(decisions + (decisions === 1 ? ' decisione' : ' decisioni'));
    const shots = Array.isArray(view.shots) ? view.shots.length : 0;
    if (shots) bits.push(shots + ' shot');

    el.sessionLine.hidden = false;
    el.sessionLine.classList.toggle('is-live', running);
    el.sessionLine.classList.toggle('is-done', status === 'done');
    el.sessionLine.classList.toggle('is-missing', view.sessionMissing === true);
    el.sessionChip.textContent = sessionParam ? 'sessione richiesta' : 'ultima sessione';
    el.sessionSay.textContent = bits.join(' · ');
    el.sessionLine.title =
      'sessione ' + (session.id || 'senza id') +
      (session.job_path ? ' · ' + session.job_path : ' · nessuna cartella dichiarata');
  }

  /* ----------------------------------------------------------- stato vuoto */

  // La pagina può non avere niente da mostrare: nessuna sessione nel registro,
  // nessun lavoro nella cartella. Non è un lavoro allo 0% in attesa: è
  // un'assenza, e va scritta come tale — con il motivo vero e con cosa fare per
  // uscirne. Niente voto, niente esito, niente regia: non c'è un lavoro a cui
  // chiederli.

  // Il server scrive i motivi senza accenti: qui tornano leggibili. L'ordine
  // conta: «perche'» prima di «e'», o diventerebbe «perchè».
  const REASON_FIXES = [
    [/\bpiu'(?![a-zà-ù])/gi, 'più'],
    [/\bcosi'(?![a-zà-ù])/gi, 'così'],
    [/\bperche'(?![a-zà-ù])/gi, 'perché'],
    [/\bgia'(?![a-zà-ù])/gi, 'già'],
    [/\bpuo'(?![a-zà-ù])/gi, 'può'],
    [/\be'(?![a-zà-ù])/g, 'è'],
    [/\bE'(?![a-zà-ù])/g, 'È'],
  ];

  function tidyReason(text) {
    let out = String(text || '').trim();
    REASON_FIXES.forEach(([pattern, word]) => {
      out = out.replace(pattern, word);
    });
    return out.replace(/\s+/g, ' ');
  }

  function paintEmpty(state) {
    const empty = (state && state.empty) || {};
    const reason = tidyReason(empty.reason) || 'nessun lavoro è stato ancora eseguito';

    // Una classe sola decide cosa si vede: il resto della pagina non esiste.
    body.classList.add('is-empty');
    body.classList.remove('is-done', 'is-stopped', 'is-failed', 'is-incomplete');
    body.classList.remove('has-master', 'is-offline', 'is-stamped');
    body.classList.add('is-idle');

    if (el.fontGate.open) closeGate();
    if (el.planGate.open) closePlanGate();
    if (!el.frameView.hidden) closeFrame({ immediate: true });
    stopLive();
    hideNow();
    lastState = state;
    lastClosed = false;

    // La riga in testa dice lo stato vero: nessuna sessione, e perché.
    paintSession(state);

    el.emptyWork.hidden = false;
    el.emptyReason.textContent = upperFirst(reason) + (/[.!?]$/.test(reason) ? '' : '.');

    el.stateWord.textContent = 'Nessuna sessione';
    el.stageHead.hidden = true;
    el.chipWrap.hidden = true;
    el.hero.hidden = true;
    el.bar.hidden = true;
    el.bar.setAttribute('aria-hidden', 'true');
    el.now.hidden = true;
    el.verdict.hidden = true;
    el.gateStop.hidden = true;
    el.jobClock.hidden = true;
    el.jobClock.textContent = '';

    el.jobTitle.textContent = 'Nessun lavoro da mostrare';
    document.title = 'vx — nessun lavoro';

    // Senza un lavoro non c'è un contratto da mostrare né un timbro da esibire.
    el.contract.hidden = true;
    el.scaletta.hidden = true;
    el.jobStamp.hidden = true;
  }

  // Il contrario: c'è un lavoro, la pagina torna a essere una pagina di lavoro.
  function clearEmpty() {
    body.classList.remove('is-empty');
    el.emptyWork.hidden = true;
    el.stageHead.hidden = false;
    el.chipWrap.hidden = false;
    el.hero.hidden = false;
    el.bar.hidden = false;
    el.bar.removeAttribute('aria-hidden');
    el.jobClock.hidden = false;
  }

  /* ------------------------------------------------------------------ stato */

  function render(state) {
    if (approving) return;
    if (state && state.empty) {
      paintEmpty(state);
      return;
    }
    clearEmpty();
    const phases = Array.isArray(state.phases) ? state.phases : [];
    const frames = Array.isArray(state.frames) ? state.frames : [];
    // La fonte di verita' e' `progress.value`: `state.percent` resta il ripiego
    // per i payload di una versione precedente del server.
    const report =
      state.progress && typeof state.progress === 'object' ? state.progress : null;
    const reported =
      report && report.value !== null && report.value !== undefined
        ? Number(report.value)
        : NaN;
    const percent = clamp(
      Number.isFinite(reported) ? reported : Number(state.percent) || 0,
      0,
      100
    );

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
    paintSession(state);
    lastState = state;
    paintJournal(state);
    paintDecisions(state);
    paintStamp(state);
    paintContract(state);
    paintScaletta(state);

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
    // Cosa dire in testa quando il lavoro è chiuso: una riga per l'esito, non
    // un'etichetta che copre il motivo.
    const closing = closed ? closeReport(state, verdict) : null;

    body.classList.toggle('is-done', verdict === 'done');
    body.classList.toggle('is-stopped', verdict === 'stopped');
    body.classList.toggle('is-failed', verdict === 'failed');
    body.classList.toggle('is-incomplete', verdict === 'incomplete');
    body.classList.toggle('is-idle', verdict === 'idle');
    body.classList.remove('is-offline');

    lastClosed = closed;
    lastVerdict = verdict;
    paintRegia(state, closed);
    paintEstimate(state, verdict);
    paintLive(state, closed);
    paintVote(state, verdict);

    const prompt = String(state.prompt || '').trim();
    el.jobTitle.textContent = prompt || 'Lavoro senza titolo';
    document.title = 'vx — ' + (prompt ? prompt.slice(0, 70) : 'lavoro in corso');

    el.stateWord.textContent = closing ? closing.word : (OUTCOME_WORD[verdict] || 'In corso');
    el.jobClock.textContent = mmss(state.elapsed_sec);

    const clips = Number(state.clips) || 0;
    el.clipChip.textContent = clips === 1 ? '1 clip' : clips > 1 ? clips + ' clip' : 'clip non dichiarate';

    // Il server manda la percentuale **vera** in `progress`: se `value` e' null
    // non c'e' un numero da mostrare (progresso assente o fermo), e uno zero
    // sarebbe un'affermazione falsa. La ragione e' `progress.why`.
    const known =
      Boolean(report) &&
      report.value !== null &&
      report.value !== undefined &&
      Number.isFinite(Number(report.value));
    const unknown = Boolean(report) && !known && Boolean(String(report.why || '').trim());

    // A lavoro chiuso senza percentuale pubblicata (nessun panel) non c'è uno
    // 0%: al posto del numero il segno dell'esito. Un lavoro aperto senza
    // progresso vivo non mostra 0% ma un trattino e la ragione. La barra è piena
    // solo quando il lavoro è davvero arrivato in fondo.
    const mark =
      (closed && percent < 1 ? OUTCOME_MARK[verdict] || '–' : '') ||
      (!closed && unknown ? '—' : '');
    el.hero.classList.toggle('is-closed', Boolean(mark));
    el.heroUnit.hidden = Boolean(mark);
    if (mark) {
      shownPercent = null;
      el.percent.textContent = mark;
    } else {
      setPercent(percent);
    }

    const barPercent = verdict === 'done' && percent < 1 ? 100 : percent;
    el.bar.setAttribute('aria-valuenow', String(Math.round(barPercent)));
    el.fill.style.setProperty('--p', String(clamp(barPercent, 0, 100) / 100));
    const barWidth = el.barTrack ? el.barTrack.clientWidth : 0;
    const barInner = Math.max(0, barWidth - 8);
    el.barHead.style.setProperty(
      '--x',
      (3 + (clamp(barPercent, 0, 100) / 100) * barInner).toFixed(1) + 'px'
    );
    el.barHead.classList.toggle('is-on', barPercent > 0);

    const activeIndex = phases.findIndex((p) => p.key === state.current);
    el.heroLabel.textContent = mark ? 'esito' : 'avanzamento';
    const why = report ? String(report.why || '').trim() : '';
    if (!closed && unknown && why) {
      // «sto lavorando · nessun aggiornamento da 12 s»: il battito resta sotto.
      el.heroSub.textContent = why;
    } else if (verdict === 'done' || (finished && !closed)) {
      // «tutte le fasi» vale solo per un lavoro arrivato in fondo: un fallito al
      // 100% ha finito il giro, non le fasi.
      el.heroSub.textContent = pad2(phases.length) + ' fasi su ' + pad2(phases.length);
    } else if (activeIndex >= 0) {
      el.heroSub.textContent = 'fase ' + pad2(activeIndex + 1) + ' di ' + pad2(phases.length);
    } else if (closed) {
      el.heroSub.textContent = 'lavoro chiuso';
    } else {
      el.heroSub.textContent = 'in attesa del lavoro';
    }

    // sequenza
    const phaseSignature = phases.map((p) => p.key).join('|');
    if (el.phases.dataset.signature !== phaseSignature) {
      el.phases.dataset.signature = phaseSignature;
      buildPhases(phases);
    }
    paintPhases(phases, activeIndex, finished || verdict === 'done');
    el.phasesEmpty.hidden = phases.length > 0;
    el.railSub.textContent =
      phases.length === 0 ? 'nessuna fase' : pad2(phases.length) + ' fasi';

    // ora / esito / cancelli
    // Nessun cancello a lavoro chiuso: le domande si mostrano solo a chi sta
    // ancora lavorando (vedi `jobRunning`).
    paintGates(state, closed || !jobRunning(state));
    if (closed) {
      el.now.hidden = true;
      hideNow();
      el.verdict.hidden = false;
      el.verdict.className = 'verdict is-' + verdict;
      el.verdictMark.textContent = OUTCOME_MARK[verdict] || '–';
      el.verdictTitle.textContent = closing.title;
      el.verdictText.textContent = closing.text;
      el.verdictMeta.textContent = closing.meta;
    } else {
      el.verdict.hidden = true;
      el.now.hidden = false;
      paintNow(state, closed);
    }

    // fotogrammi e approvazione
    const approvalReason =
      state.approval && typeof state.approval === 'object'
        ? String(state.approval.reason || '')
        : '';
    paintFrames(frames, finished, approvalReason);
    paintResult(state, closed);
    paintFontGate(state);
    paintPlanChoice(state);
    paintApproval(state, frames.length);
  }

  /* ------------------------------------------------------------------ rete */

  let inflight = false;
  let approving = false;
  let hasDrawn = false;
  let lastState = null;
  let lastVerdict = null;

  // La testina è posizionata in pixel: se cambia la larghezza va ricalcolata.
  window.addEventListener('resize', () => {
    if (!lastState) return;
    const percent = clamp(Number(lastState.percent) || 0, 0, 100);
    el.fill.style.setProperty('--p', String(percent / 100));
    const barWidth = el.barTrack ? el.barTrack.clientWidth : 0;
    el.barHead.style.setProperty(
      '--x',
      (3 + (percent / 100) * Math.max(0, barWidth - 8)).toFixed(1) + 'px'
    );
    paintRegia(lastState, lastClosed);
  });

  // La forma che il resto del file sa disegnare. Una lettura senza sessione e
  // senza nessun lavoro è uno stato vuoto anche quando non viene dal registro
  // (`vx serve DIR` su una cartella che non contiene ancora un lavoro): non c'è
  // niente da aspettare, e la pagina non deve inventare uno 0%.
  function payloadView(payload) {
    if (payload && payload.session) return sessionView(payload);
    if (!payload || typeof payload !== 'object') return payload;
    const named = String(payload.job || '').trim();
    const found = Array.isArray(payload.jobs) ? payload.jobs.length : 0;
    const events = Array.isArray(payload.events) ? payload.events.length : 0;
    if (named || found || events) return payload;
    const where = String(payload.path || '').trim();
    return Object.assign({}, payload, {
      empty: {
        reason: where
          ? 'nella cartella servita non c’è nessun lavoro: ' + where
          : 'non c’è nessun lavoro da mostrare',
      },
    });
  }

  async function tick() {
    if (inflight) return;
    inflight = true;
    try {
      const response = await fetch(scoped('/api/state'), { cache: 'no-store' });
      if (!response.ok) throw new Error('stato ' + response.status);
      const payload = await response.json();
      // Con `session` il server manda anche `job`: la pagina disegna quello, e
      // prende da `events` il racconto e il battito che il registro contiene.
      render(payloadView(payload));
    } catch (error) {
      body.classList.add('is-offline');
      el.stateWord.textContent = 'Senza collegamento';
      el.verdict.hidden = true;
      el.now.hidden = false;
      hideNow();
      el.nowText.textContent =
        'Non riesco a leggere lo stato del lavoro: il server locale non risponde.';
      el.nowNote.hidden = true;
    } finally {
      inflight = false;
      // Il lavoro scelto può essere cambiato da questa lettura: è qui che il
      // motore riparte (o resta zitto) senza un secondo orologio per lo stato.
      sceneSync();
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

  /* ------------------------------------------------------------------ motore */

  // La scena si guarda entrare. `/api/scene` si chiede solo a sezione scoperta,
  // scheda in primo piano e lavoro vivo; dopo tre letture vuote di fila la
  // domanda smette, e riprende quando cambia il lavoro scelto. Niente gusci
  // vuoti: se il motore non ha pubblicato, la sezione non esiste.
  const SCENE_EVERY_MS = 1000;
  const SCENE_EMPTY_LIMIT = 3;

  const motore = {
    key: null, // lavoro selezionato: cambiarlo riarma la domanda
    timer: null,
    inflight: false,
    ticket: 0,
    inView: false,
    nulls: 0,
    stopped: false,
    engine: '',
    frames: [],
    last: null,
    total: null,
    sha: '',
    bytes: null,
    nondet: false,
    meta: false,
    stamp: '',
    quiet: 0,
    stalled: false,
    byteTries: 0,
    finalRead: false,
  };

  // Il peso esatto è quello che il browser ha già scaricato: la riga lo legge
  // dalla voce di rete, senza chiedere di nuovo il fotogramma. Il manifesto si
  // legge una volta per scena, solo per il campo che l'API non porta.
  function sceneBytes() {
    if (motore.bytes !== null && Number(motore.bytes) > 0) return Number(motore.bytes);
    const url = motore.last && motore.last.url ? String(motore.last.url) : '';
    if (!url || typeof performance === 'undefined') return null;
    const entries = performance.getEntriesByType('resource');
    for (let i = entries.length - 1; i >= 0; i -= 1) {
      const entry = entries[i];
      if (!entry || entry.name.indexOf(url) === -1) continue;
      const size = Number(entry.encodedBodySize);
      if (Number.isFinite(size) && size > 0) {
        motore.bytes = size;
        return size;
      }
    }
    return null;
  }

  function sceneManifest() {
    const engine = motore.engine;
    if (!engine || motore.meta) return;
    motore.meta = true;
    const url = '/scene/' + encodeURIComponent(engine) + '/manifest.json';
    fetch(scoped(url), { cache: 'no-store' })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!data || !motore.engine) return;
        if (data.nondeterministic === true) motore.nondet = true;
        sceneBadge();
      })
      .catch(() => {
        // Il manifesto è un di più: la striscia vive anche senza.
      });
  }

  // A scena finita il manifesto non è più in scrittura: è il momento giusto
  // per leggere il peso esatto dell'ultimo fotogramma, senza rischi di contesa.
  function sceneFinalBytes() {
    if (motore.bytes !== null || motore.finalRead) return;
    motore.finalRead = true;
    const engine = motore.engine;
    if (!engine) return;
    fetch(scoped('/scene/' + encodeURIComponent(engine) + '/manifest.json'), {
      cache: 'no-store',
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!data || !motore.last) return;
        const rows = Array.isArray(data.frames) ? data.frames : [];
        const row = rows.find((item) => item && item.sha256 === motore.last.sha256);
        const size = row ? Number(row.bytes) : 0;
        if (size > 0) {
          motore.bytes = size;
          sceneStatus();
        }
      })
      .catch(() => {
        // Resta la voce di rete, se c'è.
      });
  }

  function sceneBadge() {
    el.motoreBadge.hidden = !motore.nondet;
  }

  // La voce di rete compare qualche istante dopo il load: si riguarda, senza
  // rileggere niente, così la riga non resta senza il peso sull'ultimo frame.
  function sceneByteRetry() {
    if (motore.bytes !== null || motore.byteTries >= 3) return;
    motore.byteTries += 1;
    window.setTimeout(() => {
      sceneStatus();
      if (motore.bytes === null) sceneByteRetry();
    }, 260);
  }

  function sceneStatus() {
    const last = motore.last;
    if (!last) return;
    const total = Number(motore.total) || motore.frames.length;
    const index = Number(last.i);
    const shown = Number.isFinite(index) ? index + 1 : motore.frames.length;
    const whole = Number(last.t) || 0;
    const t = Number.isInteger(whole) ? String(whole) : num(whole, 2).replace(/0$/, '');
    // Lo spazio duro tiene insieme numero e unità: su schermo stretto la riga
    // va a capo, ma non lascia un «B» da solo.
    let line =
      'frame ' + shown + '/' + total + ' · t\u00a0' + t + ' · ' + motore.engine;
    const bytes = Number(sceneBytes());
    if (Number.isFinite(bytes) && bytes > 0) line += ' · ' + bytes + '\u00a0B';
    if (el.motoreState.textContent !== line) el.motoreState.textContent = line;
    el.motoreFill.style.width =
      (total > 0 ? clamp((shown / total) * 100, 0, 100) : 0).toFixed(1) + '%';
  }

  function sceneHash() {
    const sha = String((motore.last && motore.last.sha256) || '');
    if (!sha) {
      el.motoreHash.textContent = '';
      el.motoreHash.removeAttribute('title');
      el.motoreHash.hidden = true;
      return;
    }
    const short = sha.slice(0, 12);
    el.motoreHash.textContent = 'impronta ' + short;
    el.motoreHash.title = 'sha256 ' + sha;
    el.motoreHash.hidden = false;
  }

  function sceneBig() {
    const last = motore.last;
    if (!last || !last.url) return;
    const url = String(last.url);
    if (el.motoreImg.getAttribute('src') === url) return;
    const index = Number(last.i);
    const shown = Number.isFinite(index) ? index + 1 : motore.frames.length;
    el.motoreImg.classList.remove('is-loaded');
    el.motoreImg.alt = 'Fotogramma ' + shown + ' di ' + (Number(motore.total) || motore.frames.length);
    el.motoreImg.src = url;
  }

  el.motoreImg.addEventListener('load', () => {
    el.motoreImg.classList.add('is-loaded');
    if (el.motoreImg.naturalWidth > 0 && el.motoreImg.naturalHeight > 0) {
      el.motore.style.setProperty(
        '--motore-aspect',
        el.motoreImg.naturalWidth + ' / ' + el.motoreImg.naturalHeight
      );
    }
    sceneStatus(); // il peso esatto arriva con la voce di rete dell'immagine
    sceneByteRetry();
  });

  el.motoreImg.addEventListener('error', () => {
    el.motoreImg.classList.remove('is-loaded');
  });

  // La striscia si aggiorna per differenza: il fotogramma che c'era resta
  // dov'è, quello nuovo si aggiunge in fondo. Ricostruire tutto a ogni poll
  // vorrebbe dire riscaricare quaranta miniature al secondo.
  function sceneCell(frame, shown, total) {
    const item = document.createElement('li');
    item.className = 'mframe';

    const img = document.createElement('img');
    img.alt = 'Fotogramma ' + shown + ' di ' + total;
    img.decoding = 'async';
    img.addEventListener('load', () => img.classList.add('is-loaded'));
    // L'anello del manifesto butta i fotogrammi vecchi: una miniatura può
    // sparire mentre la si chiede. Il riquadro resta, non l'icona rotta.
    img.addEventListener('error', () => item.classList.add('is-missing'));
    img.src = String(frame.url);

    const idx = document.createElement('span');
    idx.className = 'mframe-idx num';
    idx.setAttribute('aria-hidden', 'true');
    idx.textContent = pad2(shown);

    item.append(img, idx);
    return item;
  }

  function sceneStrip() {
    const signature = motore.frames.map((frame) => frame.i + ':' + frame.sha256).join(',');
    if (el.motoreStrip.dataset.signature === signature) return;
    el.motoreStrip.dataset.signature = signature;

    const total = Number(motore.total) || motore.frames.length;
    const newest = String((motore.last && motore.last.file) || '');
    const known = new Map();
    el.motoreStrip.querySelectorAll('.mframe').forEach((node) => {
      known.set(node.dataset.key, node);
    });

    let recent = null;
    const order = [];

    motore.frames.forEach((frame, position) => {
      // Nome + hash: una scena nuova riusa i nomi dei file ma non le immagini.
      const key = String(frame.file || frame.url) + ':' + String(frame.sha256 || '');
      const index = Number(frame.i);
      const shown = Number.isFinite(index) ? index + 1 : position + 1;
      const isNow =
        (newest && String(frame.file) === newest) ||
        (!newest && position === motore.frames.length - 1);

      let item = known.get(key);
      if (item) {
        known.delete(key);
      } else {
        item = sceneCell(frame, shown, total);
        item.dataset.key = key;
      }

      item.title =
        'frame ' + shown + ' · t ' + num(Number(frame.t) || 0, 2).replace(/0$/, '');
      const idx = item.querySelector('.mframe-idx');
      if (idx) idx.textContent = pad2(shown);
      const img = item.querySelector('img');
      if (img) img.alt = 'Fotogramma ' + shown + ' di ' + total;
      item.classList.toggle('is-now', Boolean(isNow));
      if (isNow) {
        item.setAttribute('aria-current', 'true');
        recent = item;
      } else {
        item.removeAttribute('aria-current');
      }
      order.push(item);
    });

    known.forEach((node) => node.remove());

    const current = Array.from(el.motoreStrip.children);
    const same =
      current.length === order.length && current.every((node, i) => node === order[i]);
    if (!same) order.forEach((node) => el.motoreStrip.append(node));

    // La striscia si legge da sinistra: il fotogramma nuovo è in fondo. Si
    // salta subito, senza scorrimento animato: il poll è di un secondo e una
    // corsa che non finisce mai sembrerebbe un guasto.
    if (recent) el.motoreStrip.scrollLeft = el.motoreStrip.scrollWidth;
  }

  function sceneHide() {
    el.motore.hidden = true;
    el.motoreImg.removeAttribute('src');
    el.motoreImg.classList.remove('is-loaded');
    el.motoreStrip.textContent = '';
    el.motoreStrip.dataset.signature = '';
    el.motoreState.textContent = '—';
    el.motoreFill.style.width = '0%';
    el.motoreHash.textContent = '';
    el.motoreHash.removeAttribute('title');
    el.motoreHash.hidden = true;
    el.motoreBadge.hidden = true;
    motore.engine = '';
    motore.frames = [];
    motore.last = null;
    motore.total = null;
    motore.sha = '';
    motore.bytes = null;
    motore.nondet = false;
    motore.meta = false;
    motore.stamp = '';
    motore.quiet = 0;
    motore.stalled = false;
    motore.byteTries = 0;
    motore.finalRead = false;
  }

  function scenePaint(data) {
    const engine = data && data.engine ? String(data.engine) : '';
    const frames =
      data && Array.isArray(data.frames) ? data.frames.filter((item) => item && item.url) : [];
    const last =
      data && data.last && data.last.url
        ? data.last
        : frames.length
          ? frames[frames.length - 1]
          : null;

    if (!engine || !last) {
      motore.nulls += 1;
      if (motore.nulls >= SCENE_EMPTY_LIMIT) motore.stopped = true;
      sceneHide();
      return;
    }

    motore.nulls = 0;
    motore.engine = engine;
    motore.frames = frames;
    motore.total = Number(data.total) || frames.length;
    motore.last = last;

    // Una scena che non pubblica più niente non è un lavoro vivo: dopo dodici
    // giri identici la domanda si ferma, senza spegnere la striscia.
    const stamp = String(data.updated_utc || '') + '|' + String(last.sha256 || last.file || '');
    if (stamp === motore.stamp) motore.quiet += 1;
    else {
      motore.stamp = stamp;
      motore.quiet = 0;
    }
    if (motore.quiet >= 12 && sceneBusy()) motore.stalled = true;

    const sha = String(last.sha256 || last.file || '');
    if (sha !== motore.sha) {
      motore.sha = sha;
      motore.bytes = null;
      motore.byteTries = 0;
      if (data.nondeterministic === true) motore.nondet = true;
      sceneManifest();
    }

    el.motore.hidden = false;
    sceneBadge();
    sceneBig();
    sceneStatus();
    sceneHash();
    sceneStrip();

    if (sceneBusy()) motore.finalRead = false;
    else sceneFinalBytes();
  }

  // La scena è ancora in costruzione finché il manifesto non è arrivato in
  // fondo: è questo, non la percentuale, a tenere viva la domanda.
  function sceneBusy() {
    const last = motore.last;
    if (!last) return false;
    const index = Number(last.i);
    const total = Number(motore.total);
    if (!Number.isFinite(index) || !Number.isFinite(total) || total <= 0) return false;
    return index + 1 < total;
  }

  function sceneWants() {
    if (document.visibilityState !== 'visible') return false;
    if (!motore.inView) return false;
    if (motore.stopped || motore.stalled) return false;
    if (motore.engine) return sceneBusy() || jobRunning(lastState);
    return motore.nulls < SCENE_EMPTY_LIMIT;
  }

  function sceneTimer(delay) {
    if (motore.timer !== null) {
      window.clearTimeout(motore.timer);
      motore.timer = null;
    }
    if (!sceneWants()) return;
    motore.timer = window.setTimeout(scenePoll, delay);
  }

  async function scenePoll(force) {
    motore.timer = null;
    // Una lettura è ancora in volo: si riprova tra poco, invece di perdere il giro.
    if (motore.inflight) {
      sceneTimer(250);
      return;
    }
    if (!force && !sceneWants()) return;
    if (document.visibilityState !== 'visible') return;
    motore.inflight = true;
    const ticket = motore.ticket;
    try {
      const response = await fetch(scoped('/api/scene'), { cache: 'no-store' });
      if (!response.ok) throw new Error('scena ' + response.status);
      const data = await response.json();
      if (ticket === motore.ticket) scenePaint(data);
    } catch (error) {
      // Silenzio: la sezione non racconta un guasto che non sa.
    } finally {
      motore.inflight = false;
      if (ticket === motore.ticket) sceneTimer(SCENE_EVERY_MS);
    }
  }

  function sceneReset() {
    motore.ticket += 1;
    if (motore.timer !== null) {
      window.clearTimeout(motore.timer);
      motore.timer = null;
    }
    motore.nulls = 0;
    motore.stopped = false;
    sceneHide();
  }

  // Il lavoro scelto è l'identità del polling: se cambia, la domanda riparte.
  function sceneSync() {
    const key = String(jobIndex) + '|' + String(sessionParam || '');
    if (motore.key === key) return;
    motore.key = key;
    sceneReset();
    // Una sonda sola, anche a sezione sotto la piega: serve a sapere se c'è
    // qualcosa da mostrare. Il polling vero comincia quando la sezione si vede.
    if (document.visibilityState === 'visible') scenePoll(true);
  }

  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        motore.inView = entries.some((entry) => entry.isIntersecting);
        sceneTimer(SCENE_EVERY_MS);
      },
      { threshold: 0 }
    );
    observer.observe(el.motoreSentinel);
  } else {
    motore.inView = true;
  }

  document.addEventListener('visibilitychange', () => {
    sceneTimer(SCENE_EVERY_MS);
  });

  tick();
  window.setInterval(tick, 1200);
})();
