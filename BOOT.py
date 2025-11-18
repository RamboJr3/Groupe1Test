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

# --- ÉTAT DE TOUR / PARTIE ---
# SESS[game_id] = {
#   "actions": int, "buys": int, "coins_bonus": int, "coins_spent": int,
#   "owned": {CardName: int}, "turn": int
# }
SESS: dict[str, dict] = {}


def init_turn_state(game_id: str) -> None:
    SESS.setdefault(game_id, {"owned": {}, "turn": 0})
    SESS[game_id].update(
        {"actions": 1, "buys": 1, "coins_bonus": 0, "coins_spent": 0}
    )


def get_turn_state(game_id: str) -> dict:
    return SESS.setdefault(
        game_id,
        {
            "actions": 1,
            "buys": 1,
            "coins_bonus": 0,
            "coins_spent": 0,
            "owned": {},
            "turn": 0,
        },
    )


def inc_owned(game_id: str, card: CardName) -> None:
    s = SESS.setdefault(
        game_id,
        {
            "actions": 1,
            "buys": 1,
            "coins_bonus": 0,
            "coins_spent": 0,
            "owned": {},
            "turn": 0,
        },
    )
    o = s.setdefault("owned", {})
    o[card] = o.get(card, 0) + 1


def owned(game_id: str, card: CardName) -> int:
    s = SESS.get(game_id) or {}
    o = s.get("owned") or {}
    return o.get(card, 0)


# --- COÛTS DES CARTES ---
COST = {
    # Trésors / Victoire
    CardName.COPPER: 0,
    CardName.SILVER: 3,
    CardName.GOLD: 6,
    CardName.ESTATE: 2,
    CardName.DUCHY: 5,
    CardName.PROVINCE: 8,
    CardName.CURSE: 0,

    # Prosperity
    getattr(CardName, "COLONY", None): 11 if hasattr(CardName, "COLONY") else None,

    # Trésor spécial
    getattr(CardName, "CURSED_GOLD", None): 4 if hasattr(CardName, "CURSED_GOLD") else None,

    # Actions classiques
    CardName.FESTIVAL: 5,
    CardName.LABORATORY: 5,
    CardName.VILLAGE: 3,
    CardName.WOODCUTTER: 3,
    CardName.SMITHY: 4,
    CardName.MARKET: 5,
    CardName.WITCH: 5,
    CardName.HIRELING: 6,

    # Actions déjà décrites
    getattr(CardName, "COUNCIL_ROOM", None): 5 if hasattr(CardName, "COUNCIL_ROOM") else None,
    getattr(CardName, "DISTANT_SHORE", None): 6 if hasattr(CardName, "DISTANT_SHORE") else None,
    getattr(CardName, "FARMING_VILLAGE", None): 4 if hasattr(CardName, "FARMING_VILLAGE") else None,
    getattr(CardName, "BANDIT", None): 5 if hasattr(CardName, "BANDIT") else None,
    getattr(CardName, "BUREAUCRAT", None): 4 if hasattr(CardName, "BUREAUCRAT") else None,
    getattr(CardName, "CHANCELLOR", None): 3 if hasattr(CardName, "CHANCELLOR") else None,
    getattr(CardName, "GARDENS", None): 4 if hasattr(CardName, "GARDENS") else None,
    getattr(CardName, "MILITIA", None): 4 if hasattr(CardName, "MILITIA") else None,

    # Nouvelles actions
    getattr(CardName, "ARTIFICER", None): 5 if hasattr(CardName, "ARTIFICER") else None,
    getattr(CardName, "MARQUIS", None): 6 if hasattr(CardName, "MARQUIS") else None,
    getattr(CardName, "POACHER", None): 4 if hasattr(CardName, "POACHER") else None,
    getattr(CardName, "HARVEST", None): 5 if hasattr(CardName, "HARVEST") else None,
    getattr(CardName, "MAG_PIE", None): 4 if hasattr(CardName, "MAG_PIE") else None,
    getattr(CardName, "PORT", None): 4 if hasattr(CardName, "PORT") else None,
    getattr(CardName, "REMAKE", None): 4 if hasattr(CardName, "REMAKE") else None,
}

# Nettoyage: enlever les clés None éventuelles
COST = {k: v for k, v in COST.items() if k is not None}

# --- EFFETS D'ACTIONS (actions, buys, coins_bonus, draw théorique) ---
EFFECTS: dict[str, tuple[int, int, int, int]] = {
    "FESTIVAL":       (2, 1, 2, 0),
    "LABORATORY":     (1, 0, 0, 2),
    "VILLAGE":        (2, 0, 0, 1),
    "WOODCUTTER":     (0, 1, 2, 0),
    "SMITHY":         (0, 0, 0, 3),
    "MARKET":         (1, 1, 1, 1),
    "WITCH":          (0, 0, 0, 2),
    "HIRELING":       (0, 0, 0, 0),

    # Attaques / draw
    "COUNCIL_ROOM":   (0, 1, 0, 4),
    "DISTANT_SHORE":  (1, 0, 0, 2),
    "FARMING_VILLAGE":(2, 0, 0, 1),
    "BANDIT":         (0, 0, 0, 0),
    "BUREAUCRAT":     (0, 0, 0, 0),
    "CHANCELLOR":     (0, 0, 2, 0),
    "MILITIA":        (0, 0, 2, 0),

    # Nouvelles actions
    "ARTIFICER":      (1, 0, 1, 1),  # +1 carte, +1 action, +1$
    "MARQUIS":        (0, 1, 0, 0),  # +1 buy, draw gérée par moteur
    "POACHER":        (1, 0, 1, 1),  # +1 carte, +1 action, +1$
    "HARVEST":        (0, 0, 0, 0),
    "MAG_PIE":        (1, 0, 0, 1),  # cantrip
    "PORT":           (1, 0, 0, 1),  # cantrip
    "REMAKE":         (0, 0, 0, 0),
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
# Static routes
#####################################################


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    path = Path(__file__).parent / "index.html"
    return (
        html.escape(path.read_text(encoding="utf-8"))
        if path.exists()
        else "<h1>Rhum & ruin – HyperRush</h1>"
    )


@app.get("/name")
def name() -> str:
    return "Gin te Ruine"


@app.get("/start_game")
def start_game(game_id: GameIdDependency) -> DopynionResponseStr:
    SESS[game_id] = {
        "actions": 1,
        "buys": 1,
        "coins_bonus": 0,
        "coins_spent": 0,
        "owned": {},
        "turn": 0,
    }
    return DopynionResponseStr(game_id=game_id, decision="OK")


@app.get("/start_turn")
def start_turn(game_id: GameIdDependency) -> DopynionResponseStr:
    init_turn_state(game_id)
    s = SESS.setdefault(game_id, {"owned": {}, "turn": 0})
    s["turn"] = s.get("turn", 0) + 1
    return DopynionResponseStr(game_id=game_id, decision="OK")


#####################################################
# STRATÉGIE PRINCIPALE : RUSH ~10 TOURS
#####################################################


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
    s = SESS.setdefault(
        game_id,
        {
            "actions": 1,
            "buys": 1,
            "coins_bonus": 0,
            "coins_spent": 0,
            "owned": {},
            "turn": 1,
        },
    )

    def hq(c: CardName) -> int:
        return hand.get(c, 0)

    def money_treasures() -> int:
        total = (
            hq(CardName.COPPER) * 1
            + hq(CardName.SILVER) * 2
            + hq(CardName.GOLD) * 3
        )
        # Cursed Gold = 3$
        if hasattr(CardName, "CURSED_GOLD"):
            total += hq(CardName.CURSED_GOLD) * 3
        return total

    def money_available() -> int:
        return money_treasures() + ts["coins_bonus"] - ts["coins_spent"]

    def can_buy(c: CardName) -> bool:
        return (
            ts["buys"] > 0
            and c in COST
            and stock.get(c, 0) > 0
            and money_available() >= COST[c]
        )

    def do_buy(c: CardName) -> DopynionResponseStr:
        cost = COST[c]
        ts["buys"] -= 1
        ts["coins_spent"] += cost
        inc_owned(game_id, c)
        print(
            f"[buy] game={game_id} BUY {c.name} cost={cost} "
            f"buys_left={ts['buys']} spent={ts['coins_spent']} money_after={money_available()}"
        )
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {c.name}")

    # --- infos de partie ---
    turn_no = s.get("turn", 1)
    my_score = getattr(me, "score", 0) or 0
    max_opp_score = max(
        (getattr(p, "score", 0) or 0) for p in game.players if p is not me
    ) if game.players else 0

    prov_left = stock.get(CardName.PROVINCE, 0)
    colony_card = getattr(CardName, "COLONY", None) if hasattr(CardName, "COLONY") else None
    colony_left = stock.get(colony_card, 0) if colony_card in stock else 0
    curses_left = stock.get(CardName.CURSE, 0) if CardName.CURSE in stock else 0

    owned_local = s.get("owned", {})
    wt_cnt = owned_local.get(CardName.WITCH, 0)
    sm_cnt = owned_local.get(CardName.SMITHY, 0)
    remake_card = getattr(CardName, "REMAKE", None)
    remake_cnt = owned_local.get(remake_card, 0) if remake_card else 0
    gardens_card = getattr(CardName, "GARDENS", None) if hasattr(CardName, "GARDENS") else None
    gardens_cnt = owned_local.get(gardens_card, 0) if gardens_card else 0
    magpie_card = getattr(CardName, "MAG_PIE", None) if hasattr(CardName, "MAG_PIE") else None
    magpie_cnt = owned_local.get(magpie_card, 0) if magpie_card else 0
    port_card = getattr(CardName, "PORT", None) if hasattr(CardName, "PORT") else None
    port_cnt = owned_local.get(port_card, 0) if port_card else 0

    # estimation très grossière du nb de cartes pour Gardens
    total_gained = sum(owned_local.values())
    estimated_total_cards = 10 + total_gained  # ignore trash / gains auto, suffisant pour heuristique

    print(
        f"[play] game={game_id} t={turn_no} "
        f"$={money_available()} prov_left={prov_left} colony_left={colony_left} curses_left={curses_left} "
        f"score={my_score}/{max_opp_score} "
        f"owned: WITCH={wt_cnt} REMAKE={remake_cnt} GARDENS={gardens_cnt} "
        f"MAGPIE={magpie_cnt} PORT={port_cnt} cards~={estimated_total_cards}"
    )
    # --- FIX ACTIONS : détection correcte des cartes en main
    def has_in_hand(card: CardName) -> bool:
        return hand.get(card, 0) > 0

    # --- Nouvelle evaluation eco
    def econ_strength() -> int:
        return (
            owned_local.get(CardName.SILVER, 0) * 2 +
            owned_local.get(CardName.GOLD, 0) * 3 +
            owned_local.get(getattr(CardName, "CURSED_GOLD", None), 0) * 3
        )

    # --- Nouveau trash intelligent
    def should_trash_estate() -> bool:
        return turn_no <= 10 and remake_cnt > 0

    def should_trash_copper() -> bool:
        return econ_strength() >= 6 and turn_no >= 5

    # --------------------
    # PHASE ACTION améliorée
    # --------------------
    if ts["actions"] > 0:
        # Priorité : draw > attaques > moteurs > support
        action_priority = [
            CardName.LABORATORY,
            CardName.SMITHY,
            getattr(CardName, "MARQUIS", None),
            getattr(CardName, "MAG_PIE", None),
            getattr(CardName, "PORT", None),
            CardName.VILLAGE,
            CardName.MARKET,
            CardName.FESTIVAL,
            CardName.WITCH,
            getattr(CardName, "MILITIA", None),
            getattr(CardName, "COUNCIL_ROOM", None),
            getattr(CardName, "FARMING_VILLAGE", None),
            getattr(CardName, "HARVEST", None),
            getattr(CardName, "ARTIFICER", None),
            getattr(CardName, "POACHER", None),
            getattr(CardName, "CHANCELLOR", None),
            getattr(CardName, "DISTANT_SHORE", None),
            remake_card,
            CardName.HIRELING,
        ]

        for a in action_priority:
            if a and has_in_hand(a) and a.name in EFFECTS:
                acts, buys, coins, _draw = EFFECTS[a.name]
                ts["actions"] -= 1
                ts["actions"] += acts
                ts["buys"] += buys
                ts["coins_bonus"] += coins
                print(f"[play] ACTION {a.name}")
                return DopynionResponseStr(game_id=game_id, decision=f"ACTION {a.name}")


    # --------------------
    # PHASE ACHAT améliorée
    # --------------------
    if ts["buys"] > 0:

        # A) Province si économie correcte
        if money_available() >= 8 and can_buy(CardName.PROVINCE):
            return do_buy(CardName.PROVINCE)

        # B) Gold avant tout (moteur éco)
        if can_buy(CardName.GOLD):
            return do_buy(CardName.GOLD)

        # C) Witch early
        if curses_left > 0 and wt_cnt < 2 and turn_no <= 8 and can_buy(CardName.WITCH):
            return do_buy(CardName.WITCH)

        # D) Laboratory > Smithy = draw core
        if can_buy(CardName.LABORATORY):
            return do_buy(CardName.LABORATORY)
        if sm_cnt < 1 and can_buy(CardName.SMITHY):
            return do_buy(CardName.SMITHY)

        # E) Silver simple stabilisation
        if can_buy(CardName.SILVER):
            return do_buy(CardName.SILVER)

        # F) Defer Gardens rush jusqu'à econ stable
        if gardens_card and econ_strength() >= 5 and can_buy(gardens_card):
            return do_buy(gardens_card)

        # G) Estates fin de partie
        if prov_left <= 2 and can_buy(CardName.ESTATE):
            return do_buy(CardName.ESTATE)



#####################################################
# Fin de partie
#####################################################


@app.get("/end_game")
def end_game(game_id: GameIdDependency) -> DopynionResponseStr:
    SESS.pop(game_id, None)
    return DopynionResponseStr(game_id=game_id, decision="OK")


#####################################################
# Callbacks génériques : défausse, trash, etc.
#####################################################


@app.post("/confirm_discard_card_from_hand")
async def confirm_discard_card_from_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    # On accepte de défausser quand l'arbitre le propose
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/discard_card_from_hand")
async def discard_card_from_hand(
    game_id: GameIdDependency,
    decision_input: Hand,
) -> DopynionResponseCardName:
    # Ordre de défausse : CURSE > ESTATE > COPPER > SILVER > GOLD > reste
    priority = [
        CardName.CURSE,
        CardName.ESTATE,
        CardName.COPPER,
        CardName.SILVER,
        CardName.GOLD,
    ]
    in_hand = list(decision_input.hand)
    for c in priority:
        if c in in_hand:
            print(f"[discard] choose {c.name}")
            return DopynionResponseCardName(game_id=game_id, decision=c)
    print(f"[discard] default {in_hand[0].name}")
    return DopynionResponseCardName(game_id=game_id, decision=in_hand[0])


@app.post("/confirm_trash_card_from_hand")
async def confirm_trash_card_from_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    # On accepte de trash quand l'arbitre le propose
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/trash_card_from_hand")
async def trash_card_from_hand(
    game_id: GameIdDependency,
    decision_input: Hand,
) -> DopynionResponseCardName:
    # Ordre de trash : CURSE > ESTATE > COPPER > SILVER > GOLD > reste
    priority = [
        CardName.CURSE,
        CardName.ESTATE,
        CardName.COPPER,
        CardName.SILVER,
        CardName.GOLD,
    ]
    in_hand = list(decision_input.hand)
    for c in priority:
        if c in in_hand:
            print(f"[trash] choose {c.name}")
            return DopynionResponseCardName(game_id=game_id, decision=c)
    print(f"[trash] default {in_hand[0].name}")
    return DopynionResponseCardName(game_id=game_id, decision=in_hand[0])


@app.post("/confirm_discard_deck")
async def confirm_discard_deck(
    game_id: GameIdDependency,
) -> DopynionResponseBool:
    # OK pour défausser le deck quand demandé
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/choose_card_to_receive_in_discard")
async def choose_card_to_receive_in_discard(
    game_id: GameIdDependency,
    decision_input: PossibleCards,
) -> DopynionResponseCardName:
    # Choix neutre : premier choix proposé
    return DopynionResponseCardName(
        game_id=game_id,
        decision=decision_input.possible_cards[0],
    )


@app.post("/choose_card_to_receive_in_deck")
async def choose_card_to_receive_in_deck(
    game_id: GameIdDependency,
    decision_input: PossibleCards,
) -> DopynionResponseCardName:
    # Choix neutre : premier choix proposé
    return DopynionResponseCardName(
        game_id=game_id,
        decision=decision_input.possible_cards[0],
    )


@app.post("/skip_card_reception_in_hand")
async def skip_card_reception_in_hand(
    game_id: GameIdDependency,
    _decision_input: CardNameAndHand,
) -> DopynionResponseBool:
    # On accepte de ne pas recevoir la carte en main si proposé
    return DopynionResponseBool(game_id=game_id, decision=True)


@app.post("/trash_money_card_for_better_money_card")
async def trash_money_card_for_better_money_card(
    game_id: GameIdDependency,
    decision_input: MoneyCardsInHand,
) -> DopynionResponseCardName:
    # Remake-like: on trash la plus faible valeur possible
    priority = [CardName.COPPER, CardName.SILVER, CardName.GOLD]
    in_hand = list(decision_input.money_in_hand)
    for c in priority:
        if c in in_hand:
            print(f"[trash_money] choose {c.name}")
            return DopynionResponseCardName(game_id=game_id, decision=c)
    print(f"[trash_money] default {in_hand[0].name}")
    return DopynionResponseCardName(game_id=game_id, decision=in_hand[0])
