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

# État de tour par partie
SESS: dict[str, dict] = {}   # { game_id: {"actions": int, "buys": int, "coins_bonus": int} }

def init_turn_state(game_id: str) -> None:
    SESS[game_id] = {"actions": 1, "buys": 1, "coins_bonus": 0}

def get_turn_state(game_id: str) -> dict:
    return SESS.setdefault(game_id, {"actions": 1, "buys": 1, "coins_bonus": 0})

# Effets connus (uniquement les cartes jouables pour le moment)
# (actions, buys, coins_bonus, draw) – la pioche est gérée par l’arbitre entre deux /play
EFFECTS: dict[str, tuple[int,int,int,int]] = {
    "FESTIVAL":   (2, 1, 2, 0),
    "LABORATORY": (1, 0, 0, 2),
    "VILLAGE":    (2, 0, 0, 1),
    "WOODCUTTER": (0, 1, 2, 0),
    "SMITHY":     (0, 0, 0, 3),
    "MARKET":     (1, 1, 1, 1),
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


@app.post("/play")
def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    # 0) Mon joueur = celui qui a une main visible
    me = next((p for p in game.players if p.hand is not None), None)
    if not me or not me.hand:
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    hand = me.hand.quantities      # dict[CardName, int]
    stock = game.stock.quantities  # dict[CardName, int]
    ts = get_turn_state(game_id)

    # Helpers
    def hq(card: CardName) -> int:
        return hand.get(card, 0)

    def in_stock(card: CardName) -> bool:
        return stock.get(card, 0) > 0

    # ---- PHASE ACTION (une seule à la fois) ----
    if ts["actions"] > 0:
        # Ne proposer que des actions dont l'effet est connu dans EFFECTS
        action_priority = [
            CardName.VILLAGE,
            CardName.FESTIVAL,
            CardName.MARKET,
            CardName.LABORATORY,
            CardName.SMITHY,
            CardName.WOODCUTTER,
        ]
        for a in action_priority:
            if hq(a) > 0 and a.name in EFFECTS:
                acts, buys, coins, _ = EFFECTS[a.name]
                # Consomme 1 action, applique les bonus
                ts["actions"] -= 1
                ts["actions"] += acts
                ts["buys"]    += buys
                ts["coins_bonus"] += coins
                # IMPORTANT : envoyer l'identifiant d'énum (UPPER), pas la value minuscule
                return DopynionResponseStr(game_id=game_id, decision=f"ACTION {a.name}")

    # ---- PHASE ACHAT (un seul par appel) ----
    if ts["buys"] > 0:
        money = (
            hq(CardName.COPPER) * 1 +
            hq(CardName.SILVER) * 2 +
            hq(CardName.GOLD)   * 3 +
            ts["coins_bonus"]        # bonus de Festival/Market/Woodcutter joués ce tour
        )

        # 1) Province en priorité si possible
        if money >= 8 and in_stock(CardName.PROVINCE):
            ts["buys"] -= 1
            return DopynionResponseStr(game_id=game_id, decision=f"BUY {CardName.PROVINCE.name}")

        # 2) Payload économique
        if money >= 6 and in_stock(CardName.GOLD):
            ts["buys"] -= 1
            return DopynionResponseStr(game_id=game_id, decision=f"BUY {CardName.GOLD.name}")

        # 3) Moteur d'actions / achats à 5$
        if money >= 5:
            for cand in (CardName.MARKET, CardName.FESTIVAL, CardName.DUCHY):
                if in_stock(cand):
                    ts["buys"] -= 1
                    return DopynionResponseStr(game_id=game_id, decision=f"BUY {cand.name}")

        # 4) Pioche brute à 4$
        if money >= 4 and in_stock(CardName.SMITHY):
            ts["buys"] -= 1
            return DopynionResponseStr(game_id=game_id, decision=f"BUY {CardName.SMITHY.name}")

        # 5) 3$ : Silver puis Village
        if money >= 3:
            for cand in (CardName.SILVER, CardName.VILLAGE):
                if in_stock(cand):
                    ts["buys"] -= 1
                    return DopynionResponseStr(game_id=game_id, decision=f"BUY {cand.name}")

        # 6) 2$ : Estate en dernier recours
        if money >= 2 and in_stock(CardName.ESTATE):
            ts["buys"] -= 1
            return DopynionResponseStr(game_id=game_id, decision=f"BUY {CardName.ESTATE.name}")

    # ---- Fin de tour si plus d'action ni d'achat possible ----
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
async def discard_card_from_hand(
    game_id: GameIdDependency,
    decision_input: Hand,
) -> DopynionResponseCardName:
    # NE JAMAIS défausser Province en priorité : on écarte d’abord les cartes "mortes"
    order = [
        CardName.CURSE,
        CardName.ESTATE,
        CardName.COPPER,
        CardName.SILVER,
        CardName.GOLD,
        # puis le reste
    ]
    # Choisir la première présente dans l'ordre ci-dessus
    for c in order:
        if c in decision_input.hand:
            return DopynionResponseCardName(game_id=game_id, decision=c)
    # sinon, par défaut la première
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
    # Si on doit TRASH : curser > copper > estate, JAMAIS Province
    for c in [CardName.CURSE, CardName.COPPER, CardName.ESTATE]:
        if c in decision_input.hand:
            return DopynionResponseCardName(game_id=game_id, decision=c)
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
