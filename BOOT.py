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
    init_turn_state(game_id)
    # compteur de tour
    s = SESS.setdefault(game_id, {"owned": {}})
    s["turn"] = s.get("turn", 0) + 1
    # info utile: HIRELINGs possédées
    rec_cnt = (s.get("owned") or {}).get(CardName.HIRELING, 0)
    print(f"[start_turn] game={game_id} turn={s['turn']} recrues_owned={rec_cnt}")
    return DopynionResponseStr(game_id=game_id, decision="OK")



# --- Constants de stratégie (tweakables) ---
PROV_THRESHOLD = 4            # si <= ce nombre de provinces, switch agressif
SCORE_DELTA = 4               # si un adversaire te distance >= ce delta, switch agressif
ENGINE_PROVINCE_MONEY = 12    # argent cible dans un tour pour considérer qu'on peut faire Province(s)
DOUBLE_PROVINCE_BUYS = 2      # si on a >= buys pour tenter double achat

@app.post("/play")
def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    me = next((p for p in game.players if p.hand is not None), None)
    if not me or not me.hand:
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    hand = me.hand.quantities
    stock = game.stock.quantities
    ts = get_turn_state(game_id)

    def hq(c: CardName) -> int: return hand.get(c, 0)
    def in_stock(c: CardName) -> bool: return stock.get(c, 0) > 0
    def money_treasures() -> int:
        return hq(CardName.COPPER)*1 + hq(CardName.SILVER)*2 + hq(CardName.GOLD)*3
    def money_available() -> int:
        return money_treasures() + ts["coins_bonus"] - ts["coins_spent"]

    def can_buy(c: CardName) -> bool:
        return ts["buys"] > 0 and stock.get(c, 0, ) and money_available() >= COST.get(c, 9999)

    def do_buy(c: CardName) -> DopynionResponseStr:
        cost = COST.get(c, 0)
        ts["buys"] -= 1
        ts["coins_spent"] += cost
        inc_owned(game_id, c)
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {c.name}")

    # Étape 2.1 — Classification automatique du type de partie
    action_cards = {CardName.FESTIVAL, CardName.LABORATORY, CardName.VILLAGE, CardName.WOODCUTTER,
                    CardName.MARKET, CardName.HIRELING, CardName.SMITHY}
    draw_cards = {CardName.LABORATORY, CardName.SMITHY, CardName.WITCH, CardName.HIRELING, CardName.MARKET}
    attack_cards = {CardName.WITCH, CardName.MILITIA, CardName.BUREAUCRAT}  # BUREAUCRAT manquant du stock ? à vérifier

    reserve = set(stock.keys())

    has_plus_action = any(c in reserve for c in action_cards)
    has_draw = any(c in reserve for c in draw_cards)
    has_attack = any(c in reserve for c in attack_cards)

    if has_plus_action and has_draw:
        game_type = "engine"
    elif not has_plus_action:
        game_type = "money"
    elif has_attack:
        game_type = "attack"
    else:
        game_type = "hybrid"

    print(f"[play] game_type={game_type}")

    # Choix de la stratégie
    if game_type == "money":
        # STRATÉGIE BIG MONEY
        turn_no = SESS[game_id].get("turn", 1)
        treasure = money_available()

        if treasure >= 8 and can_buy(CardName.PROVINCE):
            return do_buy(CardName.PROVINCE)
        if turn_no >= 6 and treasure >= 5 and can_buy(CardName.DUCHY):
            return do_buy(CardName.DUCHY)
        if turn_no >= 10 and treasure >= 2 and can_buy(CardName.ESTATE):
            return do_buy(CardName.ESTATE)
        if treasure >= 6 and can_buy(CardName.GOLD):
            return do_buy(CardName.GOLD)
        silver_count = owned(game_id, CardName.SILVER)
        if silver_count < 2 and treasure >= 3 and can_buy(CardName.SILVER):
            return do_buy(CardName.SILVER)

        terminal_actions = [CardName.MILITIA, CardName.BANDIT, CardName.WITCH, CardName.WOODCUTTER]
        for c in terminal_actions:
            if treasure >= COST.get(c, 999) and can_buy(c):
                return do_buy(c)

        if can_buy(CardName.SILVER):
            return do_buy(CardName.SILVER)

        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    # Si ce n'est pas une money game, ici on pourra intégrer Engine / Hybrid / Attack plus tard
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
