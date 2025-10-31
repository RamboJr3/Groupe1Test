# =======================================================
# ARBITRE LOCAL DOPYNION – VERSION "REAL STRATS MULTIPARTIES"
# AVEC STRATS SYNCHROS "BULLY" (BOOT.py) ET "RHUM & RUIN" (BOOT_OFFI.py)
# =======================================================

import random
from dataclasses import dataclass, field
from typing import List, Dict

# -------------------------------------------------------
# CONSTANTES / CARTES
# -------------------------------------------------------

class CardName:
    COPPER="COPPER"; SILVER="SILVER"; GOLD="GOLD"
    ESTATE="ESTATE"; DUCHY="DUCHY"; PROVINCE="PROVINCE"
    VILLAGE="VILLAGE"; MARKET="MARKET"; SMITHY="SMITHY"
    WITCH="WITCH"; HIRELING="HIRELING"; LABORATORY="LABORATORY"
    FESTIVAL="FESTIVAL"; WOODCUTTER="WOODCUTTER"; CURSE="CURSE"
    MILITIA="MILITIA"; BANDIT="BANDIT"

COST = {
    CardName.COPPER:0, CardName.SILVER:3, CardName.GOLD:6,
    CardName.ESTATE:2, CardName.DUCHY:5, CardName.PROVINCE:8,
    CardName.VILLAGE:3, CardName.MARKET:5, CardName.SMITHY:4,
    CardName.WITCH:5, CardName.HIRELING:6, CardName.LABORATORY:5,
    CardName.FESTIVAL:5, CardName.WOODCUTTER:3, CardName.CURSE:0, CardName.MILITIA: 4,
CardName.BANDIT: 5
}

# -------------------------------------------------------
# STRUCTURES
# -------------------------------------------------------

@dataclass
class PlayerState:
    name:str
    strat:any
    deck:List[str]=field(default_factory=lambda:[CardName.COPPER]*7+[CardName.ESTATE]*3)
    discard:List[str]=field(default_factory=list)
    hand:List[str]=field(default_factory=list)
    score:int=3
    coins:int=0
    turn:int=0

@dataclass
class GameState:
    stock:Dict[str,int]
    players:List[PlayerState]
    turn:int=0

# -------------------------------------------------------
# ÉTAT GLOBAL / HELPERS
# -------------------------------------------------------

SESS:Dict[str,dict]={}
def get_turn_state(gid:str):
    return SESS.setdefault(gid,{"actions":1,"buys":1,"coins_bonus":0,"coins_spent":0,"owned":{},"turn":0})

def inc_owned(gid:str,card:str):
    get_turn_state(gid)["owned"][card]=get_turn_state(gid)["owned"].get(card,0)+1

def owned(gid:str,card:str)->int:
    return get_turn_state(gid)["owned"].get(card,0)

# -------------------------------------------------------
# STRATÉGIE RHUM & RUIN (FROM BOOT_OFFI.py)
# -------------------------------------------------------

class strat_rhum_ruin:
    @staticmethod
    def play(player, game, gid):
        me = player
        hand = {c: player.hand.count(c) for c in player.hand}
        stock = dict(game.stock)
        ts = get_turn_state(gid)

        def hq(c): return hand.get(c, 0)
        def money_treasures(): return hq(CardName.COPPER)*1 + hq(CardName.SILVER)*2 + hq(CardName.GOLD)*3
        def money_available(): return money_treasures() + ts["coins_bonus"] - ts["coins_spent"]

        prov_left = stock.get(CardName.PROVINCE, 0)
        myscore = getattr(me, "score", 0) or 0
        maxopponentscore = max((p.score for p in game.players if p is not me), default=0)
        turnno = ts.get("turn", 1)
        rccnt = owned(gid, CardName.HIRELING)
        mkcnt = owned(gid, CardName.MARKET)
        smcnt = owned(gid, CardName.SMITHY)
        wtcnt = owned(gid, CardName.WITCH)
        labcnt = owned(gid, CardName.LABORATORY)
        gdcnt = owned(gid, CardName.GOLD)
        vgcnt = owned(gid, CardName.VILLAGE)
        AGGRODUCHY = prov_left <= 4 or (maxopponentscore-myscore >= 4)
        ENGINE_MONEY = 12

        def can_buy(c): return ts["buys"] > 0 and stock.get(c,0) > 0 and money_available() >= COST[c]
        def do_buy(c):
            ts["buys"] -= 1; ts["coins_spent"] += COST[c]; inc_owned(gid, c)
            return c

        # 0. Province si possible tjs
        if can_buy(CardName.PROVINCE): return do_buy(CardName.PROVINCE)
        # 1. Si aucune Witch & Curses restent => Witch d'abord
        cursesleft = stock.get(CardName.CURSE, 0)
        villagesleft = stock.get(CardName.VILLAGE, 0)
        if cursesleft > 0 and wtcnt < (3 if villagesleft > 7 else 2) and can_buy(CardName.WITCH): return do_buy(CardName.WITCH)
        # 2. Hireling cap 3
        if can_buy(CardName.HIRELING) and rccnt < 3: return do_buy(CardName.HIRELING)
        # 3. Market cap 2, Gold cap 2, Laboratory cap 2
        if can_buy(CardName.MARKET) and mkcnt < 2: return do_buy(CardName.MARKET)
        if can_buy(CardName.LABORATORY) and labcnt < 2: return do_buy(CardName.LABORATORY)
        if can_buy(CardName.GOLD) and gdcnt < 2: return do_buy(CardName.GOLD)
        # 4. Village deny si enemy
        if can_buy(CardName.VILLAGE) and villagesleft <= 2 and vgcnt < 2: return do_buy(CardName.VILLAGE)
        # Duchy en tempo
        if AGGRODUCHY and can_buy(CardName.DUCHY): return do_buy(CardName.DUCHY)
        # Silver par défaut
        if can_buy(CardName.SILVER): return do_buy(CardName.SILVER)
        # Si rien faire
        return None

# -------------------------------------------------------
# STRATÉGIE BULLY (FROM BOOT.py)
# -------------------------------------------------------

class strat_bully:
    @staticmethod
    def play(player, game, gid):
        me = player
        hand = {c: player.hand.count(c) for c in player.hand}
        stock = dict(game.stock)
        ts = get_turn_state(gid)
        def hq(c): return hand.get(c, 0)
        def money_treasures(): return hq(CardName.COPPER)*1 + hq(CardName.SILVER)*2 + hq(CardName.GOLD)*3
        def money_available(): return money_treasures() + ts["coins_bonus"] - ts["coins_spent"]
        prov_left = stock.get(CardName.PROVINCE, 0)
        treasure = money_available()
        def can_buy(c): return ts["buys"] > 0 and stock.get(c,0) > 0 and money_available() >= COST[c]
        def do_buy(c):
            ts["buys"] -= 1; ts["coins_spent"] += COST[c]; inc_owned(gid, c)
            return c
        # Province dès que possible
        if can_buy(CardName.PROVINCE): return do_buy(CardName.PROVINCE)
        # Duchy très tôt dès 5 pièces
        if treasure >= 5 and prov_left <= 6 and can_buy(CardName.DUCHY): return do_buy(CardName.DUCHY)
        # Duchy dès 5 pièces tout le temps après avoir 2 Provinces
        if treasure >= 5 and owned(gid,CardName.PROVINCE) >= 2 and can_buy(CardName.DUCHY): return do_buy(CardName.DUCHY)
        # Gold
        if can_buy(CardName.GOLD): return do_buy(CardName.GOLD)
        # Silver jusqu’à 5
        if owned(gid,CardName.SILVER) < 5 and can_buy(CardName.SILVER): return do_buy(CardName.SILVER)
        # Estate si rien d’autre (pour rush points)
        if treasure >= 2 and prov_left < 4 and can_buy(CardName.ESTATE): return do_buy(CardName.ESTATE)
        return None


# -------------------------------------------------------
# ARBITRE LOCAL MULTIPARTIES
# -------------------------------------------------------

class Arbiter:
    def __init__(self, strat1, strat2):
        self.strat1, self.strat2 = strat1, strat2

    def setup_game(self):
        stock = {c: 10 for c in COST}; stock[CardName.PROVINCE] = 8
        players = [PlayerState("Rhum & Ruin", self.strat1),
                   PlayerState("Bully", self.strat2)]
        for p in players:
            random.shuffle(p.deck)
            p.hand.clear()
            for _ in range(5):
                p.hand.append(p.deck.pop())
        return GameState(stock, players)

    def end_turn(self, pl):
        pl.discard.extend(pl.hand)
        pl.hand.clear()

    def draw_cards(self, pl, n=5):
        while n > 0:
            if not pl.deck:
                pl.deck = pl.discard; pl.discard = []; random.shuffle(pl.deck)
            if not pl.deck: break
            pl.hand.append(pl.deck.pop()); n -= 1

    def count_money(self, pl):
        return pl.hand.count(CardName.COPPER)+pl.hand.count(CardName.SILVER)*2+pl.hand.count(CardName.GOLD)*3

    def play_turn(self, pl, game):
        pl.turn += 1
        gid = pl.name
        SESS[gid] = {
            "actions": 1,
            "buys": 1,
            "coins_bonus": 0,
            "coins_spent": 0,
            "owned": SESS.get(gid, {}).get("owned", {}),
            "turn": pl.turn,
        }

        if not pl.hand:
            self.draw_cards(pl, 5)
        pl.coins = self.count_money(pl)

        print(f"[{pl.name}] Tour {pl.turn} | Hand: {pl.hand} | Coins: {pl.coins} | Score: {pl.score}")

        decision = None  # 👈 Ajoute cette ligne
        try:
            decision = pl.strat.play(pl, game, gid)
        except Exception as e:
            print(f"[{pl.name}] ⚠️ Erreur dans play(): {e}")

        print(f"[{pl.name}] -> Décision: {decision}")

        if decision in COST and game.stock.get(decision, 0) > 0:
            if pl.coins >= COST[decision]:
                pl.coins -= COST[decision]
                pl.discard.append(decision)
                game.stock[decision] -= 1
                if decision == CardName.PROVINCE:
                    pl.score += 6
                elif decision == CardName.DUCHY:
                    pl.score += 3
                elif decision == CardName.ESTATE:
                    pl.score += 1
                print(f"[{pl.name}] a acheté {decision} | Nouveau score: {pl.score}")
            else:
                print(f"[{pl.name}] ne peut pas se payer {decision}")
        else:
            print(f"[{pl.name}] ne fait rien ce tour.")

        self.end_turn(pl)


    def simulate_one(self,max_turns=30):
        game = self.setup_game()
        SESS.clear()
        for t in range(max_turns):
            game.turn = t + 1
            print(f"\n--- TOUR {t+1} ---")
            for pl in game.players:
                self.play_turn(pl, game)
            if game.stock[CardName.PROVINCE] <= 0:
                return game
        return game

    def simulate_batch(self, n=100):
        results = []
        for _ in range(n):
            g = self.simulate_one()
            winner = max(g.players, key=lambda p: p.score)
            results.append(winner.name)
        return results

if __name__ == "__main__":
    N = 10000
    arb = Arbiter(strat_rhum_ruin, strat_bully)
    results = arb.simulate_batch(N)
    win_r = results.count("Rhum & Ruin"); win_b = results.count("Bully")
    print(f"\nRésultats sur {N} parties :")
    print(f"Rhum & Ruin gagne {win_r} ({win_r/N*100:.1f}%)")
    print(f"Bully gagne {win_b} ({win_b/N*100:.1f}%)")
