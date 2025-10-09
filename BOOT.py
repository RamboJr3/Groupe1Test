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
    SESS[game_id] = {"actions": 1, "buys": 1, "coins_bonus": 0, "coins_spent": 0}

def get_turn_state(game_id: str) -> dict:
    return SESS.setdefault(game_id, {"actions": 1, "buys": 1, "coins_bonus": 0, "coins_spent": 0})

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
    init_turn_state(game_id)
    return DopynionResponseStr(game_id=game_id, decision="OK")


# --- Constants de stratégie (tweakables) ---
PROV_THRESHOLD = 4            # si <= ce nombre de provinces, switch agressif
SCORE_DELTA = 4               # si un adversaire te distance >= ce delta, switch agressif
ENGINE_PROVINCE_MONEY = 12    # argent cible dans un tour pour considérer qu'on peut faire Province(s)
DOUBLE_PROVINCE_BUYS = 2      # si on a >= buys pour tenter double achet

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
            CardName.VILLAGE, CardName.FESTIVAL, CardName.MARKET,
            CardName.LABORATORY, CardName.WITCH, CardName.SMITHY, CardName.WOODCUTTER
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
    # PHASE BUY (adaptive)
    # --------------------
    if ts["buys"] > 0:
        avail_before = money_available()
        print(f"[play] BUY phase -> available={avail_before} buys={ts['buys']} prov_left={prov_left}")

        def can_buy(card: CardName) -> bool:
            return ts["buys"] > 0 and in_stock(card) and money_available() >= COST.get(card, 9999)

        def do_buy(card: CardName) -> DopynionResponseStr:
            cost = COST[card]
            ts["buys"] -= 1
            ts["coins_spent"] += cost
            print(f"[play] BUY {card.name} (cost={cost}) -> buys_left={ts['buys']} spent={ts['coins_spent']} avail_now={money_available()}")
            return DopynionResponseStr(game_id=game_id, decision=f"BUY {card.name}")

        # 1) If engine ready strongly -> prioritize Province(s)
        if engine_ready and can_buy(CardName.PROVINCE):
            return do_buy(CardName.PROVINCE)

        # 2) Aggressive mode: try Duchy early to deny points
        if aggressive_mode:
            if can_buy(CardName.DUCHY):
                return do_buy(CardName.DUCHY)
            # if cannot buy Duchy, still consider Province if possible
            if can_buy(CardName.PROVINCE):
                return do_buy(CardName.PROVINCE)

        # 3) Normal engine build: aim to get motor pieces (Market/Festival/Woodcutter), then Gold, then Smithy
        # Prioritize Market (best all-around), Festival (speed), Woodcutter (cheap +buy)
        for cand in (CardName.MARKET, CardName.FESTIVAL, CardName.WOODCUTTER):
            # buy Market/Festival/Woodcutter only if it helps immediate or near-future buys:
            # e.g., if money_available >= COST OR we have actions to play them later
            if can_buy(cand):
                # small safeguard: don't buy infinite Markets if we already have many buy bonus this turn
                return do_buy(cand)

        # 4) If we have moderate available and want to secure score: buy Gold then Province/Duchy opportunistically
        if can_buy(CardName.GOLD):
            return do_buy(CardName.GOLD)

        # if we got >=1 buys and still can pick duchy opportunistically (e.g., after previous buys in same turn)
        if ts["buys"] >= 1 and can_buy(CardName.DUCHY):
            return do_buy(CardName.DUCHY)

        # 5) Smithy to draw into more treasure next calls
        if can_buy(CardName.SMITHY):
            return do_buy(CardName.SMITHY)

        # 6) fallback economy: Silver or Village
        for cand in (CardName.SILVER, CardName.VILLAGE):
            if can_buy(cand):
                return do_buy(cand)

        # 7) last resort: Estate
        if can_buy(CardName.ESTATE):
            return do_buy(CardName.ESTATE)

    # nothing else -> end turn
    print(f"[play] nothing to do -> END_TURN | final state actions={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']} spent={ts['coins_spent']}")
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
