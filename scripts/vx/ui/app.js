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
    verdictTitle: $('verdictTitle'),
    verdictText: $('verdictText'),
    verdictMeta: $('verdictMeta'),
    resultMeta: $('resultMeta'),
    resultBody: $('resultBody'),
    frames: $('frames'),
    framesEmpty: $('framesEmpty'),
    frameCount: $('frameCount'),
    phases: $('phases'),
    phasesEmpty: $('phasesEmpty'),
    railSub: $('railSub'),
    approve: $('approve'),
    approveNote: $('approveNote'),
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
    const figure = document.createElement('figure');
    figure.className = 'shot';
    figure.style.setProperty('--i', String(index));

    const box = document.createElement('div');
    box.className = 'shot-img';

    const img = document.createElement('img');
    img.alt = 'Fotogramma chiave ' + (index + 1) + ' di ' + total;
    img.decoding = 'async';

    img.addEventListener('load', () => {
      figure.classList.add('is-loaded');
      attachTilt(figure);
    });

    img.addEventListener('error', () => {
      figure.classList.add('is-missing');
      box.textContent = '';
      const fallback = document.createElement('span');
      fallback.className = 'shot-fallback';
      fallback.textContent = "L'immagine non è disponibile";
      box.append(fallback);
    });

    img.src = scoped('/frames/' + encodeURIComponent(frame.name));
    box.append(img);

    const cap = document.createElement('figcaption');
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
    figure.append(box, cap);
    return figure;
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

  /* ---------------------------------------------------------------- risultato */

  // Il risultato dipende dal file, non da una parola: `master_ready` lo dice il
  // server leggendo il disco. Se il file c'è, il player è in pagina e il pulsante
  // apre lo stesso flusso a schermo intero; se non c'è, resta una riga sola.
  function paintResult(state) {
    const ready = state.master_ready === true;
    const name = String(state.master || '').trim();
    const bytes = Number(state.master_bytes) || 0;
    const signature = (ready ? 'ready|' + name + '|' + bytes : 'empty') + '|job:' + jobIndex;

    el.resultMeta.textContent = ready ? 'pronto' : 'non ancora';
    el.resultMeta.classList.toggle('is-ready', ready);

    if (el.resultBody.dataset.signature === signature) return;
    el.resultBody.dataset.signature = signature;
    el.resultBody.textContent = '';

    if (!ready) {
      const line = document.createElement('p');
      line.className = 'result-empty';
      line.textContent = "Il risultato non c'è ancora: il master non è stato scritto.";
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
    hint.textContent = 'Anteprima in pagina. Il pulsante apre il file intero, a schermo intero.';

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

    info.append(file, specs, hint, open);
    card.append(monitor, info);
    el.resultBody.append(card);
  }

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

    const finished = !state.current && percent >= 99.9;
    const ready = state.master_ready === true;
    // Il file può esistere anche mentre il lavoro sta ancora girando: "fatto" lo
    // dice il lavoro che ha finito, il player lo dice il file che c'è.
    const done = finished;
    const waiting = !state.current && !done;
    const approved = state.approved === true;

    body.classList.toggle('is-done', done);
    body.classList.toggle('is-idle', waiting);
    body.classList.remove('is-offline');

    const prompt = String(state.prompt || '').trim();
    el.jobTitle.textContent = prompt || 'Lavoro senza titolo';
    document.title = 'vx — ' + (prompt ? prompt.slice(0, 70) : 'lavoro in corso');

    el.stateWord.textContent = ready && done
      ? 'Fatto'
      : done
        ? 'Concluso'
        : waiting
          ? 'In attesa'
          : 'In corso';
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
    if (done) {
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
    paintPhases(phases, activeIndex, done);
    el.phasesEmpty.hidden = phases.length > 0;
    el.railSub.textContent =
      phases.length === 0 ? 'nessuna fase' : pad2(phases.length) + ' fasi';

    // ora / esito
    if (done) {
      el.now.hidden = true;
      el.verdict.hidden = false;
      el.verdictTitle.textContent = 'Lavoro concluso';
      const note = upperFirst(state.note);
      el.verdictText.textContent = note
        ? note + (/[.!?]$/.test(note) ? '' : '.')
        : "Il lavoro è arrivato in fondo senza una nota di esito.";
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
    paintFrames(frames, done);
    paintResult(state);

    el.approve.classList.toggle('is-approved', approved);
    el.approve.disabled = approved || frames.length === 0;
    el.approve.textContent = approved ? 'Fotogrammi approvati' : 'Approva questi fotogrammi';

    el.approveNote.className = 'action-note' + (approved ? ' is-ok' : '');
    if (approved) {
      el.approveNote.textContent = 'Il cancello è aperto: il lavoro può procedere.';
    } else if (frames.length === 0) {
      el.approveNote.textContent = done
        ? "Non c'è niente da approvare: il lavoro non ha scritto fotogrammi."
        : 'Nessun fotogramma da approvare: il lavoro non ne ha ancora scritti.';
    } else {
      el.approveNote.textContent = "L'approvazione apre il cancello per questo lavoro.";
    }
  }

  /* ------------------------------------------------------------------ rete */

  let inflight = false;
  let approving = false;
  let hasDrawn = false;

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
    el.approveNote.className = 'action-note';
    el.approveNote.textContent = "Registro l'approvazione…";

    try {
      const response = await fetch(scoped('/api/approve'), { method: 'POST' });
      const result = await response.json();
      if (!result || result.ok !== true) {
        el.approveNote.className = 'action-note is-warn';
        el.approveNote.textContent =
          'Non ho potuto approvare: ' +
          ((result && result.reason) || 'il server non ha risposto come previsto.') +
          '.';
      }
    } catch (error) {
      el.approveNote.className = 'action-note is-warn';
      el.approveNote.textContent =
        'Non ho potuto approvare: il server locale non risponde.';
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
