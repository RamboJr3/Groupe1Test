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
SESS: dict[str, dict] = {}   # { game_id: {"actions": int, "buys": int, "coins_bonus": int, "coins_spent": int, "num_players": int, "cursed_gold_used": int} }
 
def init_turn_state(game_id: str) -> None:
    SESS.setdefault(game_id, {"owned": {}, "turn": 0, "num_players": 4, "cursed_gold_used": 0})
    SESS[game_id].update({"actions": 1, "buys": 1, "coins_bonus": 0, "coins_spent": 0})
 
 
def get_turn_state(game_id: str) -> dict:
    return SESS.setdefault(game_id, {"actions": 1, "buys": 1, "coins_bonus": 0, "coins_spent": 0, "num_players": 4, "cursed_gold_used": 0})
 
# --- suivi du deck par partie (approx via achats) ---
# SESS[game_id] = {"actions":..., "buys":..., "coins_bonus":..., "coins_spent":..., "owned": {CardName: int}, "num_players": int, "cursed_gold_used": int}
def inc_owned(game_id: str, card: CardName) -> None:
    s = SESS.setdefault(game_id, {"actions":1,"buys":1,"coins_bonus":0,"coins_spent":0,"owned":{},"num_players":4,"cursed_gold_used":0})
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
    CardName.PLATINIUM: 9,
    CardName.CURSED_GOLD: 4,
    CardName.ESTATE: 2,
    CardName.DUCHY: 5,
    CardName.PROVINCE: 8,
    CardName.COLONY: 11,
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
    "WITCH":      (0, 0, 0, 2),  # ⬅️ +2 cartes ; les Malédictions sont appliquées par l'arbitre
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
    return "Rhum & ruin"
 
 
@app.get("/start_game")
def start_game(game_id: GameIdDependency) -> DopynionResponseStr:
    # Initialiser la session pour cette partie
    SESS[game_id] = {
        "owned": {},
        "turn": 0,
        "actions": 1,
        "buys": 1,
        "coins_bonus": 0,
        "coins_spent": 0,
        "num_players": 4,  # sera mis à jour dès le premier tour
        "cursed_gold_used": 0
    }
    return DopynionResponseStr(game_id=game_id, decision="OK")
 
 
@app.get("/start_turn")
def start_turn(game_id: GameIdDependency) -> DopynionResponseStr:
    init_turn_state(game_id)
    # compteur de tour
    s = SESS.setdefault(game_id, {"owned": {}, "num_players": 4, "cursed_gold_used": 0})
    s["turn"] = s.get("turn", 0) + 1
    # info utile: HIRELINGs possédées
    rec_cnt = (s.get("owned") or {}).get(CardName.HIRELING, 0)
    print(f"[start_turn] game={game_id} turn={s['turn']} recrues_owned={rec_cnt} num_players={s.get('num_players', 4)}")
    return DopynionResponseStr(game_id=game_id, decision="OK")
 
 
 
@app.post("/play")
def play(game: Game, game_id: GameIdDependency) -> DopynionResponseStr:
    # --- trouver "moi" ---
    me = next((p for p in game.players if p.hand is not None), None)
    if not me or not me.hand:
        print(f"[play] game={game_id} no visible hand -> END_TURN")
        return DopynionResponseStr(game_id=game_id, decision="END_TURN")
 
    # Détecter le nombre de joueurs (une seule fois au premier tour)
    s = SESS.get(game_id, {})
    if s.get("num_players") == 4 and len(game.players) != 4:
        s["num_players"] = len(game.players)
        print(f"[play] Detected {len(game.players)} players in game {game_id}")
 
    hand = me.hand.quantities        # dict[CardName,int]
    stock = game.stock.quantities    # dict[CardName,int]
    ts = get_turn_state(game_id)
 
    # helpers
    def hq(c: CardName) -> int: return hand.get(c, 0)
    def in_stock(c: CardName) -> bool: return stock.get(c, 0) > 0
    def money_treasures() -> int:
        return (hq(CardName.COPPER)*1 + hq(CardName.SILVER)*2 + hq(CardName.GOLD)*3 +
                hq(CardName.PLATINIUM)*5 + hq(CardName.CURSED_GOLD)*3)
    def money_available() -> int:
        return money_treasures() + ts["coins_bonus"] - ts["coins_spent"]
 
    # basic info
    prov_left = stock.get(CardName.PROVINCE, 0)
    colony_left = stock.get(CardName.COLONY, 0)
    my_score = getattr(me, "score", 0) or 0
    max_opponent_score = max((getattr(p, "score", 0) or 0) for p in game.players if p is not me) if len(game.players) > 1 else 0
    num_players = s.get("num_players", 4)
 
    # quick deck_estimate from visible hand (cheap heuristic)
    # count actions and treasure density in hand to guess engine readiness
    actions_in_hand = sum(1 for c in hand if c not in (CardName.COPPER, CardName.SILVER, CardName.GOLD, CardName.PLATINIUM, CardName.CURSED_GOLD,
                                                       CardName.ESTATE, CardName.DUCHY, CardName.PROVINCE, CardName.COLONY, CardName.CURSE) and hand[c] > 0)
    treasure_value = money_treasures()
 
    print(f"[play] game={game_id} start | players={num_players} actions={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']} spent={ts['coins_spent']} "
          f"treasure={treasure_value} actions_in_hand={actions_in_hand} prov_left={prov_left} colony_left={colony_left} my_score={my_score} max_opp={max_opponent_score}")
 
    # --------------------
    # PHASE ACTION (only if actions > 0)
    # --------------------
    if ts["actions"] > 0:
        # Gestion du Cursed Gold : on le joue max 2 fois pour lancer l'économie
        cursed_gold_used = s.get("cursed_gold_used", 0)
        if hq(CardName.CURSED_GOLD) > 0 and cursed_gold_used < 2:
            s["cursed_gold_used"] = cursed_gold_used + 1
            print(f"[play] Playing CURSED_GOLD (usage {s['cursed_gold_used']}/2)")
            # Le Cursed Gold est joué comme un trésor, pas une action
            # Mais on le signale ici pour le comptabiliser
        
        # priority tuned for engine-first but allow attacking (WITCH) after +actions
        # Si 2 joueurs : on ne joue pas d'actions (Big Money pur)
        if num_players > 2:
            action_priority = [
                CardName.MARKET,     # +1 carte, +1 action, +1 buy, +1$
                CardName.LABORATORY, # +2 cartes, +1 action
                CardName.VILLAGE,    # +2 actions, +1 carte
                CardName.FESTIVAL,   # +2 actions, +1 buy, +2$
                CardName.HIRELING,   # terminal; on veut l'armer tôt une fois qu'on a des +actions
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
                    print(f"[play] ACTION {a.name} applied -> +acts={acts} +buys={buys} +$={coins} => actions={ts['actions']} buys={ts['buys']} bonus={ts['coins_bonus']}")
                    return DopynionResponseStr(game_id=game_id, decision=f"ACTION {a.name}")
 
    # --------------------
    # PHASE ACHAT — Deux stratégies selon le nombre de joueurs
    # --------------------
    def can_buy(c: CardName) -> bool:
        return ts["buys"] > 0 and stock.get(c, 0) > 0 and money_available() >= COST.get(c, 999)
 
    def do_buy(c: CardName) -> DopynionResponseStr:
        cost = COST.get(c, 0)
        ts["buys"] -= 1
        ts["coins_spent"] += cost
        inc_owned(game_id, c)
        print(f"[buy] BUY {c.name} cost={cost} -> buys_left={ts['buys']} spent={ts['coins_spent']} avail_now={money_available()}")
        return DopynionResponseStr(game_id=game_id, decision=f"BUY {c.name}")
 
    if ts["buys"] > 0:
        turn_no = SESS[game_id].get("turn", 1)
        
        # ========================================
        # STRATÉGIE BIG MONEY (2 joueurs)
        # ========================================
        if num_players == 2:
            print(f"[buy] BIG MONEY MODE (2 players) | turn={turn_no} money={money_available()}")
            
            plat_cnt = owned(game_id, CardName.PLATINIUM)
            gold_cnt = owned(game_id, CardName.GOLD)
            
            # Priorité : construire une économie solide avant d'acheter des cartes victoire
            # On vise 2-3 Platinium avant de commencer à acheter Colony/Province
            
            # 1. Colony (11) si on a déjà au moins 2 Platinium
            if plat_cnt >= 2 and can_buy(CardName.COLONY):
                return do_buy(CardName.COLONY)
            
            # 2. Province (8) si on a déjà au moins 2 Platinium
            if plat_cnt >= 2 and can_buy(CardName.PROVINCE):
                return do_buy(CardName.PROVINCE)
            
            # 3. Platinium (9) - priorité absolue pour l'économie (cap à 3)
            if can_buy(CardName.PLATINIUM) and plat_cnt < 3:
                return do_buy(CardName.PLATINIUM)
            
            # 4. Gold (6) - économie secondaire
            if can_buy(CardName.GOLD):
                return do_buy(CardName.GOLD)
            
            # 5. Silver (3) - base économique
            if can_buy(CardName.SILVER):
                return do_buy(CardName.SILVER)
            
            # Fin de partie : acheter des cartes victoire même sans Platinium optimal
            if colony_left <= 3 or prov_left <= 3:
                if can_buy(CardName.COLONY):
                    return do_buy(CardName.COLONY)
                if can_buy(CardName.PROVINCE):
                    return do_buy(CardName.PROVINCE)
                if can_buy(CardName.DUCHY):
                    return do_buy(CardName.DUCHY)
                if can_buy(CardName.ESTATE):
                    return do_buy(CardName.ESTATE)
        
        # ========================================
        # STRATÉGIE AGRESSIVE (3+ joueurs)
        # ========================================
        else:
            enemy_alive = any("equipe3" in (getattr(p, "name", "") or "").lower() for p in game.players)
 
            vg_cnt  = owned(game_id, CardName.VILLAGE)
            mk_cnt  = owned(game_id, CardName.MARKET)
            sm_cnt  = owned(game_id, CardName.SMITHY)
            wt_cnt  = owned(game_id, CardName.WITCH)
            lab_cnt = owned(game_id, CardName.LABORATORY)
            gd_cnt  = owned(game_id, CardName.GOLD)
            plat_cnt = owned(game_id, CardName.PLATINIUM)
            rc_cnt  = owned(game_id, CardName.HIRELING)
 
            curses_left   = stock.get(CardName.CURSE, 0) if CardName.CURSE in stock else 0
            villages_left = stock.get(CardName.VILLAGE, 0) if CardName.VILLAGE in stock else 0
            AGGRO_DUCHY   = (prov_left <= 4) or ((max_opponent_score - my_score) >= 4)
 
            print(f"[buy] AGGRESSIVE MODE ({num_players} players) | t={turn_no} $={money_available()} prov={prov_left} colony={colony_left} curses={curses_left} "
                f"owned: RC={rc_cnt} VG={vg_cnt} MK={mk_cnt} LAB={lab_cnt} WT={wt_cnt} SM={sm_cnt} GOLD={gd_cnt} PLAT={plat_cnt} "
                f"villages_left={villages_left} aggro_duchy={AGGRO_DUCHY} enemy_alive={enemy_alive}")
 
            # ===== 0) Colony > Province si possible (toujours)
            if can_buy(CardName.COLONY):
                return do_buy(CardName.COLONY)
            if can_buy(CardName.PROVINCE):
                return do_buy(CardName.PROVINCE)
 
            # ===== 1) EARLY GAME — anti-Équipe3 + installation HIRELING
            if turn_no <= 8:
                # 1a) Si aucune Witch et des Curses restent: Witch d'abord
                if curses_left > 0 and wt_cnt < 1 and can_buy(CardName.WITCH):
                    return do_buy(CardName.WITCH)
 
                # 1b) Platinium à 9$ (cap 1 en early)
                if can_buy(CardName.PLATINIUM) and plat_cnt < 1:
                    return do_buy(CardName.PLATINIUM)
 
                # 1c) À 6$ : HIRELING > GOLD (cap 2 en early)
                if can_buy(CardName.HIRELING) and rc_cnt < 2:
                    return do_buy(CardName.HIRELING)
 
                # 1d) À 5$ : Market (cap 2)
                if can_buy(CardName.MARKET) and mk_cnt < 2:
                    return do_buy(CardName.MARKET)
 
                # 1e) Gold (cap 1 en early)
                if can_buy(CardName.GOLD) and gd_cnt < 1:
                    return do_buy(CardName.GOLD)
 
                # 1f) Silver par défaut
                if can_buy(CardName.SILVER):
                    return do_buy(CardName.SILVER)
 
            # ===== 2) MID GAME — spam curse + stack HIRELING, puis deny Village
            if curses_left > 0:
                # 2a) 2e Witch (cap 2) — cap 3 si l'adversaire manque de Villages
                cap_witch = 3 if (enemy_alive and villages_left <= 7) else 2
                if can_buy(CardName.WITCH) and wt_cnt < cap_witch:
                    return do_buy(CardName.WITCH)
 
                # 2b) Stabilité: Market puis Laboratory (cap 2 chacun)
                if can_buy(CardName.MARKET) and mk_cnt < 2:
                    return do_buy(CardName.MARKET)
                if can_buy(CardName.LABORATORY) and lab_cnt < 2:
                    return do_buy(CardName.LABORATORY)
 
                # 2c) Platinium à 9$ (cap 2)
                if can_buy(CardName.PLATINIUM) and plat_cnt < 2:
                    return do_buy(CardName.PLATINIUM)
 
                # 2d) HIRELING à 6$ (cap 3 global)
                if can_buy(CardName.HIRELING) and rc_cnt < 3:
                    return do_buy(CardName.HIRELING)
 
                # 2e) Deny Village (cap 2 chez nous)
                if enemy_alive and villages_left > 0 and vg_cnt < 2 and can_buy(CardName.VILLAGE):
                    return do_buy(CardName.VILLAGE)
 
            # ===== 3) FIN DES CURSES — convertir l'avantage en points / tempo
            if curses_left == 0 and enemy_alive and villages_left <= 2:
                if can_buy(CardName.COLONY):
                    return do_buy(CardName.COLONY)
                if can_buy(CardName.PROVINCE):
                    return do_buy(CardName.PROVINCE)
                if AGGRO_DUCHY and can_buy(CardName.DUCHY):
                    return do_buy(CardName.DUCHY)
 
            # ===== 4) PLAN STANDARD (fallback)
            # Colony > Province
            if can_buy(CardName.COLONY):
                return do_buy(CardName.COLONY)
            if can_buy(CardName.PROVINCE):
                return do_buy(CardName.PROVINCE)
 
            # Duchy si rattrapage / fin
            if AGGRO_DUCHY and can_buy(CardName.DUCHY):
                return do_buy(CardName.DUCHY)
 
            # Platinium (cap 3)
            if can_buy(CardName.PLATINIUM) and plat_cnt < 3:
                return do_buy(CardName.PLATINIUM)
 
            # À 6$ : HIRELING (cap 3) > GOLD (cap 2)
            if can_buy(CardName.HIRELING) and rc_cnt < 3:
                return do_buy(CardName.HIRELING)
            if can_buy(CardName.GOLD) and gd_cnt < 2:
                return do_buy(CardName.GOLD)
 
            # À 5$ : Market (cap 2) > Laboratory (cap 2) > Witch (si Curses restent et < 2)
            if can_buy(CardName.MARKET) and mk_cnt < 2:
                return do_buy(CardName.MARKET)
            if can_buy(CardName.LABORATORY) and lab_cnt < 2:
                return do_buy(CardName.LABORATORY)
            if curses_left > 0 and can_buy(CardName.WITCH) and wt_cnt < 2:
                return do_buy(CardName.WITCH)
 
            # À 4$ : Smithy (cap 2) seulement si on a déjà +Actions
            if can_buy(CardName.SMITHY) and (vg_cnt + mk_cnt) >= 1 and sm_cnt < 2:
                return do_buy(CardName.SMITHY)
 
            # À 3$ : Silver
            if can_buy(CardName.SILVER):
                return do_buy(CardName.SILVER)
 
            # Duchy opportuniste
            if can_buy(CardName.DUCHY):
                return do_buy(CardName.DUCHY)
 
            # Estate tardif (éviter en early)
            if turn_no > 10 and can_buy(CardName.ESTATE):
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