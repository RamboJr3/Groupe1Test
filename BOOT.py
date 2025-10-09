import html
from pathlib import Path
from typing import Annotated

from dopynion.data_model import (
    CardName,
    CardNameAndHand,
    Game,
    Hand,
    MoneyCardsInHand,
    PossibleCards,
)
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI()

# --- ÉTAT DE TOUR ---
SESS: dict[str, dict] = {}   # { game_id: {"actions": int, "buys": int, "coins_bonus": int, "coins_spent": int} }

def init_turn_state(game_id: str) -> None:
    SESS.setdefault(game_id, {"owned": {}, "turn": 0})
    SESS[game_id].update({"actions": 1, "buys": 1, "coins_bonus": 0, "coins_spent": 0})


def get_turn_state(game_id: str) -> dict:
    return SESS.setdefault(game_id, {"actions": 1, "buys": 1, "coins_bonus": 0, "coins_spent": 0})

# --- suivi du deck par partie (approx via achats) ---
# SESS[game_id] = {"actions":..., "buys":..., "coins_bonus":..., "coins_spent":..., "owned": {CardName: int}}
def inc_owned(game_id: str, card: CardName) -> None:
    s = SESS.setdefault(game_id, {"actions":1,"buys":1,"coins_bonus":0,"coins_spent":0,"owned":{}})
    s.setdefault("owned", {})
    s["owned"][card] = s["owned"].get(card, 0) + 1

def owned(game_id: str, card: CardName) -> int:
    s = SESS.get(game_id) or {}
    o = s.get("owned") or {}
    return o.get(card, 0)


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
    CardName.WITCH: 5,          # ⬅️ NEW
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
    # (ré)initialise l’état de tour, préserve le suivi du deck
    init_turn_state(game_id)
    # compteur de tours (sert à éviter Estate early, etc.)
    SESS[game_id]["turn"] = SESS[game_id].get("turn", 0) + 1
    print(f"[turn] game={game_id} start turn #{SESS[game_id]['turn']}")
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
    ts = get_turn_state(game_id)

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
    # PHASE ACTION (only if actions > 0)
    # --------------------
    if ts["actions"] > 0:
        # priority tuned for engine-first but allow attacking (WITCH) after +actions
        action_priority = [
    CardName.VILLAGE,     # +2 actions, +1 carte
    CardName.MARKET,      # +1 carte, +1 action, +1 achat, +1$
    CardName.LABORATORY,  # +2 cartes, +1 action
    CardName.FESTIVAL,    # +2 actions, +1 achat, +2$
    CardName.WITCH,       # terminal, +2 cartes (attaque)
    CardName.SMITHY,      # terminal, +3 cartes
    CardName.WOODCUTTER,  # terminal, +1 achat, +2$
]



        for a in action_priority:
            if hq(a) > 0 and a.name in EFFECTS:
                acts, buys, coins, _ = EFFECTS[a.name]
                ts["actions"] -= 1
                ts["actions"] += acts
                ts["buys"] += buys
                ts["coins_bonus"] += coins
                print(f"[play] ACTION {a.name} applied -> +acts={acts} +buys={buys} +$={coins} => actions={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']}")
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
    # PHASE ACHAT — helpers D'ABORD (pour éviter NameError)
    # --------------------
    def can_buy(c: CardName) -> bool:
        return ts["buys"] > 0 and stock.get(c, 0) and money_available() >= COST[c]

    def do_buy(c: CardName) -> DopynionResponseStr:
        cost = COST[c]
        ts["buys"] -= 1
        ts["coins_spent"] += cost
        inc_owned(game_id, c)
        print(f"[buy] BUY {c.name} cost={cost} -> buys_left={ts['buys']} spent={ts['coins_spent']} avail_now={money_available()}")
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {c.name}")

    if ts["buys"] > 0:
        turn_no = SESS[game_id].get("turn", 1)
        enemy_alive = any("equipe3" in (getattr(p, "name", "") or "").lower() for p in game.players)

        vg_cnt  = owned(game_id, CardName.VILLAGE)
        mk_cnt  = owned(game_id, CardName.MARKET)
        sm_cnt  = owned(game_id, CardName.SMITHY)
        wt_cnt  = owned(game_id, CardName.WITCH)
        lab_cnt = owned(game_id, CardName.LABORATORY)
        au_cnt  = owned(game_id, CardName.SILVER) + owned(game_id, CardName.GOLD)
        gd_cnt  = owned(game_id, CardName.GOLD)

        avail_before = money_available()
        prov_left    = stock.get(CardName.PROVINCE, 0)
        curses_left  = stock.get(CardName.CURSE, 0) if CardName.CURSE in stock else 0
        AGGRO_DUCHY  = (prov_left <= 4) or ((max_opponent_score - my_score) >= 4)

        print(f"[buy] turn={turn_no} avail={avail_before} buys={ts['buys']} prov_left={prov_left} curses_left={curses_left} "
              f"owned: VG={vg_cnt} MK={mk_cnt} WT={wt_cnt} LAB={lab_cnt} SM={sm_cnt} GOLD={gd_cnt} AU={au_cnt} "
              f"aggro_duchy={AGGRO_DUCHY} enemy_alive={enemy_alive}")

        # ======== MODE CANCER + SPEED ciblé Équipe3MaGueule ========

        # (0a) Tant que Curses > 0 : sécuriser +Actions pour jouer nos Witches ET « deny » leur moteur
        if enemy_alive and curses_left > 0:
            # Deny/enable : vider Village (cap 3 chez nous)
            if stock.get(CardName.VILLAGE, 0) and vg_cnt < 3 and can_buy(CardName.VILLAGE):
                print("[anti-MaGueule] Deny engine -> BUY VILLAGE")
                return do_buy(CardName.VILLAGE)
            # Marché stabilise (cap 2) : +action +achat +$
            if mk_cnt < 2 and can_buy(CardName.MARKET):
                return do_buy(CardName.MARKET)
            # Witch pour arroser (cap 2 ici pour éviter d’être trop terminal, sauf fin de deny)
            if wt_cnt < 2 and can_buy(CardName.WITCH):
                return do_buy(CardName.WITCH)
            # Laboratory lisse sans terminal (cap 2)
            if lab_cnt < 2 and can_buy(CardName.LABORATORY):
                return do_buy(CardName.LABORATORY)

        # (0b) Si Curses vides ET pile Village basse -> on rush la fin
        if enemy_alive and curses_left == 0 and stock.get(CardName.VILLAGE, 0) is not None and stock.get(CardName.VILLAGE, 0) <= 2:
            if can_buy(CardName.PROVINCE):
                print("[anti-MaGueule] Endgame rush -> BUY PROVINCE")
                return do_buy(CardName.PROVINCE)
            if AGGRO_DUCHY and can_buy(CardName.DUCHY):
                print("[anti-MaGueule] Endgame rush -> BUY DUCHY")
                return do_buy(CardName.DUCHY)

        # ======== PLAN STANDARD optimisé “cancer-speed” ========

        # 1) Province si possible (toujours en premier hors rush-curse)
        if can_buy(CardName.PROVINCE):
            return do_buy(CardName.PROVINCE)

        # 2) Duchy tard (catch-up ou fin)
        if AGGRO_DUCHY and can_buy(CardName.DUCHY):
            return do_buy(CardName.DUCHY)

        # 3) À 5$ : priorité moteur non-terminal, puis attaque si Curses restent
        #    Market (cap 2) -> Laboratory (cap 2) -> Witch (cap 2)
        if can_buy(CardName.MARKET) and mk_cnt < 2:
            return do_buy(CardName.MARKET)
        if can_buy(CardName.LABORATORY) and lab_cnt < 2:
            return do_buy(CardName.LABORATORY)
        if curses_left > 0 and can_buy(CardName.WITCH) and wt_cnt < 2:
            return do_buy(CardName.WITCH)

        # 4) Or : cap 2 total pour enclencher les Provinces
        if can_buy(CardName.GOLD) and gd_cnt < 2:
            return do_buy(CardName.GOLD)

        # 5) À 4$ : Smithy (cap 2) seulement si on a déjà une source de +Actions
        if can_buy(CardName.SMITHY) and (vg_cnt + mk_cnt) >= 1 and sm_cnt < 2:
            return do_buy(CardName.SMITHY)

        # 6) À 3$ : Silver > (on évite Woodcutter terminal)
        if can_buy(CardName.SILVER):
            return do_buy(CardName.SILVER)

        # 7) Duchy opportuniste si rien d'autre
        if can_buy(CardName.DUCHY):
            return do_buy(CardName.DUCHY)

        # 8) Estate : éviter early ; on passe à $2 en début de partie
        if turn_no <= 10 and money_available() == 2:
            print("[pass] early $2 -> keep deck clean (no Estate)")
            # pas d’achat, on garde le deck propre
        else:
            if can_buy(CardName.ESTATE):
                return do_buy(CardName.ESTATE)

    print(f"[play] nothing to do -> END_TURN | state actions={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']} spent={ts['coins_spent']}")
    return DopynionResponseStr(game_id=game_id, decision="END_TURN")




@app.get("/end_game")
def end_game(game_id: GameIdDependency) -> DopynionResponseStr:
    SESS.pop(game_id, None)
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
