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
    return "Rhum & ruin"


@app.get("/start_game")
def start_game(game_id: GameIdDependency) -> DopynionResponseStr:
    return DopynionResponseStr(game_id=game_id, decision="OK")


@app.get("/start_turn")
def start_turn(game_id: GameIdDependency) -> DopynionResponseStr:
    init_turn_state(game_id)
    return DopynionResponseStr(game_id=game_id, decision="OK")


@app.post("/play")
def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    me = next((p for p in game.players if p.hand is not None), None)
    if not me or not me.hand:
        print(f"[play] game={game_id} no visible hand -> END_TURN")
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    hand = me.hand.quantities        # dict[CardName, int]
    stock = game.stock.quantities    # dict[CardName, int]
    ts = get_turn_state(game_id)

    def hq(c: CardName) -> int: return hand.get(c, 0)
    def in_stock(c: CardName) -> bool: return stock.get(c, 0) > 0
    def money_treasures() -> int:
        return hq(CardName.COPPER)*1 + hq(CardName.SILVER)*2 + hq(CardName.GOLD)*3
    def money_available() -> int:
        # argent en main + bonus actions - déjà dépensé ce tour
        return money_treasures() + ts["coins_bonus"] - ts["coins_spent"]

    print(f"[play] game={game_id} state_before: actions={ts['actions']} buys={ts['buys']} "
          f"bonus={ts['coins_bonus']} spent={ts['coins_spent']} "
          f"hand={{Cu:{hq(CardName.COPPER)}, Si:{hq(CardName.SILVER)}, Go:{hq(CardName.GOLD)}, "
          f"Vi:{hq(CardName.VILLAGE)}, Sm:{hq(CardName.SMITHY)}, Ma:{hq(CardName.MARKET)}, Fe:{hq(CardName.FESTIVAL)}}} "
          f"stock_Prov={stock.get(CardName.PROVINCE,0)}")

    # ---- PHASE ACTION ----
    if ts["actions"] > 0:
        action_priority = [
    CardName.VILLAGE,
    CardName.FESTIVAL,
    CardName.MARKET,
    CardName.LABORATORY,
    CardName.WITCH,      # ⬅️ on lance l’attaque après avoir sécurisé les +Actions
    CardName.SMITHY,
    CardName.WOODCUTTER,
]

        for a in action_priority:
            if hq(a) > 0 and a.name in EFFECTS:
                acts, buys, coins, _ = EFFECTS[a.name]
                ts["actions"] -= 1
                ts["actions"] += acts
                ts["buys"]    += buys
                ts["coins_bonus"] += coins
                print(f"[play] ACTION {a.name} | +acts={acts} +buys={buys} +$bonus={coins} "
                      f"-> actions={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']}")
                return DopynionResponseStr(game_id=game_id, decision=f"ACTION {a.name}")

    # ---- PHASE ACHAT ----
    if ts["buys"] > 0:
        avail = money_available()
        print(f"[play] BUY phase | money_treasures={money_treasures()} bonus={ts['coins_bonus']} "
              f"spent={ts['coins_spent']} -> available={avail}")

        def try_buy(card: CardName) -> DopynionResponseStr | None:
            cost = COST[card]
            if ts["buys"] <= 0:
                return None
            if not in_stock(card):
                return None
            if avail < cost:
                return None
            # OK : on achète
            ts["buys"] -= 1
            ts["coins_spent"] += cost
            print(f"[play] BUY {card.name} (cost={cost}) -> buys={ts['buys']} spent={ts['coins_spent']} "
                  f"available_now={money_available()}")
            return DopynionResponseStr(game_id=game_id, decision=f"BUY {card.name}")

        # 1) Province si possible
        r = try_buy(CardName.PROVINCE)
        if r: return r

        # 2) Gold si possible
        r = try_buy(CardName.GOLD)
        if r: return r
        print(f"[play] WITCH available? stock_curse={stock.get(CardName.CURSE,0)} in_stock_witch={in_stock(CardName.WITCH)}")
        # 3) Palier 5$ : Market / Festival / Witch (si Curse dispo) puis Duchy
        five_cost_candidates = [CardName.MARKET, CardName.FESTIVAL]
        # Witch prioritaire si la pile Malédiction n'est pas vide
        if stock.get(CardName.CURSE, 0) > 0:
            five_cost_candidates.append(CardName.WITCH)
        five_cost_candidates.append(CardName.DUCHY)

        for cand in five_cost_candidates:
            r = try_buy(cand)
            if r: return r

        # 4) Smithy à 4$
        r = try_buy(CardName.SMITHY)
        if r: return r

        # 5) 3$ : Silver puis Village
        for cand in (CardName.SILVER, CardName.VILLAGE):
            r = try_buy(cand)
            if r: return r

        # 6) 2$ : Estate en dernier recours
        r = try_buy(CardName.ESTATE)
        if r: return r

    print(f"[play] nothing to do -> END_TURN | final_state: actions={ts['actions']} buys={ts['buys']} "
          f"bonus={ts['coins_bonus']} spent={ts['coins_spent']}")
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
