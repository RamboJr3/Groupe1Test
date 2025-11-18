# ================================================================
# DOMINION ENGINE + TOUTES TES CARTES CUSTOM (VERSION PATCHÉE)
# ================================================================

import random
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable

# ================================================================
# CARD BASE CLASS
# ================================================================

class Card:
    def __init__(self, name, type, cost, action=None, on_buy=None):
        self.name = name
        self.type = type  # "TREASURE", "VICTORY", "CURSE", "ACTION", "ATTACK"
        self.cost = cost
        self.action = action
        self.on_buy = on_buy

    def __repr__(self):
        return self.name

# ================================================================
# ACTION EFFECTS — STANDARD
# ================================================================

def act_village(p, g):
    p.draw(1)
    p.actions += 2

def act_smithy(p, g):
    p.draw(3)

def act_market(p, g):
    p.draw(1)
    p.actions += 1
    p.buys += 1
    p.coins += 1

def act_festival(p, g):
    p.actions += 2
    p.buys += 1
    p.coins += 2

def act_laboratory(p, g):
    p.draw(2)
    p.actions += 1

def act_woodcutter(p, g):
    p.buys += 1
    p.coins += 2

def act_militia(p, g):
    p.coins += 2
    for opp in g.players:
        if opp is p:
            continue
        while len(opp.hand) > 3:
            opp.discard_one()

def act_witch(p, g):
    p.draw(2)
    for opp in g.players:
        if opp is not p:
            if g.stock["CURSE"] > 0:
                opp.gain("CURSE")
                g.stock["CURSE"] -= 1

def act_bandit(p, g):
    p.gain("GOLD")
    for opp in g.players:
        if opp is p:
            continue
        revealed = opp.reveal(2)
        if "GOLD" in revealed:
            opp.trash_card("GOLD")
            revealed.remove("GOLD")
        elif "SILVER" in revealed:
            opp.trash_card("SILVER")
            revealed.remove("SILVER")
        opp.discard_revealed(revealed)

def act_bureaucrat(p, g):
    p.gain("SILVER")
    for opp in g.players:
        if opp is p:
            continue
        vc = [c for c in opp.hand if CARDS[c].type == "VICTORY"]
        if vc:
            chosen = vc[0]
            opp.hand.remove(chosen)
            opp.deck.append(chosen)

def act_council_room(p, g):
    p.draw(4)
    p.buys += 1
    for opp in g.players:
        if opp is not p:
            opp.draw(1)

def act_chancellor(p, g):
    p.coins += 2
    if hasattr(p.strat, "chancellor_discard") and p.strat.chancellor_discard(p, g):
        p.discard.extend(p.deck)
        p.deck = []

# ================================================================
# CUSTOM CARDS EFFECTS
# ================================================================

def on_play_cursed_gold(p, g):
    p.coins += 3
    if g.stock["CURSE"] > 0:
        p.gain("CURSE")
        g.stock["CURSE"] -= 1

def act_port(p, g):
    p.draw(1)
    p.actions += 1

def on_buy_port(p, g):
    if g.stock["PORT"] > 0:
        g.stock["PORT"] -= 1
        p.gain("PORT")

def act_mag_pie(p, g):
    p.draw(1)
    p.actions += 1
    rev = p.reveal(1)
    if not rev:
        return
    top = rev[0]
    if CARDS[top].type == "TREASURE":
        p.draw(1)
        if g.stock["MAG_PIE"] > 0:
            p.gain("MAG_PIE")
            g.stock["MAG_PIE"] -= 1
    else:
        p.deck.append(top)

def act_harvest(p, g):
    rev = p.reveal(4)
    types = set(CARDS[c].type for c in rev)
    for _ in range(min(len(types), 4)):
        p.draw(1)
    p.discard_revealed(rev)

def act_artificer(p, g):
    p.draw(1)
    p.actions += 1
    p.coins += 1

    if not hasattr(p.strat, "artificer_discard"):
        return

    to_discard = p.strat.artificer_discard(p, g)
    if not to_discard:
        return

    for c in to_discard:
        if c in p.hand:
            p.hand.remove(c)
            p.discard.append(c)

    cost = len(to_discard)
    if hasattr(p.strat, "gain_cost"):
        gain = p.strat.gain_cost(p, g, cost)
        if gain and g.stock[gain] > 0:
            g.stock[gain] -= 1
            p.gain(gain)

def act_poacher(p, g):
    p.draw(1)
    p.actions += 1
    p.coins += 1
    empty = sum(1 for v in g.stock.values() if v == 0)
    for _ in range(empty):
        if p.hand:
            p.discard_one()

def act_marquis(p, g):
    p.buys += 1
    initial = len(p.hand)
    p.draw(initial)
    while len(p.hand) > 10:
        p.discard_one()

def act_remake(p, g):
    if not hasattr(p.strat, "remake_trash"):
        return
    to_trash = p.strat.remake_trash(p, g)
    if to_trash not in p.hand:
        return
    base_cost = CARDS[to_trash].cost
    p.hand.remove(to_trash)
    if hasattr(p.strat, "gain_cost"):
        gain = p.strat.gain_cost(p, g, base_cost + 1)
        if gain and g.stock[gain] > 0:
            g.stock[gain] -= 1
            p.gain(gain)

def act_farming_village(p, g):
    while True:
        if not p.deck:
            p.shuffle_discard_into_deck()
        if not p.deck:
            break
        c = p.deck.pop()
        if CARDS[c].type in ("ACTION","TREASURE"):
            p.hand.append(c)
            break
        else:
            p.discard.append(c)
    p.actions += 2

def act_hireling(p, g):
    if "HIRELING" not in p.in_play:
        p.in_play.append("HIRELING")

def act_distant_shore(p, g):
    p.draw(2)
    p.actions += 1
    p.gain("ESTATE")

# ================================================================
# CARDS REGISTER
# ================================================================

CARDS = {
    # TREASURES
    "COPPER": Card("COPPER", "TREASURE", 0),
    "SILVER": Card("SILVER", "TREASURE", 3),
    "GOLD": Card("GOLD", "TREASURE", 6),
    "CURSED_GOLD": Card("CURSED_GOLD", "TREASURE", 4, action=on_play_cursed_gold),

    # VICTORY
    "ESTATE": Card("ESTATE", "VICTORY", 2),
    "DUCHY": Card("DUCHY", "VICTORY", 5),
    "PROVINCE": Card("PROVINCE", "VICTORY", 8),
    "COLONY": Card("COLONY", "VICTORY", 11),

    # CURSE
    "CURSE": Card("CURSE", "CURSE", 0),

    # ACTIONS
    "VILLAGE": Card("VILLAGE", "ACTION", 3, action=act_village),
    "MARKET": Card("MARKET", "ACTION", 5, action=act_market),
    "SMITHY": Card("SMITHY", "ACTION", 4, action=act_smithy),
    "LABORATORY": Card("LABORATORY", "ACTION", 5, action=act_laboratory),
    "FESTIVAL": Card("FESTIVAL", "ACTION", 5, action=act_festival),
    "WOODCUTTER": Card("WOODCUTTER", "ACTION", 3, action=act_woodcutter),

    # ATTACKS
    "MILITIA": Card("MILITIA", "ATTACK", 4, action=act_militia),
    "WITCH": Card("WITCH", "ATTACK", 5, action=act_witch),
    "BANDIT": Card("BANDIT", "ATTACK", 5, action=act_bandit),
    "BUREAUCRAT": Card("BUREAUCRAT", "ATTACK", 4, action=act_bureaucrat),

    # SUPPORT
    "COUNCIL_ROOM": Card("COUNCIL_ROOM", "ACTION", 5, action=act_council_room),
    "DISTANT_SHORE": Card("DISTANT_SHORE", "ACTION", 6, action=act_distant_shore),
    "FARMING_VILLAGE": Card("FARMING_VILLAGE", "ACTION", 4, action=act_farming_village),
    "HIRELING": Card("HIRELING", "ACTION", 6, action=act_hireling),
    "CHANCELLOR": Card("CHANCELLOR", "ACTION", 3, action=act_chancellor),

    # CUSTOM CARDS
    "ARTIFICER": Card("ARTIFICER", "ACTION", 5, action=act_artificer),
    "MARQUIS": Card("MARQUIS", "ACTION", 6, action=act_marquis),
    "POACHER": Card("POACHER", "ACTION", 4, action=act_poacher),
    "HARVEST": Card("HARVEST", "ACTION", 5, action=act_harvest),
    "MAG_PIE": Card("MAG_PIE", "ACTION", 4, action=act_mag_pie),
    "PORT": Card("PORT", "ACTION", 4, action=act_port, on_buy=on_buy_port),
    "REMAKE": Card("REMAKE", "ACTION", 4, action=act_remake),
}

# ================================================================
# PLAYER CLASS
# ================================================================

@dataclass
class Player:
    name: str
    strat: any

    deck: List[str] = field(default_factory=lambda: ["COPPER"]*7 + ["ESTATE"]*3)
    hand: List[str] = field(default_factory=list)
    discard: List[str] = field(default_factory=list)
    in_play: List[str] = field(default_factory=list)

    actions: int = 1
    buys: int = 1
    coins: int = 0
    score: int = 3

    def shuffle_discard_into_deck(self):
        if self.discard:
            random.shuffle(self.discard)
            self.deck.extend(self.discard)
            self.discard = []

    def draw(self, n):
        for _ in range(n):
            if not self.deck:
                self.shuffle_discard_into_deck()
            if not self.deck:
                return
            self.hand.append(self.deck.pop())

    def reveal(self, n):
        rev=[]
        for _ in range(n):
            if not self.deck:
                self.shuffle_discard_into_deck()
            if not self.deck:
                break
            rev.append(self.deck.pop())
        return rev

    def discard_revealed(self, cards):
        self.discard.extend(cards)

    def discard_one(self):
        if not self.hand:
            return
        weakest = sorted(self.hand, key=lambda c: CARDS[c].cost)[0]
        self.hand.remove(weakest)
        self.discard.append(weakest)

    def trash_card(self, c):
        pass

    def gain(self, card):
        self.discard.append(card)
        if card == "ESTATE": self.score += 1
        elif card == "DUCHY": self.score += 3
        elif card == "PROVINCE": self.score += 6
        elif card == "COLONY": self.score += 10

    def cleanup(self):
        self.discard.extend(self.hand)
        self.hand=[]
        self.discard.extend(self.in_play)
        self.in_play=[]
        self.actions=1
        self.buys=1
        self.coins=0

# ================================================================
# GAME ENGINE
# ================================================================

@dataclass
class Game:
    players: List[Player]
    stock: Dict[str,int]

class DominionGame:
    def __init__(self, players, stock=None):
        if stock is None:
            stock = {c: 10 for c in CARDS}
            stock["PROVINCE"] = 8
            stock["CURSE"] = 30
        self.game = Game(players, stock)

    def setup(self):
        for p in self.game.players:
            random.shuffle(p.deck)
            p.draw(5)

    def play_action_cards(self, p):
        while p.actions > 0:
            acts = [c for c in p.hand if CARDS[c].type in ("ACTION","ATTACK")]
            if not acts:
                return
            chosen = p.strat.play_action(p, self.game)
            if chosen not in p.hand:
                return
            p.hand.remove(chosen)
            p.in_play.append(chosen)
            p.actions -= 1
            if CARDS[chosen].action:
                CARDS[chosen].action(p, self.game)

    def play_treasures(self, p):
        for c in list(p.hand):
            if CARDS[c].type == "TREASURE":
                if c == "COPPER": p.coins += 1
                elif c == "SILVER": p.coins += 2
                elif c == "GOLD": p.coins += 3
                elif c == "CURSED_GOLD":
                    on_play_cursed_gold(p, self.game)

    def buy_phase(self, p):
        while p.buys > 0:
            card = p.strat.buy(p, self.game)
            if not card:
                return
            if self.game.stock.get(card,0) <= 0:
                return
            if p.coins < CARDS[card].cost:
                return
            p.coins -= CARDS[card].cost
            p.gain(card)
            self.game.stock[card] -= 1
            if CARDS[card].on_buy:
                CARDS[card].on_buy(p, self.game)
            p.buys -= 1

    def end_turn(self, p):
        p.cleanup()

        # HIRELING permanent effect
        if "HIRELING" in p.in_play:
            p.draw(1)

        p.draw(5)

    def is_game_over(self):
        if self.game.stock["PROVINCE"] <= 0:
            return True
        if self.game.stock["COLONY"] <= 0:
            return True
        empty = sum(1 for v in self.game.stock.values() if v == 0)
        return empty >= 3

    def final_scoring(self):
        for p in self.game.players:
            gardens = (
                p.hand.count("GARDENS")
                + p.discard.count("GARDENS")
                + p.deck.count("GARDENS")
            )
            if gardens > 0:
                total = len(p.hand) + len(p.discard) + len(p.deck)
                p.score += (total // 10) * gardens

    def run(self, max_turns=50):
        self.setup()
        t=0
        while t < max_turns:
            t+=1
            for p in self.game.players:
                if self.is_game_over():
                    self.final_scoring()
                    return self.game
                self.play_action_cards(p)
                self.play_treasures(p)
                self.buy_phase(p)
                self.end_turn(p)
        self.final_scoring()
        return self.game

# ================================================================
# STRATÉGIES
# ================================================================

class StratRhumRuin:
    def play_action(self, p, g):
        priority = [
            "MARKET", "LABORATORY", "VILLAGE", "FESTIVAL",
            "HIRELING", "WITCH", "SMITHY", "WOODCUTTER",
        ]
        for c in priority:
            if c in p.hand and p.actions > 0:
                return c
        return None

    def buy(self, p, g):
        money = p.coins
        stock = g.stock

        def ok(x):
            return stock.get(x,0)>0 and CARDS[x].cost <= money

        if ok("PROVINCE"): return "PROVINCE"
        if ok("WITCH"): return "WITCH"
        if ok("HIRELING"): return "HIRELING"
        if ok("MARKET"): return "MARKET"
        if ok("LABORATORY"): return "LABORATORY"
        if ok("GOLD"): return "GOLD"
        if ok("VILLAGE"): return "VILLAGE"
        if ok("DUCHY") and stock["PROVINCE"] <= 4: return "DUCHY"
        if ok("SILVER"): return "SILVER"
        return None

    # MÉTHODES PAR DÉFAUT
    def artificer_discard(self, p, g): return []
    def gain_cost(self, p, g, cost): return None
    def remake_trash(self, p, g): return None
    def chancellor_discard(self, p, g): return False

class StratGinTeRuine:
    def play_action(self, p, g): return None

    def buy(self, p, g):
        m = p.coins
        s = g.stock
        def ok(x):
            return s.get(x,0)>0 and CARDS[x].cost <= m
        if ok("PROVINCE"): return "PROVINCE"
        if m>=5 and ok("DUCHY"): return "DUCHY"
        if m>=6 and ok("GOLD"): return "GOLD"
        if m>=3 and ok("SILVER"): return "SILVER"
        return None

    # MÉTHODES PAR DÉFAUT
    def artificer_discard(self, p, g): return []
    def gain_cost(self, p, g, cost): return None
    def remake_trash(self, p, g): return None
    def chancellor_discard(self, p, g): return False

# ================================================================
# SIMULATEUR DE PARTIES — 10 000 PARTIES AUTOMATIQUES
# ================================================================

def simulate_one(stratA, stratB, max_turns=40):
    """Simule une partie unique entre stratA et stratB."""
    p1 = Player("P1", stratA)
    p2 = Player("P2", stratB)
    game = DominionGame([p1, p2])
    result = game.run(max_turns=max_turns)

    # Détermination du vainqueur
    if p1.score > p2.score:
        return "P1"
    elif p2.score > p1.score:
        return "P2"
    else:
        return "DRAW"


def simulate_batch(stratA, stratB, n=10000, max_turns=40):
    """Simule n parties et retourne les statistiques."""
    wins = {"P1": 0, "P2": 0, "DRAW": 0}

    for _ in range(n):
        r = simulate_one(stratA, stratB, max_turns=max_turns)
        wins[r] += 1

    print("\n===================== RESULTATS =====================")
    print(f"P1 ({stratA.__class__.__name__}) : {wins['P1']} victoires ({wins['P1']/n*100:.2f} %)")
    print(f"P2 ({stratB.__class__.__name__}) : {wins['P2']} victoires ({wins['P2']/n*100:.2f} %)")
    print(f"Égalités : {wins['DRAW']} ({wins['DRAW']/n*100:.2f} %)")
    print("=====================================================\n")

    return wins


# ================================================================
# EXEMPLE D’UTILISATION DIRECTE
# ================================================================
if __name__ == "__main__":
    print("Simulation de 10 000 parties...\n")
    simulate_batch(StratRhumRuin(), StratGinTeRuine(), n=10000)
