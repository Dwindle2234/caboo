"""
Cabo - game engine.

Based on the original Cabo (Melissa Limes & Mandy Henning, 2010), played with
a 54-card deck and our LOCAL RULES:

  * Deal: 4 cards each, no card turned up. Each player then looks at cards 1 and 4.
  * Each game is a single round, and it only ends when someone calls Cabo.
  * Values: A-10 face value, J / Q / black K = 10, red K = -2, Joker = 11.
  * Powers trigger only when a power card is SWAPPED OUT of your hand (by
    keeping a drawn or discard-pile card). Discarding a power card you've just
    drawn does nothing. Powers act on cards in front of players:
      7/8   Peek   - look at one of your own cards
      9/10  Spy    - look at one card of another player
      J     Swap   - blind swap of any two cards (yours or other players')
      Q     Peek & swap - look at any card, then you MAY blind swap any two cards
      Joker Give one of your cards to another player, or make another player
            draw a card from the deck into their hand (face down, unseen)
      A♠    Turn up to 2 cards face up for the rest of the round (anyone's)
  * Ties: the tied player with the MOST cards wins; still tied = a draw.
  * SLAPDOWN: when a card is discarded, EVERY player (the discarder too) races
    to slap a card of the same rank onto it. Slaps must complete a pair: the
    first slap is one card; after that a slap must be a pair. Any slap may use
    cards STOLEN from other players; after each good steal the stealer must
    give the victim one of their cards, or make them draw from the deck.
    A wrong slap: the cards are shown to everyone, go back to their owners,
    and the slapper draws one card blind as a penalty.
  * QUARTET: on your turn you can play all four cards of one rank - an
    immediate win, whatever else is left in your hand.
  * No Kamikaze, no 100-point rules.

Every tunable rule lives in RULES. State is in memory, so run ONE process
(gunicorn --workers 1, any number of threads).
"""
import copy
import secrets
import threading
import time
import uuid

# --------------------------------------------------------------------------- deck
SUITS = ["S", "H", "D", "C"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
JOKERS = ["JR", "JB"]
MAX_SEATS = 5
MIN_PLAYERS = 2

RULES = {
    # ---- setup
    "cards_per_player": 4,          # dealt face-down in a row
    "initial_peek_slots": [0, 3],   # after the deal each player looks at cards 1 and 4 (fixed, not chosen)
    "peek_seconds": 6,              # how long the initial peek stays on screen
    "reveal_seconds": 4,            # how long a Peek / Spy stays on screen
    "deal_interval_ms": 160,
    # ---- card values
    "rank_values": {"A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
                    "10": 10, "J": 10, "Q": 10, "K": 10, "JOKER": 11},
    "card_values": {"KH": -2, "KD": -2},    # per-card overrides: red kings
    # ---- powers: trigger when a power card is swapped OUT of your hand (never from a drawn card)
    "powers": {"7": "peek", "8": "peek", "9": "spy", "10": "spy",
               "J": "swap", "Q": "peekswap", "JOKER": "joker"},
    "card_powers": {"AS": "turnover"},      # per-card powers (override rank powers)
    "turnover_max": 2,                      # A♠: how many cards can be turned face up
    "joker_keep_one": True,                 # can't give away your last card
    # ---- turns
    "discard_take_must_swap": True,         # a card taken from the discard pile must be kept
    "max_match": 4,                         # max cards swapped out in one go (all same rank)
    # ---- slapdown / quartet
    "slap_seconds": 5,                      # how long a discard stays slappable (reset by each good slap)
    "slap_penalty_cards": 1,                # blind cards drawn for a wrong slap
    "slap_show_seconds": 4,                 # how long a wrong slap / bad match stays face up
    "steal_choice_seconds": 15,             # stealer's time to give a card / make the victim draw (then: draw)
    # ---- end of round
    "tie_break": "most_cards",              # lowest total; ties -> most cards; still tied -> draw
}

POWER_LABEL = {"peek": "PEEK", "spy": "SPY", "swap": "SWAP", "peekswap": "PEEK & SWAP",
               "joker": "JOKER", "turnover": "TURN OVER"}


def full_deck():
    return [r + s for s in SUITS for r in RANKS] + list(JOKERS)


def rank_of(code):
    return "JOKER" if code in JOKERS else code[:-1]


def value_of(code, rules=RULES):
    if code in rules["card_values"]:
        return rules["card_values"][code]
    return rules["rank_values"][rank_of(code)]


def pretty(code):
    """'10H' -> '10♥', 'JR' -> 'Joker'."""
    if code in JOKERS:
        return "Joker"
    return code[:-1] + {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}[code[-1]]


def rank_name(code):
    """'10H' -> '10', 'JR' -> 'Joker'."""
    return "Joker" if code in JOKERS else code[:-1]


def power_of(code, rules=RULES):
    return rules["card_powers"].get(code) or rules["powers"].get(rank_of(code))


class RuleError(Exception):
    """An illegal move - the message is shown to the player."""


_rng = secrets.SystemRandom()


# --------------------------------------------------------------------------- seats
class Seat:
    def __init__(self, number):
        self.number = number
        self.name = None
        self.token = None
        self.last_seen = 0.0

    @property
    def taken(self):
        return self.token is not None

    def public(self):
        return {"seat": self.number, "name": self.name, "taken": self.taken,
                "online": self.taken and (time.time() - self.last_seen) < 30}


# --------------------------------------------------------------------------- table
class Table:
    def __init__(self, code="main"):
        self.code = code
        self.rules = copy.deepcopy(RULES)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.seats = {n: Seat(n) for n in range(1, MAX_SEATS + 1)}
        self.version = 0
        self.events = []
        self._event_id = 0
        self.wins = {}                   # round wins this session, by seat
        self.round_no = 0
        self.last_winners = []
        self._clear_round()
        self.phase = "lobby"             # lobby | peek | play | round_over

    # ------------------------------------------------------------------ helpers
    def _clear_round(self):
        self.order = []                  # seats playing this round, clockwise
        self.slots = {}                  # seat -> [card | None, ...]
        self.faceup = set()              # cards turned face up by the A♠ (cards are unique)
        self.draw = []
        self.discard = []
        self.turn = None
        self.turn_state = None           # start | drawn | power
        self.drawn = None
        self.drawn_from = None
        self.pending_power = None        # peek | spy | swap | peekswap | qswap | joker | turnover
        self.cabo_by = None
        self.starter = None
        self.peeked = set()
        self.ready = set()
        self.reveals = []                # private: {viewer, seat, slot, card, expires}
        self.public_reveals = []         # shown to everyone: {seat, slot, card, expires}
        self.slap = None                 # open slap window: {id, rank, count, discarder, expires}
        self.debts = []                  # steal paybacks owed: {id, stealer, victim, expires}
        self.deal = None
        self.result = None

    def _bump(self):
        self.version += 1
        self.changed.notify_all()

    def _event(self, kind, text, positions=(), **data):
        self._event_id += 1
        self.events.append({"id": self._event_id, "type": kind, "text": text, "t": time.time(),
                            "positions": [list(p) for p in positions], **data})
        self.events = self.events[-40:]

    def _name(self, seat):
        return self.seats[seat].name or f"Seat {seat}"

    def _after(self, delay, fn):
        def run():
            with self.lock:
                fn()
        t = threading.Timer(delay, run)
        t.daemon = True
        t.start()

    def _next_seat(self, seat):
        i = self.order.index(seat)
        return self.order[(i + 1) % len(self.order)]

    def _require_turn(self, seat, *states):
        if self.phase != "play":
            raise RuleError("The round isn't in play")
        if seat != self.turn:
            raise RuleError("It's not your turn")
        if self.turn_state not in states:
            raise RuleError("You can't do that right now")

    def _require_power(self, seat, *powers):
        self._require_turn(seat, "power")
        if self.pending_power not in powers:
            raise RuleError("You can't do that with this card")

    def _slot(self, seat, slot):
        if seat not in self.slots:
            raise RuleError("That player isn't in this round")
        cards = self.slots[seat]
        if not isinstance(slot, int) or not 0 <= slot < len(cards) or cards[slot] is None:
            raise RuleError("There's no card there")
        return cards[slot]

    def _pos(self, p):
        """Validate a [seat, slot] pair from the client."""
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            raise RuleError("Pick a card")
        seat, slot = p
        self._slot(seat, slot)
        return (seat, slot)

    def _draw_card(self):
        if not self.draw and self.discard:
            # The whole discard pile is shuffled and becomes a fresh deck
            self.draw = self.discard
            _rng.shuffle(self.draw)
            self.discard = []
            self.slap = None                    # nothing left on the pile to slap on
            self._event("reshuffle", "Deck ran out — the discard pile was shuffled into a fresh deck")
        if not self.draw:
            raise RuleError("No cards left to draw")
        return self.draw.pop()

    def _to_discard(self, card):
        self.faceup.discard(card)
        self.discard.append(card)

    def _reveal(self, viewer, seat, slot, seconds):
        self.reveals.append({"viewer": viewer, "seat": seat, "slot": slot,
                             "card": self.slots[seat][slot], "expires": time.time() + seconds})

    def _forget_reveals(self, seat, slot):
        self.reveals = [r for r in self.reveals if not (r["seat"] == seat and r["slot"] == slot)]
        self.public_reveals = [r for r in self.public_reveals
                               if not (r["seat"] == seat and r["slot"] == slot)]

    def _show_all(self, seat, slots):
        """Turn cards face up for everyone for a few seconds (wrong slap, bad match)."""
        until = time.time() + self.rules["slap_show_seconds"]
        for s in slots:
            self.public_reveals.append({"seat": seat, "slot": s, "card": self.slots[seat][s], "expires": until})

    def _add_to_hand(self, seat, card):
        """Put a card into the first gap in a player's row, or a new slot on the end."""
        dest = self.slots[seat]
        if None in dest:
            i = dest.index(None)
            dest[i] = card
        else:
            dest.append(card)
            i = len(dest) - 1
        return i

    # ---- slapdown window
    @staticmethod
    def _rank_total(rank):
        return 2 if rank == "JOKER" else 4

    def _open_slap(self, discarder, cards):
        """A turn just put `cards` (all the same rank) on the discard pile: open the slap window."""
        rank = rank_of(cards[-1])
        count = sum(1 for c in cards if rank_of(c) == rank)
        if count >= self._rank_total(rank):
            self.slap = None
            return
        self.slap = {"id": uuid.uuid4().hex[:8], "rank": rank, "count": count, "discarder": discarder,
                     "expires": time.time() + self.rules["slap_seconds"]}

    def _slap_open(self):
        return self.slap is not None and self.slap["expires"] > time.time()

    def _whose(self, viewer, seat):
        return "their own" if viewer == seat else f"{self._name(seat)}'s"

    # ------------------------------------------------------------------ seats
    def claim_seat(self, number, name):
        with self.lock:
            seat = self.seats.get(number)
            if seat is None:
                raise RuleError("No such seat")
            if seat.taken:
                raise RuleError("Seat already taken")
            name = (name or "").strip()[:16] or f"Player {number}"
            seat.name, seat.token, seat.last_seen = name, secrets.token_urlsafe(16), time.time()
            self._event("join", f"{name} sat down")
            self._bump()
            return seat.token

    def leave_seat(self, number, token):
        with self.lock:
            seat = self.auth(number, token)
            if number in self.order and self.phase in ("peek", "play"):
                raise RuleError("You're in a round — end it first")
            self._event("leave", f"{seat.name} left")
            seat.name = seat.token = None
            self.wins.pop(number, None)
            self._bump()

    def auth(self, number, token):
        seat = self.seats.get(number)
        if seat is None or not seat.token or not secrets.compare_digest(seat.token, token or ""):
            raise PermissionError("Not your seat")
        seat.last_seen = time.time()
        return seat

    # ------------------------------------------------------------------ game flow
    def start_game(self, by):
        """Start a round (each game is a single round)."""
        with self.lock:
            if self.phase not in ("lobby", "round_over"):
                raise RuleError("A round is already running")
            players = [n for n, s in self.seats.items() if s.taken]
            if len(players) < MIN_PLAYERS:
                raise RuleError(f"Need at least {MIN_PLAYERS} players")
            # last round's winner starts if still playing; otherwise someone at random
            starters = [s for s in self.last_winners if s in players] or players
            self._clear_round()
            self.order = players
            for s in players:
                self.wins.setdefault(s, 0)
            self._event("game", f"{self._name(by)} dealt a new round")
            self._start_round(starter=_rng.choice(starters))

    def _start_round(self, starter):
        self.round_no += 1
        self.starter = starter
        deck = full_deck()
        _rng.shuffle(deck)
        self.slots = {s: [] for s in self.order}
        sequence = []
        i = self.order.index(starter)
        rotated = self.order[i:] + self.order[:i]
        for _ in range(self.rules["cards_per_player"]):
            for s in rotated:
                self.slots[s].append(deck.pop())
                sequence.append(s)
        self.discard = []                       # no card is turned up after the deal
        self.draw = deck
        self.phase = "peek"
        lead_in = 1.2
        self.deal = {"id": uuid.uuid4().hex[:10], "started": time.time() + lead_in,
                     "interval_ms": self.rules["deal_interval_ms"], "sequence": sequence}
        self._event("round", f"Round {self.round_no} — {self._name(starter)} starts. "
                             f"Look at your cards {self._peek_names()}.")
        self._bump()

    def _peek_names(self):
        nums = [str(i + 1) for i in self.rules["initial_peek_slots"]]
        return " and ".join([", ".join(nums[:-1]), nums[-1]] if len(nums) > 1 else nums)

    def initial_peek(self, seat, slots=None):
        """After the deal, each player looks at their fixed cards (1 and 4) - not a free choice."""
        with self.lock:
            if self.phase != "peek" or seat not in self.order:
                raise RuleError("Not the time to peek")
            if seat in self.peeked:
                raise RuleError("You've already had your look")
            slots = [s for s in self.rules["initial_peek_slots"]
                     if s < len(self.slots[seat]) and self.slots[seat][s] is not None]
            secs = self.rules["peek_seconds"]
            for s in slots:
                self._reveal(seat, seat, s, secs)
            self.peeked.add(seat)
            self._event("peeked", f"{self._name(seat)} looked at their cards",
                        positions=[(seat, s) for s in slots], seat=seat)
            self._bump()
            round_no = self.round_no
            self._after(secs, lambda: self._mark_ready(seat, round_no))

    def _mark_ready(self, seat, round_no):
        if self.phase != "peek" or self.round_no != round_no:
            return
        self.ready.add(seat)
        if self.ready >= set(self.order):
            self.phase = "play"
            self.turn = self.starter
            self.turn_state = "start"
            self._event("turn", f"Everyone's looked. {self._name(self.turn)} to play.")
        self._bump()

    # ------------------------------------------------------------------ turn actions
    def draw_from_deck(self, seat):
        with self.lock:
            self._require_turn(seat, "start")
            if not self.draw and not self.discard:
                # every card is in someone's hand (lots of slap penalties): nothing to draw, turn passes
                self._event("pass", f"No cards left to draw — {self._name(seat)}'s turn passes", seat=seat)
                self._end_turn()
                return
            self.drawn = self._draw_card()
            self.drawn_from = "deck"
            self.turn_state = "drawn"
            self._event("draw", f"{self._name(seat)} drew from the deck", seat=seat)
            self._bump()

    def take_discard(self, seat):
        with self.lock:
            self._require_turn(seat, "start")
            if not self.discard:
                raise RuleError("The discard pile is empty")
            self.drawn = self.discard.pop()
            self.drawn_from = "discard"
            self.slap = None
            self.turn_state = "drawn"
            self._event("take", f"{self._name(seat)} took the {pretty(self.drawn)} from the discard pile",
                        seat=seat, card=self.drawn)
            self._bump()

    def replace(self, seat, slots):
        """Keep the drawn card, swapping out one card - or a set of matching cards."""
        with self.lock:
            self._require_turn(seat, "drawn")
            slots = sorted(set(slots))
            if not slots:
                raise RuleError("Pick at least one of your cards")
            if len(slots) > self.rules["max_match"]:
                raise RuleError(f"You can swap out at most {self.rules['max_match']} cards")
            cards = [self._slot(seat, s) for s in slots]
            name = self._name(seat)
            pos = [(seat, s) for s in slots]
            if len({rank_of(c) for c in cards}) > 1:
                # failed match: everyone sees the cards, they go back, the drawn card is discarded
                self._to_discard(self.drawn)
                self._open_slap(seat, [self.drawn])
                self._show_all(seat, slots)
                self._event("badmatch", f"{name} tried to match {', '.join(map(pretty, cards))} — "
                                        f"no match! Turn lost.", positions=pos, seat=seat, cards=cards)
                self._end_turn()
                return
            for s in slots:
                self._forget_reveals(seat, s)
            self.slots[seat][slots[0]] = self.drawn
            for s in slots[1:]:
                self.slots[seat][s] = None
            for c in cards:
                self._to_discard(c)
            self._open_slap(seat, cards)
            what = "a card" if len(slots) == 1 else f"{len(slots)} matching cards"
            # a power card swapped out of your hand gives you its power (one use, even for a matched set)
            powers = [power_of(c, self.rules) for c in cards]
            power = next((p for p in powers if p), None)
            text = f"{name} swapped out {what}: {', '.join(map(pretty, cards))}"
            if power:
                text += f" — {POWER_LABEL[power]}!"
            self._event("swap", text, positions=pos, seat=seat, cards=cards, power=power)
            if power:
                self.drawn = None
                self.drawn_from = None
                self.turn_state = "power"
                self.pending_power = power
                self._bump()
            else:
                self._end_turn()

    def discard_drawn(self, seat):
        """Discard the card you just drew. Its power is NOT used - powers only come from swapping out."""
        with self.lock:
            self._require_turn(seat, "drawn")
            if self.drawn_from == "discard" and self.rules["discard_take_must_swap"]:
                raise RuleError("A card taken from the discard pile must be kept")
            card = self.drawn
            self._to_discard(card)
            self._open_slap(seat, [card])
            self.drawn = None
            self._event("discard", f"{self._name(seat)} discarded the {pretty(card)}", seat=seat, card=card)
            self._end_turn()

    # ---- 7/8: peek at your own card
    def use_peek(self, seat, slot):
        with self.lock:
            self._require_power(seat, "peek")
            self._slot(seat, slot)
            self._reveal(seat, seat, slot, self.rules["reveal_seconds"])
            self._event("peek", f"{self._name(seat)} peeked at their own card",
                        positions=[(seat, slot)], seat=seat)
            self._end_turn()

    # ---- 9/10: spy on another player's card
    def use_spy(self, seat, target, slot):
        with self.lock:
            self._require_power(seat, "spy")
            if target == seat:
                raise RuleError("Spy on someone else's card")
            self._slot(target, slot)
            self._reveal(seat, target, slot, self.rules["reveal_seconds"])
            self._event("spy", f"{self._name(seat)} spied on {self._name(target)}'s card",
                        positions=[(target, slot)], seat=seat)
            self._end_turn()

    # ---- Q step 1: look at any card
    def use_qpeek(self, seat, target, slot):
        with self.lock:
            self._require_power(seat, "peekswap")
            self._slot(target, slot)
            self._reveal(seat, target, slot, self.rules["reveal_seconds"] + 2)
            self.pending_power = "qswap"
            self._event("qpeek", f"{self._name(seat)} looked at {self._whose(seat, target)} card",
                        positions=[(target, slot)], seat=seat)
            self._bump()

    # ---- J, or Q step 2: blind swap of any two cards on the table
    def use_swap(self, seat, a, b):
        with self.lock:
            self._require_power(seat, "swap", "qswap")
            a, b = self._pos(a), self._pos(b)
            if a == b:
                raise RuleError("Pick two different cards")
            (sa, ia), (sb, ib) = a, b
            self.slots[sa][ia], self.slots[sb][ib] = self.slots[sb][ib], self.slots[sa][ia]
            self._forget_reveals(sa, ia)
            self._forget_reveals(sb, ib)
            if sa == sb:
                who = f"two of {self._whose(seat, sa)} cards"
            else:
                who = f"{self._whose(seat, sa)} card with {self._whose(seat, sb)} card"
            self._event("blindswap", f"{self._name(seat)} swapped {who}", positions=[a, b], seat=seat)
            self._end_turn()

    # ---- Joker: give a card away...
    def joker_give(self, seat, slot, target):
        with self.lock:
            self._require_power(seat, "joker")
            if target == seat or target not in self.order:
                raise RuleError("Give it to another player")
            card = self._slot(seat, slot)
            if self.rules["joker_keep_one"] and sum(c is not None for c in self.slots[seat]) <= 1:
                raise RuleError("You can't give away your last card")
            self._forget_reveals(seat, slot)
            self.slots[seat][slot] = None
            dest = self.slots[target]
            if None in dest:
                new = dest.index(None)
                dest[new] = card
            else:
                dest.append(card)
                new = len(dest) - 1
            self._event("give", f"{self._name(seat)} gave a card to {self._name(target)}",
                        positions=[(seat, slot), (target, new)], seat=seat)
            self._end_turn()

    # ---- ...or make another player draw a card into their hand
    def joker_deal(self, seat, target):
        with self.lock:
            self._require_power(seat, "joker")
            if target == seat or target not in self.order:
                raise RuleError("Pick another player")
            card = self._draw_card()
            dest = self.slots[target]
            if None in dest:
                new = dest.index(None)
                dest[new] = card
            else:
                dest.append(card)
                new = len(dest) - 1
            self._event("jokerdraw", f"{self._name(seat)} made {self._name(target)} draw a card",
                        positions=[(target, new)], seat=seat)
            self._end_turn()

    # ---- A♠: turn up to 2 cards face up for the rest of the round
    def use_turnover(self, seat, positions):
        with self.lock:
            self._require_power(seat, "turnover")
            pos = [self._pos(p) for p in positions]
            if not 1 <= len(set(pos)) == len(pos) <= self.rules["turnover_max"]:
                raise RuleError(f"Pick 1 to {self.rules['turnover_max']} different cards")
            cards = []
            for s, i in pos:
                c = self.slots[s][i]
                if c in self.faceup:
                    raise RuleError("That card is already face up")
                self.faceup.add(c)
                cards.append(f"{self._whose(seat, s)} {pretty(c)}")
            self._event("turnover", f"{self._name(seat)} turned over " + " and ".join(cards),
                        positions=pos, seat=seat)
            self._end_turn()

    def skip_power(self, seat):
        with self.lock:
            self._require_turn(seat, "power")
            done = "didn't swap" if self.pending_power == "qswap" else "didn't use the power"
            self._event("skip", f"{self._name(seat)} {done}", seat=seat)
            self._end_turn()

    # ------------------------------------------------------------------ slapdown
    def slap_down(self, seat, slots, steals=None):
        """Race to slap matching card(s) onto the discard. Processed in the order slaps reach the server.

        Anyone may slap, including the player who discarded. Slaps make pairs: the first slap is ONE card;
        after that a slap must be a PAIR. Slapped cards can be your own or STOLEN from other players'
        hands (e.g. ones you've spied) - a pair can even be two stolen cards. After each successful steal
        the stealer owes that victim: give them one of their own cards, or make them draw from the deck.
        """
        with self.lock:
            if self.phase != "play" or seat not in self.order:
                raise RuleError("You can't slap now")
            if not self._slap_open():
                raise RuleError("Too late — nothing to slap")
            w = self.slap
            slots = sorted(set(s for s in slots if isinstance(s, int)))
            stolen = []
            for p in (steals or []):
                sp = self._pos(p)
                if sp[0] == seat:
                    raise RuleError("Steal from another player")
                if sp not in stolen:
                    stolen.append(sp)
            rank = w["rank"]
            needed = 1 if w["count"] % 2 else 2
            if len(slots) + len(stolen) != needed:
                if needed == 1:
                    raise RuleError("Slap ONE card to make the pair — yours or one you steal")
                raise RuleError("That pair's made — slap a PAIR now (yours, stolen, or one of each)")
            own = [(seat, s) for s in slots]
            positions = own + stolen
            cards = [self._slot(p, i) for p, i in positions]
            name = self._name(seat)
            target = pretty(self.discard[-1]) if self.discard else rank
            parts = [pretty(c) for c in cards[:len(own)]]
            parts += [f"{self._name(p)}'s {pretty(c)}" for (p, _), c in zip(stolen, cards[len(own):])]

            if all(rank_of(c) == rank for c in cards):
                for (p, i), c in zip(positions, cards):
                    self._forget_reveals(p, i)
                    self.slots[p][i] = None
                    self._to_discard(c)
                w["count"] += len(cards)
                w["expires"] = time.time() + self.rules["slap_seconds"]
                if stolen:
                    what = " + ".join(parts) + " (stolen!)"
                elif len(cards) == 1:
                    what = parts[0]
                else:
                    what = f"a pair of {rank_name(cards[0])}s"
                self._event("slap", f"SLAPDOWN! {name} slapped {what} on the {target}",
                            positions=positions, seat=seat, cards=cards, victims=[p for p, _ in stolen])
                if w["count"] >= self._rank_total(rank):
                    self.slap = None
                for victim, _ in stolen:
                    self._add_debt(seat, victim)        # one payback per stolen card
                self._bump()
                return

            # wrong slap: everyone sees the card(s), they stay with their owners,
            # and the slapper draws a blind penalty card
            for p, i in positions:
                self._show_all(p, [i])
            new = []
            for _ in range(self.rules["slap_penalty_cards"]):
                if not self.draw and not self.discard:
                    break                                   # no cards left to hand out
                new.append((seat, self._add_to_hand(seat, self._draw_card())))
            self._event("slapfail", f"{name} slapped {' + '.join(parts)} on the {target} — WRONG! "
                                    f"+{len(new)} card", positions=positions + new, seat=seat, cards=cards)
            self._bump()

    # ---- after a successful steal, the stealer must pay the victim back
    def _add_debt(self, stealer, victim):
        debt = {"id": uuid.uuid4().hex[:8], "stealer": stealer, "victim": victim,
                "expires": time.time() + self.rules["steal_choice_seconds"]}
        self.debts.append(debt)
        round_no = self.round_no
        self._after(self.rules["steal_choice_seconds"], lambda: self._auto_settle(debt["id"], round_no))

    def _auto_settle(self, debt_id, round_no):
        if self.round_no != round_no or self.phase != "play":
            return
        debt = next((d for d in self.debts if d["id"] == debt_id), None)
        if debt:
            self._settle(debt, "draw", auto=True)
            self._bump()

    def _settle(self, debt, how, slot=None, auto=False):
        """Pay a steal debt: 'give' one of the stealer's cards to the victim, or make the victim 'draw'."""
        stealer, victim = debt["stealer"], debt["victim"]
        can_draw = bool(self.draw) or bool(self.discard)
        own = [i for i, c in enumerate(self.slots[stealer]) if c is not None]
        if how == "draw" and not can_draw:
            how = "give" if own else None                   # deck empty: fall back to giving a card
            slot = own[0] if own else None
        if how == "give" and slot is None:
            slot = own[0] if own else None
            if slot is None:
                how = "draw" if can_draw else None
        self.debts.remove(debt)
        late = " (time up)" if auto else ""
        if how == "give":
            card = self.slots[stealer][slot]
            self._forget_reveals(stealer, slot)
            self.slots[stealer][slot] = None
            new = self._add_to_hand(victim, card)
            self._event("stealgive", f"{self._name(stealer)} gave {self._name(victim)} a card for the steal{late}",
                        positions=[(stealer, slot), (victim, new)], seat=stealer)
        elif how == "draw":
            new = self._add_to_hand(victim, self._draw_card())
            self._event("stealdraw", f"{self._name(stealer)} made {self._name(victim)} draw a card for the "
                                     f"steal{late}", positions=[(victim, new)], seat=stealer)
        else:
            self._event("stealfree", f"No cards to pay {self._name(victim)} back with", seat=stealer)

    def settle_steal(self, seat, how, slot=None):
        with self.lock:
            if self.phase != "play":
                raise RuleError("Not now")
            debt = next((d for d in self.debts if d["stealer"] == seat), None)
            if debt is None:
                raise RuleError("You don't owe anyone a card")
            if how == "give":
                self._slot(seat, slot)
            elif how == "draw":
                if not self.draw and not self.discard:
                    raise RuleError("The deck is empty — give them one of your cards")
            else:
                raise RuleError("Give a card or make them draw")
            self._settle(debt, how, slot)
            self._bump()

    def quartet(self, seat, slots):
        """Play all four cards of one rank at once (on your turn, instead of drawing)."""
        with self.lock:
            self._require_turn(seat, "start")
            slots = sorted(set(slots))
            if len(slots) != 4:
                raise RuleError("A quartet is four cards")
            cards = [self._slot(seat, s) for s in slots]
            name = self._name(seat)
            if len({rank_of(c) for c in cards}) > 1:
                self._show_all(seat, slots)
                self._event("badquartet", f"{name} tried a quartet with {', '.join(map(pretty, cards))} — "
                                          f"no good! Turn lost.", positions=[(seat, s) for s in slots], seat=seat)
                self._end_turn()
                return
            for s, c in zip(slots, cards):
                self._forget_reveals(seat, s)
                self.slots[seat][s] = None
                self._to_discard(c)
            self.slap = None
            self._event("quartet", f"QUARTET! {name} played all four {rank_name(cards[0])}s",
                        positions=[(seat, s) for s in slots], seat=seat, cards=cards)
            self._end_round(quartet=seat)          # any quartet wins outright

    def call_cabo(self, seat):
        with self.lock:
            self._require_turn(seat, "start")
            if self.cabo_by is not None:
                raise RuleError("Cabo has already been called")
            self.cabo_by = seat
            self._event("cabo", f"{self._name(seat)} called CABO! Everyone else gets one more turn.",
                        seat=seat)
            self._end_turn()

    def _end_turn(self):
        self.drawn = None
        self.drawn_from = None
        self.pending_power = None
        nxt = self._next_seat(self.turn)
        if self.cabo_by is not None and nxt == self.cabo_by:
            if self._slap_open() or self.debts:
                # give everyone the chance to slap on the last discard before the cards are shown
                self.turn = None
                self.turn_state = "ending"
                self._event("ending", "Last chance to slap…")
                self._bump()
                self._finish_after_slaps(self.round_no)
            else:
                self._end_round()
            return
        self.turn = nxt
        self.turn_state = "start"
        self._bump()

    def _finish_after_slaps(self, round_no):
        def check():
            if self.phase != "play" or self.turn_state != "ending" or self.round_no != round_no:
                return
            if self._slap_open() or self.debts:
                self._finish_after_slaps(round_no)      # a slap extended the window / steal not paid back yet
            else:
                self._end_round()
        now = time.time()
        waits = [d["expires"] - now for d in self.debts] + ([self.slap["expires"] - now] if self.slap else [])
        waits = [w for w in waits if w > 0]
        self._after((min(waits) if waits else 0) + 0.05, check)

    # ------------------------------------------------------------------ scoring
    def _end_round(self, quartet=None):
        for debt in list(self.debts):                   # any unpaid steals: the victim draws
            self._settle(debt, "draw", auto=True)
        hands = {s: [c for c in self.slots[s] if c is not None] for s in self.order}
        sums = {s: sum(value_of(c, self.rules) for c in hands[s]) for s in self.order}
        counts = {s: len(hands[s]) for s in self.order}
        low = min(sums.values())
        lowest = [s for s in self.order if sums[s] == low]
        caller = self.cabo_by
        notes = []
        winners = lowest
        if len(lowest) > 1:
            most = max(counts[s] for s in lowest)
            winners = [s for s in lowest if counts[s] == most]
            names = " & ".join(self._name(s) for s in lowest)
            if len(winners) == 1:
                notes.append(f"{names} tied on {low} — {self._name(winners[0])} wins with the most cards "
                             f"({most}).")
            else:
                notes.append(f"It's a draw! {' & '.join(self._name(s) for s in winners)} tied on {low} "
                             f"points and {most} cards.")
        if caller is not None:
            if caller in winners:
                notes.append(f"{self._name(caller)} called Cabo and won.")
            else:
                notes.append(f"{self._name(caller)} called Cabo but didn't win!")
        if quartet is not None:
            winners = [quartet]
            notes = [f"{self._name(quartet)} made a QUARTET and wins outright — cards and points don't count!"]
        for s in winners:
            self.wins[s] = self.wins.get(s, 0) + 1
        ranking = sorted(self.order, key=lambda s: (quartet is not None and s != quartet, sums[s], -counts[s]))
        self.last_winners = winners
        self.result = {
            "round": self.round_no,
            "hands": {str(s): list(self.slots[s]) for s in self.order},
            "sums": {str(s): sums[s] for s in self.order},
            "counts": {str(s): counts[s] for s in self.order},
            "draw": len(winners) > 1,
            "quartet": quartet,
            "ranking": ranking,
            "winners": winners,
            "caller": caller,
            "notes": notes,
        }
        self.phase = "round_over"
        self.turn = None
        self.turn_state = None
        self.reveals = []
        self.public_reveals = []
        self.slap = None
        self.debts = []
        names = " & ".join(self._name(s) for s in winners)
        if quartet is not None:
            self._event("roundend", f"QUARTET! {names} wins round {self.round_no}!")
        elif len(winners) > 1:
            self._event("roundend", f"Round {self.round_no} over — it's a draw between {names}!")
        else:
            self._event("roundend", f"Round {self.round_no} over — {names} wins with {low}!")
        self._bump()

    def end_game(self, by):
        with self.lock:
            if self.phase == "lobby":
                return
            self._event("game", f"{self._name(by)} ended the round")
            self._clear_round()
            self.phase = "lobby"
            self._bump()

    # ------------------------------------------------------------------ views
    def view(self, viewer=None):
        """Public state, plus private bits for `viewer` (a seat number they've authenticated)."""
        with self.lock:
            now = time.time()
            self.reveals = [r for r in self.reveals if r["expires"] > now]
            self.public_reveals = [r for r in self.public_reveals if r["expires"] > now]
            slap = None
            if self._slap_open():
                w = self.slap
                slap = {"id": w["id"], "rank": w["rank"], "needed": 1 if w["count"] % 2 else 2,
                        "discarder": w["discarder"], "expires": w["expires"]}
            # each slot: None = empty, "?" = face down, or the card code if turned face up
            slots = {str(s): [None if c is None else (c if c in self.faceup else "?") for c in cards]
                     for s, cards in self.slots.items()}
            st = {
                "table": self.code,
                "version": self.version,
                "server_time": now,
                "phase": self.phase,
                "seats": [s.public() for s in self.seats.values()],
                "min_players": MIN_PLAYERS,
                "order": self.order,
                "wins": {str(k): v for k, v in self.wins.items()},
                "round_no": self.round_no,
                "starter": self.starter,
                "turn": self.turn,
                "turn_state": self.turn_state,
                "cabo_by": self.cabo_by,
                "slots": slots,
                "draw_count": len(self.draw),
                "discard_top": self.discard[-1] if self.discard else None,
                "discard_count": len(self.discard),
                "drawn_from": self.drawn_from,
                "drawn_public": self.drawn if self.drawn_from == "discard" else None,
                "pending_power": self.pending_power,
                "slap": slap,
                "debts": [{"id": d["id"], "stealer": d["stealer"], "victim": d["victim"],
                           "expires": d["expires"]} for d in self.debts],
                "public_reveals": [{"seat": r["seat"], "slot": r["slot"], "card": r["card"],
                                    "expires": r["expires"]} for r in self.public_reveals],
                "peeked": sorted(self.peeked),
                "ready": sorted(self.ready),
                "deal": self.deal,
                "result": self.result,
                "events": self.events[-15:],
                "rules": {"initial_peek_slots": self.rules["initial_peek_slots"],
                          "turnover_max": self.rules["turnover_max"],
                          "slap_seconds": self.rules["slap_seconds"],
                          "steal_choice_seconds": self.rules["steal_choice_seconds"],
                          "labels": POWER_LABEL},
            }
            if viewer is not None:
                st["me"] = {
                    "seat": viewer,
                    "drawn": self.drawn if (self.turn == viewer and self.drawn) else None,
                    "reveals": [{"seat": r["seat"], "slot": r["slot"], "card": r["card"],
                                 "expires": r["expires"]}
                                for r in self.reveals if r["viewer"] == viewer],
                }
            return st

    def wait_for_change(self, since, timeout=20.0):
        """Long-poll: block until the version moves past `since` (or timeout)."""
        with self.lock:
            end = time.time() + timeout
            while self.version == since:
                left = end - time.time()
                if left <= 0:
                    break
                self.changed.wait(left)


class TableRegistry:
    def __init__(self):
        self._tables = {}
        self._lock = threading.Lock()

    def get(self, code):
        code = (code or "main").lower()[:24]
        with self._lock:
            if code not in self._tables:
                self._tables[code] = Table(code)
            return self._tables[code]
