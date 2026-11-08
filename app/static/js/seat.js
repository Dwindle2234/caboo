/* Cabo - a player's phone screen */
(function () {
  const $ = id => document.getElementById(id);
  const SEAT = parseInt($('screen').dataset.seat, 10);
  const E = Cabo.esc;

  let token = Cabo.getToken(SEAT);
  let st = null;
  let poller = null;
  // tap selection: own slots, any cards [[seat,slot]], joker target seat
  let sel = { mine: [], cards: [], target: null, joker: null };
  let selContext = '';
  let deal = { id: null, arrived: {}, done: true, cancel: null };
  let seenEvent = null;
  const flashes = new Map();                // "seat:slot" -> expiry (ms)
  let busy = false;
  let resultsHidden = false, resultsRound = null;
  let rerenderTimer = null;
  let slapMode = false, slapSel = [], slapSteals = [], slapId = null;   // slapdown: tap SLAP, then card(s)
  let quartetMode = false, quartetSel = [];
  let giveMode = false;                                 // paying back a steal by giving a card

  // ------------------------------------------------------------------ helpers
  const inGame = () => st && st.order.includes(SEAT);
  const nm = s => Cabo.name(st, s);
  const label = p => (st.rules.labels || {})[p] || p;
  const has = (list, seat, slot) => list.some(([s, i]) => s === seat && i === slot);

  function canSlap() {
    const w = st && st.slap;
    return !!(w && token && inGame() && st.phase === 'play' && w.expires > Cabo.serverNow());   // anyone may slap
  }
  const rankLabel = r => (r === 'JOKER' ? 'Joker' : r);
  const myDebt = () => ((st && st.debts) || []).find(d => d.stealer === SEAT && d.expires > Cabo.serverNow());

  function mode() {
    if (!st || !token || !inGame()) return 'none';
    if (slapMode && canSlap()) return 'slap';
    if (giveMode && myDebt()) return 'debt-give';
    if (quartetMode && st.phase === 'play' && st.turn === SEAT && st.turn_state === 'start') return 'quartet';
    if (st.phase === 'peek') {
      if (!deal.done) return 'dealing';
      return st.peeked.includes(SEAT) ? 'wait' : 'initial';
    }
    if (st.phase === 'play' && st.turn === SEAT) {
      if (st.turn_state === 'start') return 'start';
      if (st.turn_state === 'drawn') return 'drawn';
      if (st.turn_state === 'power') {
        if (st.pending_power === 'joker' && sel.joker) return 'joker-' + sel.joker;
        return 'power-' + st.pending_power;
      }
    }
    return 'wait';
  }

  // which cards can be tapped in each mode
  const ANY_CARD = ['power-swap', 'power-qswap', 'power-peekswap', 'power-turnover'];
  const peekSlots = () => (st && st.rules.initial_peek_slots) || [0, 3];
  const peekNames = () => peekSlots().map(i => i + 1).join(' and ');
  let slotIndex = -1;                                    // slot being rendered (for the fixed initial peek)
  function pickable(m, seat, code) {
    const mine = seat === SEAT;
    if (m === 'power-turnover' && code !== '?') return false;       // already face up
    if (m === 'slap') return true;                                    // any slap may steal one card
    if (m === 'initial') return mine && peekSlots().includes(slotIndex);  // only cards 1 and 4
    if (ANY_CARD.includes(m)) return true;
    if (mine) return ['initial', 'drawn', 'power-peek', 'joker-give', 'slap', 'quartet', 'debt-give'].includes(m);
    return ['power-spy', 'joker-give', 'joker-deal'].includes(m);
  }

  function publicReveals() {
    const now = Cabo.serverNow();
    return ((st && st.public_reveals) || []).filter(r => r.expires > now);
  }

  function reveals() {
    const now = Cabo.serverNow();
    return ((st && st.me && st.me.reveals) || []).filter(r => r.expires > now);
  }

  function toast(text, isError) {
    const t = $('toast');
    t.textContent = text;
    t.className = 'toast' + (isError ? ' error' : '');
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { t.hidden = true; }, isError ? 3000 : 3500);
  }

  async function act(action, extra) {
    if (busy) return;
    busy = true;
    try {
      const ns = await Cabo.api('act', Object.assign({ seat: SEAT, token, action }, extra || {}));
      poller.push(ns);
    } catch (e) {
      toast(e.message, true);
    } finally {
      busy = false;
      render();
    }
  }

  // ------------------------------------------------------------------ rendering
  function visibleSlots(seat) {
    const occ = (st.slots[String(seat)] || []);
    if (deal.done || st.phase !== 'peek') return occ;
    const n = deal.arrived[seat] || 0;
    return occ.map((o, i) => (i < n ? o : null));
  }

  function slotHtml(seat, i, code, m) {
    const cls = ['slot'];
    let inner = '';
    const shown = st.result && st.phase === 'round_over' ? st.result.hands[String(seat)][i] : null;
    if (shown) {
      inner = `<img src="${Cabo.cardUrl(shown)}" alt="${shown}">`;
    } else if (code === null || code === undefined) {
      cls.push('empty');
    } else {
      const r = reveals().find(x => x.seat === seat && x.slot === i);
      const pub = publicReveals().find(x => x.seat === seat && x.slot === i);
      if (pub) {
        cls.push('exposed');
        inner = `<img src="${Cabo.cardUrl(pub.card)}" alt="${pub.card}">`;
      } else if (code !== '?') {
        cls.push('faceup');
        inner = `<img src="${Cabo.cardUrl(code)}" alt="${code}">`;
      } else if (r) {
        const left = Math.max(0, r.expires - Cabo.serverNow());
        cls.push('revealed');
        inner = `<img src="${Cabo.cardUrl(r.card)}" alt="${r.card}">` +
                `<span class="timer" style="animation-duration:${left.toFixed(2)}s"></span>`;
      } else {
        inner = `<img src="${Cabo.backUrl()}" alt="">`;
      }
      slotIndex = i;
      if (pickable(m, seat, code)) cls.push('pickable');
      const stolen = m === 'slap' && has(slapSteals, seat, i);
      const selected = stolen || (seat === SEAT && (m === 'slap' ? slapSel.includes(i)
        : m === 'quartet' ? quartetSel.includes(i) : sel.mine.includes(i))) || has(sel.cards, seat, i);
      if (selected) cls.push('selected');
    }
    if ((flashes.get(seat + ':' + i) || 0) > Date.now()) cls.push('flash');
    return `<div class="${cls.join(' ')}" data-seat="${seat}" data-slot="${i}">${inner}</div>`;
  }

  function winsPill(s) {
    const w = (st.wins || {})[String(s)] || 0;
    return `<span class="total-pill" title="Rounds won">🏆 ${w}</span>`;
  }

  function renderOpponents(m) {
    const list = inGame() ? Cabo.clockwiseFrom(st, SEAT) : st.order;
    const el = $('opponents');
    el.className = 'opponents n' + list.length;
    el.innerHTML = list.map(s => {
      const seatInfo = st.seats[s - 1];
      const cls = ['opp'];
      if (st.turn === s) cls.push('turn');
      if (!seatInfo.online) cls.push('offline');
      if (m === 'joker-give' || m === 'joker-deal') cls.push('target-pick');
      if (sel.target === s) cls.push('target');
      const slots = visibleSlots(s).map((o, i) => slotHtml(s, i, o, m)).join('');
      return `<div class="${cls.join(' ')}" id="opp-${s}" data-opp="${s}">
        <div class="opp-head">
          <span class="opp-name">${E(nm(s))}</span>
          ${st.cabo_by === s ? '<span class="badge cabo">CABO</span>' : ''}
          ${st.phase === 'peek' && st.peeked.includes(s) ? '<span class="badge ok">✓</span>' : ''}
          <span class="spacer"></span>${winsPill(s)}
        </div>
        <div class="opp-slots" id="slots-${s}">${slots}</div>
      </div>`;
    }).join('');
  }

  function statusText(m) {
    if (st.phase === 'lobby') return '';
    if (!inGame()) return 'Round in progress — you can join the next one.';
    const turnName = nm(st.turn);
    const caboNote = st.cabo_by && st.phase === 'play' ? ` · ${nm(st.cabo_by)} called CABO — last turns!` : '';
    switch (m) {
      case 'dealing': return 'Dealing…';
      case 'initial': return `Look at your cards ${peekNames()} — tap one of them (or the button). Remember them!`;
      case 'slap': return st.slap.needed === 2
        ? `SLAP a PAIR of ${rankLabel(st.slap.rank)}s: tap two cards — yours, other players' (to steal), or one of each.`
        : `SLAP a ${rankLabel(st.slap.rank)}: tap yours, or tap another player's to steal it!`;
      case 'debt-give': return 'Tap the card to give away.';
      case 'quartet': return 'QUARTET: tap your four matching cards, then Play quartet.';
      case 'wait':
        if (st.phase === 'play' && st.turn_state === 'ending') return 'CABO round over… last chance to slap!';
        if (st.phase === 'peek') return `Waiting for everyone to look… (${st.ready.length}/${st.order.length})`;
        if (st.phase === 'play') {
          if (st.turn_state === 'power') return `${turnName} is playing ${label(st.pending_power === 'qswap' ? 'peekswap' : st.pending_power)}…` + caboNote;
          return `${turnName}'s turn` + caboNote;
        }
        return '';
      case 'start': return 'Your turn: draw from the deck, take the discard, or call CABO.' + caboNote;
      case 'drawn':
        if (st.drawn_from === 'discard') return 'Tap the card (or matching cards) to swap it for, then Swap. Swapping out a power card gives you its power.';
        return 'Keep it — tap card(s) to swap out — or discard it. Swapping out a power card gives you its power.';
      case 'power-peek': return 'You swapped out a PEEK: tap one of your cards to look at it.';
      case 'power-spy': return 'You swapped out a SPY: tap another player\'s card to look at it.';
      case 'power-swap': return 'You swapped out a SWAP: tap any two cards (yours or anyone\'s), then Swap. Nobody looks.';
      case 'power-peekswap': return 'You swapped out a PEEK & SWAP: tap any card to look at it.';
      case 'power-qswap': return 'Now swap any two cards if you like (it doesn\'t have to be that one) — or don\'t.';
      case 'power-joker': return 'You swapped out a JOKER: give one of your cards to another player, or make someone draw a card.';
      case 'joker-deal': return 'Tap the player who has to draw a card.';
      case 'joker-give': return 'Tap the card to give away, then the player to give it to.';
      case 'power-turnover': return `You swapped out the ACE OF SPADES: tap up to ${st.rules.turnover_max} cards to turn face up for everyone.`;
    }
    return '';
  }

  function actionsHtml(m) {
    const b = (id, text, cls, disabled) =>
      `<button class="btn ${cls || 'btn-ghost'}" data-act="${id}" ${disabled ? 'disabled' : ''}>${text}</button>`;
    if (st.phase === 'round_over' && resultsHidden) return b('show-results', 'Show results', 'btn-neon');
    switch (m) {
      case 'initial': return b('initial_peek', `Look at cards ${peekNames()}`, 'btn-neon');
      case 'start': {
        const held = (st.slots[String(SEAT)] || []).filter(x => x !== null).length;
        return b('draw', 'Draw', 'btn-neon') + b('take_discard', 'Take discard', 'btn-ghost', !st.discard_top)
          + (st.cabo_by ? '' : b('cabo', 'CABO!', 'btn-gold'))
          + (held >= 4 ? b('quartet-mode', 'Quartet', 'btn-ghost') : '');
      }
      case 'quartet': return b('quartet', 'Play quartet', 'btn-gold', quartetSel.length !== 4) + b('quartet-cancel', 'Cancel');
      case 'slap': return '';
      case 'drawn': {
        const n = sel.mine.length;
        let h = b('replace', n > 1 ? `Match ${n}` : 'Swap', 'btn-neon', n === 0);
        if (st.drawn_from === 'deck') h += b('discard', 'Discard');
        return h;
      }
      case 'power-peek': case 'power-spy': case 'power-peekswap': return b('skip_power', 'Skip');
      case 'power-swap': return b('swap', 'Swap', 'btn-neon', sel.cards.length !== 2) + b('skip_power', 'Skip');
      case 'power-qswap': return b('swap', 'Swap', 'btn-neon', sel.cards.length !== 2) + b('skip_power', "Don't swap");
      case 'power-joker': return b('joker-give', 'Give a card away', 'btn-neon') + b('joker-deal', 'Make someone draw', 'btn-gold')
        + b('skip_power', 'Skip');
      case 'joker-deal': return b('joker_deal', sel.target ? `${E(nm(sel.target))} draws` : 'Pick a player', 'btn-neon',
        !sel.target) + b('joker-back', 'Back');
      case 'joker-give': return b('joker_give', sel.target ? `Give to ${E(nm(sel.target))}` : 'Give', 'btn-neon',
        !(sel.mine.length === 1 && sel.target)) + b('joker-back', 'Back');
      case 'power-turnover': return b('turnover', `Turn over ${sel.cards.length || ''}`.trim(), 'btn-neon',
        sel.cards.length === 0) + b('skip_power', 'Skip');
    }
    return '';
  }

  function renderPiles(m) {
    $('deck-count').textContent = st.draw_count;
    $('deck').classList.toggle('pickable', m === 'start');
    $('deck').classList.toggle('empty', !st.draw_count);
    const di = $('discard-img');
    if (st.discard_top && (deal.done || st.phase !== 'peek')) {
      di.src = Cabo.cardUrl(st.discard_top); di.hidden = false;
    } else { di.hidden = true; }
    $('discard').classList.toggle('pickable', m === 'start' && !!st.discard_top);

    const dEl = $('drawn'), dImg = $('drawn-img'), dLab = $('drawn-label');
    const mine = st.me && st.me.drawn;
    if (mine) {
      dImg.src = Cabo.cardUrl(mine);
      dLab.textContent = st.drawn_from === 'discard' ? 'You took' : 'You drew';
      dEl.hidden = false;
    } else if (st.turn_state === 'drawn' && st.turn !== SEAT) {
      dImg.src = st.drawn_public ? Cabo.cardUrl(st.drawn_public) : Cabo.backUrl();
      dLab.textContent = `${nm(st.turn)} ${st.drawn_public ? 'took' : 'drew'}`;
      dEl.hidden = false;
    } else {
      dEl.hidden = true;
    }
  }

  function renderSlapBar(m) {
    const bar = $('slap-bar');
    if (!canSlap()) { bar.hidden = true; slapMode = false; slapSel = []; return; }
    const w = st.slap;
    const left = Math.max(0, w.expires - Cabo.serverNow());
    const what = w.needed === 2 ? `a pair of ${rankLabel(w.rank)}s? (yours or stolen)` : `a ${rankLabel(w.rank)}? (or steal one)`;
    bar.hidden = false;
    bar.classList.toggle('armed', m === 'slap');
    bar.innerHTML = (m === 'slap'
      ? `<span class="slap-text">${w.needed === 2 ? 'Tap 2 cards — yours and/or theirs to steal…' : 'Tap your card — or theirs to steal…'}</span>
         <button class="btn btn-ghost btn-small" data-act="slap-cancel">Cancel</button>`
      : `<button class="btn slap-btn" data-act="slap-mode">SLAP!</button>
         <span class="slap-text">Got ${what}</span>`)
      + `<span class="slap-timer" style="animation-duration:${left.toFixed(2)}s"></span>`;
  }

  function renderDebtBar(m) {
    const bar = $('debt-bar');
    const d = myDebt();
    if (!d) { bar.hidden = true; giveMode = false; return; }
    const left = Math.max(0, d.expires - Cabo.serverNow());
    const who = E(nm(d.victim));
    bar.hidden = false;
    bar.classList.toggle('armed', m === 'debt-give');
    bar.innerHTML = (m === 'debt-give'
      ? `<span class="slap-text">Tap one of your cards to give ${who}…</span>
         <button class="btn btn-ghost btn-small" data-act="give-cancel">Back</button>`
      : `<span class="slap-text">You stole from ${who}! Pay back:</span>
         <button class="btn btn-small btn-neon" data-act="give-mode">Give a card</button>
         <button class="btn btn-small btn-gold" data-act="steal_draw">${who} draws</button>`)
      + `<span class="slap-timer" style="animation-duration:${left.toFixed(2)}s"></span>`;
  }

  function renderLobby() {
    const box = $('lobby-box');
    const seated = st.seats.filter(s => s.taken);
    box.innerHTML = `<div class="lobby-players">${seated.map(s =>
      `<span class="chip ${s.online ? 'on' : ''}">${E(s.name)}</span>`).join('') || 'Nobody yet'}</div>
      <p>${seated.length}/${st.seats.length} seated — need ${st.min_players}+ to play.</p>
      ${token ? `<button class="btn btn-neon" data-act="start_game" ${seated.length < st.min_players ? 'disabled' : ''}>Deal</button>` : ''}`;
  }

  function render() {
    if (!st) return;
    const m = mode();
    const me = st.seats[SEAT - 1];

    if (!token) {
      $('join-overlay').hidden = false;
      const taken = me.taken;
      $('join-msg').textContent = taken ? `${me.name} is sitting here. Pick another seat.` : 'What do they call you?';
      $('join-name').hidden = taken;
      $('join-form').querySelector('button[type=submit]').hidden = taken;
      if (!taken && !$('join-name').value) $('join-name').value = Cabo.getName();
    } else {
      $('join-overlay').hidden = true;
    }
    $('leave-btn').hidden = !token || (inGame() && (st.phase === 'peek' || st.phase === 'play'));
    $('end-game-btn').hidden = !(token && (st.phase === 'peek' || st.phase === 'play'));
    $('me-label').textContent = me.name ? `Seat ${SEAT} · ${me.name}` : `Seat ${SEAT}`;

    const lobby = st.phase === 'lobby';
    $('lobby-box').hidden = !lobby;
    $('piles').hidden = lobby;
    $('opponents').hidden = lobby;
    $('my-area').hidden = lobby || !inGame();
    if (lobby) renderLobby();
    else {
      renderOpponents(m);
      renderPiles(m);
      if (inGame()) {
        $('my-name').textContent = me.name || 'You';
        $('my-turn').hidden = !(st.phase === 'play' && st.turn === SEAT);
        $('my-cabo').hidden = st.cabo_by !== SEAT;
        $('my-total').textContent = '🏆 ' + ((st.wins || {})[String(SEAT)] || 0);
        $('my-area').classList.toggle('turn', st.phase === 'play' && st.turn === SEAT);
        $('my-slots').innerHTML = visibleSlots(SEAT).map((o, i) => slotHtml(SEAT, i, o, m)).join('');
      }
    }
    renderSlapBar(m);
    renderDebtBar(m);
    $('status').textContent = statusText(m);
    $('actions').innerHTML = lobby ? '' : actionsHtml(m);
    $('log').innerHTML = (st.events || []).slice(-3).reverse().map(e => `<div>${E(e.text)}</div>`).join('');

    const over = st.phase === 'round_over';
    $('results-overlay').hidden = !over || resultsHidden;
    if (over) {
      $('results').innerHTML = Cabo.resultsHtml(st, SEAT);
      $('next-btn').hidden = !token;
      $('next-btn').textContent = 'Deal again';
    }

    clearTimeout(rerenderTimer);
    const now = Cabo.serverNow();
    const times = reveals().concat(publicReveals()).concat((st.debts || []).filter(d => d.stealer === SEAT))
      .map(r => (r.expires - now) * 1000)
      .concat(st.slap && canSlap() ? [(st.slap.expires - now) * 1000] : [])
      .concat([...flashes.values()].map(t => t - Date.now()).filter(t => t > 0));
    if (times.length) rerenderTimer = setTimeout(render, Math.min(...times) + 50);
  }

  // ------------------------------------------------------------------ state updates
  function startDeal(d) {
    if (deal.cancel) deal.cancel();
    deal = { id: d.id, arrived: {}, done: false, cancel: null };
    st.order.forEach(s => { deal.arrived[s] = 0; });
    deal.cancel = Cabo.playDeal(d, (i, seat, animate) => {
      if (!animate) { deal.arrived[seat]++; return; }
      const target = seat === SEAT ? $('my-slots') : $('slots-' + seat);
      Cabo.fly($('deck'), target, { duration: 360, endScale: seat === SEAT ? 1.3 : 0.5 })
        .then(() => { deal.arrived[seat]++; render(); });
    }, () => { deal.done = true; render(); });
  }

  function onState(ns) {
    st = ns;
    if (token && !st.seats[SEAT - 1].taken) { token = null; Cabo.setToken(SEAT, null); }

    const ctx = [st.round_no, st.phase, st.turn, st.turn_state, st.pending_power].join('|');
    if (ctx !== selContext) { selContext = ctx; sel = { mine: [], cards: [], target: null, joker: null }; }

    const sid = st.slap ? st.slap.id + ':' + st.slap.needed : null;
    if (sid !== slapId) { slapId = sid; slapSel = []; slapSteals = []; slapMode = false; }
    if (!(st.phase === 'play' && st.turn === SEAT && st.turn_state === 'start')) { quartetMode = false; quartetSel = []; }

    if (st.deal && st.deal.id !== deal.id) startDeal(st.deal);
    if (!st.deal && deal.id) { if (deal.cancel) deal.cancel(); deal = { id: null, arrived: {}, done: true }; }

    const evs = st.events || [];
    const last = evs.length ? evs[evs.length - 1].id : 0;
    if (seenEvent === null) seenEvent = last;
    evs.filter(e => e.id > seenEvent).forEach(e => {
      const until = Date.now() + 2600;
      (e.positions || []).forEach(([s, i]) => flashes.set(s + ':' + i, until));
      if (e.seat !== SEAT || ['badmatch', 'roundend', 'slapfail', 'badquartet', 'stealdraw', 'stealgive'].includes(e.type)) toast(e.text);
    });
    seenEvent = Math.max(seenEvent, last);
    for (const [k, t] of flashes) if (t < Date.now()) flashes.delete(k);

    if (st.result && st.result.round !== resultsRound) { resultsRound = st.result.round; resultsHidden = false; }
    render();
  }

  // ------------------------------------------------------------------ input
  function toggleCard(seat, slot, max) {
    if (has(sel.cards, seat, slot)) sel.cards = sel.cards.filter(([s, i]) => !(s === seat && i === slot));
    else if (sel.cards.length < max) sel.cards = sel.cards.concat([[seat, slot]]);
  }

  function onSlot(seat, slot) {
    const m = mode();
    const mine = seat === SEAT;
    const toggleMine = () => {
      sel.mine = sel.mine.includes(slot) ? sel.mine.filter(x => x !== slot) : sel.mine.concat(slot);
    };
    switch (m) {
      case 'slap': {
        // pick `need` cards in total: your own and/or ones stolen from other players
        const need = st.slap.needed;
        const picked = slapSel.length + slapSteals.length;
        if (mine) {
          if (slapSel.includes(slot)) slapSel = slapSel.filter(x => x !== slot);
          else if (picked < need) slapSel = slapSel.concat(slot);
        } else if (has(slapSteals, seat, slot)) {
          slapSteals = slapSteals.filter(([s, i]) => !(s === seat && i === slot));
        } else if (picked < need) {
          slapSteals = slapSteals.concat([[seat, slot]]);
        }
        if (slapSel.length + slapSteals.length === need) {
          const payload = { slots: slapSel.slice(), steals: slapSteals.slice() };
          slapSel = []; slapSteals = []; slapMode = false;
          return act('slap', payload);
        }
        break;
      }
      case 'debt-give': giveMode = false; return act('steal_give', { slot });
      case 'quartet':
        quartetSel = quartetSel.includes(slot) ? quartetSel.filter(x => x !== slot)
          : (quartetSel.length < 4 ? quartetSel.concat(slot) : quartetSel);
        break;
      case 'initial': return act('initial_peek');           // cards 1 and 4 are fixed - tap either
      case 'drawn': if (mine) toggleMine(); break;
      case 'power-peek': return act('peek', { slot });
      case 'power-spy': return act('spy', { target: seat, target_slot: slot });
      case 'power-peekswap': return act('qpeek', { target: seat, target_slot: slot });
      case 'power-swap': case 'power-qswap': toggleCard(seat, slot, 2); break;
      case 'power-turnover': toggleCard(seat, slot, st.rules.turnover_max); break;
      case 'joker-give':
        if (mine) sel.mine = sel.mine.includes(slot) ? [] : [slot];
        else sel.target = sel.target === seat ? null : seat;
        break;
      case 'joker-deal':
        if (!mine) sel.target = sel.target === seat ? null : seat;
        break;
      default: return;
    }
    render();
  }

  document.addEventListener('click', ev => {
    const slotEl = ev.target.closest('.slot.pickable');
    if (slotEl) return onSlot(+slotEl.dataset.seat, +slotEl.dataset.slot);
    const oppEl = ev.target.closest('.opp.target-pick');
    if (oppEl) { const s = +oppEl.dataset.opp; sel.target = sel.target === s ? null : s; return render(); }

    const a = ev.target.closest('[data-act]');
    if (a && !a.disabled) {
      const id = a.dataset.act;
      if (id === 'show-results') { resultsHidden = false; return render(); }
      if (id === 'give-mode') { giveMode = true; return render(); }
      if (id === 'give-cancel') { giveMode = false; return render(); }
      if (id === 'slap-mode') { slapMode = true; slapSel = []; slapSteals = []; return render(); }
      if (id === 'slap-cancel') { slapMode = false; slapSel = []; slapSteals = []; return render(); }
      if (id === 'quartet-mode') { quartetMode = true; quartetSel = []; return render(); }
      if (id === 'quartet-cancel') { quartetMode = false; quartetSel = []; return render(); }
      if (id === 'quartet') { const slots = quartetSel; quartetMode = false; quartetSel = []; return act('quartet', { slots }); }
      if (id === 'joker-give' || id === 'joker-deal') {
        sel.joker = id.slice(6); sel.mine = []; sel.target = null; return render();
      }
      if (id === 'joker-back') { sel.joker = null; return render(); }
      if (id === 'joker_deal') return act('joker_deal', { target: sel.target });
      if (id === 'replace') return act('replace', { slots: sel.mine });
      if (id === 'swap') return act('swap', { a: sel.cards[0], b: sel.cards[1] });
      if (id === 'turnover') return act('turnover', { positions: sel.cards });
      if (id === 'joker_give') return act('joker_give', { slot: sel.mine[0], target: sel.target });
      if (id === 'cabo' && !confirm('Call CABO? Everyone else gets one more turn.')) return;
      return act(id);
    }
    if (ev.target.closest('#deck.pickable')) return act('draw');
    if (ev.target.closest('#discard.pickable')) return act('take_discard');

    const open = ev.target.closest('[data-open]');
    if (open) $(open.dataset.open).hidden = false;
    const close = ev.target.closest('[data-close]');
    if (close) $(close.dataset.close).hidden = true;
  });

  $('hide-results').addEventListener('click', () => { resultsHidden = true; render(); });
  $('next-btn').addEventListener('click', () => act('start_game'));
  $('end-game-btn').addEventListener('click', () => {
    if (confirm('End this round for everyone?')) {
      $('rules-overlay').hidden = true;
      act('end_game');
    }
  });

  $('join-form').addEventListener('submit', async ev => {
    ev.preventDefault();
    const name = $('join-name').value.trim();
    if (!name) return;
    try {
      const r = await Cabo.api('claim', { seat: SEAT, name });
      token = r.token;
      Cabo.setToken(SEAT, token);
      Cabo.setName(name);
      render();
    } catch (e) { $('join-msg').textContent = e.message; }
  });

  $('leave-btn').addEventListener('click', async () => {
    try { await Cabo.api('leave', { seat: SEAT, token }); }
    catch (e) { return toast(e.message, true); }
    Cabo.setToken(SEAT, null);
    location.href = Cabo.lobbyUrl;
  });

  window.addEventListener('resize', render);
  poller = Cabo.poll(onState, { seat: SEAT, token: () => token });
})();
