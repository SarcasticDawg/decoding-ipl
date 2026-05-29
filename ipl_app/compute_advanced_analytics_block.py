def compute_advanced_analytics(innings_raw, info):
    players_map, teams, flat, timeline, first_total = info.get("players", {}), info.get("teams", []), [], [], None
    for idx, inn_block in enumerate(innings_raw):
        for _, inn_data in inn_block.items():
            rows = []
            for d in inn_data.get("deliveries", []):
                for b_id, ball in d.items(): rows.append((b_id, ball))
            flat.append({"index": idx + 1, "team": inn_data.get("team", ""), "deliveries": rows})

    player_delta, phase_stats = defaultdict(lambda: {"batting_wp": 0.0, "bowling_wp": 0.0, "fielding_wp": 0.0, "events": 0, "team": ""}), defaultdict(lambda: defaultdict(lambda: {"runs": 0, "balls": 0, "wickets": 0, "dots": 0}))
    momentum_events, pressure_curve, win_curve, recent_runs, recent_wickets, ewma_rr, alpha = [], [], [], deque(maxlen=12), deque(maxlen=12), 0.0, 0.35

    for inn in flat:
        batting_team, bowling_team, target, runs, wickets, legal_balls = inn["team"], next((t for t in teams if t != inn["team"]), ""), (first_total + 1 if first_total is not None else None), 0, 0, 0
        for raw_ball_id, ball in inn["deliveries"]:
            before = MatchState(inn["index"], batting_team, bowling_team, runs, wickets, legal_balls, target)
            before_wp = win_probability(before)
            runs_obj = ball.get("runs", {})
            total_r, bat_r, wicket_n, legal = int(runs_obj.get("total", 0)), int(runs_obj.get("batsman", runs_obj.get("batter", 0))), _wicket_count(ball), _is_legal_ball(ball)
            
            phase = _phase(raw_ball_id)
            batsman, bowler = ball.get("batsman", ball.get("batter", "")), ball.get("bowler", "")
            
            runs += total_r; wickets += wicket_n
            if legal: legal_balls += 1
            
            # 1. Phase Stats (All scoring included)
            ps = phase_stats[batting_team][phase]
            ps["runs"] += total_r; ps["wickets"] += wicket_n
            if legal:
                ps["balls"] += 1
                if total_r == 0: ps["dots"] += 1

            after = MatchState(inn["index"], batting_team, bowling_team, runs, wickets, legal_balls, target)
            after_wp = win_probability(after); delta = round(after_wp - before_wp, 4); pressure = pressure_index(after)

            # 2. Player Delta (WPA)
            if batsman:
                player_delta[batsman].update({"team": batting_team, "batting_wp": player_delta[batsman]["batting_wp"] + delta * (0.72 if bat_r else 0.35), "events": player_delta[batsman]["events"] + 1})
            if bowler:
                player_delta[bowler].update({"team": bowling_team, "bowling_wp": player_delta[bowler]["bowling_wp"] - delta * (0.58 if (bat_r == 0 or wicket_n) else 0.25), "events": player_delta[bowler]["events"] + 1})
            for wicket in _wicket_entries(ball):
                for fld in wicket.get("fielders", []):
                    fname = fld if isinstance(fld, str) else fld.get("name", "")
                    if fname:
                        player_delta[fname].update({"team": "", "fielding_wp": player_delta[fname]["fielding_wp"] - delta * (0.45 if "run out" in wicket.get("kind", "") else 0.30), "events": player_delta[fname]["events"] + 1})

            # 3. Momentum Sequence (Legal balls only)
            if legal:
                recent_runs.append(total_r); recent_wickets.append(wicket_n)
                if len(recent_runs) >= 6:
                    rolling_rr = sum(recent_runs) * 6 / len(recent_runs)
                    prev_ewma, ewma_rr = ewma_rr, (rolling_rr if ewma_rr == 0 else alpha * rolling_rr + (1 - alpha) * ewma_rr)
                    wicket_cluster, acceleration = sum(recent_wickets), ewma_rr - prev_ewma
                    if acceleration > 2.4 or wicket_cluster >= 2:
                        momentum_events.append({
                            "ball": str(raw_ball_id), "team": batting_team,
                            "type": "scoring acceleration" if acceleration > 2.4 else "wicket cluster",
                            "intensity": round(min(1.0, abs(acceleration) / 4 + wicket_cluster * 0.18), 3),
                            "rolling_run_rate": round(rolling_rr, 2), "ewma_run_rate": round(ewma_rr, 2),
                            "wickets_last_12": wicket_cluster
                        })

            timeline.append({"ball": str(raw_ball_id), "innings": inn["index"], "batting_team": batting_team, "bowling_team": bowling_team, "batter": batsman, "bowler": bowler, "runs": total_r, "bat_runs": bat_r, "wickets": wicket_n, "phase": phase, "state": asdict(after), "pressure": pressure, "win_probability_before": before_wp, "win_probability_after": after_wp, "win_probability_delta": delta})
            pressure_curve.append([len(timeline), pressure]); win_curve.append([len(timeline), after_wp, batting_team])
        
        if first_total is None: first_total = runs

    phase_dominance = []
    for team, phases in phase_stats.items():
        for phase, s in phases.items():
            rr = s["runs"] * 6 / (s["balls"] or 1); dot_pct = s["dots"] / (s["balls"] or 1)
            phase_dominance.append({"team": team, "phase": phase, "run_rate": round(rr, 2), "performance": f"{s['runs']}-{s['wickets']}", "wickets": s["wickets"], "dot_ball_pct": round(dot_pct * 100, 1), "dominance_score": round(rr * 0.55 + s["wickets"] * 1.45 - dot_pct * 2.0, 2)})
    
    phase_dominance.sort(key=lambda x: (x["phase"]=="powerplay", x["phase"]=="middle", x["phase"]=="death"), reverse=True)
    player_impact = sorted([{"name": n, "team": s["team"], "batting_wp": round(s["batting_wp"], 4), "bowling_wp": round(s["bowling_wp"], 4), "fielding_wp": round(s["fielding_wp"], 4), "total_wp": round(s["batting_wp"] + s["bowling_wp"] + s["fielding_wp"], 4), "events": s["events"]} for n, s in player_delta.items()], key=lambda x: abs(x["total_wp"]), reverse=True)
    
    candidates = []
    for e in timeline:
        delta_wp, abs_delta, pressure, over = e["win_probability_delta"], abs(e["win_probability_delta"]), e["pressure"], int(float(e["ball"]))
        tp_score = abs_delta * 100 * (1 + 0.25 * pressure)
        if e["wickets"] > 0: tp_score += 1.5
        if e["bat_runs"] >= 4 and over >= 16: tp_score += 1.0
        if e["runs"] == 0 and over >= 18 and pressure >= 0.85: tp_score += 0.8
        
        if abs_delta >= 0.05 or tp_score >= 6.0 or (e["wickets"] > 0 and abs_delta >= 0.035) or (over >= 18 and pressure >= 0.85 and abs_delta >= 0.025):
            candidates.append({"ball": e["ball"], "innings": e["innings"], "team": e["batting_team"], "batter": e["batter"], "bowler": e["bowler"], "event": f"{e['runs']} run(s), {e['wickets']} wicket(s)", "wp_delta": delta_wp, "pressure": pressure, "score": round(tp_score, 2), "benefited_team": (e["batting_team"] if delta_wp > 0 else e["bowling_team"]), "wp_before": round(e["win_probability_before"] * 100, 1), "wp_after": round(e["win_probability_after"] * 100, 1)})

    return {"summary": {"model": "hybrid_rule_logistic_v1", "balls_processed": len(timeline), "final_pressure": pressure_curve[-1][1] if pressure_curve else 0, "final_win_probability": win_curve[-1][1] if win_curve else 0.5}, "win_probability_curve": win_curve, "pressure_curve": pressure_curve, "momentum_events": momentum_events[-10:], "turning_points": sorted(candidates, key=lambda x: x["score"], reverse=True)[:8], "phase_dominance": phase_dominance, "player_impact_decomposition": player_impact[:10], "algorithm_spec": {}}
