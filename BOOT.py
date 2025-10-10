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
COST = {
    CardName.COPPER: 0,
    CardName.SILVER: 3,
    CardName.GOLD: 6,
    CardName.ESTATE: 2,
    CardName.DUCHY: 5,
    CardName.PROVINCE: 8,
    CardName.FESTIVAL: 5,
    CardName.LABORATORY: 5,
    CardName.VILLAGE: 3,
    CardName.WOODCUTTER: 3,
    CardName.SMITHY: 4,
    CardName.MARKET: 5,
    CardName.WITCH: 5,
    CardName.HIRELING: 6,
    CardName.BANDIT: 5,
    CardName.BUREAUCRAT: 4,
    CardName.CHANCELLOR: 3,
    CardName.GARDENS: 4,
    CardName.MILITIA: 4,
}


# --- EFFETS D'ACTIONS (actions, buys, coins_bonus, draw) ---
EFFECTS: dict[str, tuple[int, int, int, int]] = {
    "FESTIVAL":   (2, 1, 2, 0),
    "LABORATORY": (1, 0, 0, 2),
    "VILLAGE":    (2, 0, 0, 1),
    "WOODCUTTER": (0, 1, 2, 0),
    "SMITHY":     (0, 0, 0, 3),
    "MARKET":     (1, 1, 1, 1),
    "WITCH":      (0, 0, 0, 2),  # ⬅️ +2 cartes ; les Malédictions sont appliquées par l’arbitre
    "HIRELING": (0, 0, 0, 0),  # on la joue, pas d'effet immédiat (le moteur gère le +1 carte/turn)
    "BANDIT":      (0, 0, 0, 0),  # gain d'Or + attaque gérés par l’arbitre, terminal
    "BUREAUCRAT":  (0, 0, 0, 0),  # gagne Argent au-dessus du deck + attaque, terminal
    "CHANCELLOR":  (0, 0, 2, 0),  # +2 pièces, option de défausser le deck
    "MILITIA" : (0, 0, 2, 0),
    # GARDENS n’a pas d’effet en jeu (carte Victoire)
}


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
# Template extra bonus
#####################################################


# The root of the website shows the code of the website
@app.get("/", response_class=HTMLResponse)
def root() -> str:
    header = (
        "<html><head><title>Dopynion template</title></head><body>"
        "<h1>Dopynion documentation</h1>"
        "<h2>API documentation</h2>"
        '<p><a href="/docs">Read the documentation.</a></p>'
        "<h2>Code template</h2>"
        "<p>The code of this website is:</p>"
        "<pre>"
    )
    footer = "</pre></body></html>"
    return header + html.escape(Path(__file__).read_text(encoding="utf-8")) + footer


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
        # bonus de pioche persistant = nb de Hirelings joués (approx: possédés)
        s["draw_bonus"] = s.get("owned", {}).get(CardName.HIRELING, 0)
        print(f"[start_turn] game={game_id} turn={s['turn']} hirelings={s['draw_bonus']}")
    return DopynionResponseStr(game_id=game_id, decision="OK")



# --- Constants de stratégie (tweakables) ---
PROV_THRESHOLD = 4            # si <= ce nombre de provinces, switch agressif
SCORE_DELTA = 4               # si un adversaire te distance >= ce delta, switch agressif
ENGINE_PROVINCE_MONEY = 12    # argent cible dans un tour pour considérer qu'on peut faire Province(s)
DOUBLE_PROVINCE_BUYS = 2      # si on a >= buys pour tenter double achat

@app.post("/play")
def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    # --- trouver "moi" ---
    me = next((p for p in game.players if p.hand is not None), None)
    if not me or not me.hand:
        print(f"[play] game={game_id} no visible hand -> END_TURN")
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    hand = me.hand.quantities        # dict[CardName,int]
    stock = game.stock.quantities    # dict[CardName,int]
    # l'état doit être lu/modifié sous verrou
    with with_game_lock(game_id):
        ts = _sess(game_id)
        # on peut juste tracer ici si tu veux
        print(f"[play] state at entry: acts={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']} spent={ts['coins_spent']}")


    # helpers
    def hq(c: CardName) -> int: return hand.get(c, 0)
    def in_stock(c: CardName) -> bool: return stock.get(c, 0) > 0
    def money_treasures() -> int:
        return hq(CardName.COPPER)*1 + hq(CardName.SILVER)*2 + hq(CardName.GOLD)*3
    def money_available() -> int:
        return money_treasures() + ts["coins_bonus"] - ts["coins_spent"]

    # basic info
    prov_left = stock.get(CardName.PROVINCE, 0)
    my_score = getattr(me, "score", 0) or 0
    max_opponent_score = max((getattr(p, "score", 0) or 0) for p in game.players if p is not me) if game.players else 0

    # quick deck_estimate from visible hand (cheap heuristic)
    # count actions and treasure density in hand to guess engine readiness
    actions_in_hand = sum(1 for c in hand if c not in (CardName.COPPER, CardName.SILVER, CardName.GOLD,
                                                       CardName.ESTATE, CardName.DUCHY, CardName.PROVINCE, CardName.CURSE) and hand[c] > 0)
    treasure_value = money_treasures()

    print(f"[play] game={game_id} start | actions={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']} spent={ts['coins_spent']} "
          f"treasure={treasure_value} actions_in_hand={actions_in_hand} prov_left={prov_left} my_score={my_score} max_opp={max_opponent_score}")
        # === DÉRIVÉS / FLAGS POUR LA STRAT ===
    # lecture sûre de notre deck suivi par SESS
    with with_game_lock(game_id):
        deck_sz = sum(_sess(game_id).get("owned", {}).values())

    # piles visibles
    curses_left   = stock.get(CardName.CURSE, 0) if CardName.CURSE in stock else 0
    villages_left = stock.get(CardName.VILLAGE, 0) if CardName.VILLAGE in stock else 0
    gardens_left  = stock.get(CardName.GARDENS, 0) if CardName.GARDENS in stock else 0

    # sommes-nous DEVANT ? (=> mode "cancer" : spam Witch/Bandit/Bureaucrat)
    AGGRO_CURSE = (my_score >= max_opponent_score)

    # sommes-nous DERRIÈRE ? (=> verdissement agressif Duchy/Province/Gardens)
    AGGRO_GREEN = (my_score + 3 <= max_opponent_score) or (prov_left <= PROV_THRESHOLD)

    # seuil à partir duquel Gardens commence à bien scorer (≈ 2 PV à 20 cartes)
    GARDENS_ONLINE = (deck_sz >= 20)


    # --------------------
    # PHASE ACTION (thread-safe, on ne touche pas aux helpers hq(), money_available(), etc.)
    # --------------------
    militia_card = getattr(CardName, "MILITIA", None)
        # ordre des terminaux selon le contexte
    # - s'il reste des Curses -> Witch d'abord (priorité à la malédiction)
    # - sinon -> Militia d'abord (hand-size attack "cancer")
    if militia_card:
        if curses_left and curses_left > 0:
            terminal_order = [CardName.WITCH, militia_card, CardName.SMITHY]
        else:
            terminal_order = [militia_card, CardName.WITCH, CardName.SMITHY]
    else:
        terminal_order = [CardName.WITCH, CardName.SMITHY]
    action_priority = [
        # non-terminaux d'abord (pioche/+actions)
        CardName.MARKET,
        CardName.LABORATORY,
        CardName.VILLAGE,
        CardName.FESTIVAL,
        # puis terminaux adaptés
        *terminal_order,
        # utilitaires/nuisibles si présents
        getattr(CardName, "BUREAUCRAT", None),
        getattr(CardName, "BANDIT", None),
        getattr(CardName, "CHANCELLOR", None),
        CardName.WOODCUTTER,
    ]
    action_priority = [c for c in action_priority if c is not None]

    for a in action_priority:
        if hq(a) > 0 and a.name in EFFECTS:
            acts, buys, coins, _ = EFFECTS[a.name]
            with with_game_lock(game_id):
                ts = _sess(game_id)
                if ts["actions"] <= 0:
                    break
                ts["actions"] -= 1
                ts["actions"] += acts
                ts["buys"]    += buys
                ts["coins_bonus"] += coins
            print(f"[play] ACTION {a.name} -> +acts={acts} +buys={buys} +$={coins} | "
                f"state actions={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']}")
            return DopynionResponseStr(game_id=game_id, decision=f"ACTION {a.name}")

    # --------------------
    # MODE FLAGS (déterminent Curse spam vs Green)
    # --------------------
    score_lead   = my_score - max_opponent_score
    is_ahead     = score_lead > 0
    is_behind    = score_lead < 0

    prov_left     = stock.get(CardName.PROVINCE, 0)
    curses_left   = stock.get(CardName.CURSE, 0) if CardName.CURSE in stock else 0
    villages_left = stock.get(CardName.VILLAGE, 0) if CardName.VILLAGE in stock else 0
    gardens_left  = stock.get(getattr(CardName, "GARDENS", CardName.ESTATE), 0) if hasattr(CardName, "GARDENS") else 0

    with with_game_lock(game_id):
        turn_no = _sess(game_id).get("turn", 1)

    # estimation grossière de la taille de deck : 10 + total acheté
    deck_sz = 10 + sum((SESS.get(game_id, {}).get("owned") or {}).values())
    enemy_equipe3 = any("equipe3" in (getattr(p, "name", "") or "").lower() for p in game.players)

    # On veut spammer les malédictions quand on n'est PAS devant
    AGGRO_CURSE = (is_behind or (not is_ahead)) and (curses_left > 0)
    # On ne "green" que quand on est devant OU fin de partie
    AGGRO_GREEN = is_ahead and (prov_left <= 5 or score_lead >= 8)
    # Mode Gardens si on perd et qu’il y a Gardens
    GARDENS_MODE = gardens_left and is_behind and deck_sz >= 20

    # Comptes de nos cartes
    vg_cnt  = owned(game_id, CardName.VILLAGE)
    mk_cnt  = owned(game_id, CardName.MARKET)
    sm_cnt  = owned(game_id, CardName.SMITHY)
    wt_cnt  = owned(game_id, CardName.WITCH)
    lab_cnt = owned(game_id, CardName.LABORATORY)
    gd_cnt  = owned(game_id, CardName.GOLD)
    pr_cnt  = owned(game_id, CardName.PROVINCE)
    hr_cnt  = owned(game_id, getattr(CardName, "HIRELING", CardName.GOLD))
    bu_cnt  = owned(game_id, getattr(CardName, "BUREAUCRAT", CardName.ESTATE))
    bd_cnt  = owned(game_id, getattr(CardName, "BANDIT", CardName.ESTATE))
    ch_cnt  = owned(game_id, getattr(CardName, "CHANCELLOR", CardName.ESTATE))
    mi_cnt  = owned(game_id, getattr(CardName, "MILITIA", CardName.ESTATE))
    fest_cnt = owned(game_id, CardName.FESTIVAL) if CardName.FESTIVAL in COST else 0
    ga_cnt  = owned(game_id, getattr(CardName, "GARDENS", CardName.ESTATE))



    print(f"[mode] t={turn_no} lead={score_lead} ahead={is_ahead} behind={is_behind} "
        f"prov_left={prov_left} curses_left={curses_left} GARDENS_MODE={bool(GARDENS_MODE)} "
        f"AGGRO_CURSE={AGGRO_CURSE} AGGRO_GREEN={AGGRO_GREEN} deck_sz≈{deck_sz}")

    # --------------------
    # PHASE ACHAT — helpers thread-safe (PRESERVÉS)
    # --------------------
    def can_buy(c: CardName) -> bool:
        # figer l'état sous verrou
        with with_game_lock(game_id):
            ts_local = _sess(game_id).copy()
        return (
            ts_local["buys"] > 0
            and stock.get(c, 0)
            and (hq(CardName.COPPER)*1 + hq(CardName.SILVER)*2 + hq(CardName.GOLD)*3
                + ts_local["coins_bonus"] - ts_local["coins_spent"]) >= COST[c]
        )

    def do_buy(c: CardName) -> DopynionResponseStr:
        cost = COST[c]
        with with_game_lock(game_id):
            ts2 = _sess(game_id)
            total_money = hq(CardName.COPPER)*1 + hq(CardName.SILVER)*2 + hq(CardName.GOLD)*3 + ts2["coins_bonus"]
            if ts2["buys"] <= 0 or ts2["coins_spent"] + cost > total_money:
                raise HTTPException(status_code=409, detail="Concurrent state changed: cannot buy now")
            ts2["buys"] -= 1
            ts2["coins_spent"] += cost
        inc_owned(game_id, c)   # déjà sous verrou dans la fonction
        print(f"[buy] BUY {c.name} cost={cost}")
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {c.name}")

    # --------------------
    # OPPONENT SIGNATURES & MODES
    # --------------------
    # Piles supplémentaires utilisées pour la détection d'ouverture
    labs_left      = stock.get(CardName.LABORATORY, 0) if CardName.LABORATORY in stock else 0
    markets_left   = stock.get(CardName.MARKET, 0) if CardName.MARKET in stock else 0
    festival_left  = stock.get(CardName.FESTIVAL, 0) if CardName.FESTIVAL in stock else 0
    gold_left      = stock.get(CardName.GOLD, 0)
    FULL = 10  # taille standard des piles royaume

    BIGMONEY_MODE   = (turn_no >= 4 and villages_left >= 8 and labs_left >= 9 and markets_left >= 9 and curses_left == FULL)
    ENGINE_MODE     = (turn_no <= 8 and (villages_left <= 7 or labs_left <= 8 or markets_left <= 8))
    WITCH_SPAM_MODE = (turn_no <= 8 and curses_left <= FULL - 10)
    MILITIA_LOCK    = (festival_left <= 8 and curses_left == FULL)
    GARDENS_RACE    = (gardens_left > 0 and gardens_left < FULL and turn_no <= 12)

    # --------------------
    # STRATEGY SWITCHBOARD (7 strats)
    # --------------------
    # S1: Anti-BigMoney Rush
    if BIGMONEY_MODE:
        if can_buy(CardName.PROVINCE) and (prov_left <= 6 or my_score >= max_opponent_score):
            return do_buy(CardName.PROVINCE)
        if gd_cnt < 2 and can_buy(CardName.GOLD):
            return do_buy(CardName.GOLD)
        # insère ici : 1 Militia tôt
        if hasattr(CardName, "MILITIA") and mi_cnt < 1 and can_buy(CardName.MILITIA):
            return do_buy(CardName.MILITIA)
        if turn_no <= 6 and curses_left == FULL and wt_cnt < 2 and can_buy(CardName.WITCH):
            return do_buy(CardName.WITCH)
        if can_buy(CardName.SILVER):
            return do_buy(CardName.SILVER)


    # S2: Engine-Crush (deny Village + Bandit tôt)
    if ENGINE_MODE:
        if villages_left > 0 and vg_cnt < 2 and can_buy(CardName.VILLAGE):
            return do_buy(CardName.VILLAGE)
        if hasattr(CardName, "MILITIA"):
            plus_actions = vg_cnt + fest_cnt + mk_cnt  # Market donne +1 action
            cap_militia = 2 if plus_actions >= 2 else 1
            if mi_cnt < cap_militia and can_buy(CardName.MILITIA):
                return do_buy(CardName.MILITIA)
        if bd_cnt < 1 and can_buy(getattr(CardName, "BANDIT", CardName.GOLD)):
            return do_buy(getattr(CardName, "BANDIT", CardName.GOLD))
        if mk_cnt < 2 and can_buy(CardName.MARKET):
            return do_buy(CardName.MARKET)
        if lab_cnt < 3 and can_buy(CardName.LABORATORY):
            return do_buy(CardName.LABORATORY)
        if gd_cnt < 2 and can_buy(CardName.GOLD):
            return do_buy(CardName.GOLD)
        if can_buy(CardName.PROVINCE):
            return do_buy(CardName.PROVINCE)


    # S3: Hybrid Witch->Gold (ouverture générique)
    if turn_no <= 6 and curses_left > 0 and wt_cnt < 2 and can_buy(CardName.WITCH):
        return do_buy(CardName.WITCH)
    if mk_cnt < 2 and can_buy(CardName.MARKET):
        return do_buy(CardName.MARKET)
    if lab_cnt < 2 and can_buy(CardName.LABORATORY):
        return do_buy(CardName.LABORATORY)
    if gd_cnt < 2 and can_buy(CardName.GOLD):
        return do_buy(CardName.GOLD)

    # S4: Anti Witch-Spam
    if WITCH_SPAM_MODE:
        if mk_cnt < 2 and can_buy(CardName.MARKET):
            return do_buy(CardName.MARKET)
        if lab_cnt < 3 and can_buy(CardName.LABORATORY):
            return do_buy(CardName.LABORATORY)
        if hasattr(CardName, "MILITIA") and mi_cnt < 1 and (vg_cnt + fest_cnt + mk_cnt) >= 1 and can_buy(CardName.MILITIA):
            return do_buy(CardName.MILITIA)
        if gd_cnt < 2 and can_buy(CardName.GOLD):
            return do_buy(CardName.GOLD)


    # S5: Anti Milice-Lock
    if MILITIA_LOCK:
        if 'FESTIVAL' in CardName.__members__ and fest_cnt < 2 and can_buy(CardName.FESTIVAL):
            return do_buy(CardName.FESTIVAL)
        if hasattr(CardName, "MILITIA"):
            cap_militia = 2 if (vg_cnt + fest_cnt + mk_cnt) >= 2 else 1
            if mi_cnt < cap_militia and can_buy(CardName.MILITIA):
                return do_buy(CardName.MILITIA)
        if mk_cnt < 2 and can_buy(CardName.MARKET):
            return do_buy(CardName.MARKET)
        if lab_cnt < 2 and can_buy(CardName.LABORATORY):
            return do_buy(CardName.LABORATORY)
        
    # --- Friction quand on est derrière (avant S6) ---
    # (Optionnel mais conseillé) éviter d’empiler des terminaux sans +Actions :
    plus_actions = vg_cnt + fest_cnt + mk_cnt   # Village/Festival/Market
    if is_behind and hasattr(CardName, "MILITIA") and mi_cnt < 1 and plus_actions >= 1 and can_buy(CardName.MILITIA):
        return do_buy(CardName.MILITIA)

    if is_behind and bd_cnt < 1 and can_buy(getattr(CardName, "BANDIT", CardName.GOLD)):
        return do_buy(getattr(CardName, "BANDIT", CardName.GOLD))



    # S6: Late-Gardens Counter (nerf spam & choix VP)
    # - Seulement si on est derrière
    # - Deck "online" à 30+ cartes (Garden ≈ 3 VP mini)
    # - Pas en fin de course Provinces
    # - Cap dynamique: <= min(6, deck_sz//10)
    gardens_value = deck_sz // 10           # VP par Garden (approx Dominion)
    GARDENS_CAP   = min(6, gardens_value)   # ex: 30→3, 40→4, max 6

    if is_behind and gardens_left > 0 and deck_sz >= 30 and prov_left >= 5 and ga_cnt < GARDENS_CAP:
        # Ne pas prendre Garden si on peut finir/avancer aux Provinces
        if not can_buy(CardName.PROVINCE):
            # Duchy = 3 VP pour 5 — Garden ne vaut le coup que si >= 3-4 VP
            if can_buy(CardName.DUCHY):
                if gardens_value >= 4 and can_buy(CardName.GARDENS):
                    return do_buy(CardName.GARDENS)
            else:
                if gardens_value >= 3 and can_buy(CardName.GARDENS):
                    return do_buy(CardName.GARDENS)


    # S7: Safe BM fallback & endgame
    if prov_left <= 4 and can_buy(CardName.PROVINCE):
        return do_buy(CardName.PROVINCE)
    if gd_cnt < 2 and can_buy(CardName.GOLD):
        return do_buy(CardName.GOLD)
    if can_buy(CardName.PROVINCE):
        return do_buy(CardName.PROVINCE)
    if prov_left <= 4 and can_buy(CardName.DUCHY):
        return do_buy(CardName.DUCHY)
    if can_buy(CardName.SILVER):
        return do_buy(CardName.SILVER)
    
    # pre S7: avant l'argent de secours
    if hasattr(CardName, "MILITIA") and mi_cnt < 1 and (wt_cnt + sm_cnt + mi_cnt) < 2 and can_buy(CardName.MILITIA):
        return do_buy(CardName.MILITIA)
    
    # S7: Safe BM fallback & endgame
    if prov_left <= 4 and can_buy(CardName.PROVINCE):
        return do_buy(CardName.PROVINCE)
    if gd_cnt < 2 and can_buy(CardName.GOLD):
        return do_buy(CardName.GOLD)
    if can_buy(CardName.PROVINCE):
        return do_buy(CardName.PROVINCE)
    if prov_left <= 4 and can_buy(CardName.DUCHY):
        return do_buy(CardName.DUCHY)
    if can_buy(CardName.SILVER):
        return do_buy(CardName.SILVER)
    

    print(f"[play] nothing to do -> END_TURN | state actions={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']} spent={ts['coins_spent']}")
    return DopynionResponseStr(game_id=game_id, decision="END_TURN")




@app.get("/end_game")
def end_game(game_id: GameIdDependency) -> DopynionResponseStr:
    with MASTER_LOCK:
        SESS.pop(game_id, None)
        SESS_LOCKS.pop(game_id, None)
    return DopynionResponseStr(game_id=game_id, decision="OK")




@app.post("/confirm_discard_card_from_hand")
async def confirm_discard_card_from_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/discard_card_from_hand")
async def discard_card_from_hand(game_id: GameIdDependency, decision_input: Hand) -> DopynionResponseCardName:
    # ordre safe
    order = [CardName.CURSE, CardName.ESTATE, CardName.COPPER, CardName.SILVER, CardName.GOLD]
    for c in order:
        if c in decision_input.hand:
            print(f"[discard] choose {c.name}")
            return DopynionResponseCardName(game_id=game_id, decision=c)
    print(f"[discard] default {decision_input.hand[0].name}")
    return DopynionResponseCardName(game_id=game_id, decision=decision_input.hand[0])



@app.post("/confirm_trash_card_from_hand")
async def confirm_trash_card_from_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/trash_card_from_hand")
async def trash_card_from_hand(game_id: GameIdDependency, decision_input: Hand) -> DopynionResponseCardName:
    for c in [CardName.CURSE, CardName.COPPER, CardName.ESTATE]:
        if c in decision_input.hand:
            print(f"[trash] choose {c.name}")
            return DopynionResponseCardName(game_id=game_id, decision=c)
    print(f"[trash] default {decision_input.hand[0].name}")
    return DopynionResponseCardName(game_id=game_id, decision=decision_input.hand[0])


@app.post("/confirm_discard_deck")
async def confirm_discard_deck(
    game_id: GameIdDependency,
) -> DopynionResponseBool:
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/choose_card_to_receive_in_discard")
async def choose_card_to_receive_in_discard(
    game_id: GameIdDependency,
    decision_input: PossibleCards,
) -> DopynionResponseCardName:
    return DopynionResponseCardName(
        game_id=game_id,
        decision=decision_input.possible_cards[0],
    )


@app.post("/choose_card_to_receive_in_deck")
async def choose_card_to_receive_in_deck(
    game_id: GameIdDependency,
    decision_input: PossibleCards,
) -> DopynionResponseCardName:
    return DopynionResponseCardName(
        game_id=game_id,
        decision=decision_input.possible_cards[0],
    )


@app.post("/skip_card_reception_in_hand")
async def skip_card_reception_in_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/trash_money_card_for_better_money_card")
async def trash_money_card_for_better_money_card(
    game_id: GameIdDependency,
    decision_input: MoneyCardsInHand,
) -> DopynionResponseCardName:
    return DopynionResponseCardName(
        game_id=game_id,
        decision=decision_input.money_in_hand[0],
    )
