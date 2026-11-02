// vx — la pagina del lavoro. Nessuna rete, nessuna dipendenza: solo fetch locale.
const $ = (id) => document.getElementById(id);

async function state() {
  const response = await fetch('/api/state', { cache: 'no-store' });
  return response.json();
}

function render(s) {
  $('title').textContent = s.prompt || 'Lavoro in corso';
  const clips = s.clips === 1 ? '1 clip' : `${s.clips} clip`;
  $('meta').textContent = `${clips} · ${s.elapsed_sec} secondi`;
  $('fill').style.width = `${Math.max(0, Math.min(100, s.percent))}%`;

  const status = $('status');
  const phase = (s.phases || []).find((p) => p.key === s.current);
  status.textContent = s.current ? (phase ? phase.label : s.current) : (s.note || 'in attesa');
  status.className = s.current ? 'muted' : 'ok';

  const box = $('frames');
  const want = s.frames.map((f) => f.name).join('|');
  if (box.dataset.want !== want) {
    box.dataset.want = want;
    box.innerHTML = '';
    for (const frame of s.frames) {
      const figure = document.createElement('figure');
      const img = document.createElement('img');
      img.src = `/frames/${encodeURIComponent(frame.name)}`;
      img.alt = frame.name;
      const caption = document.createElement('figcaption');
      caption.textContent = frame.name;
      figure.append(img, caption);
      box.append(figure);
    }
  }

  const button = $('approve');
  button.disabled = s.approved || s.frames.length === 0;
  button.textContent = s.approved ? 'Approvati' : 'Approva questi fotogrammi';
  $('verdict').textContent = s.approved ? 'Il cancello è aperto per questo lavoro.' : '';
}

async function tick() {
  try {
    render(await state());
  } catch (error) {
    $('status').textContent = 'in attesa del lavoro…';
  }
}

$('approve').addEventListener('click', async () => {
  await fetch('/api/approve', { method: 'POST' });
  tick();
});

setInterval(tick, 1000);
tick();
