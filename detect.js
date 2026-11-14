() => {
  const norm = s => s.replace(/\s/g, '').toLowerCase();
  const tabs = [...document.querySelectorAll('[role="tab"]')]
    .filter(e => norm(e.textContent) === 'wave2');
  if (tabs.length !== 1) throw new Error('Wave 2 tab missing or ambiguous');
  const panel = document.getElementById(tabs[0].getAttribute('aria-controls'));
  if (!panel) throw new Error('Wave 2 panel missing');
  const cards = [...panel.querySelectorAll('.ticket-card')].filter(c =>
    [...c.querySelectorAll('h3')].some(h => norm(h.textContent) === '1-daylower'));
  if (cards.length !== 1) throw new Error('1-day Lower card missing or ambiguous');
  const card = cards[0];
  const text = card.textContent.replace(/\s+/g, ' ').trim();
  const controls = [...card.querySelectorAll('a,button')];
  const buy = controls.find(a => /^(buy now|buy tickets|get tickets)$/i.test(a.textContent.trim())
    && a.tagName === 'A' && !a.hasAttribute('disabled')
    && a.getAttribute('aria-disabled') !== 'true'
    && /^https:\/\//.test(a.href)
    && !/coming soon|sold out/i.test(a.textContent));
  const soon = /coming\s+soon/i.test(text);
  const sold = controls.some(a => /^sold\s*out$/i.test(a.textContent.trim()));
  if (buy && !soon && !sold) return {status:'sale_link', text, url:buy.href};
  if (!buy && soon) return {status:'coming_soon', text};
  if (!buy && sold) return {status:'sold_out', text};
  throw new Error('Unrecognized ticket status; manual check needed');
}
