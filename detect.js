() => {
  const norm = s => s.replace(/\s/g, '').toLowerCase();
  const label = s => s.replace(/\s+/g, ' ').trim();
  const tabs = [...document.querySelectorAll('[role="tab"]')]
    .filter(e => norm(e.textContent) === 'wave2');
  if (tabs.length !== 1) throw new Error('Wave 2 tab missing or ambiguous');
  const panel = document.getElementById(tabs[0].getAttribute('aria-controls'));
  if (!panel) throw new Error('Wave 2 panel missing');
  const cards = [...panel.querySelectorAll('.ticket-card')].filter(c =>
    [...c.querySelectorAll('h3')].some(h => norm(h.textContent) === '1-daylower'));
  if (cards.length !== 1) throw new Error('1-day Lower card missing or ambiguous');
  const card = cards[0];
  const text = label(card.textContent);
  const usable = el => {
    // Wave 2 itself may be an inactive tab; exclude hidden controls within its card.
    for (let node = el; node && card.contains(node); node = node.parentElement) {
      if (node.hidden || getComputedStyle(node).display === 'none') return false;
    }
    return true;
  };
  const controls = [...card.querySelectorAll('a,button')].filter(usable);
  const buys = controls.filter(a => {
    if (a.tagName !== 'A' || !/^(buy now|buy tickets|get tickets)$/i.test(label(a.textContent))) return false;
    for (let node = a; node && card.contains(node); node = node.parentElement) {
      if (node.hasAttribute('disabled') || node.getAttribute('aria-disabled')?.toLowerCase() === 'true'
          || node.classList.contains('disabled')) return false;
    }
    const raw = a.getAttribute('href')?.trim();
    if (!raw || raw.startsWith('#')) return false;
    const url = new URL(a.href);
    if (url.protocol !== 'https:' || url.username || url.password) return false;
    const current = new URL(location.href);
    return !(url.origin === current.origin && url.pathname === current.pathname && url.search === current.search);
  });
  const visibleText = [...card.querySelectorAll('*')].filter(el => !el.children.length && usable(el))
    .map(el => el.textContent).join(' ');
  const soon = /coming\s+soon/i.test(visibleText);
  const sold = controls.some(a => /^sold\s*out$/i.test(label(a.textContent)));
  if (Number(soon) + Number(sold) + Number(buys.length > 0) !== 1 || buys.length > 1)
    throw new Error('Unrecognized or conflicting ticket status; manual check needed');
  if (buys.length === 1) return {status:'sale_link', text, url:buys[0].href};
  if (soon) return {status:'coming_soon', text};
  return {status:'sold_out', text};
}
