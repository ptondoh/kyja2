/* Photos d'inspiration (demande sur mesure) : aperçu immédiat à la sélection, envoi, retrait.
   Aucun script en ligne (CSP) : ce fichier est chargé depuis /static. */
(() => {
  const input = document.getElementById('photo-input');
  if (!input) return;

  const list = document.getElementById('photo-previews');
  const counter = document.getElementById('photo-count');
  const message = document.getElementById('photo-message');
  const zone = document.getElementById('photo-zone');
  const MAX = Number(input.dataset.max || 10);
  const MAX_BYTES = Number(input.dataset.maxBytes || 10 * 1024 * 1024);
  const TYPES = ['image/jpeg', 'image/png', 'image/webp'];
  const csrf = document.querySelector('meta[name="csrf-token"]').content;

  const cards = () => list.querySelectorAll('[data-photo]');
  const say = (text) => { message.textContent = text || ''; };

  function refresh() {
    const n = cards().length;
    counter.textContent = n + ' / ' + MAX + ' photos';
    const full = n >= MAX;
    input.disabled = full;
    zone.classList.toggle('opacity-50', full);
    zone.classList.toggle('pointer-events-none', full);
  }

  function makeCard(src, alt) {
    const li = document.createElement('li');
    li.className = 'photo-card';
    li.dataset.photo = '1';
    const img = document.createElement('img');
    img.src = src;
    img.alt = alt;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'photo-remove';
    remove.dataset.remove = '1';
    remove.setAttribute('aria-label', 'Retirer la photo');
    remove.textContent = '×';
    const state = document.createElement('span');
    state.className = 'photo-state';
    li.append(img, remove, state);
    return { li, state };
  }

  async function upload(file, li, state, previewUrl) {
    const data = new FormData();
    data.append('file', file);
    li.dataset.pending = '1';
    state.textContent = 'Envoi…';
    try {
      const res = await fetch('/bookings/photos', {
        method: 'POST',
        body: data,
        headers: { 'X-CSRF-Token': csrf },
        credentials: 'same-origin',
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || "L'envoi a échoué.");
      li.dataset.id = String(body.id);
      const hidden = document.createElement('input');
      hidden.type = 'hidden';
      hidden.name = 'photo_ids';
      hidden.value = String(body.id);
      li.append(hidden);
      state.textContent = '';
      li.classList.add('is-uploaded');
    } catch (err) {
      say(file.name + ' : ' + err.message);
      li.remove();
      URL.revokeObjectURL(previewUrl);
    } finally {
      delete li.dataset.pending;
      refresh();
    }
  }

  input.addEventListener('change', async () => {
    say('');
    const files = Array.from(input.files);
    input.value = '';
    const room = MAX - cards().length;
    const accepted = [];
    const refused = [];
    for (const f of files) {
      if (!TYPES.includes(f.type)) refused.push(f.name + ' : format non pris en charge (JPEG, PNG ou WebP).');
      else if (f.size > MAX_BYTES) refused.push(f.name + ' : photo trop volumineuse (max ' + Math.round(MAX_BYTES / 1048576) + ' Mo).');
      else if (accepted.length >= room) refused.push(f.name + ' : maximum ' + MAX + ' photos atteint.');
      else accepted.push(f);
    }
    if (refused.length) say(refused.join(' '));

    // Aperçus immédiats (avant l'envoi), puis envoi un par un.
    const queue = accepted.map((file) => {
      const url = URL.createObjectURL(file);
      const { li, state } = makeCard(url, 'Aperçu de ' + file.name);
      list.append(li);
      return { file, li, state, url };
    });
    refresh();
    for (const q of queue) await upload(q.file, q.li, q.state, q.url);
  });

  list.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-remove]');
    if (!btn) return;
    const li = btn.closest('[data-photo]');
    if (li.dataset.pending) return;
    const id = li.dataset.id;
    btn.disabled = true;
    if (id) {
      const res = await fetch('/bookings/photos/' + id + '/delete', {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf },
        credentials: 'same-origin',
      });
      if (!res.ok) { btn.disabled = false; say("Impossible de retirer la photo."); return; }
    }
    const img = li.querySelector('img');
    if (img && img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);
    li.remove();
    say('');
    refresh();
  });

  // Le formulaire ne part pas tant qu'une photo est en cours d'envoi.
  input.form.addEventListener('submit', (e) => {
    if (list.querySelector('[data-pending]')) {
      e.preventDefault();
      say("Patientez, l'envoi des photos est en cours.");
    }
  });

  refresh();
})();
