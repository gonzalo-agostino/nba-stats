"""Deterministic Parlay (Combinada) Builder, DuckDB persistence, and automatic boxscore grader."""

from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from nba_stats.api.schemas import ParlayBuildRequest, SaveParlayRequest
from nba_stats.data.schema import init_schema


def estimate_leg_probability(item: dict[str, Any]) -> float:
    """Estimate realistic single-leg hit probability (bounded 0.42–0.78) from real trends + ML."""
    l10_pct = float((item.get("l10") or {}).get("pct") or 50.0) / 100.0
    l5_pct = float((item.get("l5") or {}).get("pct") or l10_pct * 100.0) / 100.0
    l20_pct = float((item.get("l20") or {}).get("pct") or l10_pct * 100.0) / 100.0

    # Shrink empirical hit rates slightly toward 0.52 so small samples don't overfit
    empirical = 0.50 * l10_pct + 0.25 * l5_pct + 0.25 * l20_pct
    prob = 0.72 * empirical + 0.28 * 0.52

    ml_agrees = bool(item.get("ml_agrees", False))
    abs_edge = abs(float(item.get("ml_edge") or 0.0))
    if ml_agrees:
        # Sweet spot observed in live performance: Edge 1.0 - 4.0
        if 1.0 <= abs_edge <= 4.0:
            prob += 0.05
        elif abs_edge > 4.0:
            prob += 0.025
        else:
            prob += 0.015
    else:
        prob -= 0.03

    if bool(item.get("blowout_risk")) and str(item.get("recommended_side")) == "OVER":
        prob -= 0.05
    if str(item.get("injury_status") or "") == "DTD":
        prob -= 0.04
    if bool(item.get("is_back_to_back")) and str(item.get("recommended_side")) == "OVER":
        prob -= 0.015

    return round(max(0.42, min(0.78, prob)), 4)


def _format_leg(item: dict[str, Any], locked: bool) -> dict[str, Any]:
    est_prob = estimate_leg_probability(item)
    return {
        "pick_id": str(item["id"]),
        "game_id": str(item.get("game_id") or ""),
        "player_id": int(item["player_id"]),
        "player_name": str(item["player_name"]),
        "team_abbreviation": str(item["team_abbreviation"]),
        "opponent_abbreviation": str(item["opponent_abbreviation"]),
        "is_home": bool(item.get("is_home", False)),
        "is_back_to_back": bool(item.get("is_back_to_back", False)),
        "injury_status": item.get("injury_status"),
        "blowout_risk": bool(item.get("blowout_risk", False)),
        "stat": str(item["stat"]),
        "bookmaker": str(item.get("bookmaker") or ""),
        "line": float(item["line"]),
        "side": str(item["recommended_side"]),
        "odds_decimal": round(float(item["odds_decimal"]), 2),
        "ml_projection": round(float(item["ml_projection"]), 1),
        "ml_edge": round(float(item["ml_edge"]), 1),
        "ml_agrees": bool(item.get("ml_agrees", False)),
        "l10_pct": float((item.get("l10") or {}).get("pct") or 0.0),
        "l10_hits": int((item.get("l10") or {}).get("hits") or 0),
        "l10_total": int((item.get("l10") or {}).get("total") or 0),
        "conviction_score": int(item.get("conviction_score") or 0),
        "est_prob": est_prob,
        "locked": locked,
    }


def _summarize_parlay(
    legs: list[dict[str, Any]],
    target_odds: float,
    strategy: str,
) -> dict[str, Any]:
    combined_odds = 1.0
    joint_prob = 1.0
    warnings: list[str] = []

    for leg in legs:
        combined_odds *= max(1.01, float(leg["odds_decimal"]))
        joint_prob *= float(leg["est_prob"])
        if leg.get("blowout_risk") and leg.get("side") == "OVER":
            warnings.append(f"{leg['player_name']}: riesgo de paliza")
        if leg.get("injury_status") == "DTD":
            warnings.append(f"{leg['player_name']}: DTD (lesión)")
        if leg.get("is_back_to_back"):
            warnings.append(f"{leg['player_name']}: Back-to-Back")

    combined_odds = round(combined_odds, 2)
    joint_prob_pct = round(joint_prob * 100.0, 1)
    implied_prob_pct = round((1.0 / combined_odds) * 100.0, 1) if combined_odds > 0 else 0.0
    ev_pct = round(((joint_prob * combined_odds) - 1.0) * 100.0, 1)
    avg_conviction = (
        round(sum(int(leg["conviction_score"]) for leg in legs) / len(legs), 1)
        if legs
        else 0.0
    )

    return {
        "strategy": strategy,
        "legs": legs,
        "num_legs": len(legs),
        "combined_odds": combined_odds,
        "target_odds": round(target_odds, 2),
        "odds_diff": round(combined_odds - target_odds, 2),
        "estimated_prob_pct": joint_prob_pct,
        "implied_prob_pct": implied_prob_pct,
        "ev_pct": ev_pct,
        "avg_conviction": avg_conviction,
        "warnings": list(dict.fromkeys(warnings))[:4],
    }


def build_parlay_recommendations(
    board_items: list[dict[str, Any]],
    request: ParlayBuildRequest,
) -> dict[str, Any]:
    """Build up to `max_results` optimal parlay suggestions matching target_odds and locked picks.

    Allows the same player multiple times with different stats by default (deduplicates by pick_id).
    """
    by_id = {str(item["id"]): item for item in board_items}
    excluded_set = set(request.excluded_pick_ids)

    # 1. Resolve locked picks (preserving user order, unique by pick_id)
    locked_items: list[dict[str, Any]] = []
    seen_locked_ids: set[str] = set()
    for pid in request.locked_pick_ids:
        if pid in by_id and pid not in seen_locked_ids:
            locked_items.append(by_id[pid])
            seen_locked_ids.add(pid)

    locked_legs = [_format_leg(it, locked=True) for it in locked_items]
    locked_odds = 1.0
    for lg in locked_legs:
        locked_odds *= max(1.01, float(lg["odds_decimal"]))

    max_legs = max(request.max_legs, len(locked_legs))
    min_legs = min(max(request.min_legs, len(locked_legs)), max_legs)

    # If locked picks already reach target_odds and satisfy min_legs, return them directly
    if locked_legs and len(locked_legs) >= min_legs and locked_odds >= request.target_odds * 0.95:
        single = _summarize_parlay(locked_legs, request.target_odds, "Tus picks fijados")
        return {
            "target_odds": request.target_odds,
            "locked_count": len(locked_legs),
            "candidates_evaluated": 0,
            "parlays": [single],
            "note": "Tus picks fijados ya alcanzan la cuota objetivo.",
        }

    # 2. Filter candidate pool for auto-completion
    allowed_stats = (
        {s.strip().lower() for s in request.allowed_stats if s.strip()}
        if request.allowed_stats
        else None
    )
    locked_players = {int(lg["player_id"]) for lg in locked_legs}
    locked_games = {str(lg["game_id"]) for lg in locked_legs if lg.get("game_id")}

    def _filter_pool(strict: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in board_items:
            item_id = str(item["id"])
            if item_id in seen_locked_ids or item_id in excluded_set:
                continue
            if allowed_stats and str(item["stat"]).lower() not in allowed_stats:
                continue
            if request.one_per_player and int(item["player_id"]) in locked_players:
                continue
            if request.one_per_game and str(item.get("game_id") or "") in locked_games:
                continue
            if strict:
                if request.require_ml_agrees and not bool(item.get("ml_agrees")):
                    continue
                if (
                    request.exclude_blowout
                    and bool(item.get("blowout_risk"))
                    and str(item.get("recommended_side")) == "OVER"
                ):
                    continue
                if request.exclude_dtd and str(item.get("injury_status") or "") == "DTD":
                    continue
            out.append(item)
        return out

    pool_raw = _filter_pool(strict=True)
    relaxed_note: str | None = None
    if len(pool_raw) < max(1, min_legs - len(locked_legs)):
        pool_raw = _filter_pool(strict=False)
        if pool_raw:
            relaxed_note = "Se relajaron filtros de calidad para encontrar suficientes patas."

    candidates = [_format_leg(it, locked=False) for it in pool_raw]
    # Rank candidates by estimated probability & conviction score, cap to top 28 for instant search
    candidates.sort(
        key=lambda c: (c["est_prob"], c["conviction_score"], abs(c["ml_edge"])),
        reverse=True,
    )
    candidates = candidates[:28]

    # 3. Search valid combinations (beam/DFS)
    target = float(request.target_odds)
    min_target = target * 0.88
    max_target = target * 1.45
    slots_min = max(0, min_legs - len(locked_legs))
    slots_max = max(0, max_legs - len(locked_legs))

    found: list[tuple[list[dict[str, Any]], float, float]] = []  # (legs, odds, prob)
    best_fallback: tuple[list[dict[str, Any]], float, float] | None = None
    best_fallback_dist = float("inf")

    locked_prob = 1.0
    for lg in locked_legs:
        locked_prob *= float(lg["est_prob"])

    def _dfs(
        start_idx: int,
        chosen: list[dict[str, Any]],
        cur_odds: float,
        cur_prob: float,
        used_players: set[int],
        used_games: set[str],
    ) -> None:
        nonlocal best_fallback, best_fallback_dist
        k = len(chosen)
        if k >= slots_min:
            dist = abs(math.log(max(cur_odds, 1.01) / max(target, 1.01)))
            if dist < best_fallback_dist and (k > 0 or len(locked_legs) > 0):
                best_fallback_dist = dist
                best_fallback = (locked_legs + chosen, cur_odds, cur_prob)

            if min_target <= cur_odds <= max_target:
                found.append((locked_legs + chosen, cur_odds, cur_prob))
                if len(found) >= 400:
                    return

        if k >= slots_max or cur_odds > max_target or len(found) >= 400:
            return

        for idx in range(start_idx, len(candidates)):
            cand = candidates[idx]
            pid = int(cand["player_id"])
            gid = str(cand.get("game_id") or "")
            if request.one_per_player and pid in used_players:
                continue
            if request.one_per_game and gid and gid in used_games:
                continue

            next_odds = cur_odds * max(1.01, float(cand["odds_decimal"]))
            # Prune if already way above max_target
            if next_odds > max_target * 1.15:
                continue

            if request.one_per_player:
                used_players.add(pid)
            if request.one_per_game and gid:
                used_games.add(gid)

            _dfs(
                idx + 1,
                chosen + [cand],
                next_odds,
                cur_prob * float(cand["est_prob"]),
                used_players,
                used_games,
            )

            if request.one_per_player:
                used_players.discard(pid)
            if request.one_per_game and gid:
                used_games.discard(gid)

            if len(found) >= 400:
                return

    _dfs(
        0,
        [],
        locked_odds,
        locked_prob,
        set(locked_players),
        set(locked_games),
    )

    if not found and best_fallback is not None:
        found.append(best_fallback)

    if not found:
        return {
            "target_odds": target,
            "locked_count": len(locked_legs),
            "candidates_evaluated": len(candidates),
            "parlays": [],
            "note": "No se encontraron suficientes picks para armar una combinada con estos filtros.",
        }

    # 4. Select up to `max_results` distinct parlays by strategy
    selected: list[dict[str, Any]] = []
    seen_signatures: set[frozenset[str]] = set()

    def _pick_best(
        strategy_name: str,
        key_fn,
    ) -> None:
        if len(selected) >= request.max_results:
            return
        ranked = sorted(found, key=key_fn, reverse=True)
        for legs, _odds, _prob in ranked:
            sig = frozenset(str(lg["pick_id"]) for lg in legs)
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)
            selected.append(_summarize_parlay(legs, target, strategy_name))
            return

    # Strategy 1: Más Segura (highest joint probability)
    _pick_best("Más Segura", lambda x: (x[2], -abs(x[1] - target)))
    # Strategy 2: Mejor Balance / EV (highest EV = prob * odds)
    _pick_best("Mejor Balance (EV)", lambda x: (x[2] * x[1], x[2]))
    # Strategy 3: Cuota Exacta (closest to target_odds, tie-break by probability)
    _pick_best("Cuota Exacta", lambda x: (-abs(x[1] - target), x[2]))

    return {
        "target_odds": target,
        "locked_count": len(locked_legs),
        "candidates_evaluated": len(candidates),
        "parlays": selected,
        "note": relaxed_note,
    }


class ParlayService:
    """Persists user-saved parlays in DuckDB and grades them automatically against NBA boxscores."""

    def __init__(self, duckdb_path: Path) -> None:
        self.duckdb_path = duckdb_path

    def _connect_rw(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(str(self.duckdb_path))
        init_schema(con)
        return con

    def save_parlay(self, req: SaveParlayRequest) -> dict[str, Any]:
        ref_date = date.fromisoformat(req.game_date[:10])
        parlay_id = f"parlay_{ref_date.isoformat()}_{uuid.uuid4().hex[:8]}"
        now_ts = datetime.now(UTC).replace(tzinfo=None)

        legs_payload = [
            {
                **lg.model_dump(),
                "status": "PENDING",
                "actual_value": None,
            }
            for lg in req.legs
        ]

        with self._connect_rw() as con:
            con.execute(
                """
                INSERT INTO tracked_parlays (
                    parlay_id, game_date, label, target_odds, combined_odds,
                    estimated_prob, stake_units, legs_json, status,
                    legs_won, legs_lost, legs_void, legs_total,
                    effective_odds, profit_units, created_at, graded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, 0, 0, ?, ?, NULL, ?, NULL)
                """,
                [
                    parlay_id,
                    ref_date,
                    req.label.strip() or "Combinada",
                    req.target_odds,
                    round(float(req.combined_odds), 2),
                    req.estimated_prob,
                    round(float(req.stake_units), 2),
                    json.dumps(legs_payload, ensure_ascii=False),
                    len(legs_payload),
                    round(float(req.combined_odds), 2),
                    now_ts,
                ],
            )

        return {
            "parlay_id": parlay_id,
            "game_date": ref_date.isoformat(),
            "label": req.label.strip() or "Combinada",
            "combined_odds": round(float(req.combined_odds), 2),
            "legs_total": len(legs_payload),
            "status": "PENDING",
        }

    def delete_parlay(self, parlay_id: str) -> bool:
        with self._connect_rw() as con:
            existing = con.execute(
                "SELECT COUNT(*) FROM tracked_parlays WHERE parlay_id = ?",
                [parlay_id],
            ).fetchall()[0][0]
            if not existing:
                return False
            con.execute("DELETE FROM tracked_parlays WHERE parlay_id = ?", [parlay_id])
            return True

    def grade_pending_parlays(self) -> dict[str, Any]:
        """Grade all PENDING parlays leg-by-leg against actual boxscores in player_game_logs."""
        now_ts = datetime.now(UTC).replace(tzinfo=None)
        graded = 0
        wins = 0
        losses = 0
        voids = 0

        with self._connect_rw() as con:
            pending_rows = con.execute(
                """
                SELECT parlay_id, game_date, stake_units, legs_json
                FROM tracked_parlays
                WHERE status = 'PENDING'
                """
            ).fetchall()

            for parlay_id, gdate, stake_units, legs_json in pending_rows:
                legs: list[dict[str, Any]] = json.loads(legs_json)
                any_changed = False

                for lg in legs:
                    if lg.get("status") not in (None, "PENDING"):
                        continue
                    pid = int(lg["player_id"])
                    stat = str(lg["stat"])
                    line = float(lg["line"])
                    side = str(lg["side"]).upper()

                    log_rows = con.execute(
                        """
                        SELECT min, pts, reb, ast, stl, blk, tov, fg3m
                        FROM player_game_logs
                        WHERE player_id = ?
                          AND game_date BETWEEN ? AND ?
                        ORDER BY ABS(DATEDIFF('day', game_date, ?)) ASC
                        LIMIT 1
                        """,
                        [pid, gdate - timedelta(days=1), gdate + timedelta(days=1), gdate],
                    ).fetchall()

                    if not log_rows:
                        day_games = int(
                            con.execute(
                                "SELECT COUNT(*) FROM games WHERE game_date = ?",
                                [gdate],
                            ).fetchall()[0][0]
                        )
                        if day_games > 0 and gdate < date.today():
                            lg["status"] = "VOID"
                            lg["actual_value"] = None
                            any_changed = True
                        continue

                    m_min, pts, reb, ast, stl, blk, tov, fg3m = [
                        float(x or 0.0) for x in log_rows[0]
                    ]
                    if m_min <= 0.0:
                        lg["status"] = "VOID"
                        lg["actual_value"] = 0.0
                        any_changed = True
                        continue

                    stat_map = {
                        "min": m_min,
                        "pts": pts,
                        "reb": reb,
                        "ast": ast,
                        "pra": pts + reb + ast,
                        "pr": pts + reb,
                        "pa": pts + ast,
                        "ra": reb + ast,
                        "stl": stl,
                        "blk": blk,
                        "tov": tov,
                        "fg3m": fg3m,
                    }
                    actual = round(stat_map.get(stat, 0.0), 2)
                    lg["actual_value"] = actual
                    if abs(actual - line) < 1e-6:
                        lg["status"] = "VOID"
                    elif (side == "OVER" and actual > line) or (side == "UNDER" and actual < line):
                        lg["status"] = "WIN"
                    else:
                        lg["status"] = "LOSS"
                    any_changed = True

                l_won = sum(1 for lg in legs if lg.get("status") == "WIN")
                l_lost = sum(1 for lg in legs if lg.get("status") == "LOSS")
                l_void = sum(1 for lg in legs if lg.get("status") == "VOID")
                l_pending = sum(1 for lg in legs if lg.get("status") in (None, "PENDING"))

                # Calculate effective odds excluding VOID legs
                eff_odds = 1.0
                for lg in legs:
                    if lg.get("status") != "VOID":
                        eff_odds *= max(1.01, float(lg["odds_decimal"]))
                eff_odds = round(eff_odds, 2)

                stake = float(stake_units or 1.0)
                new_status = "PENDING"
                profit: float | None = None
                graded_at = None

                if l_lost > 0:
                    new_status = "LOSS"
                    profit = round(-stake, 2)
                    graded_at = now_ts
                    graded += 1
                    losses += 1
                elif l_pending == 0:
                    if l_won > 0:
                        new_status = "WIN"
                        profit = round(stake * (eff_odds - 1.0), 2)
                        wins += 1
                    else:
                        new_status = "VOID"
                        profit = 0.0
                        voids += 1
                    graded_at = now_ts
                    graded += 1

                if any_changed or new_status != "PENDING":
                    con.execute(
                        """
                        UPDATE tracked_parlays
                        SET legs_json = ?, status = ?, legs_won = ?, legs_lost = ?,
                            legs_void = ?, effective_odds = ?, profit_units = ?, graded_at = ?
                        WHERE parlay_id = ?
                        """,
                        [
                            json.dumps(legs, ensure_ascii=False),
                            new_status,
                            l_won,
                            l_lost,
                            l_void,
                            eff_odds,
                            profit,
                            graded_at,
                            parlay_id,
                        ],
                    )

        return {
            "graded": graded,
            "wins": wins,
            "losses": losses,
            "voids": voids,
        }

    def get_parlay_history(self, limit: int = 100) -> dict[str, Any]:
        """Return accumulated Win Rate, ROI, net units, and list of tracked parlays."""
        with self._connect_rw() as con:
            rows = con.execute(
                """
                SELECT
                    parlay_id, CAST(game_date AS VARCHAR), label, target_odds,
                    combined_odds, estimated_prob, stake_units, legs_json,
                    status, legs_won, legs_lost, legs_void, legs_total,
                    effective_odds, profit_units, CAST(created_at AS VARCHAR)
                FROM tracked_parlays
                ORDER BY created_at DESC
                """
            ).fetchall()

        all_parlays = [
            {
                "parlay_id": str(r[0]),
                "game_date": str(r[1])[:10],
                "label": str(r[2]),
                "target_odds": float(r[3]) if r[3] is not None else None,
                "combined_odds": float(r[4]),
                "estimated_prob": float(r[5]) if r[5] is not None else None,
                "stake_units": float(r[6]),
                "legs": json.loads(r[7]),
                "status": str(r[8]),
                "legs_won": int(r[9]),
                "legs_lost": int(r[10]),
                "legs_void": int(r[11]),
                "legs_total": int(r[12]),
                "effective_odds": float(r[13]) if r[13] is not None else float(r[4]),
                "profit_units": float(r[14]) if r[14] is not None else None,
                "created_at": str(r[15])[:19],
            }
            for r in rows
        ]

        wins = sum(1 for p in all_parlays if p["status"] == "WIN")
        losses = sum(1 for p in all_parlays if p["status"] == "LOSS")
        voids = sum(1 for p in all_parlays if p["status"] == "VOID")
        pending = sum(1 for p in all_parlays if p["status"] == "PENDING")
        decided = wins + losses
        staked = round(
            sum(float(p["stake_units"]) for p in all_parlays if p["status"] in ("WIN", "LOSS")),
            2,
        )
        net_units = round(
            sum(float(p["profit_units"] or 0.0) for p in all_parlays),
            2,
        )
        win_rate = round((wins / decided) * 100.0, 1) if decided > 0 else None
        roi = round((net_units / staked) * 100.0, 1) if staked > 0 else None
        avg_odds = (
            round(sum(float(p["combined_odds"]) for p in all_parlays) / len(all_parlays), 2)
            if all_parlays
            else None
        )

        return {
            "overall": {
                "total": len(all_parlays),
                "wins": wins,
                "losses": losses,
                "voids": voids,
                "pending": pending,
                "win_rate_pct": win_rate,
                "roi_pct": roi,
                "net_units": net_units,
                "avg_odds": avg_odds,
            },
            "parlays": all_parlays[:limit],
        }
