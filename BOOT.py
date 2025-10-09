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
    return "Gin & ruin test"


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

    # --------------------
    # PHASE ACTION (sous verrou)
    # --------------------
    action_priority = [
        CardName.MARKET,      # +1 carte, +1 action, +1 buy, +1$
        CardName.LABORATORY,  # +2 cartes, +1 action
        CardName.VILLAGE,     # +2 actions, +1 carte
        CardName.FESTIVAL,    # +2 actions, +1 buy, +2$
        CardName.HIRELING,    # terminal (durée)
        CardName.WITCH,       # terminal
        CardName.SMITHY,      # terminal
        CardName.WOODCUTTER,  # terminal
    ]

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
    # Decide mode: engine-first or aggressive Duchy-steal
    # --------------------
    aggressive_mode = False
    if prov_left <= PROV_THRESHOLD:
        aggressive_mode = True
    if (max_opponent_score - my_score) >= SCORE_DELTA:
        aggressive_mode = True

    # also consider buying Duchy opportunistically if we have many buys and medium money
    # engine condition: do we realistically have enough output to buy provinces reliably?
    engine_ready = (money_available() >= ENGINE_PROVINCE_MONEY) or (treasure_value + ts["coins_bonus"] >= 8 and ts["buys"] >= 1)

    print(f"[play] mode decision -> aggressive={aggressive_mode} engine_ready={engine_ready} money_avail={money_available()}")

    # --------------------
    # Decide mode: engine-first or aggressive Duchy-steal
    # --------------------
    aggressive_mode = False
    if prov_left <= PROV_THRESHOLD:
        aggressive_mode = True
    if (max_opponent_score - my_score) >= SCORE_DELTA:
        aggressive_mode = True

    # also consider buying Duchy opportunistically if we have many buys and medium money
    engine_ready = (money_available() >= ENGINE_PROVINCE_MONEY) or (treasure_value + ts["coins_bonus"] >= 8 and ts["buys"] >= 1)

    print(f"[play] mode decision -> aggressive={aggressive_mode} engine_ready={engine_ready} money_avail={money_available()}")

    # --------------------
    # PHASE ACHAT — helpers thread-safe
    # --------------------
    def can_buy(c: CardName) -> bool:
        # Lecture figée de l'état sous verrou
        with with_game_lock(game_id):
            ts_local = _sess(game_id).copy()
        return ts_local["buys"] > 0 and stock.get(c, 0) and \
            (hq(CardName.COPPER)*1 + hq(CardName.SILVER)*2 + hq(CardName.GOLD)*3
                + ts_local["coins_bonus"] - ts_local["coins_spent"]) >= COST[c]

    def do_buy(c: CardName) -> DopynionResponseStr:
        cost = COST[c]
        with with_game_lock(game_id):
            ts2 = _sess(game_id)
            if ts2["buys"] <= 0 or ts2["coins_spent"] + cost > \
            (hq(CardName.COPPER)*1 + hq(CardName.SILVER)*2 + hq(CardName.GOLD)*3 + ts2["coins_bonus"]):
                raise HTTPException(status_code=409, detail="Concurrent state changed: cannot buy now")
            ts2["buys"] -= 1
            ts2["coins_spent"] += cost
        inc_owned(game_id, c)   # déjà sous verrou dans l'implémentation
        print(f"[buy] BUY {c.name} cost={cost}")
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {c.name}")

    if True:  # on n'utilise plus 'if ts["buys"] > 0' sans verrou
        with with_game_lock(game_id):
            turn_no = _sess(game_id).get("turn", 1)
        enemy_alive = any("equipe3" in (getattr(p, "name", "") or "").lower() for p in game.players)

        vg_cnt  = owned(game_id, CardName.VILLAGE)
        mk_cnt  = owned(game_id, CardName.MARKET)
        sm_cnt  = owned(game_id, CardName.SMITHY)
        wt_cnt  = owned(game_id, CardName.WITCH)
        lab_cnt = owned(game_id, CardName.LABORATORY)
        gd_cnt  = owned(game_id, CardName.GOLD)
        rc_cnt  = owned(game_id, CardName.HIRELING)

        prov_left     = stock.get(CardName.PROVINCE, 0)
        curses_left   = stock.get(CardName.CURSE, 0) if CardName.CURSE in stock else 0
        villages_left = stock.get(CardName.VILLAGE, 0) if CardName.VILLAGE in stock else 0
        AGGRO_DUCHY   = (prov_left <= 4) or ((max_opponent_score - my_score) >= 4)

        print(f"[buy] t={turn_no} prov={prov_left} curses={curses_left} "
            f"owned RC={rc_cnt} VG={vg_cnt} MK={mk_cnt} LAB={lab_cnt} WT={wt_cnt} SM={sm_cnt} GOLD={gd_cnt} "
            f"villages_left={villages_left} enemy_alive={enemy_alive}")
        # ===== 0) Province si possible (toujours)
        if can_buy(CardName.PROVINCE):
            return do_buy(CardName.PROVINCE)

        # ===== 1) EARLY GAME — anti-Équipe3 + installation HIRELING
        if turn_no <= 8:
            # 1a) Si aucune Witch et des Curses restent: Witch d'abord
            if curses_left > 0 and wt_cnt < 1 and can_buy(CardName.WITCH):
                return do_buy(CardName.WITCH)

            # 1b) À 6$ : HIRELING > GOLD (cap 2 en early)
            if can_buy(CardName.HIRELING) and rc_cnt < 2:
                return do_buy(CardName.HIRELING)

            # 1c) À 5$ : Market (cap 2)
            if can_buy(CardName.MARKET) and mk_cnt < 2:
                return do_buy(CardName.MARKET)

            # 1d) Gold (cap 1 en early)
            if can_buy(CardName.GOLD) and gd_cnt < 1:
                return do_buy(CardName.GOLD)

            # 1e) Silver par défaut
            if can_buy(CardName.SILVER):
                return do_buy(CardName.SILVER)

        # ===== 2) MID GAME — spam curse + stack HIRELING, puis deny Village
        if curses_left > 0:
            # 2a) 2e Witch (cap 2) — cap 3 si l’adversaire manque de Villages
            cap_witch = 3 if (enemy_alive and villages_left <= 7) else 2
            if can_buy(CardName.WITCH) and wt_cnt < cap_witch:
                return do_buy(CardName.WITCH)

            # 2b) Stabilité: Market puis Laboratory (cap 2 chacun)
            if can_buy(CardName.MARKET) and mk_cnt < 2:
                return do_buy(CardName.MARKET)
            if can_buy(CardName.LABORATORY) and lab_cnt < 2:
                return do_buy(CardName.LABORATORY)

            # 2c) HIRELING à 6$ (cap 3 global)
            if can_buy(CardName.HIRELING) and rc_cnt < 3:
                return do_buy(CardName.HIRELING)

            # 2d) Deny Village (cap 2 chez nous)
            if enemy_alive and villages_left > 0 and vg_cnt < 2 and can_buy(CardName.VILLAGE):
                return do_buy(CardName.VILLAGE)

        # ===== 3) FIN DES CURSES — convertir l’avantage en points / tempo
        if curses_left == 0 and enemy_alive and villages_left <= 2:
            if can_buy(CardName.PROVINCE):
                return do_buy(CardName.PROVINCE)
            if AGGRO_DUCHY and can_buy(CardName.DUCHY):
                return do_buy(CardName.DUCHY)

        # ===== 4) PLAN STANDARD (fallback)
        # Province
        if can_buy(CardName.PROVINCE):
            return do_buy(CardName.PROVINCE)

        # Duchy si rattrapage / fin
        if AGGRO_DUCHY and can_buy(CardName.DUCHY):
            return do_buy(CardName.DUCHY)

        # À 6$ : HIRELING (cap 3) > GOLD (cap 2)
        if can_buy(CardName.HIRELING) and rc_cnt < 3:
            return do_buy(CardName.HIRELING)
        if can_buy(CardName.GOLD) and gd_cnt < 2:
            return do_buy(CardName.GOLD)

        # À 5$ : Market (cap 2) > Laboratory (cap 2) > Witch (si Curses restent et < 2)
        if can_buy(CardName.MARKET) and mk_cnt < 2:
            return do_buy(CardName.MARKET)
        if can_buy(CardName.LABORATORY) and lab_cnt < 2:
            return do_buy(CardName.LABORATORY)
        if curses_left > 0 and can_buy(CardName.WITCH) and wt_cnt < 2:
            return do_buy(CardName.WITCH)

        # À 4$ : Smithy (cap 2) seulement si on a déjà +Actions
        if can_buy(CardName.SMITHY) and (vg_cnt + mk_cnt) >= 1 and sm_cnt < 2:
            return do_buy(CardName.SMITHY)

        # À 3$ : Silver
        if can_buy(CardName.SILVER):
            return do_buy(CardName.SILVER)

        # Duchy opportuniste
        if can_buy(CardName.DUCHY):
            return do_buy(CardName.DUCHY)

        # Estate tardif (éviter en early)
        if turn_no > 10 and can_buy(CardName.ESTATE):
            return do_buy(CardName.ESTATE)


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
