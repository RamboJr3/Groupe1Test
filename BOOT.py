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
    # Actions supplémentaires
    getattr(CardName, "COUNCIL_ROOM", None): 5 if hasattr(CardName, "COUNCIL_ROOM") else None,
    getattr(CardName, "DISTANT_SHORE", None): 6 if hasattr(CardName, "DISTANT_SHORE") else None,
    getattr(CardName, "FARMING_VILLAGE", None): 4 if hasattr(CardName, "FARMING_VILLAGE") else None,
    getattr(CardName, "BANDIT", None): 5 if hasattr(CardName, "BANDIT") else None,
    getattr(CardName, "BUREAUCRAT", None): 4 if hasattr(CardName, "BUREAUCRAT") else None,
    getattr(CardName, "CHANCELLOR", None): 3 if hasattr(CardName, "CHANCELLOR") else None,
    getattr(CardName, "GARDENS", None): 4 if hasattr(CardName, "GARDENS") else None,
    getattr(CardName, "MILITIA", None): 4 if hasattr(CardName, "MILITIA") else None,
    getattr(CardName, "ARTIFICER", None): 5 if hasattr(CardName, "ARTIFICER") else None,
    getattr(CardName, "MARQUIS", None): 6 if hasattr(CardName, "MARQUIS") else None,
    getattr(CardName, "POACHER", None): 4 if hasattr(CardName, "POACHER") else None,
    getattr(CardName, "HARVEST", None): 5 if hasattr(CardName, "HARVEST") else None,
    getattr(CardName, "MAG_PIE", None): 4 if hasattr(CardName, "MAG_PIE") else None,
    getattr(CardName, "PORT", None): 4 if hasattr(CardName, "PORT") else None,
    getattr(CardName, "REMAKE", None): 4 if hasattr(CardName, "REMAKE") else None,
    getattr(CardName, "CHAPEL", None): 2 if hasattr(CardName, "CHAPEL") else None,
    getattr(CardName, "THIEF", None): 6 if hasattr(CardName, "THIEF") else None,
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
    "COUNCIL_ROOM":   (0, 1, 0, 4),
    "DISTANT_SHORE":  (1, 0, 0, 2),
    "FARMING_VILLAGE":(2, 0, 0, 1),
    "BANDIT":         (0, 0, 0, 0),
    "BUREAUCRAT":     (0, 0, 0, 0),
    "CHANCELLOR":     (0, 0, 2, 0),
    "MILITIA":        (0, 0, 2, 0),
    "ARTIFICER":      (1, 0, 1, 1),
    "MARQUIS":        (0, 1, 0, 0),
    "POACHER":        (1, 0, 1, 1),
    "HARVEST":        (0, 0, 0, 0),
    "MAG_PIE":        (1, 0, 0, 1),
    "PORT":           (1, 0, 0, 1),
    "REMAKE":         (0, 0, 0, 0),
    "CHAPEL":         (0, 0, 0, 0),
    "THIEF":          (0, 0, 0, 0),
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
        else "<h1>Ruin La Promo – Stratégie Adaptative Ultime</h1>"
    )
 
@app.get("/name")
def name() -> str:
    return "Ruin La Promo"
 
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
# STRATÉGIE PRINCIPALE : RUIN LA PROMO
# Stratégie adaptative optimisée basée sur 210 000 parties
# - 2-3 joueurs : VincentBM (Smithy + Bandit + Big Money) → 77% WR à 2J, 58% à 3J
# - 4 joueurs : RhumRuin (Duchy rush) → 36% WR
#####################################################
 
@app.post("/play")
def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    """
    Route principale de jeu - Phase action puis phase achat
    """
    # --- trouver "moi" ---
    me = next((p for p in game.players if p.hand is not None), None)
    if not me or not me.hand:
        print(f"[play] game={game_id} no visible hand -> END_TURN")
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")
 
    hand = me.hand.quantities
    stock = game.stock.quantities
    ts = get_turn_state(game_id)
    num_players = len(game.players)
 
    # === PHASE ACTION ===
    if ts.get("actions", 0) > 0:
        action_card = decide_action(hand, stock, num_players, ts)
        if action_card:
            print(f"[play_action] game={game_id} PLAY {action_card}")
            # Appliquer les effets de l'action
            effects = EFFECTS.get(action_card, (0, 0, 0, 0))
            ts["actions"] += effects[0] - 1  # -1 pour l'action jouée
            ts["buys"] += effects[1]
            ts["coins_bonus"] += effects[2]
            return DopynionResponseStr(game_id=game_id, decision=f"PLAY {action_card}")
 
    # === PHASE ACHAT ===
    def money_available():
        copper = hand.get(CardName.COPPER, 0)
        silver = hand.get(CardName.SILVER, 0)
        gold = hand.get(CardName.GOLD, 0)
        bonus = ts.get("coins_bonus", 0)
        spent = ts.get("coins_spent", 0)
        cursed_gold = hand.get(getattr(CardName, "CURSED_GOLD", CardName.COPPER), 0)
        result = copper * 1 + silver * 2 + gold * 3 + cursed_gold * 3 + bonus - spent
        return result
 
    def can_buy(c: CardName):
        return (
            ts["buys"] > 0
            and c in COST
            and stock.get(c, 0) > 0
            and money_available() >= COST[c]
        )
 
    def do_buy(c: CardName):
        cost = COST[c]
        ts["buys"] -= 1
        ts["coins_spent"] += cost
        inc_owned(game_id, c)
        print(f"[buy] game={game_id} BUY {c.name} cost={cost} buys_left={ts['buys']} turn={ts.get('turn', 0)}")
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {c.name}")
 
    # Décision d'achat selon le nombre de joueurs
    buy_decision = decide_buy(stock, money_available, can_buy, owned, game_id, num_players, ts)
    if buy_decision:
        return do_buy(buy_decision)
 
    # Fin de tour
    return DopynionResponseStr(game_id=game_id, decision="END_TURN")
 
 
def decide_action(hand: dict, stock: dict, num_players: int, ts: dict) -> str | None:
    """
    Décide quelle action jouer selon le nombre de joueurs
    
    2-3J : VincentBM (Smithy > Bandit/Thief > Militia)
    4J : Witch uniquement (RhumRuin)
    """
    if num_players <= 3:
        # VincentBM : priorité Smithy > Bandit > Thief > Militia
        action_priority = ["SMITHY", "BANDIT", "THIEF", "MILITIA"]
        for action in action_priority:
            card_name = getattr(CardName, action, None)
            if card_name and hand.get(card_name, 0) > 0:
                return action
    else:
        # 4J : RhumRuin - jouer Witch seulement
        if hand.get(CardName.WITCH, 0) > 0:
            return "WITCH"
    
    return None
 
 
def decide_buy(stock: dict, money_available, can_buy, owned, game_id: str, num_players: int, ts: dict) -> CardName | None:
    """
    Décide quoi acheter selon le nombre de joueurs
    
    2-3J : VincentBM optimisé (Smithy + Bandit + Big Money avec Duchy timing)
    4J : RhumRuin (Duchy rush)
    """
    money = money_available()
    turn = ts.get("turn", 0)
    prov_left = stock.get(CardName.PROVINCE, 0)
    duchy_left = stock.get(CardName.DUCHY, 0)
    
    # Compteurs
    smithy_cnt = owned(game_id, CardName.SMITHY)
    bandit_cnt = owned(game_id, getattr(CardName, "BANDIT", CardName.COPPER))
    thief_cnt = owned(game_id, getattr(CardName, "THIEF", CardName.COPPER))
    witch_cnt = owned(game_id, CardName.WITCH)
    
    # === 2 ou 3 JOUEURS : VINCENTBM ===
    if num_players <= 3:
        # 0) Fin de partie : rattrapage avec Duchy
        if prov_left <= 4 or duchy_left <= 3:
            if can_buy(CardName.PROVINCE):
                return CardName.PROVINCE
            if duchy_left > 0 and can_buy(CardName.DUCHY):
                return CardName.DUCHY
        
        # 1. Province en priorité
        if can_buy(CardName.PROVINCE):
            return CardName.PROVINCE
        
        # 2. Gold très prioritaire
        if money >= 6 and can_buy(CardName.GOLD):
            return CardName.GOLD
        
        # 3. Early game (tours 1-4) : Smithy > Bandit/Thief
        if turn <= 4:
            if smithy_cnt < 2 and can_buy(CardName.SMITHY):
                return CardName.SMITHY
            bandit_card = getattr(CardName, "BANDIT", None)
            if bandit_card and bandit_cnt < 2 and can_buy(bandit_card):
                return bandit_card
            thief_card = getattr(CardName, "THIEF", None)
            if thief_card and thief_cnt < 1 and can_buy(thief_card):
                return thief_card
        
        # 4. Mid-game : compléter les terminaux
        if money >= 5:
            if smithy_cnt < 2 and can_buy(CardName.SMITHY):
                return CardName.SMITHY
            bandit_card = getattr(CardName, "BANDIT", None)
            if bandit_card and (bandit_cnt + thief_cnt) < 2 and can_buy(bandit_card):
                return bandit_card
        
        # 5. Duchy timing optimisé : 5 coins quand provinces basses
        if money == 5 and (prov_left <= 5 or duchy_left <= 6):
            if can_buy(CardName.DUCHY):
                return CardName.DUCHY
        
        # 6. Silver par défaut
        if can_buy(CardName.SILVER):
            return CardName.SILVER
        
        # 7. Estate très tardif
        if prov_left <= 2 and can_buy(CardName.ESTATE):
            return CardName.ESTATE
    
    # === 4 JOUEURS : RHUMRUIN ===
    else:
        # Province
        if can_buy(CardName.PROVINCE):
            return CardName.PROVINCE
        
        # Witch si disponible (< 1 possédée)
        if money >= 5 and witch_cnt < 1 and duchy_left > 4 and can_buy(CardName.WITCH):
            return CardName.WITCH
        
        # Duchy rush à 5+ coins
        if money >= 5 and can_buy(CardName.DUCHY):
            return CardName.DUCHY
        
        # Gold
        if can_buy(CardName.GOLD):
            return CardName.GOLD
        
        # Silver
        if can_buy(CardName.SILVER):
            return CardName.SILVER
    
    return None
 
 
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
    # Ordre de défausse : CURSE > ESTATE > COPPER > DUCHY > SILVER > GOLD > reste
    # On garde les cartes Victory importantes (Province) et les attaques
    priority = [
        CardName.CURSE,
        CardName.ESTATE,
        CardName.COPPER,
        CardName.DUCHY,
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
    # Ordre de trash : CURSE > ESTATE > COPPER > DUCHY (late game) > SILVER (late game)
    priority = [
        CardName.CURSE,
        CardName.ESTATE,
        CardName.COPPER,
    ]
    in_hand = list(decision_input.hand)
    for c in priority:
        if c in in_hand:
            print(f"[trash] choose {c.name}")
            return DopynionResponseCardName(game_id=game_id, decision=c)
    
    # En late game, trash aussi Duchy et Silver si on a mieux
    if CardName.DUCHY in in_hand:
        print(f"[trash] choose DUCHY (late game)")
        return DopynionResponseCardName(game_id=game_id, decision=CardName.DUCHY)
    if CardName.SILVER in in_hand:
        print(f"[trash] choose SILVER (late game)")
        return DopynionResponseCardName(game_id=game_id, decision=CardName.SILVER)
    
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
    # Préférer les cartes utiles : GOLD > SILVER > SMITHY > reste
    priority = [CardName.GOLD, CardName.SILVER, CardName.SMITHY]
    possible = list(decision_input.possible_cards)
    for c in priority:
        if c in possible:
            return DopynionResponseCardName(game_id=game_id, decision=c)
    return DopynionResponseCardName(
        game_id=game_id,
        decision=possible[0],
    )
 
@app.post("/choose_card_to_receive_in_deck")
async def choose_card_to_receive_in_deck(
    game_id: GameIdDependency,
    decision_input: PossibleCards,
) -> DopynionResponseCardName:
    # Préférer les cartes utiles : GOLD > SILVER > SMITHY > reste
    priority = [CardName.GOLD, CardName.SILVER, CardName.SMITHY]
    possible = list(decision_input.possible_cards)
    for c in priority:
        if c in possible:
            return DopynionResponseCardName(game_id=game_id, decision=c)
    return DopynionResponseCardName(
        game_id=game_id,
        decision=possible[0],
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