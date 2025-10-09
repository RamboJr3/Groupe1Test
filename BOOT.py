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
    return DopynionResponseStr(game_id=game_id, decision="OK")


@app.post("/play")

def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    # 1. Identification du joueur
    me = next((p for p in game.players if p.name == "Gin & ruin test"), None)
    if not me or not me.hand:
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")
    hand = me.hand.quantities
    stock = game.stock.quantities
    # 2. Phase d'action : joue la première action dispo selon priorité
    action_priority = [
        CardName.WITCH, CardName.VILLAGE, CardName.FESTIVAL, CardName.SMITHY,
        CardName.COUNCILROOM, CardName.DISTANTSHORE, CardName.FARMINGVILLAGE
    ]
    for card in action_priority:
        if hand.get(card, 0) > 0:
            return DopynionResponseStr(game_id=game_id, decision=f"ACTION {card.value}")
    # Sinon, joue n'importe quelle autre action (hors trésors et victoires)
    for card, qty in hand.items():
        if qty > 0 and card not in [
            CardName.COPPER, CardName.SILVER, CardName.GOLD,
            CardName.ESTATE, CardName.DUCHY, CardName.PROVINCE, CardName.CURSE
        ]:
            return DopynionResponseStr(game_id=game_id, decision=f"ACTION {card.value}")
    # 3. Phase d'achat inspirée de strategy.py/policy.py
    nb_copper = hand.get(CardName.COPPER, 0)
    nb_silver = hand.get(CardName.SILVER, 0)
    nb_gold = hand.get(CardName.GOLD, 0)
    money = nb_copper * 1 + nb_silver * 2 + nb_gold * 3
    # Province
    if money >= 8 and stock.get(CardName.PROVINCE, 0) > 0:
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {CardName.PROVINCE.value}")
    # Gold
    if money >= 6 and stock.get(CardName.GOLD, 0) > 0:
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {CardName.GOLD.value}")
    # Silver (conversion 3 Copper ou si possible)
    if money >= 3 and stock.get(CardName.SILVER, 0) > 0:
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {CardName.SILVER.value}")
    # Duchy
    if money >= 5 and stock.get(CardName.DUCHY, 0) > 0:
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {CardName.DUCHY.value}")
    # Estate
    if money >= 2 and stock.get(CardName.ESTATE, 0) > 0:
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {CardName.ESTATE.value}")
    # Sinon, END_TURN
    return DopynionResponseStr(game_id=game_id, decision="END_TURN")


@app.get("/end_game")
def end_game(game_id: GameIdDependency) -> DopynionResponseStr:
    return DopynionResponseStr(game_id=game_id, decision="OK")


@app.post("/confirm_discard_card_from_hand")
async def confirm_discard_card_from_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/discard_card_from_hand")
async def discard_card_from_hand(
    game_id: GameIdDependency,
    decision_input: Hand,
) -> DopynionResponseCardName:
    # Si une Province est en main, la défausser en priorité
    if CardName.PROVINCE in decision_input.hand:
        return DopynionResponseCardName(game_id=game_id, decision=CardName.PROVINCE)
    # Sinon, défausser la première carte
    return DopynionResponseCardName(game_id=game_id, decision=decision_input.hand[0])


@app.post("/confirm_trash_card_from_hand")
async def confirm_trash_card_from_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/trash_card_from_hand")
async def trash_card_from_hand(
    game_id: GameIdDependency,
    decision_input: Hand,
) -> DopynionResponseCardName:
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
