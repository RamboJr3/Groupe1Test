import html
import threading
from pathlib import Path
from typing import Annotated, Dict, Tuple, List, Optional
from contextlib import contextmanager

from fastapi import Depends, FastAPI, Header, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from dopynion.data_model import (
    CardName, CardNameAndHand, Game, Hand, MoneyCardsInHand, PossibleCards,
)

app = FastAPI()

# =============================================================================
# ÉTAT GLOBAL PAR PARTIE (thread-safe)
# =============================================================================
MASTER_LOCK = threading.RLock()
SESS: Dict[str, dict] = {}           # { game_id: { actions, buys, coins_bonus, coins_spent, owned{}, turn, draw_bonus } }
SESS_LOCKS: Dict[str, threading.RLock] = {}

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

def get_turn_state_readonly(game_id: str) -> dict:
    with with_game_lock(game_id):
        return _sess(game_id).copy()

def inc_owned(game_id: str, card: CardName) -> None:
    with with_game_lock(game_id):
        s = _sess(game_id)
        o = s.setdefault("owned", {})
        o[card] = o.get(card, 0) + 1

def owned(game_id: str, card: Optional[CardName]) -> int:
    if card is None:
        return 0
    with with_game_lock(game_id):
        s = _sess(game_id)
        return s.get("owned", {}).get(card, 0)

# =============================================================================
# COÛTS & EFFETS (conservateurs, extensibles)
# =============================================================================
COST: Dict[CardName, int] = {}
def safe_add_cost(name: str, cost: int):
    if hasattr(CardName, name):
        COST[getattr(CardName, name)] = cost

# Trésors / Victoire / Base
safe_add_cost("COPPER", 0)
safe_add_cost("SILVER", 3)
safe_add_cost("GOLD", 6)
safe_add_cost("ESTATE", 2)
safe_add_cost("DUCHY", 5)
safe_add_cost("PROVINCE", 8)
safe_add_cost("CURSE", 0)

# Actions classiques
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

# Nouvelles cartes de ton set
safe_add_cost("FORTUNETELLER", 3)     # +$1 ; attaque : adversaires révèlent jusqu'à Curse/Victory (moteur)
safe_add_cost("LIBRARY", 5)           # pioche jusqu'à 7 ; skip actions (confirm handlers)
safe_add_cost("WORKSHOP", 3)          # gagne une carte coût ≤4 (moteur)
safe_add_cost("MAGNET", 5)            # pioche 1 par trésor en main
safe_add_cost("CEILLOR", 2)           # = Cellar-like
safe_add_cost("FEAST", 4)             # trash self → gagne ≤5
safe_add_cost("ADVENTURER", 6)        # pioche jusqu’à 2 trésors
safe_add_cost("COUNCILROOM", 5)       # +4 cartes, +1 buy, opp +1 carte
safe_add_cost("DISTANTSHORE", 6)      # +2 cartes, +1 action, (gain/jeu → Estate clog)
safe_add_cost("FARMINGVILLAGE", 4)    # +2 actions, pioche jusqu'à Action/Trésor

# Effets simplifiés -> (acts, buys, coins_bonus, draw)
EFFECTS: Dict[str, Tuple[int, int, int, int]] = {}
def safe_add_effect(name: str, acts: int, buys: int, coins: int, draw: int):
    if hasattr(CardName, name):
        EFFECTS[name] = (acts, buys, coins, draw)

# Connus
safe_add_effect("FESTIVAL",   2, 1, 2, 0)
safe_add_effect("LABORATORY", 1, 0, 0, 2)
safe_add_effect("VILLAGE",    2, 0, 0, 1)
safe_add_effect("WOODCUTTER", 0, 1, 2, 0)
safe_add_effect("SMITHY",     0, 0, 0, 3)
safe_add_effect("MARKET",     1, 1, 1, 1)
safe_add_effect("WITCH",      0, 0, 0, 2)
safe_add_effect("HIRELING",   0, 0, 0, 0)
safe_add_effect("BANDIT",     0, 0, 0, 0)
safe_add_effect("BUREAUCRAT", 0, 0, 0, 0)
safe_add_effect("CHANCELLOR", 0, 0, 2, 0)
safe_add_effect("MILITIA",    0, 0, 2, 0)

# Nouveaux
safe_add_effect("FORTUNETELLER", 0, 0, 1, 0)
safe_add_effect("LIBRARY",       0, 0, 0, 0)  # pilotée via endpoints de discard/confirm
safe_add_effect("WORKSHOP",      0, 0, 0, 0)
safe_add_effect("MAGNET",        0, 0, 0, 0)  # effet variable (géré moteur), ici neutre
safe_add_effect("CEILLOR",       1, 0, 0, 0)
safe_add_effect("FEAST",         0, 0, 0, 0)
safe_add_effect("ADVENTURER",    0, 0, 0, 0)
safe_add_effect("COUNCILROOM",   0, 1, 0, 4)  # terminal draw + buy
safe_add_effect("DISTANTSHORE",  1, 0, 0, 2)  # on ignore le gain automatique d'Estate (moteur)
safe_add_effect("FARMINGVILLAGE",2, 0, 0, 1)  # approx : +2A et pioche 1 (révélation simulée)

# =============================================================================
# Data models
# =============================================================================
class DopynionResponseBool(BaseModel):
    game_id: str
    decision: bool

class DopynionResponseCardName(BaseModel):
    game_id: str
    decision: CardName

class DopynionResponseStr(BaseModel):
    game_id: str
    decision: str

# =============================================================================
# Helpers FastAPI
# =============================================================================
def get_game_id(x_game_id: str = Header(description="ID of the game")) -> str:
    return x_game_id

GameIdDependency = Annotated[str, Depends(get_game_id)]

# =============================================================================
# Error handling
# =============================================================================
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

# =============================================================================
# Strategy metadata
# =============================================================================
@app.get("/name")
def name() -> str:
    return "Bully"

@app.get("/start_game")
def start_game(game_id: GameIdDependency) -> DopynionResponseStr:
    with MASTER_LOCK:
        SESS.pop(game_id, None)
        SESS_LOCKS.pop(game_id, None)
    return DopynionResponseStr(game_id=game_id, decision="OK")

@app.get("/start_turn")
def start_turn(game_id: GameIdDependency) -> DopynionResponseStr:
    with with_game_lock(game_id):
        s = _sess(game_id)
        s["actions"] = 1
        s["buys"] = 1
        s["coins_bonus"] = 0
        s["coins_spent"] = 0
        s["turn"] = s.get("turn", 0) + 1
        s["draw_bonus"] = s.get("owned", {}).get(getattr(CardName, "HIRELING", None), 0)
        print(f"[start_turn] game={game_id} turn={s['turn']} hirelings={s['draw_bonus']}")
    return DopynionResponseStr(game_id=game_id, decision="OK")

# =============================================================================
# Constantes de stratégie & utilitaires
# =============================================================================
PROV_THRESHOLD         = 5      # on verdit plus tôt
DUCHY_PIVOT            = 5
ESTATE_PIVOT           = 3
SCORE_DELTA_ENDING     = 5
EARLY_WITCH_TURNS      = 8
EARLY_ATTACK_CAP       = 3       # Witch jusqu'à 3 si Curses encore là
MAX_GOLD_BEFORE_GREEN  = 3
GARDENS_MIN_DECK       = 24
GARDENS_MAX_BUYS       = 8
MIN_ENGINE_PLUS_ACTION = 2
FULL_PILE_SIZE         = 10

COUNCIL_CAP            = 2       # limite Council Room
DSHORE_CAP             = 1       # limite Distant Shore (éviter self-clog)
MAGNET_THRESH_TREAS    = 6       # seuil trésors pour Magnet
LIBRARY_TOGGLES_ACTION = True

def _in(h: Dict[CardName, int], c: Optional[CardName]) -> int:
    if c is None:
        return 0
    return h.get(c, 0)

def _money_in_hand(hand: Dict[CardName, int]) -> int:
    c = getattr(CardName, "COPPER", None)
    s = getattr(CardName, "SILVER", None)
    g = getattr(CardName, "GOLD", None)
    return _in(hand, c)*1 + _in(hand, s)*2 + _in(hand, g)*3

def _stock_qty(stock: Dict[CardName, int], name: str) -> int:
    c = getattr(CardName, name, None)
    return 0 if c is None else stock.get(c, 0)

def _first_defined(*names: str) -> Optional[CardName]:
    for n in names:
        if hasattr(CardName, n):
            return getattr(CardName, n)
    return None

# --- Helpers adaptatifs (vitesse d’épuisement de piles / présence) ---
def any_in_supply(*names: str) -> bool:
    return any(hasattr(CardName, n) for n in names)

def pile_depleted_fast(stock: Dict[CardName, int], name: str, threshold_taken: int) -> bool:
    q = _stock_qty(stock, name)
    if q == 0:
        return True
    return (FULL_PILE_SIZE - q) >= threshold_taken

def count_taken(stock: Dict[CardName, int], name: str) -> int:
    q = _stock_qty(stock, name)
    return (FULL_PILE_SIZE - q) if 0 <= q <= FULL_PILE_SIZE else 0

# =============================================================================
# COEUR : /play
# =============================================================================
@app.post("/play")
def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    print(game)

    # Trouver "moi"
    me = next((p for p in game.players if p.hand is not None), None)
    if not me or not me.hand:
        print(f"[play] game={game_id} no visible hand -> END_TURN")
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    hand = me.hand.quantities
    stock = game.stock.quantities

    with with_game_lock(game_id):
        ts = _sess(game_id).copy()

    prov_left   = _stock_qty(stock, "PROVINCE")
    duchy_left  = _stock_qty(stock, "DUCHY")
    estate_left = _stock_qty(stock, "ESTATE")
    curses_left = _stock_qty(stock, "CURSE")

    my_score = getattr(me, "score", 0) or 0
    opp_max  = max((getattr(p, "score", 0) or 0) for p in game.players if p is not me) if game.players else 0

    with with_game_lock(game_id):
        deck_sz = 10 + sum(_sess(game_id).get("owned", {}).values())

    score_lead = my_score - opp_max
    is_ahead   = score_lead > 0
    is_behind  = score_lead < 0

    # ==== PHASE ACTIONS ====
    anti_draw_combos = any_in_supply("LIBRARY", "COUNCILROOM")
    engine_actions: List[str] = ["MARKET","LABORATORY","VILLAGE","FESTIVAL","FARMINGVILLAGE","CEILLOR","DISTANTSHORE"]
    attacks: List[str] = (["MILITIA","FORTUNETELLER","WITCH","BUREAUCRAT","BANDIT"] if anti_draw_combos
                          else ["FORTUNETELLER","WITCH","MILITIA","BUREAUCRAT","BANDIT"])
    utilities: List[str] = ["COUNCILROOM","SMITHY","LIBRARY","MAGNET","ADVENTURER","WORKSHOP","WOODCUTTER","CHANCELLOR"]

    action_priority: List[CardName] = []
    _seen: set = set()
    for name in [*engine_actions, *attacks, *utilities]:
        c = getattr(CardName, name, None)
        if c and c not in _seen and name in EFFECTS:
            _seen.add(c)
            action_priority.append(c)

    for a in action_priority:
        if _in(hand, a) > 0:
            acts, buys, coins, draw = EFFECTS[a.name]
            with with_game_lock(game_id):
                ts2 = _sess(game_id)
                if ts2["actions"] <= 0:
                    break
                ts2["actions"] -= 1
                ts2["actions"] += acts
                ts2["buys"]    += buys
                ts2["coins_bonus"] += coins
            print(f"[play] ACTION {a.name} -> +acts={acts} +buys={buys} +$={coins} +draw={draw}")
            return DopynionResponseStr(game_id=game_id, decision=f"ACTION {a.name}")

    # ==== PHASE ACHATS ====
    def money_available() -> int:
        with with_game_lock(game_id):
            ts_local = _sess(game_id)
            return _money_in_hand(hand) + ts_local["coins_bonus"] - ts_local["coins_spent"]

    def buys_left() -> int:
        with with_game_lock(game_id):
            return _sess(game_id)["buys"]

    def can_buy(c: Optional[CardName]) -> bool:
        if c is None:
            return False
        with with_game_lock(game_id):
            _ = _sess(game_id)  # lecture
        cost = COST.get(c, None)
        if cost is None:
            return False
        return buys_left() > 0 and stock.get(c, 0) > 0 and (money_available() >= cost)

    def do_buy(c: CardName) -> DopynionResponseStr:
        cost = COST.get(c, 999)
        with with_game_lock(game_id):
            ts2 = _sess(game_id)
            total_money = _money_in_hand(hand) + ts2["coins_bonus"]
            if ts2["buys"] <= 0 or ts2["coins_spent"] + cost > total_money:
                raise HTTPException(status_code=409, detail="Concurrent state changed: cannot buy now")
            ts2["buys"] -= 1
            ts2["coins_spent"] += cost
        inc_owned(game_id, c)
        print(f"[buy] BUY {c.name} cost={cost} | money_avail={total_money} buys_left={buys_left()}")
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {c.name}")

    turn_no = get_turn_state_readonly(game_id).get("turn", 1)

    villages_left  = _stock_qty(stock, "VILLAGE")
    labs_left      = _stock_qty(stock, "LABORATORY")
    markets_left   = _stock_qty(stock, "MARKET")
    festivals_left = _stock_qty(stock, "FESTIVAL")
    farming_left   = _stock_qty(stock, "FARMINGVILLAGE")
    gardens_left   = _stock_qty(stock, "GARDENS")

    ENGINE_DENY    = (
        pile_depleted_fast(stock,"VILLAGE",3) or
        pile_depleted_fast(stock,"LABORATORY",3) or
        pile_depleted_fast(stock,"MARKET",3) or
        pile_depleted_fast(stock,"FARMINGVILLAGE",3)
    )
    GARDENS_RACE   = gardens_left>0 and (pile_depleted_fast(stock,"GARDENS",2) or deck_sz>=GARDENS_MIN_DECK)
    CURSES_EXIST   = curses_left>0
    DRAW_COMBOS    = any_in_supply("LIBRARY","COUNCILROOM")
    DSHORE_IN_SUP  = any_in_supply("DISTANTSHORE")

    BIGMONEY_LIKELY  = (labs_left>=8 and markets_left>=8 and villages_left>=8 and curses_left==FULL_PILE_SIZE)

    # ==== Double Province si possible
    if buys_left() >= 2 and money_available() >= 16 and prov_left >= 2:
        return do_buy(getattr(CardName, "PROVINCE"))

    # ==== Fin agressive + 3-piles si on mène
    if is_ahead and (prov_left <= PROV_THRESHOLD or score_lead >= SCORE_DELTA_ENDING):
        if can_buy(getattr(CardName, "PROVINCE", None)):
            return do_buy(getattr(CardName, "PROVINCE"))
        if prov_left <= DUCHY_PIVOT and can_buy(getattr(CardName, "DUCHY", None)):
            return do_buy(getattr(CardName, "DUCHY"))
        if prov_left <= ESTATE_PIVOT and can_buy(getattr(CardName, "ESTATE", None)):
            return do_buy(getattr(CardName, "ESTATE"))
        for target in [_first_defined("ESTATE"), _first_defined("WORKSHOP"), _first_defined("VILLAGE"),
                       _first_defined("WOODCUTTER"), _first_defined("SMITHY"), _first_defined("CEILLOR")]:
            if target and can_buy(target):
                return do_buy(target)

    # ==== Attaques tant qu'il reste des Curses
    if CURSES_EXIST and owned(game_id, getattr(CardName, "WITCH", None)) < EARLY_ATTACK_CAP and can_buy(getattr(CardName, "WITCH", None)):
        return do_buy(getattr(CardName, "WITCH", None))
    if CURSES_EXIST and owned(game_id, getattr(CardName, "FORTUNETELLER", None)) < 3 and can_buy(getattr(CardName, "FORTUNETELLER", None)):
        return do_buy(getattr(CardName, "FORTUNETELLER", None))

    # ==== Anti Library/CouncilRoom : Militia > Bandit
    if DRAW_COMBOS:
        if owned(game_id, getattr(CardName, "MILITIA", None)) < 2 and can_buy(getattr(CardName, "MILITIA", None)):
            return do_buy(getattr(CardName, "MILITIA", None))
        if owned(game_id, getattr(CardName, "BANDIT", None)) < 1 and can_buy(getattr(CardName, "BANDIT", None)):
            return do_buy(getattr(CardName, "BANDIT", None))

    # ==== Engine deny : on assèche +A/+pioche
    if ENGINE_DENY:
        if villages_left>0 and owned(game_id, getattr(CardName, "VILLAGE", None)) < 3 and can_buy(getattr(CardName, "VILLAGE", None)):
            return do_buy(getattr(CardName, "VILLAGE", None))
        if owned(game_id, getattr(CardName, "MARKET", None)) < 3 and can_buy(getattr(CardName, "MARKET", None)):
            return do_buy(getattr(CardName, "MARKET", None))
        if owned(game_id, getattr(CardName, "LABORATORY", None)) < 3 and can_buy(getattr(CardName, "LABORATORY", None)):
            return do_buy(getattr(CardName, "LABORATORY", None))
        if owned(game_id, getattr(CardName, "FARMINGVILLAGE", None)) < 2 and can_buy(getattr(CardName, "FARMINGVILLAGE", None)):
            return do_buy(getattr(CardName, "FARMINGVILLAGE", None))

    # ==== Big Money probable
    if BIGMONEY_LIKELY:
        if can_buy(getattr(CardName, "PROVINCE", None)) and (prov_left <= 6 or my_score >= opp_max):
            return do_buy(getattr(CardName, "PROVINCE", None))
        if owned(game_id, getattr(CardName, "GOLD", None)) < 2 and can_buy(getattr(CardName, "GOLD", None)):
            return do_buy(getattr(CardName, "GOLD", None))
        if owned(game_id, getattr(CardName, "MILITIA", None)) < 1 and can_buy(getattr(CardName, "MILITIA", None)):
            return do_buy(getattr(CardName, "MILITIA", None))
        if turn_no <= 6 and curses_left == FULL_PILE_SIZE and owned(game_id, getattr(CardName, "WITCH", None)) < 2 and can_buy(getattr(CardName, "WITCH", None)):
            return do_buy(getattr(CardName, "WITCH", None))
        if can_buy(getattr(CardName, "SILVER", None)):
            return do_buy(getattr(CardName, "SILVER", None))

    # ==== Anti-Distant Shore : on se limite & on renforce les attaques
    if DSHORE_IN_SUP:
        if owned(game_id, getattr(CardName, "DISTANTSHORE", None)) < DSHORE_CAP and can_buy(getattr(CardName, "DISTANTSHORE", None)):
            return do_buy(getattr(CardName, "DISTANTSHORE", None))
        if owned(game_id, getattr(CardName, "MILITIA", None)) < 2 and can_buy(getattr(CardName, "MILITIA", None)):
            return do_buy(getattr(CardName, "MILITIA", None))
        if owned(game_id, getattr(CardName, "BANDIT", None)) < 1 and can_buy(getattr(CardName, "BANDIT", None)):
            return do_buy(getattr(CardName, "BANDIT", None))

    # ==== Magnet si deck très riche en trésors
    total_treas_est = owned(game_id, getattr(CardName, "COPPER", None)) + owned(game_id, getattr(CardName, "SILVER", None)) + owned(game_id, getattr(CardName, "GOLD", None))
    if total_treas_est >= MAGNET_THRESH_TREAS and can_buy(getattr(CardName, "MAGNET", None)):
        return do_buy(getattr(CardName, "MAGNET", None))

    # ==== Workshop utilitaire (deny + 3-piles)
    if can_buy(getattr(CardName, "WORKSHOP", None)) and owned(game_id, getattr(CardName, "WORKSHOP", None)) < 2:
        return do_buy(getattr(CardName, "WORKSHOP", None))

    # ==== Anti-Gardens (course ou deck volumineux)
    if GARDENS_RACE:
        gardens_value = deck_sz // 10
        cap = min(GARDENS_MAX_BUYS, max(3, gardens_value))
        if owned(game_id, getattr(CardName, "WORKSHOP", None)) < 2 and can_buy(getattr(CardName, "WORKSHOP", None)):
            return do_buy(getattr(CardName, "WORKSHOP", None))
        if owned(game_id, getattr(CardName, "GARDENS", None)) < cap and can_buy(getattr(CardName, "GARDENS", None)):
            return do_buy(getattr(CardName, "GARDENS", None))
        if can_buy(getattr(CardName, "ESTATE", None)):  # accélère 3-piles
            return do_buy(getattr(CardName, "ESTATE", None))

    # ==== Conversions & fin standard
    if _in(hand, getattr(CardName, "COPPER", None)) >= 3 and can_buy(getattr(CardName, "SILVER", None)):
        print("[play] conversion: >=3 COPPER -> SILVER")
        return do_buy(getattr(CardName, "SILVER", None))
    if _in(hand, getattr(CardName, "SILVER", None)) >= 3 and can_buy(getattr(CardName, "GOLD", None)):
        print("[play] conversion: >=3 SILVER -> GOLD")
        return do_buy(getattr(CardName, "GOLD", None))

    if can_buy(getattr(CardName, "PROVINCE", None)):
        return do_buy(getattr(CardName, "PROVINCE", None))
    if owned(game_id, getattr(CardName, "GOLD", None)) < MAX_GOLD_BEFORE_GREEN and can_buy(getattr(CardName, "GOLD", None)):
        return do_buy(getattr(CardName, "GOLD", None))
    if is_behind and curses_left > 0 and owned(game_id, getattr(CardName, "WITCH", None)) < 3 and can_buy(getattr(CardName, "WITCH", None)):
        return do_buy(getattr(CardName, "WITCH", None))
    if prov_left <= DUCHY_PIVOT and can_buy(getattr(CardName, "DUCHY", None)):
        return do_buy(getattr(CardName, "DUCHY", None))
    if can_buy(getattr(CardName, "SILVER", None)):
        return do_buy(getattr(CardName, "SILVER", None))

    print(f"[play] END_TURN | acts={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']} spent={ts['coins_spent']}")
    return DopynionResponseStr(game_id=game_id, decision="END_TURN")

# =============================================================================
# End of game
# =============================================================================
@app.get("/end_game")
def end_game(game_id: GameIdDependency) -> DopynionResponseStr:
    with MASTER_LOCK:
        SESS.pop(game_id, None)
        SESS_LOCKS.pop(game_id, None)
    return DopynionResponseStr(game_id=game_id, decision="OK")

# =============================================================================
# Endpoints d’interaction (Library/Workshop/Feast/etc.)
# =============================================================================
@app.post("/confirm_discard_card_from_hand")
async def confirm_discard_card_from_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    # Accepte (Library & co) pour viser 7 cartes utiles.
    return DopynionResponseBool(game_id=game_id, decision=True)

@app.post("/discard_card_from_hand")
async def discard_card_from_hand(game_id: GameIdDependency, decision_input: Hand) -> DopynionResponseCardName:
    # Défausse prioritaire : CURSE > ESTATE > COPPER > SILVER > GOLD
    order: List[Optional[CardName]] = [
        getattr(CardName, "CURSE", None),
        getattr(CardName, "ESTATE", None),
        getattr(CardName, "COPPER", None),
        getattr(CardName, "SILVER", None),
        getattr(CardName, "GOLD", None),
    ]
    for c in order:
        if c and c in decision_input.hand:
            print(f"[discard] choose {c.name}")
            return DopynionResponseCardName(game_id=game_id, decision=c)
    print(f"[discard] default {decision_input.hand[0].name}")
    return DopynionResponseCardName(game_id=game_id, decision=decision_input.hand[0])

@app.post("/confirm_trash_card_from_hand")
async def confirm_trash_card_from_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    # OK pour Feast (self-trash) ou upgrades money.
    return DopynionResponseBool(game_id=game_id, decision=True)

@app.post("/trash_card_from_hand")
async def trash_card_from_hand(game_id: GameIdDependency, decision_input: Hand) -> DopynionResponseCardName:
    # Priorité trash : CURSE > COPPER > ESTATE > (sinon première)
    for c in [getattr(CardName, "CURSE", None), getattr(CardName, "COPPER", None), getattr(CardName, "ESTATE", None)]:
        if c and c in decision_input.hand:
            print(f"[trash] choose {c.name}")
            return DopynionResponseCardName(game_id=game_id, decision=c)
    print(f"[trash] default {decision_input.hand[0].name}")
    return DopynionResponseCardName(game_id=game_id, decision=decision_input.hand[0])

@app.post("/confirm_discard_deck")
async def confirm_discard_deck(
    game_id: GameIdDependency,
) -> DopynionResponseBool:
    # Autorise (utile pour Library/Adventurer selon impl)
    return DopynionResponseBool(game_id=game_id, decision=True)

@app.post("/choose_card_to_receive_in_discard")
async def choose_card_to_receive_in_discard(
    game_id: GameIdDependency,
    decision_input: PossibleCards,
) -> DopynionResponseCardName:
    # Heuristique : prend la plus chère (Workshop/Feast)
    def cost_of(c: CardName) -> int:
        return COST.get(c, 0)
    chosen = max(decision_input.possible_cards, key=cost_of)
    print(f"[choose_discard] choose {chosen.name} among {[c.name for c in decision_input.possible_cards]}")
    return DopynionResponseCardName(game_id=game_id, decision=chosen)

@app.post("/choose_card_to_receive_in_deck")
async def choose_card_to_receive_in_deck(
    game_id: GameIdDependency,
    decision_input: PossibleCards,
) -> DopynionResponseCardName:
    def cost_of(c: CardName) -> int:
        return COST.get(c, 0)
    chosen = max(decision_input.possible_cards, key=cost_of)
    print(f"[choose_deck] choose {chosen.name} among {[c.name for c in decision_input.possible_cards]}")
    return DopynionResponseCardName(game_id=game_id, decision=chosen)

@app.post("/skip_card_reception_in_hand")
async def skip_card_reception_in_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    # Skip si proposé
    return DopynionResponseBool(game_id=game_id, decision=True)

@app.post("/trash_money_card_for_better_money_card")
async def trash_money_card_for_better_money_card(
    game_id: GameIdDependency,
    decision_input: MoneyCardsInHand,
) -> DopynionResponseCardName:
    # Trash la pire pièce (première) → upgrade moteur
    print(f"[trash_money] available money in hand: {[c.name for c in decision_input.money_in_hand]}")
    return DopynionResponseCardName(game_id=game_id, decision=decision_input.money_in_hand[0])
