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
    """
    Version classe pour le moteur DominionGame, dérivée de ta deuxième strat.

    Philosophie :
    - Moteur léger Village/Market/Laboratory/Festival/Hireling.
    - Witch agressive pendant la phase Curses.
    - HIRELING en priorité à 6$ en mid/late.
    - Gestion Duchy agressive si retard ou fin de pile Province.
    - Deny Village si un bot 'Equipe3' est à la table.
    """

    def __init__(self):
        self.turn: int = 1
        self.owned: Dict[str, int] = {}   # suivi des cartes achetées par ce bot

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------
    def _hand_counts(self, p: Player) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for c in p.hand:
            counts[c] = counts.get(c, 0) + 1
        return counts

    def _has_in_hand(self, p: Player, card_name: str) -> bool:
        return card_name in p.hand

    # ------------------------------------------------------------------
    # PHASE ACTION
    # ------------------------------------------------------------------
    def play_action(self, p: Player, g: Game) -> Optional[str]:
        """
        Retourne le nom de la carte Action à jouer, ou None.
        Le moteur DominionGame appliquera ensuite CARDS[chosen].action.
        """
        if p.actions <= 0:
            return None

        # priority tuned for engine-first but allow attacking (WITCH) after +actions
        action_priority = [
            "MARKET",      # +1 carte, +1 action, +1 buy, +1$
            "LABORATORY",  # +2 cartes, +1 action
            "VILLAGE",     # +2 actions, +1 carte
            "FESTIVAL",    # +2 actions, +1 buy, +2$
            "HIRELING",    # terminal draw continu
            "WITCH",       # attaque
            "SMITHY",      # +3 cartes
            "WOODCUTTER",  # +1 buy, +2$
        ]

        for name in action_priority:
            if name in CARDS and self._has_in_hand(p, name):
                return name

        return None

    # ------------------------------------------------------------------
    # PHASE ACHAT
    # ------------------------------------------------------------------
    def buy(self, p: Player, g: Game) -> Optional[str]:
        """
        Retourne le nom de la carte à acheter, ou None.
        """
        money = p.coins
        stock = g.stock

        def can_buy(name: str) -> bool:
            return (
                name in CARDS
                and stock.get(name, 0) > 0
                and CARDS[name].cost <= money
            )

        # infos de game
        prov_left = stock.get("PROVINCE", 0)
        curses_left = stock.get("CURSE", 0)
        villages_left = stock.get("VILLAGE", 0)

        my_score = getattr(p, "score", 0) or 0
        max_opp_score = max(
            (getattr(op, "score", 0) or 0)
            for op in g.players
            if op is not p
        ) if g.players else 0

        # mode Duchy agressif si peu de Provinces ou gros retard au score
        AGGRO_DUCHY = (prov_left <= 4) or ((max_opp_score - my_score) >= 4)

        # détection d'un bot 'Equipe3' en face (pour deny Village)
        enemy_alive = any(
            "equipe3" in (getattr(op, "name", "") or "").lower()
            for op in g.players
            if op is not p
        )

        # comptages internes (approx) des cartes possédées par cette strat
        vg_cnt = self.owned.get("VILLAGE", 0)
        mk_cnt = self.owned.get("MARKET", 0)
        sm_cnt = self.owned.get("SMITHY", 0)
        wt_cnt = self.owned.get("WITCH", 0)
        lab_cnt = self.owned.get("LABORATORY", 0)
        gd_cnt = self.owned.get("GOLD", 0)
        rc_cnt = self.owned.get("HIRELING", 0)

        turn_no = self.turn

        chosen: Optional[str] = None

        # ===== 0) Province si possible (toujours)
        if can_buy("PROVINCE"):
            chosen = "PROVINCE"

        # ===== 1) EARLY GAME — installation Witch / HIRELING / Market / Gold
        elif turn_no <= 8:
            # 1a) Si aucune Witch et des Curses restent: Witch d'abord
            if curses_left > 0 and wt_cnt < 1 and can_buy("WITCH"):
                chosen = "WITCH"

            # 1b) À 6$ : HIRELING > GOLD (cap 2 en early)
            elif can_buy("HIRELING") and rc_cnt < 2:
                chosen = "HIRELING"

            # 1c) À 5$ : Market (cap 2)
            elif can_buy("MARKET") and mk_cnt < 2:
                chosen = "MARKET"

            # 1d) Gold (cap 1 en early)
            elif can_buy("GOLD") and gd_cnt < 1:
                chosen = "GOLD"

            # 1e) Silver par défaut
            elif can_buy("SILVER"):
                chosen = "SILVER"

        # ===== 2) MID GAME — spam Witch + moteur + deny Village
        if not chosen and curses_left > 0:
            # 2a) 2e/3e Witch (cap 3 si l’adversaire manque de Villages)
            cap_witch = 3 if (enemy_alive and villages_left <= 7) else 2
            if can_buy("WITCH") and wt_cnt < cap_witch:
                chosen = "WITCH"

            # 2b) Stabilité: Market puis Laboratory (cap 2 chacun)
            elif can_buy("MARKET") and mk_cnt < 2:
                chosen = "MARKET"
            elif can_buy("LABORATORY") and lab_cnt < 2:
                chosen = "LABORATORY"

            # 2c) HIRELING à 6$ (cap 3 global)
            elif can_buy("HIRELING") and rc_cnt < 3:
                chosen = "HIRELING"

            # 2d) Deny Village (cap 2 chez nous)
            elif enemy_alive and villages_left > 0 and vg_cnt < 2 and can_buy("VILLAGE"):
                chosen = "VILLAGE"

        # ===== 3) FIN DES CURSES — convertir l’avantage
        if not chosen and curses_left == 0 and enemy_alive and villages_left <= 2:
            if can_buy("PROVINCE"):
                chosen = "PROVINCE"
            elif AGGRO_DUCHY and can_buy("DUCHY"):
                chosen = "DUCHY"

        # ===== 4) PLAN STANDARD (fallback)
        if not chosen:
            # Province
            if can_buy("PROVINCE"):
                chosen = "PROVINCE"

            # Duchy si rattrapage / fin
            elif AGGRO_DUCHY and can_buy("DUCHY"):
                chosen = "DUCHY"

            # À 6$ : HIRELING (cap 3) > GOLD (cap 2)
            elif can_buy("HIRELING") and rc_cnt < 3:
                chosen = "HIRELING"
            elif can_buy("GOLD") and gd_cnt < 2:
                chosen = "GOLD"

            # À 5$ : Market (cap 2) > Laboratory (cap 2) > Witch (si Curses restent et < 2)
            elif can_buy("MARKET") and mk_cnt < 2:
                chosen = "MARKET"
            elif can_buy("LABORATORY") and lab_cnt < 2:
                chosen = "LABORATORY"
            elif curses_left > 0 and can_buy("WITCH") and wt_cnt < 2:
                chosen = "WITCH"

            # À 4$ : Smithy (cap 2) seulement si on a déjà +Actions
            elif can_buy("SMITHY") and (vg_cnt + mk_cnt) >= 1 and sm_cnt < 2:
                chosen = "SMITHY"

            # À 3$ : Silver
            elif can_buy("SILVER"):
                chosen = "SILVER"

            # Duchy opportuniste (si jamais rien d’autre)
            elif can_buy("DUCHY"):
                chosen = "DUCHY"

            # Estate tardif
            elif turn_no > 10 and can_buy("ESTATE"):
                chosen = "ESTATE"

        # Mise à jour de l'état interne
        if chosen:
            self.owned[chosen] = self.owned.get(chosen, 0) + 1
            self.turn += 1
            return chosen

        # Si vraiment rien
        self.turn += 1
        return None


class StratGinTeRuine:
    """
    Portage local de ta grosse strat (Witch/Militia/Bandit/Bureaucrat + modes BigMoney/Engine/Gardens)
    vers le moteur DominionGame :
      - play_action(self, p, g)
      - buy(self, p, g)

    Hypothèses :
      - Les cartes sont des strings ("COPPER", "PROVINCE", "WITCH", etc.)
      - Les coûts viennent de CARDS[card].cost
      - Les pièces disponibles sont dans p.coins au moment du buy()
      - On estime notre deck par deck+main+défausse+in_play
    """

    PROV_THRESHOLD = 4      # seuil de "fin de game" sur Provinces
    SCORE_DELTA    = 6      # écart de score pour considérer qu'on est derrière
    FULL_PILE      = 10     # taille standard des piles royaume (hors Provinces/Curses)

    # ==========================
    #   PHASE ACTION
    # ==========================
    def play_action(self, p, g):
        hand = p.hand
        stock = g.stock

        # helper simple
        def has(card: str) -> bool:
            return card in hand

        # Militia éventuellement présente dans le set de cartes
        militia_card = "MILITIA" if "MILITIA" in CARDS else None

        # nb de Curses restantes
        curses_left = stock.get("CURSE", 0) if "CURSE" in stock else 0

        # ordre des terminaux en fonction du contexte
        if militia_card:
            if curses_left > 0:
                terminal_order = ["WITCH", militia_card, "SMITHY"]
            else:
                terminal_order = [militia_card, "WITCH", "SMITHY"]
        else:
            terminal_order = ["WITCH", "SMITHY"]

        # priorité actions :
        action_priority = [
            # moteurs non-terminaux
            "MARKET",        # +1 carte, +1 action, +1 buy, +1$
            "LABORATORY",    # +2 cartes, +1 action
            "VILLAGE",       # +2 actions, +1 carte
            "FESTIVAL",      # +2 actions, +1 buy, +2$

            # terminaux contextuels
            *terminal_order,

            # cartes de nuisance / utilitaires
            "BUREAUCRAT" if "BUREAUCRAT" in CARDS else None,
            "BANDIT" if "BANDIT" in CARDS else None,
            "CHANCELLOR" if "CHANCELLOR" in CARDS else None,
            "WOODCUTTER",
        ]
        action_priority = [c for c in action_priority if c is not None]

        # on choisit la première action de la liste présente en main
        if p.actions > 0:
            for card in action_priority:
                if card in hand and CARDS[card].type in ("ACTION", "ATTACK"):
                    return card  # le moteur jouera l’effet automatiquement

        return None

    # ==========================
    #   PHASE ACHAT
    # ==========================
    def buy(self, p, g):
        stock = g.stock
        money = p.coins
        players = g.players

        # -------- helpers locaux --------
        def can_buy(card: str) -> bool:
            return (
                card in CARDS
                and stock.get(card, 0) > 0
                and CARDS[card].cost <= money
            )

        def owned(p_local: "Player", card: str) -> int:
            return (
                p_local.deck.count(card)
                + p_local.hand.count(card)
                + p_local.discard.count(card)
                + p_local.in_play.count(card)
            )

        def deck_size(p_local: "Player") -> int:
            return (
                len(p_local.deck)
                + len(p_local.hand)
                + len(p_local.discard)
                + len(p_local.in_play)
            )

        # -------- infos globales --------
        prov_left = stock.get("PROVINCE", 0)
        curses_left = stock.get("CURSE", 0) if "CURSE" in stock else 0
        gardens_left = stock.get("GARDENS", 0) if "GARDENS" in CARDS else 0

        my_score = getattr(p, "score", 0) or 0
        max_opp_score = 0
        for opp in players:
            if opp is p:
                continue
            sc = getattr(opp, "score", 0) or 0
            if sc > max_opp_score:
                max_opp_score = sc

        score_lead = my_score - max_opp_score
        is_ahead = score_lead > 0
        is_behind = score_lead < 0

        dsz = deck_size(p)
        enemy_equipe3 = any(
            "equipe3" in (getattr(op, "name", "") or "").lower()
            for op in players
            if op is not p
        )

        # Comptes de nos cartes clés
        vg_cnt  = owned(p, "VILLAGE")
        mk_cnt  = owned(p, "MARKET")
        sm_cnt  = owned(p, "SMITHY")
        wt_cnt  = owned(p, "WITCH")
        lab_cnt = owned(p, "LABORATORY")
        gd_cnt  = owned(p, "GOLD")
        pr_cnt  = owned(p, "PROVINCE")
        fest_cnt = owned(p, "FESTIVAL") if "FESTIVAL" in CARDS else 0
        hr_cnt  = owned(p, "HIRELING") if "HIRELING" in CARDS else 0
        bu_cnt  = owned(p, "BUREAUCRAT") if "BUREAUCRAT" in CARDS else 0
        bd_cnt  = owned(p, "BANDIT") if "BANDIT" in CARDS else 0
        mi_cnt  = owned(p, "MILITIA") if "MILITIA" in CARDS else 0
        ga_cnt  = owned(p, "GARDENS") if "GARDENS" in CARDS else 0

        # Piles restantes (pour détection de mode adverse)
        labs_left     = stock.get("LABORATORY", 0) if "LABORATORY" in stock else 0
        markets_left  = stock.get("MARKET", 0) if "MARKET" in stock else 0
        festival_left = stock.get("FESTIVAL", 0) if "FESTIVAL" in stock else 0
        villages_left = stock.get("VILLAGE", 0) if "VILLAGE" in stock else 0

        FULL = self.FULL_PILE

        # Modes adverses estimés
        BIGMONEY_MODE   = (
            villages_left >= 8
            and labs_left >= 9
            and markets_left >= 9
            and curses_left == FULL
        )
        ENGINE_MODE     = (
            (villages_left <= 7) or (labs_left <= 8) or (markets_left <= 8)
        )
        WITCH_SPAM_MODE = (curses_left <= FULL - 10)
        MILITIA_LOCK    = (festival_left <= 8 and curses_left == FULL)
        GARDENS_RACE    = (gardens_left > 0 and gardens_left < FULL)

        # -----------------------------------
        # STRATS DÉRIVÉES (S1..S7)
        # -----------------------------------

        # S1: Anti-BigMoney Rush
        if BIGMONEY_MODE:
            if can_buy("PROVINCE") and (prov_left <= 6 or my_score >= max_opp_score):
                return "PROVINCE"
            if gd_cnt < 2 and can_buy("GOLD"):
                return "GOLD"
            if "MILITIA" in CARDS and mi_cnt < 1 and can_buy("MILITIA"):
                return "MILITIA"
            if curses_left == FULL and wt_cnt < 2 and can_buy("WITCH"):
                return "WITCH"
            if can_buy("SILVER"):
                return "SILVER"

        # S2: Engine-Crush (deny Village + Bandit tôt)
        if ENGINE_MODE:
            if villages_left > 0 and vg_cnt < 2 and can_buy("VILLAGE"):
                return "VILLAGE"
            if "MILITIA" in CARDS:
                plus_actions = vg_cnt + fest_cnt + mk_cnt
                cap_mi = 2 if plus_actions >= 2 else 1
                if mi_cnt < cap_mi and can_buy("MILITIA"):
                    return "MILITIA"
            if bd_cnt < 1 and can_buy("BANDIT" if "BANDIT" in CARDS else "GOLD"):
                return "BANDIT" if "BANDIT" in CARDS else "GOLD"
            if mk_cnt < 2 and can_buy("MARKET"):
                return "MARKET"
            if lab_cnt < 3 and can_buy("LABORATORY"):
                return "LABORATORY"
            if gd_cnt < 2 and can_buy("GOLD"):
                return "GOLD"
            if can_buy("PROVINCE"):
                return "PROVINCE"

        # S3: Hybrid Witch->Gold (opening générique)
        if curses_left > 0 and wt_cnt < 2 and can_buy("WITCH"):
            return "WITCH"
        if mk_cnt < 2 and can_buy("MARKET"):
            return "MARKET"
        if lab_cnt < 2 and can_buy("LABORATORY"):
            return "LABORATORY"
        if gd_cnt < 2 and can_buy("GOLD"):
            return "GOLD"

        # S4: Anti Witch-Spam
        if WITCH_SPAM_MODE:
            if mk_cnt < 2 and can_buy("MARKET"):
                return "MARKET"
            if lab_cnt < 3 and can_buy("LABORATORY"):
                return "LABORATORY"
            if (
                "MILITIA" in CARDS
                and mi_cnt < 1
                and (vg_cnt + fest_cnt + mk_cnt) >= 1
                and can_buy("MILITIA")
            ):
                return "MILITIA"
            if gd_cnt < 2 and can_buy("GOLD"):
                return "GOLD"

        # S5: Anti Militia-Lock
        if MILITIA_LOCK:
            if "FESTIVAL" in CARDS and fest_cnt < 2 and can_buy("FESTIVAL"):
                return "FESTIVAL"
            if "MILITIA" in CARDS:
                cap_mi = 2 if (vg_cnt + fest_cnt + mk_cnt) >= 2 else 1
                if mi_cnt < cap_mi and can_buy("MILITIA"):
                    return "MILITIA"
            if mk_cnt < 2 and can_buy("MARKET"):
                return "MARKET"
            if lab_cnt < 2 and can_buy("LABORATORY"):
                return "LABORATORY"

        # friction quand on est derrière : une Militia / Bandit si possible
        plus_actions = vg_cnt + fest_cnt + mk_cnt
        if is_behind and "MILITIA" in CARDS and mi_cnt < 1 and plus_actions >= 1 and can_buy("MILITIA"):
            return "MILITIA"
        if is_behind and bd_cnt < 1 and can_buy("BANDIT" if "BANDIT" in CARDS else "GOLD"):
            return "BANDIT" if "BANDIT" in CARDS else "GOLD"

        # S6: Late-Gardens Counter (si derrière et deck large)
        if "GARDENS" in CARDS and gardens_left > 0:
            gardens_value = dsz // 10
            gardens_cap   = min(6, gardens_value)  # max 6
            if is_behind and dsz >= 30 and prov_left >= 5 and ga_cnt < gardens_cap:
                if not can_buy("PROVINCE"):
                    # Duchy = 3 VP ; Garden value >=3/4
                    if can_buy("DUCHY"):
                        if gardens_value >= 4 and can_buy("GARDENS"):
                            return "GARDENS"
                    else:
                        if gardens_value >= 3 and can_buy("GARDENS"):
                            return "GARDENS"

        # S7: Safe BM fallback & endgame
        if prov_left <= 4 and can_buy("PROVINCE"):
            return "PROVINCE"
        if gd_cnt < 2 and can_buy("GOLD"):
            return "GOLD"
        if can_buy("PROVINCE"):
            return "PROVINCE"
        if prov_left <= 4 and can_buy("DUCHY"):
            return "DUCHY"
        if can_buy("SILVER"):
            return "SILVER"

        # fallback extrême : rien à acheter
        return None



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
