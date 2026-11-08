/* Cabo - shared client helpers (used by lobby, seat and board pages) */
(function () {
  const body = document.body;
  const API = body.dataset.api;          // includes any reverse-proxy prefix
  const CARDS = body.dataset.cards;
  const V = body.dataset.v ? '?v=' + body.dataset.v : '';
  const TABLE = body.dataset.table;

  let clockOffset = 0;                   // server_time - client_time (seconds)

  async function api(path, payload, headers) {
    const opts = payload === undefined
      ? { headers: headers || {} }
      : { method: 'POST', headers: Object.assign({ 'Content-Type': 'application/json' }, headers || {}),
          body: JSON.stringify(payload) };
    const res = await fetch(API + path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.error || res.statusText), { status: res.status });
    return data;
  }

  const cardUrl = code => CARDS + code + '.svg' + V;
  const backUrl = () => CARDS + 'back.svg' + V;

  /* Show any script error on screen instead of failing silently. */
  function showError(msg) {
    let bar = document.getElementById('error-bar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'error-bar';
      bar.innerHTML = '<span></span><button type="button">Reload</button>';
      bar.querySelector('button').onclick = () => location.reload();
      document.body.appendChild(bar);
    }
    bar.querySelector('span').textContent = 'Something went wrong: ' + msg;
  }
  window.addEventListener('error', e => showError(e.message || 'script error'));
  window.addEventListener('unhandledrejection', e => showError((e.reason && e.reason.message) || String(e.reason)));
  const serverNow = () => Date.now() / 1000 + clockOffset;

  /*
   * Keep this phone's idea of the server's clock in step. Every state carries server_time (stamped as it
   * is sent), so we can correct on EVERY update - even slow ones - not just quick round trips. Slap
   * windows, peeks and the deal all depend on this; a phone/server clock difference must not break them.
   */
  function syncClock(serverTime, t0, t1) {
    if (typeof serverTime !== 'number') return;
    const now = t1 || Date.now() / 1000;
    const quick = t0 && (now - t0) < 1.5;
    // quick round trip: assume the server stamped it halfway; otherwise it was stamped just before arrival
    clockOffset = serverTime - (quick ? (t0 + now) / 2 : now);
  }
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /*
   * Long-poll the table state. The server holds each request until something
   * changes (or ~20s pass), so moves appear on every screen almost instantly.
   */
  function poll(cb, opts) {
    opts = opts || {};
    let version = null, stopped = false;
    async function loop() {
      while (!stopped) {
        try {
          const q = new URLSearchParams();
          if (version !== null) q.set('since', version);
          if (opts.seat) q.set('seat', opts.seat);
          const h = opts.token ? { 'X-Seat-Token': opts.token() || '' } : {};
          const t0 = Date.now() / 1000;
          const st = await api('state?' + q, undefined, h);
          const t1 = Date.now() / 1000;
          syncClock(st.server_time, t0, t1);
          version = st.version;
          cb(st);
        } catch (e) {
          console.warn('poll failed', e);
          await new Promise(r => setTimeout(r, 2000));
        }
      }
    }
    loop();
    return {
      stop: () => { stopped = true; },
      push: st => { syncClock(st.server_time); version = st.version; cb(st); },   // state returned by an action
    };
  }

  /* Seat tokens are remembered per table+seat on this device. */
  const tokenKey = seat => 'cabo:' + TABLE + ':seat' + seat;
  function safeGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function safeSet(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} }

  /* Animate a card back flying from one element to another. */
  function fly(fromEl, toEl, opts) {
    opts = opts || {};
    return new Promise(resolve => {
      if (!fromEl || !toEl) return resolve();
      const a = fromEl.getBoundingClientRect(), b = toEl.getBoundingClientRect();
      const w = opts.width || a.width, h = w * 1.4;
      const img = document.createElement('img');
      img.src = opts.face ? cardUrl(opts.face) : backUrl();
      img.className = 'flying-card';
      Object.assign(img.style, { width: w + 'px', height: h + 'px',
        left: (a.left + a.width / 2 - w / 2) + 'px', top: (a.top + a.height / 2 - h / 2) + 'px' });
      document.body.appendChild(img);
      const dx = (b.left + b.width / 2) - (a.left + a.width / 2);
      const dy = (b.top + b.height / 2) - (a.top + a.height / 2);
      const spin = Math.random() * 30 - 15;
      img.animate([
        { transform: 'translate(0,0) rotate(0deg) scale(1)' },
        { transform: `translate(${dx}px, ${dy}px) rotate(${spin}deg) scale(${opts.endScale || 0.7})` }
      ], { duration: opts.duration || 380, easing: 'cubic-bezier(.2,.7,.3,1)', fill: 'forwards' })
        .finished.then(() => { img.remove(); resolve(); });
    });
  }

  /* Play a deal in step with the server clock (late joiners see it already done). */
  function playDeal(deal, onCard, onDone) {
    let cancelled = false;
    const elapsedMs = (serverNow() - deal.started) * 1000;
    const timers = [];
    deal.sequence.forEach((seat, i) => {
      const at = i * deal.interval_ms - elapsedMs;
      if (at <= 0) onCard(i, seat, false);
      else timers.push(setTimeout(() => { if (!cancelled) onCard(i, seat, true); }, at));
    });
    const finishAt = deal.sequence.length * deal.interval_ms - elapsedMs + 450;
    timers.push(setTimeout(() => { if (!cancelled && onDone) onDone(); }, Math.max(0, finishAt)));
    return () => { cancelled = true; timers.forEach(clearTimeout); };
  }

  /* ---------------------------------------------------------------- shared renderers */
  const name = (st, seat) => {
    const s = st.seats[seat - 1];
    return (s && s.name) || 'Seat ' + seat;
  };

  // players clockwise, starting after `from` (or from the first player)
  function clockwiseFrom(st, from) {
    const o = st.order;
    const i = o.indexOf(from);
    if (i < 0) return o.slice();
    return o.slice(i + 1).concat(o.slice(0, i));
  }

  function resultsHtml(st, me) {
    const r = st.result;
    if (!r) return '';
    const rows = r.ranking.map((s, place) => {
      const k = String(s);
      const cards = r.hands[k].map(c => c
        ? `<img src="${cardUrl(c)}" alt="${c}">` : '<span class="gap"></span>').join('');
      const badges = (r.winners.includes(s) ? `<span class="badge win">${r.draw ? 'DRAW' : 'WIN'}</span>` : '')
        + (r.caller === s ? '<span class="badge cabo">CABO</span>' : '');
      const n = r.counts ? r.counts[k] : r.hands[k].filter(Boolean).length;
      const wins = (st.wins || {})[k] || 0;
      return `<div class="res-row ${s === me ? 'me' : ''}">
        <div class="res-name"><span class="place">${place + 1}</span> ${esc(name(st, s))} ${badges}</div>
        <div class="res-cards">${cards}</div>
        <div class="res-nums"><span title="cards">${n} card${n === 1 ? '' : 's'}</span>
          <strong title="points">${r.sums[k]}</strong>
          <span title="rounds won">🏆 ${wins}</span></div></div>`;
    }).join('');
    const who = r.winners.map(s => esc(name(st, s))).join(' & ');
    const title = r.draw ? `Round ${r.round} — it's a draw: ${who}` : `Round ${r.round} — ${who} wins!`;
    return `<h2>${title}</h2>
      ${r.notes.map(n => `<p class="note">${esc(n)}</p>`).join('')}
      <div class="res-head"><span>Player</span><span>Cards · points · rounds won</span></div>
      ${rows}`;
  }

  window.Cabo = {
    api, poll, fly, playDeal, cardUrl, backUrl, serverNow, esc, name, clockwiseFrom,
    resultsHtml,
    table: TABLE, lobbyUrl: body.dataset.lobby,
    getToken: seat => safeGet(tokenKey(seat)),
    setToken: (seat, t) => safeSet(tokenKey(seat), t),
    getName: () => safeGet('cabo:name') || '',
    setName: n => safeSet('cabo:name', n),
  };
})();
