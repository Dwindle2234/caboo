/* Cabo - shared table view (TV / laptop). Public information only. */
(function () {
  const $ = id => document.getElementById(id);
  const E = Cabo.esc;
  let st = null, deal = { id: null, arrived: {}, done: true, cancel: null }, seenEvent = null;
  const flashes = new Map();
  let timer = null;

  function slots(seat) {
    const occ = st.slots[String(seat)] || [];
    const hand = st.result && st.phase === 'round_over' ? st.result.hands[String(seat)] : null;
    const n = (deal.done || st.phase !== 'peek') ? Infinity : (deal.arrived[seat] || 0);
    return occ.map((o, i) => {
      const f = (flashes.get(seat + ':' + i) || 0) > Date.now() ? ' flash' : '';
      if (hand) return hand[i] ? `<div class="slot${f}"><img src="${Cabo.cardUrl(hand[i])}" alt=""></div>` : '<div class="slot empty"></div>';
      if (!o || i >= n) return `<div class="slot empty${f}"></div>`;
      const pub = (st.public_reveals || []).find(r => r.seat === seat && r.slot === i && r.expires > Cabo.serverNow());
      if (pub) return `<div class="slot exposed${f}"><img src="${Cabo.cardUrl(pub.card)}" alt=""></div>`;
      if (o !== '?') return `<div class="slot faceup${f}"><img src="${Cabo.cardUrl(o)}" alt=""></div>`;
      return `<div class="slot${f}"><img src="${Cabo.backUrl()}" alt=""></div>`;
    }).join('');
  }

  function render() {
    if (!st) return;
    $('board-info').textContent = st.round_no ? `Table ${st.table} · Round ${st.round_no}` : `Table ${st.table}`;
    const players = st.order.length ? st.order : st.seats.filter(s => s.taken).map(s => s.seat);
    $('players').innerHTML = players.map(s => `
      <div class="bp ${st.turn === s ? 'turn' : ''}">
        <div class="opp-head"><span class="opp-name">${E(Cabo.name(st, s))}</span>
          ${st.cabo_by === s ? '<span class="badge cabo">CABO</span>' : ''}
          <span class="spacer"></span><span class="total-pill">🏆 ${(st.wins || {})[String(s)] || 0}</span></div>
        <div class="bp-slots" id="slots-${s}">${slots(s)}</div>
      </div>`).join('');

    $('deck-count').textContent = st.draw_count;
    const di = $('discard-img');
    di.hidden = !(st.discard_top && (deal.done || st.phase !== 'peek'));
    if (!di.hidden) di.src = Cabo.cardUrl(st.discard_top);
    const drawing = st.turn_state === 'drawn';
    $('drawn').hidden = !drawing;
    if (drawing) {
      $('drawn-img').src = st.drawn_public ? Cabo.cardUrl(st.drawn_public) : Cabo.backUrl();
      $('drawn-label').textContent = `${Cabo.name(st, st.turn)} ${st.drawn_public ? 'took' : 'drew'}`;
    }

    let status = '';
    if (st.phase === 'lobby') status = `${players.length} seated — deal from a phone`;
    else if (st.phase === 'peek') status = deal.done ? `Everyone looks at their cards ${(st.rules.initial_peek_slots || [0, 3]).map(i => i + 1).join(' and ')}… (${st.ready.length}/${st.order.length})` : 'Dealing…';
    else if (st.phase === 'play' && st.turn_state === 'ending') status = 'CABO round over — last chance to slap!';
    else if (st.phase === 'play') status = `${Cabo.name(st, st.turn)}'s turn` + (st.cabo_by ? ` · CABO called by ${Cabo.name(st, st.cabo_by)}` : '');
    if (st.slap && st.slap.expires > Cabo.serverNow()) {
      const r = st.slap.rank === 'JOKER' ? 'Joker' : st.slap.rank;
      status += st.slap.needed === 2 ? ` · SLAP window: a pair of ${r}s!` : ` · SLAP window: ${r}s!`;
    }
    $('status').textContent = status;
    $('log').innerHTML = (st.events || []).slice(-6).reverse().map(e => `<div>${E(e.text)}</div>`).join('');

    const over = st.phase === 'round_over';
    $('results-overlay').hidden = !over;
    if (over) $('results').innerHTML = Cabo.resultsHtml(st, null);

    clearTimeout(timer);
    const now = Cabo.serverNow();
    const left = [...flashes.values()].map(t => t - Date.now())
      .concat((st.public_reveals || []).map(r => (r.expires - now) * 1000))
      .concat(st.slap ? [(st.slap.expires - now) * 1000] : []).filter(t => t > 0);
    if (left.length) timer = setTimeout(render, Math.min(...left) + 50);
  }

  function onState(ns) {
    st = ns;
    if (st.deal && st.deal.id !== deal.id) {
      if (deal.cancel) deal.cancel();
      deal = { id: st.deal.id, arrived: {}, done: false };
      st.order.forEach(s => { deal.arrived[s] = 0; });
      render();
      deal.cancel = Cabo.playDeal(st.deal, (i, seat, animate) => {
        if (!animate) { deal.arrived[seat]++; return; }
        Cabo.fly($('deck'), $('slots-' + seat), { duration: 420, endScale: 0.6 })
          .then(() => { deal.arrived[seat]++; render(); });
      }, () => { deal.done = true; render(); });
    }
    const evs = st.events || [];
    const last = evs.length ? evs[evs.length - 1].id : 0;
    if (seenEvent === null) seenEvent = last;
    evs.filter(e => e.id > seenEvent).forEach(e => {
      const until = Date.now() + 2600;
      (e.positions || []).forEach(([s, i]) => flashes.set(s + ':' + i, until));
    });
    seenEvent = Math.max(seenEvent, last);
    render();
  }

  document.addEventListener('click', ev => {
    const open = ev.target.closest('[data-open]');
    if (open) $(open.dataset.open).hidden = false;
    const close = ev.target.closest('[data-close]');
    if (close) $(close.dataset.close).hidden = true;
  });

  Cabo.poll(onState);
})();
