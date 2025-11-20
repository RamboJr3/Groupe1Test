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
    s = get_turn_state(game_id)

    s["turn"] = s.get("turn", 0) + 1

    # === Correctif ÉCONOMIE (vraie variable utilisée partout) ===
    s["coins_spent"] = 0
    # Bonus éventuels remis à zéro aussi (ton moteur n'est pas robuste sinon)
    s["coins_bonus"] = 0
    # ==========================================================

    return DopynionResponseStr(game_id=game_id, decision="OK")


 
#####################################################
# STRATÉGIE PRINCIPALE : RUIN LA PROMO
# Stratégie adaptative optimisée basée sur 210 000 parties
# - 2-3 joueurs : VincentBM (Smithy + Bandit + Big Money) → 77% WR à 2J, 58% à 3J
# - 4 joueurs : RhumRuin (Duchy rush) → 36% WR
#####################################################

# --- Constants de stratégie (tweakables) ---
PROV_THRESHOLD = 4            # si <= ce nombre de provinces, on passe en mode agressif (Duchy / fin de partie)
SCORE_DELTA = 4               # si un adversaire te distance de >= 4 PV, on force le switch agressif
ENGINE_PROVINCE_MONEY = 12    # seuil d'argent dans un tour pour considérer que l'engine peut enchaîner les Provinces
DOUBLE_PROVINCE_BUYS = 2      # nb de buys requis pour envisager double Province dans le même tour

 
@app.post("/play")
def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    ts = get_turn_state(game_id)

    if ts.get("coins_spent", None) is None:
        ts["coins_spent"] = 0
    else:
        ts["coins_spent"] = 0

    # --- trouver "moi" ---
    me = next((p for p in game.players if p.hand is not None), None)
    if not me or not me.hand:
        print(f"[play] game={game_id} no visible hand -> END_TURN")
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    hand = me.hand.quantities        # dict[CardName,int]
    stock = game.stock.quantities    # dict[CardName,int]
    ts = get_turn_state(game_id)
    num_players = len(game.players)

    # =====================================================================
    # BRANCHE 2 JOUEURS : STRAT RUIN LA PROMO (VincentBM / 2J)
    # =====================================================================
    if num_players == 2:
        # === PHASE ACTION ===
        if ts.get("actions", 0) > 0:
            action_card = decide_action(hand, stock, num_players, ts)
            if action_card:
                print(f"[play_action] game={game_id} ACTION {action_card}")
                # Appliquer les effets de l'action
                effects = EFFECTS.get(action_card, (0, 0, 0, 0))
                ts["actions"] += effects[0] - 1  # -1 pour l'action jouée
                ts["buys"] += effects[1]
                ts["coins_bonus"] += effects[2]
                return DopynionResponseStr(game_id=game_id, decision=f"ACTION {action_card}")

        # === PHASE ACHAT ===
        def money_available_2j() -> int:
            copper = hand.get(CardName.COPPER, 0)
            silver = hand.get(CardName.SILVER, 0)
            gold = hand.get(CardName.GOLD, 0)
            bonus = ts.get("coins_bonus", 0)
            spent = ts.get("coins_spent", 0)

            cursed_gold_card = getattr(CardName, "CURSED_GOLD", None)
            cursed_gold = hand.get(cursed_gold_card, 0) if cursed_gold_card else 0

            result = copper * 1 + silver * 2 + gold * 3 + cursed_gold * 3 + bonus - spent
            print(
                f"[money] game={game_id} copper={copper} silver={silver} gold={gold} "
                f"cursed_gold={cursed_gold} bonus={bonus} spent={spent} => total={result}"
            )
            return result

        def can_buy_2j(c: CardName) -> bool:
            can = (
                ts["buys"] > 0
                and c in COST
                and stock.get(c, 0) > 0
                and money_available_2j() >= COST[c]
            )
            if not can:
                print(
                    f"[can_buy] game={game_id} card={c.name} buys={ts['buys']} "
                    f"in_cost={c in COST} in_stock={stock.get(c, 0)} "
                    f"money={money_available_2j()} cost={COST.get(c, '?')} => {can}"
                )
            return can

        def do_buy_2j(c: CardName) -> DopynionResponseStr:
            cost = COST[c]
            ts["buys"] -= 1
            ts["coins_spent"] += cost
            inc_owned(game_id, c)
            print(
                f"[buy] game={game_id} BUY {c.name} cost={cost} "
                f"buys_left={ts['buys']} turn={ts.get('turn', 0)}"
            )
            return DopynionResponseStr(game_id=game_id, decision=f"BUY {c.name}")

        buy_decision = decide_buy(
            stock=stock,
            money_available=money_available_2j,
            can_buy=can_buy_2j,
            owned=owned,
            game_id=game_id,
            num_players=num_players,
            ts=ts,
        )
        if buy_decision:
            return do_buy_2j(buy_decision)

        return DopynionResponseStr(game_id=game_id, decision="END_TURN")

    # =====================================================================
    # BRANCHE 3–4 JOUEURS : STRAT RHUM & RUIN COMPLÈTE
    # =====================================================================

    # helpers
    def hq(c: CardName) -> int:
        return hand.get(c, 0)

    def money_treasures() -> int:
        return (
            hq(CardName.COPPER) * 1
            + hq(CardName.SILVER) * 2
            + hq(CardName.GOLD) * 3
        )

    def money_available() -> int:
        return money_treasures() + ts["coins_bonus"] - ts["coins_spent"]

    # basic info
    prov_left = stock.get(CardName.PROVINCE, 0)
    my_score = getattr(me, "score", 0) or 0
    max_opponent_score = (
        max(
            (getattr(p, "score", 0) or 0)
            for p in game.players
            if p is not me
        )
        if game.players
        else 0
    )

    # quick deck_estimate from visible hand (cheap heuristic)
    actions_in_hand = sum(
        1
        for c in hand
        if c
        not in (
            CardName.COPPER,
            CardName.SILVER,
            CardName.GOLD,
            CardName.ESTATE,
            CardName.DUCHY,
            CardName.PROVINCE,
            CardName.CURSE,
        )
        and hand[c] > 0
    )
    treasure_value = money_treasures()

    print(
        f"[play] game={game_id} start | actions={ts['actions']} buys={ts['buys']} "
        f"bonus={ts['coins_bonus']} spent={ts['coins_spent']} treasure={treasure_value} "
        f"actions_in_hand={actions_in_hand} prov_left={prov_left} "
        f"my_score={my_score} max_opp={max_opponent_score}"
    )

    # --------------------
    # PHASE ACTION (Rhum & Ruin)
    # --------------------
    if ts["actions"] > 0:
        action_priority = [
            CardName.MARKET,     # +1 carte, +1 action, +1 buy, +1$
            CardName.LABORATORY, # +2 cartes, +1 action
            CardName.VILLAGE,    # +2 actions, +1 carte
            CardName.FESTIVAL,   # +2 actions, +1 buy, +2$
            CardName.HIRELING,   # terminal
            CardName.WITCH,      # terminal
            CardName.SMITHY,     # terminal
            CardName.WOODCUTTER, # terminal
        ]

        for a in action_priority:
            if hq(a) > 0 and a.name in EFFECTS:
                acts, buys, coins, _ = EFFECTS[a.name]
                ts["actions"] -= 1
                ts["actions"] += acts
                ts["buys"] += buys
                ts["coins_bonus"] += coins
                print(
                    f"[play] ACTION {a.name} applied -> +acts={acts} +buys={buys} "
                    f"+$={coins} => actions={ts['actions']} buys={ts['buys']} "
                    f"bonus={ts['coins_bonus']}"
                )
                return DopynionResponseStr(
                    game_id=game_id, decision=f"ACTION {a.name}"
                )

    # --------------------
    # Decide mode: engine-first or aggressive Duchy-steal
    # --------------------
    aggressive_mode = False
    if prov_left <= PROV_THRESHOLD:
        aggressive_mode = True
    if (max_opponent_score - my_score) >= SCORE_DELTA:
        aggressive_mode = True

    engine_ready = (money_available() >= ENGINE_PROVINCE_MONEY) or (
        treasure_value + ts["coins_bonus"] >= 8 and ts["buys"] >= 1
    )

    print(
        f"[play] mode decision -> aggressive={aggressive_mode} "
        f"engine_ready={engine_ready} money_avail={money_available()}"
    )

    # --------------------
    # PHASE ACHAT — Rhum & Ruin
    # --------------------
    def can_buy(c: CardName) -> bool:
        return ts["buys"] > 0 and stock.get(c, 0) and money_available() >= COST[c]

    def do_buy(c: CardName) -> DopynionResponseStr:
        cost = COST[c]
        ts["buys"] -= 1
        ts["coins_spent"] += cost
        inc_owned(game_id, c)
        print(
            f"[buy] BUY {c.name} cost={cost} -> buys_left={ts['buys']} "
            f"spent={ts['coins_spent']} avail_now={money_available()}"
        )
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {c.name}")

    if ts["buys"] > 0:
        turn_no = SESS[game_id].get("turn", 1)
        enemy_alive = any(
            "equipe3" in (getattr(p, "name", "") or "").lower()
            for p in game.players
        )

        vg_cnt = owned(game_id, CardName.VILLAGE)
        mk_cnt = owned(game_id, CardName.MARKET)
        sm_cnt = owned(game_id, CardName.SMITHY)
        wt_cnt = owned(game_id, CardName.WITCH)
        lab_cnt = owned(game_id, CardName.LABORATORY)
        gd_cnt = owned(game_id, CardName.GOLD)
        rc_cnt = owned(game_id, CardName.HIRELING)

        prov_left = stock.get(CardName.PROVINCE, 0)
        curses_left = (
            stock.get(CardName.CURSE, 0) if CardName.CURSE in stock else 0
        )
        villages_left = (
            stock.get(CardName.VILLAGE, 0)
            if CardName.VILLAGE in stock
            else 0
        )
        AGGRO_DUCHY = (prov_left <= 4) or (
            (max_opponent_score - my_score) >= 4
        )

        print(
            f"[buy] t={turn_no} $={money_available()} prov={prov_left} "
            f"curses={curses_left} owned: RC={rc_cnt} VG={vg_cnt} MK={mk_cnt} "
            f"LAB={lab_cnt} WT={wt_cnt} SM={sm_cnt} GOLD={gd_cnt} "
            f"villages_left={villages_left} aggro_duchy={AGGRO_DUCHY} "
            f"enemy_alive={enemy_alive}"
        )

        # 0) Province si possible
        if can_buy(CardName.PROVINCE):
            return do_buy(CardName.PROVINCE)

        # 1) EARLY GAME — anti-Équipe3 + installation HIRELING
        if turn_no <= 8:
            if curses_left > 0 and wt_cnt < 1 and can_buy(CardName.WITCH):
                return do_buy(CardName.WITCH)

            if can_buy(CardName.HIRELING) and rc_cnt < 2:
                return do_buy(CardName.HIRELING)

            if can_buy(CardName.MARKET) and mk_cnt < 2:
                return do_buy(CardName.MARKET)

            if can_buy(CardName.GOLD) and gd_cnt < 1:
                return do_buy(CardName.GOLD)

            if can_buy(CardName.SILVER):
                return do_buy(CardName.SILVER)

        # 2) MID GAME — spam curse + stack HIRELING, puis deny Village
        if curses_left > 0:
            cap_witch = 3 if (enemy_alive and villages_left <= 7) else 2
            if can_buy(CardName.WITCH) and wt_cnt < cap_witch:
                return do_buy(CardName.WITCH)

            if can_buy(CardName.MARKET) and mk_cnt < 2:
                return do_buy(CardName.MARKET)
            if can_buy(CardName.LABORATORY) and lab_cnt < 2:
                return do_buy(CardName.LABORATORY)

            if can_buy(CardName.HIRELING) and rc_cnt < 3:
                return do_buy(CardName.HIRELING)

            if enemy_alive and villages_left > 0 and vg_cnt < 2 and can_buy(
                CardName.VILLAGE
            ):
                return do_buy(CardName.VILLAGE)

        # 3) FIN DES CURSES — convertir l’avantage en points / tempo
        if curses_left == 0 and enemy_alive and villages_left <= 2:
            if can_buy(CardName.PROVINCE):
                return do_buy(CardName.PROVINCE)
            if AGGRO_DUCHY and can_buy(CardName.DUCHY):
                return do_buy(CardName.DUCHY)

        # 4) PLAN STANDARD (fallback)
        if can_buy(CardName.PROVINCE):
            return do_buy(CardName.PROVINCE)

        if AGGRO_DUCHY and can_buy(CardName.DUCHY):
            return do_buy(CardName.DUCHY)

        if can_buy(CardName.HIRELING) and rc_cnt < 3:
            return do_buy(CardName.HIRELING)
        if can_buy(CardName.GOLD) and gd_cnt < 2:
            return do_buy(CardName.GOLD)

        if can_buy(CardName.MARKET) and mk_cnt < 2:
            return do_buy(CardName.MARKET)
        if can_buy(CardName.LABORATORY) and lab_cnt < 2:
            return do_buy(CardName.LABORATORY)
        if curses_left > 0 and can_buy(CardName.WITCH) and wt_cnt < 2:
            return do_buy(CardName.WITCH)

        if can_buy(CardName.SMITHY) and (vg_cnt + mk_cnt) >= 1 and sm_cnt < 2:
            return do_buy(CardName.SMITHY)

        if can_buy(CardName.SILVER):
            return do_buy(CardName.SILVER)

        if can_buy(CardName.DUCHY):
            return do_buy(CardName.DUCHY)

        if turn_no > 10 and can_buy(CardName.ESTATE):
            return do_buy(CardName.ESTATE)

    print(
        f"[play] nothing to do -> END_TURN | state actions={ts['actions']} "
        f"buys={ts['buys']} bonus={ts['coins_bonus']} spent={ts['coins_spent']}"
    )
    return DopynionResponseStr(game_id=game_id, decision="END_TURN")

 
 
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