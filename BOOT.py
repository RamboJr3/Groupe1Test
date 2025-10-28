# BOOT_RHUM_RUIN_V1.py
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

# ============================
# ÉTAT THREAD-SAFE PAR PARTIE
# ============================
MASTER_LOCK = threading.RLock()
SESS: Dict[str, dict] = {}
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
    if game_id not in SESS:
        SESS[game_id] = {
            "actions": 1, "buys": 1, "coins_bonus": 0, "coins_spent": 0,
            "owned": {}, "turn": 0, "draw_bonus": 0
        }
    return SESS[game_id]

def inc_owned(game_id: str, card: CardName) -> None:
    with with_game_lock(game_id):
        s = _sess(game_id)
        o = s.setdefault("owned", {})
        o[card] = o.get(card, 0) + 1

def owned(game_id: str, card: Optional[CardName]) -> int:
    if card is None:
        return 0
    with with_game_lock(game_id):
        return _sess(game_id).get("owned", {}).get(card, 0)

# ============
# COÛTS CARTES
# ============
COST: Dict[CardName, int] = {}
def safe_add_cost(name: str, cost: int):
    if hasattr(CardName, name):
        COST[getattr(CardName, name)] = cost

# Basique
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

# Nouvelles cartes (set utilisateur)
safe_add_cost("FORTUNETELLER", 3)
safe_add_cost("LIBRARY", 5)
safe_add_cost("WORKSHOP", 3)
safe_add_cost("MAGNET", 5)
safe_add_cost("CEILLOR", 2)           # Cellar-like
safe_add_cost("FEAST", 4)
safe_add_cost("ADVENTURER", 6)
safe_add_cost("COUNCILROOM", 5)
safe_add_cost("DISTANTSHORE", 6)
safe_add_cost("FARMINGVILLAGE", 4)

# =================
# EFFETS SIMPLIFIÉS
# =================
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
safe_add_effect("LIBRARY",       0, 0, 0, 0)
safe_add_effect("WORKSHOP",      0, 0, 0, 0)
safe_add_effect("MAGNET",        0, 0, 0, 0)
safe_add_effect("CEILLOR",       1, 0, 0, 0)
safe_add_effect("FEAST",         0, 0, 0, 0)
safe_add_effect("ADVENTURER",    0, 0, 0, 0)
safe_add_effect("COUNCILROOM",   0, 1, 0, 4)
safe_add_effect("DISTANTSHORE",  1, 0, 0, 2)
safe_add_effect("FARMINGVILLAGE",2, 0, 0, 1)

# =========
# MODELS IO
# =========
class DopynionResponseBool(BaseModel):
    game_id: str
    decision: bool

class DopynionResponseCardName(BaseModel):
    game_id: str
    decision: CardName

class DopynionResponseStr(BaseModel):
    game_id: str
    decision: str

# =======
# HELPERS
# =======
def get_game_id(x_game_id: str = Header(description="ID of the game")) -> str:
    return x_game_id

GameIdDependency = Annotated[str, Depends(get_game_id)]

@app.exception_handler(Exception)
def unknown_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    print(exc.__class__.__name__, str(exc))
    return JSONResponse(status_code=500, content={"message":"Oops!","detail":str(exc),"name":exc.__class__.__name__})

@app.get("/", response_class=HTMLResponse)
def root() -> str:
    header = "<html><head><title>Rhum & Ruin</title></head><body><h1>Rhum & Ruin</h1><pre>"
    footer = "</pre></body></html>"
    return header + html.escape(Path(__file__).read_text(encoding="utf-8")) + footer

@app.get("/name")
def name() -> str:
    return "Bully_Test"

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

# ==========================
# PARAMÈTRES STRATÉGIQUES
# ==========================
# Philosophy R&R: engine-first, multi-buys, pivot vert optimal, attaques “suffisantes” (pas spam débile)
PROV_THRESHOLD         = 4
DUCHY_PIVOT            = 4
ESTATE_PIVOT           = 2
SCORE_DELTA_ENDING     = 6

ENGINE_VILLAGE_CAP     = 3
ENGINE_MARKET_CAP      = 3
ENGINE_LAB_CAP         = 3
ENGINE_FARM_CAP        = 2
ENGINE_HIRELING_CAP    = 1

WITCH_CAP              = 2         # on gagne la curse-race sans pourrir notre enchaînement
FTELLER_CAP            = 2
MILITIA_CAP            = 1
BANDIT_CAP             = 1

COUNCIL_CAP            = 2         # +buys + draw (attention feed adverse)
DSHORE_CAP             = 1         # DS utile mais limite clog
WORKSHOP_CAP           = 2         # pour denier ≤4 et accélérer 3 piles si besoin
MAGNET_TREAS_THRESH    = 6         # deck orienté trésors → Magnet devient value

MAX_GOLD_PRE_GREEN     = 3
GARDENS_MIN_DECK       = 28
GARDENS_MAX_BUYS       = 6

FULL_PILE_SIZE         = 10

# ========
# UTILS
# ========
def _in(h: Dict[CardName,int], c: Optional[CardName]) -> int:
    return 0 if c is None else h.get(c, 0)

def _money_in_hand(hand: Dict[CardName,int]) -> int:
    C,S,G = getattr(CardName,"COPPER",None), getattr(CardName,"SILVER",None), getattr(CardName,"GOLD",None)
    return _in(hand,C)*1 + _in(hand,S)*2 + _in(hand,G)*3

def _stock_qty(stock: Dict[CardName,int], name: str) -> int:
    c = getattr(CardName, name, None)
    return 0 if c is None else stock.get(c, 0)

def any_in_supply(*names: str) -> bool:
    return any(hasattr(CardName, n) for n in names)

def pile_depleted(stock: Dict[CardName,int], name: str, taken: int) -> bool:
    q = _stock_qty(stock, name)
    return q == 0 or (FULL_PILE_SIZE - q) >= taken

def can_buy(stock: Dict[CardName,int], game_id: str, hand: Dict[CardName,int], card: Optional[CardName]) -> bool:
    if card is None: return False
    cost = COST.get(card, None)
    if cost is None: return False
    with with_game_lock(game_id):
        ts = _sess(game_id)
        buys = ts["buys"]
        money = _money_in_hand(hand) + ts["coins_bonus"] - ts["coins_spent"]
    return buys > 0 and stock.get(card,0) > 0 and money >= cost

def do_buy(game_id: str, hand: Dict[CardName,int], card: CardName) -> DopynionResponseStr:
    cost = COST.get(card, 999)
    with with_game_lock(game_id):
        ts = _sess(game_id)
        total = _money_in_hand(hand) + ts["coins_bonus"]
        if ts["buys"] <= 0 or ts["coins_spent"] + cost > total:
            raise HTTPException(status_code=409, detail="Concurrent state changed: cannot buy now")
        ts["buys"] -= 1
        ts["coins_spent"] += cost
    inc_owned(game_id, card)
    print(f"[buy] {card.name} ({cost})")
    return DopynionResponseStr(game_id=game_id, decision=f"BUY {card.name}")

# =====
# PLAY
# =====
@app.post("/play")
def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    me = next((p for p in game.players if p.hand is not None), None)
    if not me or not me.hand:
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    hand  = me.hand.quantities
    stock = game.stock.quantities

    with with_game_lock(game_id):
        ts = _sess(game_id).copy()

    prov_left = _stock_qty(stock,"PROVINCE")
    duch_left = _stock_qty(stock,"DUCHY")
    est_left  = _stock_qty(stock,"ESTATE")
    curse_l   = _stock_qty(stock,"CURSE")

    my_score  = getattr(me,"score",0) or 0
    opp_max   = max((getattr(p,"score",0) or 0) for p in game.players if p is not me) if game.players else 0
    score_lead= my_score - opp_max
    is_ahead  = score_lead > 0
    is_behind = score_lead < 0

    with with_game_lock(game_id):
        deck_sz = 10 + sum(_sess(game_id).get("owned", {}).values())

    # ===== Phase ACTION : priorité engine puis value/attaques contrôlées
    engine = ["HIRELING","VILLAGE","FARMINGVILLAGE","MARKET","LABORATORY","CEILLOR","COUNCILROOM","DISTANTSHORE"]
    attacks= ["WITCH","FORTUNETELLER","MILITIA","BANDIT","BUREAUCRAT"]
    utils  = ["SMITHY","LIBRARY","MAGNET","ADVENTURER","WORKSHOP","WOODCUTTER","CHANCELLOR"]

    # anti-synergie adverse (Library/CouncilRoom) → Militia avant Witch pour couper à 3 cartes
    if any_in_supply("LIBRARY","COUNCILROOM"):
        attacks = ["MILITIA","WITCH","FORTUNETELLER","BANDIT","BUREAUCRAT"]

    prio: List[CardName] = []
    seen=set()
    for name in [*engine,*attacks,*utils]:
        c = getattr(CardName,name,None)
        if c and c not in seen and name in EFFECTS:
            seen.add(c); prio.append(c)

    for a in prio:
        if _in(hand, a) > 0:
            acts, buys, coins, draw = EFFECTS[a.name]
            with with_game_lock(game_id):
                t = _sess(game_id)
                if t["actions"] <= 0: break
                t["actions"] -= 1
                t["actions"] += acts
                t["buys"]    += buys
                t["coins_bonus"] += coins
            return DopynionResponseStr(game_id=game_id, decision=f"ACTION {a.name}")

    # ===== Phase ACHATS
    def buys_left() -> int:
        with with_game_lock(game_id):
            return _sess(game_id)["buys"]

    turn_no = _sess(game_id).get("turn", 1)

    # Détection “engine contest” (piles +A/+pioche qui s’érodent)
    eng_deny = (
        pile_depleted(stock,"VILLAGE",2) or
        pile_depleted(stock,"MARKET",2) or
        pile_depleted(stock,"LABORATORY",2) or
        pile_depleted(stock,"FARMINGVILLAGE",2)
    )

    # Double Province si on peut
    if buys_left() >= 2 and _money_in_hand(hand) + ts["coins_bonus"] - ts["coins_spent"] >= 16 and prov_left >= 2:
        return do_buy(game_id, hand, getattr(CardName,"PROVINCE"))

    # Fin agressive si on mène
    if is_ahead and (prov_left <= PROV_THRESHOLD or score_lead >= SCORE_DELTA_ENDING):
        if can_buy(stock, game_id, hand, getattr(CardName,"PROVINCE",None)):
            return do_buy(game_id, hand, getattr(CardName,"PROVINCE"))
        if prov_left <= DUCHY_PIVOT and can_buy(stock, game_id, hand, getattr(CardName,"DUCHY",None)):
            return do_buy(game_id, hand, getattr(CardName,"DUCHY"))
        if prov_left <= ESTATE_PIVOT and can_buy(stock, game_id, hand, getattr(CardName,"ESTATE",None)):
            return do_buy(game_id, hand, getattr(CardName,"ESTATE"))
        # accélérer 3 piles si ahead
        for tgt in [getattr(CardName,"WORKSHOP",None), getattr(CardName,"VILLAGE",None),
                    getattr(CardName,"WOODCUTTER",None), getattr(CardName,"SMITHY",None), getattr(CardName,"ESTATE",None)]:
            if tgt and can_buy(stock, game_id, hand, tgt):
                return do_buy(game_id, hand, tgt)

    # Core engine build (capés) — priorité +A/+pioche/+buys
    targets_engine: List[Tuple[str,int]] = [
        ("HIRELING", ENGINE_HIRELING_CAP),
        ("VILLAGE",  ENGINE_VILLAGE_CAP),
        ("FARMINGVILLAGE", ENGINE_FARM_CAP),
        ("MARKET",   ENGINE_MARKET_CAP),
        ("LABORATORY", ENGINE_LAB_CAP),
        ("COUNCILROOM", COUNCIL_CAP),
        ("DISTANTSHORE", DSHORE_CAP),
    ]
    for n,cap in targets_engine:
        c = getattr(CardName,n,None)
        if c and owned(game_id, c) < cap and can_buy(stock, game_id, hand, c):
            return do_buy(game_id, hand, c)

    # Attaques “suffisantes” (curse-race contrôlée + anti-draw combos)
    if curse_l > 0 and owned(game_id, getattr(CardName,"WITCH",None)) < WITCH_CAP and can_buy(stock, game_id, hand, getattr(CardName,"WITCH",None)):
        return do_buy(game_id, hand, getattr(CardName,"WITCH"))
    if curse_l > 0 and owned(game_id, getattr(CardName,"FORTUNETELLER",None)) < FTELLER_CAP and can_buy(stock, game_id, hand, getattr(CardName,"FORTUNETELLER",None)):
        return do_buy(game_id, hand, getattr(CardName,"FORTUNETELLER"))
    if any_in_supply("LIBRARY","COUNCILROOM") and owned(game_id, getattr(CardName,"MILITIA",None)) < MILITIA_CAP and can_buy(stock, game_id, hand, getattr(CardName,"MILITIA",None)):
        return do_buy(game_id, hand, getattr(CardName,"MILITIA"))
    if owned(game_id, getattr(CardName,"BANDIT",None)) < BANDIT_CAP and can_buy(stock, game_id, hand, getattr(CardName,"BANDIT",None)):
        return do_buy(game_id, hand, getattr(CardName,"BANDIT"))

    # Workshop utilitaire : deny ≤4 (Village/Smithy/Woodcutter) et plan 3 piles
    if owned(game_id, getattr(CardName,"WORKSHOP",None)) < WORKSHOP_CAP and can_buy(stock, game_id, hand, getattr(CardName,"WORKSHOP",None)):
        return do_buy(game_id, hand, getattr(CardName,"WORKSHOP"))

    # Magnet si deck riche en trésors
    tot_treas = owned(game_id, getattr(CardName,"COPPER",None)) + owned(game_id, getattr(CardName,"SILVER",None)) + owned(game_id, getattr(CardName,"GOLD",None))
    if tot_treas >= MAGNET_TREAS_THRESH and can_buy(stock, game_id, hand, getattr(CardName,"MAGNET",None)):
        return do_buy(game_id, hand, getattr(CardName,"MAGNET"))

    # Anti-Gardens (course ou deck volumineux)
    gardens_left = _stock_qty(stock,"GARDENS")
    if gardens_left>0 and (pile_depleted(stock,"GARDENS",2) or deck_sz>=GARDENS_MIN_DECK):
        gardens_value = deck_sz // 10
        cap = min(GARDENS_MAX_BUYS, max(3, gardens_value))
        if owned(game_id, getattr(CardName,"WORKSHOP",None)) < WORKSHOP_CAP and can_buy(stock, game_id, hand, getattr(CardName,"WORKSHOP",None)):
            return do_buy(game_id, hand, getattr(CardName,"WORKSHOP"))
        if owned(game_id, getattr(CardName,"GARDENS",None)) < cap and can_buy(stock, game_id, hand, getattr(CardName,"GARDENS",None)):
            return do_buy(game_id, hand, getattr(CardName,"GARDENS"))
        if can_buy(stock, game_id, hand, getattr(CardName,"ESTATE",None)):
            return do_buy(game_id, hand, getattr(CardName,"ESTATE"))

    # Pivot vert / éco
    if can_buy(stock, game_id, hand, getattr(CardName,"PROVINCE",None)):
        return do_buy(game_id, hand, getattr(CardName,"PROVINCE"))
    if owned(game_id, getattr(CardName,"GOLD",None)) < MAX_GOLD_PRE_GREEN and can_buy(stock, game_id, hand, getattr(CardName,"GOLD",None)):
        return do_buy(game_id, hand, getattr(CardName,"GOLD",None))
    if prov_left <= DUCHY_PIVOT and can_buy(stock, game_id, hand, getattr(CardName,"DUCHY",None)):
        return do_buy(game_id, hand, getattr(CardName,"DUCHY"))
    if can_buy(stock, game_id, hand, getattr(CardName,"SILVER",None)):
        return do_buy(game_id, hand, getattr(CardName,"SILVER"))

    return DopynionResponseStr(game_id=game_id, decision="END_TURN")

# ==========
# END GAME
# ==========
@app.get("/end_game")
def end_game(game_id: GameIdDependency) -> DopynionResponseStr:
    with MASTER_LOCK:
        SESS.pop(game_id, None)
        SESS_LOCKS.pop(game_id, None)
    return DopynionResponseStr(game_id=game_id, decision="OK")

# ======================
# HANDLERS D’INTERACTION
# ======================
@app.post("/confirm_discard_card_from_hand")
async def confirm_discard_card_from_hand(game_id: GameIdDependency, _decision_input: CardNameAndHand) -> DopynionResponseBool:
    # Library & co : accepte (on vise 7 cartes utiles)
    return DopynionResponseBool(game_id=game_id, decision=True)

@app.post("/discard_card_from_hand")
async def discard_card_from_hand(game_id: GameIdDependency, decision_input: Hand) -> DopynionResponseCardName:
    order: List[Optional[CardName]] = [
        getattr(CardName,"CURSE",None),
        getattr(CardName,"ESTATE",None),
        getattr(CardName,"COPPER",None),
        getattr(CardName,"SILVER",None),
        getattr(CardName,"GOLD",None),
    ]
    for c in order:
        if c and c in decision_input.hand:
            return DopynionResponseCardName(game_id=game_id, decision=c)
    return DopynionResponseCardName(game_id=game_id, decision=decision_input.hand[0])

@app.post("/confirm_trash_card_from_hand")
async def confirm_trash_card_from_hand(game_id: GameIdDependency, _decision_input: CardNameAndHand) -> DopynionResponseBool:
    return DopynionResponseBool(game_id=game_id, decision=True)

@app.post("/trash_card_from_hand")
async def trash_card_from_hand(game_id: GameIdDependency, decision_input: Hand) -> DopynionResponseCardName:
    for c in [getattr(CardName,"CURSE",None), getattr(CardName,"COPPER",None), getattr(CardName,"ESTATE",None)]:
        if c and c in decision_input.hand:
            return DopynionResponseCardName(game_id=game_id, decision=c)
    return DopynionResponseCardName(game_id=game_id, decision=decision_input.hand[0])

@app.post("/confirm_discard_deck")
async def confirm_discard_deck(game_id: GameIdDependency) -> DopynionResponseBool:
    return DopynionResponseBool(game_id=game_id, decision=True)

@app.post("/choose_card_to_receive_in_discard")
async def choose_card_to_receive_in_discard(game_id: GameIdDependency, decision_input: PossibleCards) -> DopynionResponseCardName:
    def cost_of(c: CardName) -> int: return COST.get(c, 0)
    chosen = max(decision_input.possible_cards, key=cost_of)
    return DopynionResponseCardName(game_id=game_id, decision=chosen)

@app.post("/choose_card_to_receive_in_deck")
async def choose_card_to_receive_in_deck(game_id: GameIdDependency, decision_input: PossibleCards) -> DopynionResponseCardName:
    def cost_of(c: CardName) -> int: return COST.get(c, 0)
    chosen = max(decision_input.possible_cards, key=cost_of)
    return DopynionResponseCardName(game_id=game_id, decision=chosen)

@app.post("/skip_card_reception_in_hand")
async def skip_card_reception_in_hand(game_id: GameIdDependency, _decision_input: CardNameAndHand) -> DopynionResponseBool:
    return DopynionResponseBool(game_id=game_id, decision=True)

@app.post("/trash_money_card_for_better_money_card")
async def trash_money_card_for_better_money_card(game_id: GameIdDependency, decision_input: MoneyCardsInHand) -> DopynionResponseCardName:
    return DopynionResponseCardName(game_id=game_id, decision=decision_input.money_in_hand[0])
