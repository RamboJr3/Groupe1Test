import html
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from dopynion.data_model import (
    CardName,
    CardNameAndHand,
    Cards,
    Game,
    Hand,
    MoneyCardsInHand,
    PossibleCards,
)
from dopynion.cards import Card
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
# Root page: show code
#####################################################


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    header = (
        "<html><head><title>Dopynion strategy</title></head><body>"
        "<h1>Dopynion documentation</h1>"
        "<h2>API documentation</h2>"
        '<p><a href="/docs">Read the documentation.</a></p>'
        "<h2>Code</h2>"
        "<p>The code of this website is:</p>"
        "<pre>"
    )
    footer = "</pre></body></html>"
    return header + html.escape(Path(__file__).read_text(encoding="utf-8")) + footer


#####################################################
# Helpers: card introspection
#####################################################


@lru_cache(maxsize=None)
def card_class(card_name: CardName) -> type[Card]:
    """Return the dopynion Card subclass for a given CardName."""
    return Card.types.get(card_name, Card)


def is_action(card_name: CardName) -> bool:
    return bool(getattr(card_class(card_name), "is_action", False))


def is_treasure(card_name: CardName) -> bool:
    return bool(getattr(card_class(card_name), "is_treasure", False))


def is_victory(card_name: CardName) -> bool:
    return bool(getattr(card_class(card_name), "is_victory", False))


def cost(card_name: CardName) -> int:
    return int(getattr(card_class(card_name), "cost", 0))


def money_value(card_name: CardName) -> int:
    return int(getattr(card_class(card_name), "money", 0))


def more_actions(card_name: CardName) -> int:
    return int(getattr(card_class(card_name), "more_actions", 0))


def more_buys(card_name: CardName) -> int:
    return int(getattr(card_class(card_name), "more_purchases", 0))


def more_money(card_name: CardName) -> int:
    return int(getattr(card_class(card_name), "more_money", 0))


def victory_points(card_name: CardName) -> int:
    return int(getattr(card_class(card_name), "victory_points", 0))


#####################################################
# Evaluation helpers for discard/gain
#####################################################


def eval_badness_for_discard(card_name: CardName) -> float:
    """
    Score of 'badness' for discarding / trashing.
    Higher = we are happier to get rid of the card.
    """
    cls = card_class(card_name)
    score = 0.0

    if is_victory(card_name):
        vp = victory_points(card_name)
        if vp < 0:
            score += 200  # Curse: awful
        else:
            score += 50 + vp  # Estates / Duchies / etc. are often dead in hand

    if is_treasure(card_name):
        # Treasures are usually good to keep
        score -= money_value(card_name) * 20

    if is_action(card_name):
        mc = getattr(cls, "more_cards_from_deck", 0)
        ma = more_actions(card_name)
        mm = more_money(card_name)
        score -= mc * 15
        score -= ma * 10
        score -= mm * 12

    score -= cost(card_name) * 1.0
    return score


def eval_goodness_for_gain(card_name: CardName) -> float:
    """
    Score of 'goodness' for gaining a card (in discard or deck).
    Higher = we prefer to gain the card.
    """
    cls = card_class(card_name)
    score = 0.0

    if is_treasure(card_name):
        score += money_value(card_name) * 30

    if is_action(card_name):
        mc = getattr(cls, "more_cards_from_deck", 0)
        ma = more_actions(card_name)
        mm = more_money(card_name)
        score += mc * 20
        score += ma * 12
        score += mm * 15

    if is_victory(card_name):
        vp = victory_points(card_name)
        if vp < 0:
            score -= 200
        else:
            score += vp * 10

    score += cost(card_name) * 1.5
    return score


def pick_worst_card(cards: list[CardName]) -> CardName:
    return max(cards, key=eval_badness_for_discard)


def pick_best_card(cards: list[CardName]) -> CardName:
    return max(cards, key=eval_goodness_for_gain)


#####################################################
# State tracking per game
#####################################################


@dataclass
class TurnState:
    actions: int = 1
    buys: int = 1
    extra_money: int = 0  # money from +$ actions (Festival, Militia, Poacher, ...)


@dataclass
class GameState:
    turn_number: int = 0
    turn_state: TurnState = field(default_factory=TurnState)
    # only ce qu'on a acheté (on ne connaît pas exactement le deck initial)
    owned_cards: Counter = field(default_factory=Counter)


GAME_STATES: dict[str, GameState] = {}


def get_state(game_id: str) -> GameState:
    if game_id not in GAME_STATES:
        GAME_STATES[game_id] = GameState()
    return GAME_STATES[game_id]


def reset_turn_state(game_id: str) -> None:
    state = get_state(game_id)
    state.turn_number += 1
    state.turn_state = TurnState()


#####################################################
# Strategy core helpers
#####################################################


def cards_to_list(cards: Cards | None) -> list[CardName]:
    if cards is None:
        return []
    result: list[CardName] = []
    for card_name, qty in cards.quantities.items():
        result.extend([card_name] * qty)
    return result


def hand_to_list(hand: Hand) -> list[CardName]:
    return list(hand.hand)


def total_treasure_in_hand(hand_cards: list[CardName]) -> int:
    return sum(money_value(cn) for cn in hand_cards if is_treasure(cn))


def choose_action_to_play(
    hand_cards: list[CardName], state: GameState
) -> CardName | None:
    """Pick the best action to play this step, or None to move to buy phase."""
    actions_left = state.turn_state.actions
    if actions_left <= 0:
        return None

    action_cards = [cn for cn in hand_cards if is_action(cn)]
    if not action_cards:
        return None

    # Base priorities tuned roughly for this Kingdom
    PRIORITY = {
        CardName.MARQUIS: 100,
        CardName.FESTIVAL: 90,
        CardName.VILLAGE: 80,
        CardName.COUNCILROOM: 75,
        CardName.POACHER: 70,
        CardName.SMITHY: 65,
        CardName.MILITIA: 60,
        CardName.SWAP: 60,
        CardName.CELLAR: 40,
        CardName.ADVENTURER: 35,
    }

    # Combien de "mauvaises" cartes en main (pour savoir si Cellar vaut le coup)
    bad_cards_in_hand = sum(
        1 for cn in hand_cards if eval_badness_for_discard(cn) > 30
    )

    best_card: CardName | None = None
    best_score = float("-inf")

    for cn in action_cards:
        score = PRIORITY.get(cn, 0)

        # Éviter de jouer Cellar s'il n'y a rien à cycler
        if cn is CardName.CELLAR and bad_cards_in_hand < 2:
            score -= 100

        # Terminal vs non-terminal
        if actions_left == 1 and more_actions(cn) == 0:
            score -= 25

        # Militia est très forte en early game contre Big Money
        if cn is CardName.MILITIA and state.turn_number <= 10:
            score += 10

        if score > best_score:
            best_score = score
            best_card = cn

    if best_card is None or best_score <= 0:
        return None
    return best_card


def choose_buy(
    coins: int,
    stock: dict[CardName, int],
    state: GameState,
) -> CardName | None:
    """Choose which card to buy given guaranteed coins and current stock."""

    def available(cn: CardName) -> bool:
        return stock.get(cn, 0) > 0 and cost(cn) <= coins

    owned = state.owned_cards
    province_left = stock.get(CardName.PROVINCE, 0)
    colony_left = stock.get(CardName.COLONY, 0)
    colony_game = CardName.COLONY in stock
    endgame = province_left <= 4 or colony_left <= 4

    # 1) Colony / Province priority
    if colony_game:
        if available(CardName.COLONY):
            return CardName.COLONY
        if available(CardName.PLATINUM):
            return CardName.PLATINUM
        if endgame and available(CardName.PROVINCE):
            return CardName.PROVINCE
    else:
        if available(CardName.PROVINCE):
            return CardName.PROVINCE

    # 2) Endgame green
    if endgame:
        if available(CardName.DUCHY) and coins >= 5:
            return CardName.DUCHY
        if available(CardName.ESTATE) and coins >= 2:
            return CardName.ESTATE

    # 3) Key kingdom cards vs Big Money
    # D'abord les attaques (Militia) pour casser les mains adverses
    if available(CardName.MILITIA) and owned[CardName.MILITIA] < 2:
        return CardName.MILITIA

    # Puis les pièces de moteur
    if available(CardName.FESTIVAL) and owned[CardName.FESTIVAL] < 2:
        return CardName.FESTIVAL
    if available(CardName.MARQUIS) and owned[CardName.MARQUIS] < 2:
        return CardName.MARQUIS
    if available(CardName.POACHER) and owned[CardName.POACHER] < 2:
        return CardName.POACHER
    if available(CardName.VILLAGE) and owned[CardName.VILLAGE] < 2:
        return CardName.VILLAGE
    if available(CardName.COUNCILROOM) and owned[CardName.COUNCILROOM] < 2:
        return CardName.COUNCILROOM
    if available(CardName.SMITHY) and owned[CardName.SMITHY] < 2:
        return CardName.SMITHY
    if available(CardName.SWAP) and owned[CardName.SWAP] < 1:
        return CardName.SWAP

    # Un seul Cellar pour smoother les mains
    if available(CardName.CELLAR) and owned[CardName.CELLAR] < 1:
        return CardName.CELLAR

    # Éventuellement un CursedGold (risqué, mais fort) hors endgame
    if (
        available(CardName.CURSEDGOLD)
        and owned[CardName.CURSEDGOLD] < 1
        and not endgame
    ):
        return CardName.CURSEDGOLD

    # 4) Pure economy
    if available(CardName.PLATINUM) and coins >= cost(CardName.PLATINUM):
        return CardName.PLATINUM
    if available(CardName.GOLD) and coins >= cost(CardName.GOLD):
        return CardName.GOLD
    if available(CardName.SILVER) and coins >= cost(CardName.SILVER):
        return CardName.SILVER

    # 5) Fallback: green cheap en toute fin
    if available(CardName.ESTATE) and endgame and coins >= 2:
        return CardName.ESTATE

    return None


#####################################################
# Public API: name & lifecycle
#####################################################


PLAYER_BASE_NAME = "Monty_Python"


@app.get("/name")
def name() -> str:
    return PLAYER_BASE_NAME


@app.get("/start_game")
def start_game(game_id: GameIdDependency) -> DopynionResponseStr:
    # Reset state for this game
    GAME_STATES[game_id] = GameState()
    return DopynionResponseStr(game_id=game_id, decision="OK")


@app.get("/start_turn")
def start_turn(game_id: GameIdDependency) -> DopynionResponseStr:
    reset_turn_state(game_id)
    return DopynionResponseStr(game_id=game_id, decision="OK")


@app.post("/play")
def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    state = get_state(game_id)
    ts = state.turn_state

    # Joueur actif = celui dont la main n'est pas None
    me = None
    for p in game.players:
        if p.hand is not None:
            me = p
            break

    if me is None:
        # Sécurité : on ne fait rien
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    hand_cards = cards_to_list(me.hand)
    stock_dict = game.stock.quantities

    # 1) Phase action : essayer de jouer une action
    action_to_play = choose_action_to_play(hand_cards, state)
    if action_to_play is not None and ts.actions > 0:
        ts.actions -= 1
        ts.actions += more_actions(action_to_play)
        ts.buys += more_buys(action_to_play)
        ts.extra_money += more_money(action_to_play)
        decision = f"ACTION {action_to_play.name}"
        return DopynionResponseStr(game_id=game_id, decision=decision)

    # 2) Phase achat
    if ts.buys <= 0:
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    coins = ts.extra_money + total_treasure_in_hand(hand_cards)
    buy_card = choose_buy(coins, stock_dict, state)

    if buy_card is None:
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    # Double-check légalité pour éviter l'élimination
    if stock_dict.get(buy_card, 0) <= 0 or cost(buy_card) > coins:
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    ts.buys -= 1
    state.owned_cards[buy_card] += 1
    decision = f"BUY {buy_card.name}"
    return DopynionResponseStr(game_id=game_id, decision=decision)


@app.get("/end_game")
def end_game(game_id: GameIdDependency) -> DopynionResponseStr:
    # Nettoyage mémoire
    GAME_STATES.pop(game_id, None)
    return DopynionResponseStr(game_id=game_id, decision="OK")


#####################################################
# Hooks pour discard / trash / gain / skip / upgrade
#####################################################


@app.post("/confirm_discard_card_from_hand")
async def confirm_discard_card_from_hand(
    game_id: GameIdDependency,
    decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    card = decision_input.card_name

    iv = is_victory(card)
    vp = victory_points(card)
    it = is_treasure(card)

    if iv and vp < 0:
        decision = True  # Curse
    elif iv and vp == 1 and not it:
        decision = True  # Estate early
    else:
        decision = False

    return DopynionResponseBool(game_id=game_id, decision=decision)


@app.post("/discard_card_from_hand")
async def discard_card_from_hand(
    game_id: GameIdDependency,
    decision_input: Hand,
) -> DopynionResponseCardName:
    hand_cards = hand_to_list(decision_input)
    choice = pick_worst_card(hand_cards)
    return DopynionResponseCardName(game_id=game_id, decision=choice)


@app.post("/confirm_trash_card_from_hand")
async def confirm_trash_card_from_hand(
    game_id: GameIdDependency,
    decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    card = decision_input.card_name
    iv = is_victory(card)
    vp = victory_points(card)
    it = is_treasure(card)
    m = money_value(card)

    if iv and vp < 0:
        decision = True  # Curse
    elif iv and vp == 1 and not it:
        decision = True  # Estate
    elif it and m == 0:
        decision = True  # Trésor bizarre à 0
    else:
        decision = False

    return DopynionResponseBool(game_id=game_id, decision=decision)


@app.post("/trash_card_from_hand")
async def trash_card_from_hand(
    game_id: GameIdDependency,
    decision_input: Hand,
) -> DopynionResponseCardName:
    hand_cards = hand_to_list(decision_input)
    choice = pick_worst_card(hand_cards)
    return DopynionResponseCardName(game_id=game_id, decision=choice)


@app.post("/confirm_discard_deck")
async def confirm_discard_deck(
    game_id: GameIdDependency,
) -> DopynionResponseBool:
    # Très conservateur : on ne jette pas tout le deck
    return DopynionResponseBool(game_id=game_id, decision=False)


@app.post("/choose_card_to_receive_in_discard")
async def choose_card_to_receive_in_discard(
    game_id: GameIdDependency,
    decision_input: PossibleCards,
) -> DopynionResponseCardName:
    choice = pick_best_card(decision_input.possible_cards)
    return DopynionResponseCardName(game_id=game_id, decision=choice)


@app.post("/choose_card_to_receive_in_deck")
async def choose_card_to_receive_in_deck(
    game_id: GameIdDependency,
    decision_input: PossibleCards,
) -> DopynionResponseCardName:
    choice = pick_best_card(decision_input.possible_cards)
    return DopynionResponseCardName(game_id=game_id, decision=choice)


@app.post("/skip_card_reception_in_hand")
async def skip_card_reception_in_hand(
    game_id: GameIdDependency,
    decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    card = decision_input.card_name
    iv = is_victory(card)
    vp = victory_points(card)

    decision = bool(iv and vp < 0)  # refuser uniquement les Curses
    return DopynionResponseBool(game_id=game_id, decision=decision)


@app.post("/trash_money_card_for_better_money_card")
async def trash_money_card_for_better_money_card(
    game_id: GameIdDependency,
    decision_input: MoneyCardsInHand,
) -> DopynionResponseCardName:
    if not decision_input.money_in_hand:
        # Cas limite : choisir Copper par défaut
        choice = CardName.COPPER
    else:
        # On sacrifie le trésor le plus faible
        choice = min(
            decision_input.money_in_hand,
            key=money_value,
        )

    return DopynionResponseCardName(game_id=game_id, decision=choice)
