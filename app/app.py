"""
Cabo - Flask web app.

Routes
  /                          -> lobby for the default table
  /t/<table>/                -> lobby for a named table
  /t/<table>/seat/<n>        -> a player's phone screen (seats 1-5)
  /t/<table>/board           -> shared table view (TV / laptop), public info only
  /t/<table>/api/state       -> long-poll for state (?since=<version>)
  /t/<table>/api/claim|leave -> take / leave a seat
  /t/<table>/api/act         -> every game move
  /healthz                   -> health check
"""
import os
import re
import secrets

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from game import MAX_SEATS, RULES, RuleError, TableRegistry

app = Flask(__name__)
# Flask's signing key. Cabo doesn't use cookies/sessions yet, so it isn't critical - but it must never live
# in the repo. Set CABO_SECRET_KEY in the untracked .env file; if it's not set, a random key is made at startup.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

_hops = int(os.environ.get("PROXY_HOPS", "1"))
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_hops, x_proto=_hops, x_host=_hops, x_prefix=_hops)

tables = TableRegistry()
TABLE_RE = re.compile(r"^[a-z0-9-]{1,24}$")
LONG_POLL_SECONDS = 20


def get_table(code):
    code = code.lower()
    if not TABLE_RE.match(code):
        abort(404)
    return tables.get(code)


def body():
    return request.get_json(silent=True) or {}


def _read_version():
    """version.txt is bumped by hand on every change. It's added to every static URL as ?v=<version>,
    so phones always fetch the new JS/CSS/cards after an update."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.txt")
    try:
        with open(path) as f:
            return f.read().strip() or "0"
    except OSError:
        return "0"


ASSET_VERSION = _read_version()


def asset(filename):
    return url_for("static", filename=filename, v=ASSET_VERSION)


@app.context_processor
def inject_rules():
    return {"RULES": RULES, "asset": asset, "ASSET_VERSION": ASSET_VERSION}


@app.after_request
def no_stale_pages(resp):
    # Pages and API are never cached. Static files (JS/CSS/cards) may be cached, but the phone must check
    # with the server each time (a tiny "304 Not Modified" when unchanged). So a fixed file always reaches
    # the phones, even if version.txt wasn't bumped. The ?v=<version> in the URL is still a second safety net.
    if request.endpoint != "static":
        resp.headers["Cache-Control"] = "no-store"
    else:
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# --------------------------------------------------------------------------- pages
@app.route("/")
def home():
    return redirect(url_for("lobby", table="main"))


@app.route("/t/<table>/")
def lobby(table):
    t = get_table(table)
    return render_template("lobby.html", table=t.code, seats=range(1, MAX_SEATS + 1))


@app.route("/t/<table>/seat/<int:seat>")
def seat_view(table, seat):
    t = get_table(table)
    if seat not in range(1, MAX_SEATS + 1):
        abort(404)
    return render_template("seat.html", table=t.code, seat=seat)


@app.route("/t/<table>/board")
def board_view(table):
    t = get_table(table)
    return render_template("board.html", table=t.code)


@app.route("/healthz")
def healthz():
    return {"ok": True}


# --------------------------------------------------------------------------- api
def _viewer(t):
    seat = request.args.get("seat", type=int)
    token = request.headers.get("X-Seat-Token")
    if seat and token:
        try:
            t.auth(seat, token)
            return seat
        except PermissionError:
            pass
    return None


@app.get("/t/<table>/api/state")
def api_state(table):
    t = get_table(table)
    since = request.args.get("since", type=int)
    if since is not None:
        t.wait_for_change(since, LONG_POLL_SECONDS)
    return jsonify(t.view(_viewer(t)))


@app.post("/t/<table>/api/claim")
def api_claim(table):
    t = get_table(table)
    data = body()
    try:
        token = t.claim_seat(int(data.get("seat", 0)), data.get("name", ""))
    except RuleError as e:
        return jsonify(error=str(e)), 409
    return jsonify(token=token)


@app.post("/t/<table>/api/leave")
def api_leave(table):
    t = get_table(table)
    data = body()
    try:
        t.leave_seat(int(data.get("seat", 0)), data.get("token"))
    except PermissionError as e:
        return jsonify(error=str(e)), 403
    except RuleError as e:
        return jsonify(error=str(e)), 409
    return jsonify(ok=True)


def _int(data, key):
    v = data.get(key)
    return v if isinstance(v, int) else None


@app.post("/t/<table>/api/act")
def api_act(table):
    t = get_table(table)
    data = body()
    seat = _int(data, "seat")
    try:
        t.auth(seat, data.get("token"))
    except PermissionError as e:
        return jsonify(error=str(e)), 403

    action = data.get("action")
    slots = [s for s in (data.get("slots") or []) if isinstance(s, int)]
    def pos(key):
        p = data.get(key)
        return p if isinstance(p, list) and len(p) == 2 and all(isinstance(x, int) for x in p) else None

    actions = {
        "start_game": lambda: t.start_game(seat),
        "next_round": lambda: t.start_game(seat),
        "end_game": lambda: t.end_game(seat),
        "initial_peek": lambda: t.initial_peek(seat, slots),
        "draw": lambda: t.draw_from_deck(seat),
        "take_discard": lambda: t.take_discard(seat),
        "replace": lambda: t.replace(seat, slots),
        "discard": lambda: t.discard_drawn(seat),
        "peek": lambda: t.use_peek(seat, _int(data, "slot")),
        "spy": lambda: t.use_spy(seat, _int(data, "target"), _int(data, "target_slot")),
        "qpeek": lambda: t.use_qpeek(seat, _int(data, "target"), _int(data, "target_slot")),
        "swap": lambda: t.use_swap(seat, pos("a"), pos("b")),
        "joker_give": lambda: t.joker_give(seat, _int(data, "slot"), _int(data, "target")),
        "joker_deal": lambda: t.joker_deal(seat, _int(data, "target")),
        "turnover": lambda: t.use_turnover(seat, [p for p in (data.get("positions") or [])
                                                  if isinstance(p, list)]),
        "skip_power": lambda: t.skip_power(seat),
        "cabo": lambda: t.call_cabo(seat),
        "slap": lambda: t.slap_down(seat, slots, [p for p in (data.get("steals") or [])
                                                  if isinstance(p, list) and len(p) == 2
                                                  and all(isinstance(x, int) for x in p)]),
        "quartet": lambda: t.quartet(seat, slots),
        "steal_give": lambda: t.settle_steal(seat, "give", _int(data, "slot")),
        "steal_draw": lambda: t.settle_steal(seat, "draw"),
    }
    if action not in actions:
        return jsonify(error="Unknown action"), 400
    try:
        actions[action]()
    except RuleError as e:
        return jsonify(error=str(e)), 409
    return jsonify(t.view(seat))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True, threaded=True)
