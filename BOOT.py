import html
import threading
from pathlib import Path
from typing import Annotated
from contextlib import contextmanager

from fastapi import Depends, FastAPI, Header, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from dopynion.data_model import (
    CardName, CardNameAndHand, Game, Hand, MoneyCardsInHand, PossibleCards,
)

app = FastAPI()

# === ÉTAT GLOBAL PAR PARTIE ===
MASTER_LOCK = threading.RLock()                 # protège la création des locks/sessions
SESS: dict[str, dict] = {}                      # { game_id: { actions, buys, coins_bonus, coins_spent, owned{}, turn, draw_bonus } }
SESS_LOCKS: dict[str, threading.RLock] = {}     # un verrou par game_id

def _lock_for(game_id: str) -> threading.RLock:
    with MASTER_LOCK:
        lock = SESS_LOCKS.get(game_id)
        if lock is None:
            lock = threading.RLock()
            SESS_LOCKS[game_id] = lock
        return lock

@contextmanager
def with_game_lock(game_id: str):
    lock = _lock_for(game_id)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()

def _sess(game_id: str) -> dict:
    # doit être appelé sous with_game_lock OU sous MASTER_LOCK
    if game_id not in SESS:
        SESS[game_id] = {
            "actions": 1, "buys": 1, "coins_bonus": 0, "coins_spent": 0,
            "owned": {}, "turn": 0, "draw_bonus": 0
        }
    return SESS[game_id]

def init_turn_state(game_id: str) -> None:
    with with_game_lock(game_id):
        s = _sess(game_id)
        s["actions"] = 1
        s["buys"] = 1
        s["coins_bonus"] = 0
        s["coins_spent"] = 0
        # on NE reset PAS owned/turn/draw_bonus ici

def get_turn_state_readonly(game_id: str) -> dict:
    # lecture sans modifier
    with with_game_lock(game_id):
        return _sess(game_id).copy()

def inc_owned(game_id: str, card: CardName) -> None:
    with with_game_lock(game_id):
        s = _sess(game_id)
        o = s.setdefault("owned", {})
        o[card] = o.get(card, 0) + 1

def owned(game_id: str, card: CardName) -> int:
    with with_game_lock(game_id):
        s = _sess(game_id)
        return s.get("owned", {}).get(card, 0)


# --- COÛTS MINIMAUX UTILISÉS (cartes jouables aujourd'hui) ---
# Construction sûre : n'ajoute une entrée que si CardName expose l'attribut
COST = {}
def safe_add_cost(name: str, cost: int):
    if hasattr(CardName, name):
        COST[getattr(CardName, name)] = cost

# cartes de base connues
safe_add_cost("COPPER", 0)
safe_add_cost("SILVER", 3)
safe_add_cost("GOLD", 6)
safe_add_cost("ESTATE", 2)
safe_add_cost("DUCHY", 5)
safe_add_cost("PROVINCE", 8)
safe_add_cost("FESTIVAL", 5)
safe_add_cost("LABORATORY", 5)
safe_add_cost("VILLAGE", 3)
safe_add_cost("WOODCUTTER", 3)
safe_add_cost("SMITHY", 4)
safe_add_cost("MARKET", 5)
safe_add_cost("WITCH", 5)
safe_add_cost("HIRELING", 6)
safe_add_cost("BANDIT", 5)
safe_add_cost("BUREAUCRAT", 4)
safe_add_cost("CHANCELLOR", 3)
safe_add_cost("GARDENS", 4)
safe_add_cost("MILITIA", 4)

# nouvelles cartes éventuelles (ton listing)
safe_add_cost("FORTUNETELLER", 3)   # Fortuneteller (3c)
safe_add_cost("LIBRARY", 5)        # Library (5c)
safe_add_cost("WORKSHOP", 3)       # Workshop (3c)
safe_add_cost("MAGNET", 5)         # Magnet (5c)
safe_add_cost("CEILLOR", 2)        # Ceillor (2c) - probablement "Cellar" orthographié différemment
safe_add_cost("FEAST", 4)          # Feast (4c)
safe_add_cost("ADVENTURER", 6)     # Adventurer (6c)


# --- EFFETS D'ACTIONS (actions, buys, coins_bonus, draw) ---
# Valeur simple et conservative : des effets complexes/interactifs sont
# gérés via les endpoints de confirmation/discard qui existent déjà plus bas.
EFFECTS: dict[str, tuple[int, int, int, int]] = {}

def safe_add_effect(name: str, acts: int, buys: int, coins: int, draw: int):
    if hasattr(CardName, name):
        EFFECTS[name] = (acts, buys, coins, draw)

# Effets connus
safe_add_effect("FESTIVAL",   2, 1, 2, 0)
safe_add_effect("LABORATORY", 1, 0, 0, 2)
safe_add_effect("VILLAGE",    2, 0, 0, 1)
safe_add_effect("WOODCUTTER", 0, 1, 2, 0)
safe_add_effect("SMITHY",     0, 0, 0, 3)
safe_add_effect("MARKET",     1, 1, 1, 1)
safe_add_effect("WITCH",      0, 0, 0, 2)  # +2 cartes ; malédiction gérée par arbitre
safe_add_effect("HIRELING",   0, 0, 0, 0)  # effet récurrent géré côté moteur
safe_add_effect("BANDIT",     0, 0, 0, 0)  # attaque / gain géré par l'arbitre
safe_add_effect("BUREAUCRAT", 0, 0, 0, 0)  # attaque / gain géré par l'arbitre
safe_add_effect("CHANCELLOR", 0, 0, 2, 0)
safe_add_effect("MILITIA",    0, 0, 2, 0)

# Ajouts pour nouvelles cartes (simplifiés)
# Fortuneteller : te donne 1 cuivre immédiat (+1 piece) et est attaque (malédiction/disruption gérée par l'arbitre)
safe_add_effect("FORTUNETELLER", 0, 0, 1, 0)
# Library : terminal qui répare la main jusqu'à 7 ; interactions de garder action sont faites via endpoints
safe_add_effect("LIBRARY", 0, 0, 0, 0)
# Workshop : gain d'une carte <=4 dans la défausse (terminal)
safe_add_effect("WORKSHOP", 0, 0, 0, 0)
# Magnet : pioche une carte par trésor en main (variable -> géré moteur)
safe_add_effect("MAGNET", 0, 0, 0, 0)
# Ceillor (Cellar-like) : +1 action et permet défausse/piocher
safe_add_effect("CEILLOR", 1, 0, 0, 0)
# Feast : terminal, gain carte coût <=5, est défaussé/échoué (trashed) après usage
safe_add_effect("FEAST", 0, 0, 0, 0)
# Adventurer : draw until 2 treasures (terminal)
safe_add_effect("ADVENTURER", 0, 0, 0, 0)


#####################################################
# Data model for responses
#####################################################


class DopynionResponseBool(BaseModel):
    game_id: str
    decision: bool


class DopynionResponseCardName(BaseModel):
    game_id: str
    decision: CardName


class DopynionResponseStr(BaseModel):
    game_id: str
    decision: str


#####################################################
# Getter for the game identifier
#####################################################


def get_game_id(x_game_id: str = Header(description="ID of the game")) -> str:
    return x_game_id


GameIdDependency = Annotated[str, Depends(get_game_id)]


#####################################################
# Error management
#####################################################


@app.exception_handler(Exception)
def unknown_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    print(exc.__class__.__name__, str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "message": "Oops!",
            "detail": str(exc),
            "name": exc.__class__.__name__,
        },
    )


#####################################################
# The code of the strategy
#####################################################


@app.get("/name")
def name() -> str:
    return "Bully"

@app.get("/start_game")
def start_game(game_id: GameIdDependency) -> DopynionResponseStr:
    return DopynionResponseStr(game_id=game_id, decision="OK")


@app.get("/start_turn")
def start_turn(game_id: GameIdDependency) -> DopynionResponseStr:
    with with_game_lock(game_id):
        s = _sess(game_id)
        # reset état de tour
        s["actions"] = 1
        s["buys"] = 1
        s["coins_bonus"] = 0
        s["coins_spent"] = 0
        # compteur de tour
        s["turn"] = s.get("turn", 0) + 1
        # bonus de pioche persistant = nb de Hirelings possédés
        s["draw_bonus"] = s.get("owned", {}).get(getattr(CardName, "HIRELING", None), 0)
        print(f"[start_turn] game={game_id} turn={s['turn']} hirelings={s['draw_bonus']}")
    return DopynionResponseStr(game_id=game_id, decision="OK")



# --- Constants de stratégie (tweakables) ---
PROV_THRESHOLD = 4            # si <= ce nombre de provinces, switch agressif
SCORE_DELTA = 4               # si un adversaire te distance >= ce delta, switch agressif
ENGINE_PROVINCE_MONEY = 12    # argent cible dans un tour pour considérer qu'on peut faire Province(s)
DOUBLE_PROVINCE_BUYS = 2      # si on a >= buys pour tenter double achat

@app.post("/play")
def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    # Log gameplay object
    print(game)
    # --- trouver "moi" (player with visible hand) ---
    me = next((p for p in game.players if p.hand is not None), None)
    if not me or not me.hand:
        print(f"[play] game={game_id} no visible hand -> END_TURN")
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    hand = me.hand.quantities        # dict[CardName,int]
    stock = game.stock.quantities    # dict[CardName,int]
    # état thread-safe
    with with_game_lock(game_id):
        ts = _sess(game_id)
        print(f"[play] state entry: acts={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']} spent={ts['coins_spent']}")

    # helpers
    def hq(c: CardName) -> int: return hand.get(c, 0)
    def in_stock(c: CardName) -> bool: return stock.get(c, 0) > 0
    def money_treasures() -> int:
        return hq(getattr(CardName, "COPPER", None))*1 + hq(getattr(CardName, "SILVER", None))*2 + hq(getattr(CardName, "GOLD", None))*3
    def money_available() -> int:
        return money_treasures() + ts["coins_bonus"] - ts["coins_spent"]

    # basic info
    prov_left = stock.get(getattr(CardName, "PROVINCE", None), 0)
    my_score = getattr(me, "score", 0) or 0
    max_opponent_score = max((getattr(p, "score", 0) or 0) for p in game.players if p is not me) if game.players else 0

    # quick deck_estimate from visible hand (cheap heuristic)
    actions_in_hand = sum(1 for c in hand if c not in (getattr(CardName, "COPPER", None), getattr(CardName, "SILVER", None), getattr(CardName, "GOLD", None),
                                                       getattr(CardName, "ESTATE", None), getattr(CardName, "DUCHY", None), getattr(CardName, "PROVINCE", None), getattr(CardName, "CURSE", None)) and hand[c] > 0)
    treasure_value = money_treasures()

    print(f"[play] start | actions={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']} treasure={treasure_value} actions_in_hand={actions_in_hand} prov_left={prov_left} my_score={my_score} max_opp={max_opponent_score}")

    # =================
    # DERIVED FLAGS
    # =================
    with with_game_lock(game_id):
        deck_sz = 10 + sum(_sess(game_id).get("owned", {}).values())

    curses_left   = stock.get(getattr(CardName, "CURSE", None), 0) if getattr(CardName, "CURSE", None) in stock else stock.get(CardName.CURSE if hasattr(CardName, "CURSE") else None, 0)
    villages_left = stock.get(getattr(CardName, "VILLAGE", None), 0)
    gardens_left  = stock.get(getattr(CardName, "GARDENS", None), 0)

    score_lead   = my_score - max_opponent_score
    is_ahead     = score_lead > 0
    is_behind    = score_lead < 0

    AGGRO_CURSE = (is_behind or (not is_ahead)) and (curses_left > 0)
    AGGRO_GREEN = is_ahead and (prov_left <= 5 or score_lead >= 8)
    GARDENS_MODE = gardens_left and is_behind and deck_sz >= 20

    # --------------------
    # PHASE ACTION
    # --------------------
    # Terminal ordering: si il y a des malédictions -> attaque prioritaire
    militia_card = getattr(CardName, "MILITIA", None)
    # Build terminal order factoring new attack Fortuneteller and existing Witch
    terminal_order = []
    if hasattr(CardName, "FORTUNETELLER"):
        terminal_order.append(getattr(CardName, "FORTUNETELLER"))
    if hasattr(CardName, "WITCH"):
        terminal_order.append(getattr(CardName, "WITCH"))
    if militia_card:
        terminal_order.append(militia_card)
    # Smithy last among terminals
    terminal_order.append(getattr(CardName, "SMITHY", None))

    # action priority: +actions / draw => play engines first, then attacks/terminals, puis utilitaires
    action_priority = [
        getattr(CardName, "MARKET", None),
        getattr(CardName, "LABORATORY", None),
        getattr(CardName, "VILLAGE", None),
        getattr(CardName, "FESTIVAL", None),
        getattr(CardName, "LIBRARY", None),
        getattr(CardName, "CEILLOR", None),   # cellar-like, +actions and discard/redraw
        # then terminals & attacks
        *terminal_order,
        getattr(CardName, "MAGNET", None),
        getattr(CardName, "ADVENTURER", None),
        getattr(CardName, "WORKSHOP", None),
        getattr(CardName, "BUREAUCRAT", None),
        getattr(CardName, "BANDIT", None),
        getattr(CardName, "CHANCELLOR", None),
        getattr(CardName, "WOODCUTTER", None),
    ]
    # filter None and duplicates
    seen = set()
    action_priority = [c for c in action_priority if c is not None and (c not in seen and not seen.add(c))]

    # Execute first playable action according to priority
    for a in action_priority:
        if hq(a) > 0 and a.name in EFFECTS:
            acts, buys, coins, draw = EFFECTS[a.name]
            with with_game_lock(game_id):
                ts_local = _sess(game_id)
                if ts_local["actions"] <= 0:
                    break
                ts_local["actions"] -= 1
                ts_local["actions"] += acts
                ts_local["buys"]    += buys
                ts_local["coins_bonus"] += coins
            print(f"[play] ACTION {a.name} -> +acts={acts} +buys={buys} +$={coins} +draw={draw} | state actions={ts_local['actions']} buys={ts_local['buys']} bonus={ts_local['coins_bonus']}")
            return DopynionResponseStr(game_id=game_id, decision=f"ACTION {a.name}")

    # --------------------
    # MODE FLAGS POUR ACHAT
    # --------------------
    prov_left     = stock.get(getattr(CardName, "PROVINCE", None), 0)
    curses_left   = stock.get(getattr(CardName, "CURSE", None), 0) if hasattr(CardName, "CURSE") else 0
    villages_left = stock.get(getattr(CardName, "VILLAGE", None), 0)
    gardens_left  = stock.get(getattr(CardName, "GARDENS", None), 0)

    with with_game_lock(game_id):
        turn_no = _sess(game_id).get("turn", 1)

    # deck estimate
    deck_sz = 10 + sum((SESS.get(game_id, {}).get("owned") or {}).values())
    enemy_equipe3 = any("equipe3" in (getattr(p, "name", "") or "").lower() for p in game.players)

    # Mode detection heuristics (tweakables)
    FULL = 10
    labs_left      = stock.get(getattr(CardName, "LABORATORY", None), 0)
    markets_left   = stock.get(getattr(CardName, "MARKET", None), 0)
    festival_left  = stock.get(getattr(CardName, "FESTIVAL", None), 0)
    gold_left      = stock.get(getattr(CardName, "GOLD", None), 0)

    BIGMONEY_MODE   = (turn_no >= 4 and villages_left >= 8 and labs_left >= 9 and markets_left >= 9 and curses_left == FULL)
    ENGINE_MODE     = (turn_no <= 8 and (villages_left <= 7 or labs_left <= 8 or markets_left <= 8))
    WITCH_SPAM_MODE = (turn_no <= 8 and curses_left <= FULL - 10)
    MILITIA_LOCK    = (festival_left <= 8 and curses_left == FULL)
    GARDENS_RACE    = (gardens_left > 0 and gardens_left < FULL and turn_no <= 12)

    # --------------------
    # Helpers d'achat
    # --------------------
    def can_buy(c: CardName) -> bool:
        with with_game_lock(game_id):
            ts_local = _sess(game_id).copy()
        if not c:
            return False
        cost = COST.get(c, None)
        if cost is None:
            return False
        return (
            ts_local["buys"] > 0
            and stock.get(c, 0)
            and (hq(getattr(CardName, "COPPER", None))*1 + hq(getattr(CardName, "SILVER", None))*2 + hq(getattr(CardName, "GOLD", None))*3
                + ts_local["coins_bonus"] - ts_local["coins_spent"]) >= cost
        )

    def do_buy(c: CardName) -> DopynionResponseStr:
        cost = COST.get(c, 999)
        with with_game_lock(game_id):
            ts2 = _sess(game_id)
            total_money = hq(getattr(CardName, "COPPER", None))*1 + hq(getattr(CardName, "SILVER", None))*2 + hq(getattr(CardName, "GOLD", None))*3 + ts2["coins_bonus"]
            if ts2["buys"] <= 0 or ts2["coins_spent"] + cost > total_money:
                raise HTTPException(status_code=409, detail="Concurrent state changed: cannot buy now")
            ts2["buys"] -= 1
            ts2["coins_spent"] += cost
        inc_owned(game_id, c)
        print(f"[buy] BUY {c.name} cost={cost}")
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {c.name}")

    # --------------------
    # STRATEGY SWITCHBOARD (prioritise achats)
    # --------------------

    # S1: Anti-BigMoney Rush
    if BIGMONEY_MODE:
        if can_buy(getattr(CardName, "PROVINCE", None)) and (prov_left <= 6 or my_score >= max_opponent_score):
            return do_buy(getattr(CardName, "PROVINCE", None))
        if owned(game_id, getattr(CardName, "GOLD", getattr(CardName, "GOLD", None))) < 2 and can_buy(getattr(CardName, "GOLD", None)):
            return do_buy(getattr(CardName, "GOLD", None))
        if hasattr(CardName, "MILITIA") and owned(game_id, getattr(CardName, "MILITIA", None)) < 1 and can_buy(getattr(CardName, "MILITIA", None)):
            return do_buy(getattr(CardName, "MILITIA", None))
        if turn_no <= 6 and curses_left == FULL and owned(game_id, getattr(CardName, "WITCH", None)) < 2 and can_buy(getattr(CardName, "WITCH", None)):
            return do_buy(getattr(CardName, "WITCH", None))
        if can_buy(getattr(CardName, "SILVER", None)):
            return do_buy(getattr(CardName, "SILVER", None))

    # S2: Engine-Crush (deny Village early)
    if ENGINE_MODE:
        if villages_left > 0 and owned(game_id, getattr(CardName, "VILLAGE", None)) < 2 and can_buy(getattr(CardName, "VILLAGE", None)):
            return do_buy(getattr(CardName, "VILLAGE", None))
        # pick up Militia early if we have some +actions already
        plus_actions = owned(game_id, getattr(CardName, "VILLAGE", None)) + owned(game_id, getattr(CardName, "FESTIVAL", None)) + owned(game_id, getattr(CardName, "MARKET", None))
        cap_militia = 2 if plus_actions >= 2 else 1
        if hasattr(CardName, "MILITIA") and owned(game_id, getattr(CardName, "MILITIA", None)) < cap_militia and can_buy(getattr(CardName, "MILITIA", None)):
            return do_buy(getattr(CardName, "MILITIA", None))
        # if Bandit available and we lack it, pick it
        if hasattr(CardName, "BANDIT") and owned(game_id, getattr(CardName, "BANDIT", None)) < 1 and can_buy(getattr(CardName, "BANDIT", None)):
            return do_buy(getattr(CardName, "BANDIT", None))
        if owned(game_id, getattr(CardName, "MARKET", None)) < 2 and can_buy(getattr(CardName, "MARKET", None)):
            return do_buy(getattr(CardName, "MARKET", None))
        if owned(game_id, getattr(CardName, "LABORATORY", None)) < 3 and can_buy(getattr(CardName, "LABORATORY", None)):
            return do_buy(getattr(CardName, "LABORATORY", None))
        if owned(game_id, getattr(CardName, "GOLD", None)) < 2 and can_buy(getattr(CardName, "GOLD", None)):
            return do_buy(getattr(CardName, "GOLD", None))
        if can_buy(getattr(CardName, "PROVINCE", None)):
            return do_buy(getattr(CardName, "PROVINCE", None))

    # S3: Hybrid Witch->Gold opening (generic) and conversion rules
    # Aggressively pick WITCH early if we're behind to slow opponents,
    # otherwise prioritise building money (Gold) and engines.
    if is_behind and turn_no <= 6 and curses_left > 0 and owned(game_id, getattr(CardName, "WITCH", None)) < 2 and can_buy(getattr(CardName, "WITCH", None)):
        return do_buy(getattr(CardName, "WITCH", None))
    if owned(game_id, getattr(CardName, "MARKET", None)) < 2 and can_buy(getattr(CardName, "MARKET", None)):
        return do_buy(getattr(CardName, "MARKET", None))
    if owned(game_id, getattr(CardName, "LABORATORY", None)) < 2 and can_buy(getattr(CardName, "LABORATORY", None)):
        return do_buy(getattr(CardName, "LABORATORY", None))
    # conversion: if we have a lot of low-value coins in hand, convert upward
    if hq(getattr(CardName, "COPPER", None)) >= 3 and can_buy(getattr(CardName, "SILVER", None)):
        print(f"[play] conversion rule: {hq(getattr(CardName, 'COPPER', None))} coppers -> buy SILVER")
        return do_buy(getattr(CardName, "SILVER", None))
    if hq(getattr(CardName, "SILVER", None)) >= 3 and can_buy(getattr(CardName, "GOLD", None)):
        print(f"[play] conversion rule: {hq(getattr(CardName, 'SILVER', None))} silvers -> buy GOLD")
        return do_buy(getattr(CardName, "GOLD", None))

    # S4: Anti Witch-Spam / stabilise economy
    if WITCH_SPAM_MODE:
        if owned(game_id, getattr(CardName, "MARKET", None)) < 2 and can_buy(getattr(CardName, "MARKET", None)):
            return do_buy(getattr(CardName, "MARKET", None))
        if owned(game_id, getattr(CardName, "LABORATORY", None)) < 3 and can_buy(getattr(CardName, "LABORATORY", None)):
            return do_buy(getattr(CardName, "LABORATORY", None))
        if hasattr(CardName, "MILITIA") and owned(game_id, getattr(CardName, "MILITIA", None)) < 1 and (owned(game_id, getattr(CardName, "VILLAGE", None)) + owned(game_id, getattr(CardName, "FESTIVAL", None)) + owned(game_id, getattr(CardName, "MARKET", None))) >= 1 and can_buy(getattr(CardName, "MILITIA", None)):
            return do_buy(getattr(CardName, "MILITIA", None))
        if owned(game_id, getattr(CardName, "GOLD", None)) < 2 and can_buy(getattr(CardName, "GOLD", None)):
            return do_buy(getattr(CardName, "GOLD", None))

    # S5: Anti Militia-Lock
    if MILITIA_LOCK:
        if hasattr(CardName, "FESTIVAL") and owned(game_id, getattr(CardName, "FESTIVAL", None)) < 2 and can_buy(getattr(CardName, "FESTIVAL", None)):
            return do_buy(getattr(CardName, "FESTIVAL", None))
        if hasattr(CardName, "MILITIA"):
            cap_militia = 2 if (owned(game_id, getattr(CardName, "VILLAGE", None)) + owned(game_id, getattr(CardName, "FESTIVAL", None)) + owned(game_id, getattr(CardName, "MARKET", None))) >= 2 else 1
            if owned(game_id, getattr(CardName, "MILITIA", None)) < cap_militia and can_buy(getattr(CardName, "MILITIA", None)):
                return do_buy(getattr(CardName, "MILITIA", None))
        if owned(game_id, getattr(CardName, "MARKET", None)) < 2 and can_buy(getattr(CardName, "MARKET", None)):
            return do_buy(getattr(CardName, "MARKET", None))
        if owned(game_id, getattr(CardName, "LABORATORY", None)) < 2 and can_buy(getattr(CardName, "LABORATORY", None)):
            return do_buy(getattr(CardName, "LABORATORY", None))

    # If behind, consider militia/bandit to disrupt
    plus_actions = owned(game_id, getattr(CardName, "VILLAGE", None)) + owned(game_id, getattr(CardName, "FESTIVAL", None)) + owned(game_id, getattr(CardName, "MARKET", None))
    if is_behind and hasattr(CardName, "MILITIA") and owned(game_id, getattr(CardName, "MILITIA", None)) < 1 and plus_actions >= 1 and can_buy(getattr(CardName, "MILITIA", None)):
        return do_buy(getattr(CardName, "MILITIA", None))
    if is_behind and hasattr(CardName, "BANDIT") and owned(game_id, getattr(CardName, "BANDIT", None)) < 1 and can_buy(getattr(CardName, "BANDIT", None)):
        return do_buy(getattr(CardName, "BANDIT", None))

    # S6: Gardens counter (buy Gardens if behind and deck big enough)
    gardens_value = deck_sz // 10
    GARDENS_CAP   = min(6, gardens_value)
    if is_behind and gardens_left > 0 and deck_sz >= 30 and prov_left >= 5 and owned(game_id, getattr(CardName, "GARDENS", None)) < GARDENS_CAP:
        if not can_buy(getattr(CardName, "PROVINCE", None)):
            if can_buy(getattr(CardName, "DUCHY", None)):
                if gardens_value >= 4 and can_buy(getattr(CardName, "GARDENS", None)):
                    return do_buy(getattr(CardName, "GARDENS", None))
            else:
                if gardens_value >= 3 and can_buy(getattr(CardName, "GARDENS", None)):
                    return do_buy(getattr(CardName, "GARDENS", None))

    # S7: Late-game / safe fallback and endgame
    # Always try to take provinces when possible (highest priority)
    if can_buy(getattr(CardName, "PROVINCE", None)):
        return do_buy(getattr(CardName, "PROVINCE", None))
    # If we can't buy province right now, prefer Gold to ramp economy
    if owned(game_id, getattr(CardName, "GOLD", None)) < 4 and can_buy(getattr(CardName, "GOLD", None)):
        return do_buy(getattr(CardName, "GOLD", None))
    # If we are behind, pick up attacks to disrupt
    if is_behind and hasattr(CardName, "WITCH") and curses_left > 0 and owned(game_id, getattr(CardName, "WITCH", None)) < 3 and can_buy(getattr(CardName, "WITCH", None)):
        return do_buy(getattr(CardName, "WITCH", None))
    # conversion fallback: upgrade silvers to gold where possible
    if hq(getattr(CardName, "SILVER", None)) >= 3 and can_buy(getattr(CardName, "GOLD", None)):
        return do_buy(getattr(CardName, "GOLD", None))
    # otherwise take Duchy if near end or Province shortage
    if prov_left <= 4 and can_buy(getattr(CardName, "DUCHY", None)):
        return do_buy(getattr(CardName, "DUCHY", None))
    # take Silver as economic fallback
    if can_buy(getattr(CardName, "SILVER", None)):
        return do_buy(getattr(CardName, "SILVER", None))

    # pre-S7: opportunistic militia pick
    if hasattr(CardName, "MILITIA") and owned(game_id, getattr(CardName, "MILITIA", None)) < 1 and (owned(game_id, getattr(CardName, "WITCH", None)) + owned(game_id, getattr(CardName, "SMITHY", None)) + owned(game_id, getattr(CardName, "MILITIA", None))) < 2 and can_buy(getattr(CardName, "MILITIA", None)):
        return do_buy(getattr(CardName, "MILITIA", None))

    # final defensive fallback
    if can_buy(getattr(CardName, "SILVER", None)):
        return do_buy(getattr(CardName, "SILVER", None))

    print(f"[play] nothing to do -> END_TURN | state actions={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']} spent={ts['coins_spent']}")
    return DopynionResponseStr(game_id=game_id, decision="END_TURN")


@app.get("/end_game")
def end_game(game_id: GameIdDependency) -> DopynionResponseStr:
    with MASTER_LOCK:
        SESS.pop(game_id, None)
        SESS_LOCKS.pop(game_id, None)
    return DopynionResponseStr(game_id=game_id, decision="OK")




# -------------------------
# Endpoints d'interaction / confirmations (handlers génériques)
# -------------------------

@app.post("/confirm_discard_card_from_hand")
async def confirm_discard_card_from_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    # par défaut accepte toujours la demande (utile pour Library / autres)
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/discard_card_from_hand")
async def discard_card_from_hand(game_id: GameIdDependency, decision_input: Hand) -> DopynionResponseCardName:
    # ordre safe de défausse : CURSE > ESTATE > COPPER > SILVER > GOLD
    order = []
    if hasattr(CardName, "CURSE"): order.append(getattr(CardName, "CURSE"))
    if hasattr(CardName, "ESTATE"): order.append(getattr(CardName, "ESTATE"))
    if hasattr(CardName, "COPPER"): order.append(getattr(CardName, "COPPER"))
    if hasattr(CardName, "SILVER"): order.append(getattr(CardName, "SILVER"))
    if hasattr(CardName, "GOLD"): order.append(getattr(CardName, "GOLD"))
    for c in order:
        if c in decision_input.hand:
            print(f"[discard] choose {c.name}")
            return DopynionResponseCardName(game_id=game_id, decision=c)
    # sinon, renvoie la première carte
    print(f"[discard] default {decision_input.hand[0].name}")
    return DopynionResponseCardName(game_id=game_id, decision=decision_input.hand[0])


@app.post("/confirm_trash_card_from_hand")
async def confirm_trash_card_from_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    # accepte le trash (utile pour Feast qui demande trashing of itself)
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/trash_card_from_hand")
async def trash_card_from_hand(game_id: GameIdDependency, decision_input: Hand) -> DopynionResponseCardName:
    # Priorité trash : CURSE > COPPER > ESTATE
    for c in [getattr(CardName, "CURSE", None), getattr(CardName, "COPPER", None), getattr(CardName, "ESTATE", None)]:
        if c and c in decision_input.hand:
            print(f"[trash] choose {c.name}")
            return DopynionResponseCardName(game_id=game_id, decision=c)
    # default
    print(f"[trash] default {decision_input.hand[0].name}")
    return DopynionResponseCardName(game_id=game_id, decision=decision_input.hand[0])


@app.post("/confirm_discard_deck")
async def confirm_discard_deck(
    game_id: GameIdDependency,
) -> DopynionResponseBool:
    # Autorise par défaut
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/choose_card_to_receive_in_discard")
async def choose_card_to_receive_in_discard(
    game_id: GameIdDependency,
    decision_input: PossibleCards,
) -> DopynionResponseCardName:
    # Réception par défaut : prend la carte la plus chère disponible (ou première fournie)
    possible = decision_input.possible_cards
    # trier par coût connu décroissant
    def cost_of(c):
        return COST.get(c, 0)
    chosen = max(possible, key=cost_of)
    print(f"[choose_discard] choose {chosen.name} from {[c.name for c in possible]}")
    return DopynionResponseCardName(
        game_id=game_id,
        decision=chosen,
    )


@app.post("/choose_card_to_receive_in_deck")
async def choose_card_to_receive_in_deck(
    game_id: GameIdDependency,
    decision_input: PossibleCards,
) -> DopynionResponseCardName:
    # Idem : reçois la carte la plus forte
    possible = decision_input.possible_cards
    def cost_of(c):
        return COST.get(c, 0)
    chosen = max(possible, key=cost_of)
    print(f"[choose_deck] choose {chosen.name} from {[c.name for c in possible]}")
    return DopynionResponseCardName(
        game_id=game_id,
        decision=chosen,
    )


@app.post("/skip_card_reception_in_hand")
async def skip_card_reception_in_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    # Par défaut on skippe la réception si demandé
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/trash_money_card_for_better_money_card")
async def trash_money_card_for_better_money_card(
    game_id: GameIdDependency,
    decision_input: MoneyCardsInHand,
) -> DopynionResponseCardName:
    # trash la pire pièce (première présentée)
    print(f"[trash_money] available money in hand: {[c.name for c in decision_input.money_in_hand]}")
    return DopynionResponseCardName(
        game_id=game_id,
        decision=decision_input.money_in_hand[0],
    )
