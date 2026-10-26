(async () => {
  // The existing CMS and its protected admin remain on the original host.
  const cmsOrigin = 'https://underhood-detailing-montana.romanmontanov1.chatgpt.site';
  async function readJSON(url) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 6000);
    try {
      const response = await fetch(url, {cache: 'no-store', signal: controller.signal});
      if (!response.ok) throw new Error('Content request failed');
      return await response.json();
    } finally { clearTimeout(timeout); }
  }
  function text(value, fallback) { return typeof value === 'string' ? value : fallback; }
  function mergeContent(base, saved) {
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return base;
    const c = {...base};
    for (const key of ['heroTagline', 'heroSubtitle', 'intro', 'phone', 'telegram', 'address']) {
      c[key] = text(saved[key], base[key]);
    }
    c.services = base.services.map(service => {
      const update = Array.isArray(saved.services) && saved.services.find(s => s && s.id === service.id);
      if (!update) return service;
      const result = {...service};
      for (const key of ['title', 'tag', 'description', 'price']) result[key] = text(update[key], service[key]);
      return result;
    });
    if (Array.isArray(saved.faq)) c.faq = saved.faq.filter(f => f && typeof f.q === 'string' && typeof f.a === 'string');
    if (Array.isArray(saved.cases)) c.cases = saved.cases.slice(0, 50).filter(w => w && typeof w.image === 'string' && typeof w.title === 'string');
    return c;
  }
  let defaults;
  try { defaults = await readJSON('./content-default.json'); }
  catch { console.warn('Local content unavailable; using page content'); return; }
  function apply(c) {
    document.querySelectorAll('[data-content]').forEach(el => { if (typeof c[el.dataset.content] === 'string') el.textContent = c[el.dataset.content]; });
    document.querySelectorAll('[data-service]').forEach(el => {
      const service = c.services.find(s => s.id === el.dataset.service);
      if (service && typeof service[el.dataset.field] === 'string') el.textContent = service[el.dataset.field];
    });
    const digits = c.phone.replace(/\D/g, '');
    if (digits) {
      document.querySelectorAll('a[href^="tel:"]').forEach(a => { a.href = 'tel:' + c.phone.replace(/[^+\d]/g, ''); a.textContent = c.phone; });
      document.querySelectorAll('a[href*="wa.me/"]').forEach(a => { a.href = 'https://wa.me/' + digits; });
    }
    if (/^[a-zA-Z0-9_]+$/.test(c.telegram)) document.querySelectorAll('a[href*="t.me/"]').forEach(a => { a.href = 'https://t.me/' + c.telegram; });
    document.querySelectorAll('.service-details article').forEach(article => {
      const id = article.querySelector('[data-service]')?.dataset.service;
      const service = c.services.find(s => s.id === id);
      if (service && article.querySelector('.lead')) article.querySelector('.lead').textContent = service.description;
    });
    document.querySelectorAll('[data-address]').forEach(el => { el.textContent = c.address; });
    const faq = document.querySelector('#faq-list');
    if (faq) faq.replaceChildren(...c.faq.map(f => {
      const d = document.createElement('details'); d.className = 'faq-item';
      const summary = document.createElement('summary'); summary.textContent = f.q;
      const plus = document.createElement('span'); plus.className = 'plus'; plus.setAttribute('aria-hidden', 'true'); summary.append(plus);
      const answer = document.createElement('p'); answer.textContent = f.a; d.append(summary, answer); return d;
    }));
    const cases = document.querySelector('#case-list');
    if (cases) {
      const articles = c.cases.flatMap(work => {
        let url;
        try { url = new URL(work.image, cmsOrigin); } catch { return []; }
        if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return [];
        const article = document.createElement('article'); const img = document.createElement('img');
        img.src = url.href; img.alt = work.title; img.loading = 'lazy'; img.decoding = 'async'; img.width = 960; img.height = 720;
        const h = document.createElement('h3'); h.textContent = work.title;
        const p = document.createElement('p'); p.textContent = text(work.description, '');
        article.append(img, h, p); return [article];
      });
      cases.replaceChildren(...articles);
      document.querySelector('#case-section').hidden = !articles.length;
    }
  }
  // Apply local content immediately, even when the remote CMS fails or times out.
  apply(defaults);
  try { const saved = await readJSON(cmsOrigin + '/api/content'); apply(mergeContent(defaults, saved.content)); }
  catch { console.warn('CMS unavailable; using local content'); }
})();
