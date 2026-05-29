# ── Impact engine ─────────────────────────────────────────────────────────────
def compute_impact(match_id: str):
    data = _load_match_data(match_id)
    if data is None: return None
    info, innings_raw, teams, winner, by, pom, dates, players_map = data.get("info", {}), data.get("innings", []), data.get("teams", []), data.get("info", {}).get("outcome", {}).get("winner", ""), data.get("info", {}).get("outcome", {}).get("by", {}), data.get("info", {}).get("player_of_match", []), data.get("info", {}).get("dates", []), data.get("info", {}).get("players", {})
    bat, bowl, field, innings_out, first_total = {}, {}, {}, [], None
    batting_positions = {(team, p): i + 1 for team, p_list in players_map.items() for i, p in enumerate(p_list)}
    PHASE_E = {"powerplay": 1.35, "middle": 1.25, "death": 1.70, "default": 1.40}
    worm = {}

    for inn_idx, inn_block in enumerate(innings_raw):
        for _, inn_data in inn_block.items():
            team, deliveries = inn_data.get("team", ""), inn_data.get("deliveries", [])
            inn_runs, inn_wkts, inn_balls, target = 0, 0, 0, (first_total + 1 if first_total is not None else None)
            batter_entry_wkts, current_over, curve = {}, {"num": -1, "runs": 0, "balls": 0}, []
            for delivery in deliveries:
                for ball_id, ball in delivery.items():
                    over_num = int(float(str(ball_id))); phase = "powerplay" if over_num < 6 else "middle" if over_num < 15 else "death"
                    batsman, non_striker, bowler = ball.get("batsman", ball.get("batter", "")), ball.get("non_striker", ""), ball.get("bowler", "")
                    runs_obj = ball.get("runs", {}); bat_r, total_r, extras_r = int(runs_obj.get("batsman", 0)), int(runs_obj.get("total", 0)), int(runs_obj.get("extras", 0))
                    wicket, is_legal = ball.get("wicket"), (_is_legal_ball(ball) and "noballs" not in ball.get("extras", {}))
                    if batsman and batsman not in batter_entry_wkts: batter_entry_wkts[batsman] = inn_wkts
                    if non_striker and non_striker not in batter_entry_wkts: batter_entry_wkts[non_striker] = inn_wkts
                    
                    state_before = MatchState(inn_idx+1, team, "", inn_runs, inn_wkts, inn_balls, target)
                    val_before = (_projected_score(state_before) if target is None else win_probability(state_before)*100)
                    inn_runs += total_r; if wicket: inn_wkts += 1; if is_legal: inn_balls += 1
                    state_after = MatchState(inn_idx+1, team, "", inn_runs, inn_wkts, inn_balls, target)
                    val_after = (_projected_score(state_after) if target is None else win_probability(state_after)*100)
                    raw_delta = val_after - val_before
                    
                    curve.append([round(inn_balls/6, 2), inn_runs, inn_wkts])

                    if batsman:
                        b = bat.setdefault(batsman, {"runs":0, "balls":0, "fours":0, "sixes":0, "dots":0, "team":team, "balls_pp":0, "balls_mid":0, "balls_death":0, "is_winning_shot": False, "entry_wkts": batter_entry_wkts[batsman]})
                        b["runs"] += bat_r
                        if is_legal: b["balls"] += 1
                        if bat_r == 4: b["fours"] += 1
                        if bat_r == 6: b["sixes"] += 1
                        if bat_r == 0: b["dots"] += 1
                        if phase == "powerplay": b["balls_pp"] += (1 if is_legal else 0)
                        elif phase == "middle": b["balls_mid"] += (1 if is_legal else 0)
                        else: b["balls_death"] += (1 if is_legal else 0)
                    if bowler:
                        bw = bowl.setdefault(bowler, {"balls":0, "runs":0, "wickets":0, "dots":0, "maidens":0, "team":"", "balls_pp":0, "balls_mid":0, "balls_death":0, "dots_pp":0, "dots_mid":0, "dots_death":0, "wkt_value_total": 0.0, "is_final_over_bowler": False})
                        if is_legal: bw["balls"] += 1; bw["runs"] += (total_r - extras_r)
                        if bat_r == 0 and extras_r == 0:
                            bw["dots"] += 1
                            if phase == "powerplay": bw["dots_pp"] += 1
                            elif phase == "middle": bw["dots_mid"] += 1
                            else: bw["dots_death"] += 1
                        if phase == "powerplay": bw["balls_pp"] += (1 if is_legal else 0)
                        elif phase == "middle": bw["balls_mid"] += (1 if is_legal else 0)
                        else: bw["balls_death"] += (1 if is_legal else 0)
                        if not bw["team"]:
                            for t, pl in players_map.items():
                                if bowler in pl: bw["team"] = t; break
                        if current_over["num"] != over_num: current_over = {"num": over_num, "runs": 0, "balls": 0}
                        current_over["runs"] += total_r
                        if is_legal: current_over["balls"] += 1
                        if current_over["balls"] == 6 and current_over["runs"] == 0: bw["maidens"] += 1
                        if wicket and wicket.get("kind") not in ["run out", "retired hurt"]:
                            bw["wickets"] += 1; opp_team = next((t for t in teams if t != team), ""); pos = batting_positions.get((opp_team, wicket.get("player_out", "")), 0)
                            val = 18 if 1<=pos<=3 else 15 if 4<=pos<=6 else 11 if 7<=pos<=8 else 7 if 9<=pos<=11 else 15
                            bw["wkt_value_total"] += (val * (1 + 0.04 * (inn_wkts-1)))
                    if wicket:
                        for fld in wicket.get("fielders", []):
                            fname = (fld if isinstance(fld, str) else fld.get("name", ""))
                            if fname:
                                fe = field.setdefault(fname, {"impact": 0.0}); kind = wicket.get("kind", "")
                                if kind == "caught": fe["impact"] += 5
                                elif kind == "stumped": fe["impact"] += 6
                                elif kind == "run out": fe["impact"] += (8 if len(wicket.get("fielders",[]))==1 else 5)
                    if target and inn_runs >= target:
                        if batsman: bat[batsman]["is_winning_shot"] = True
                        if bowler: bowl[bowler]["is_final_over_bowler"] = True
            
            worm[team] = curve
            innings_out.append(dict(team=team, runs=inn_runs, wickets=inn_wkts, balls=inn_balls))
            if first_total is None: first_total = inn_runs

    for name, b in bat.items():
        R, B, F, S, D = b["runs"], b["balls"], b["fours"], b["sixes"], b["dots"]
        if B == 0: continue
        E = (b["balls_pp"] * 1.35 + b["balls_mid"] * 1.25 + b["balls_death"] * 1.70) / B
        bi = (0.65 * R) + (1.25 * (R - B * E)) + (1.0 * F) + (2.0 * S) - (0.35 * D)
        sr = (R / B) * 100
        if R >= 100 and sr >= 170: bi += 25
        elif R >= 75 and sr >= 160: bi += 14
        elif R >= 50 and sr >= 150: bi += 8
        if b.get("is_winning_shot"):
            bi += 10
            if b["balls_death"] > 0: bi += 5
        if b["entry_wkts"] >= 3 and b["balls_pp"] > 0: bi += 5
        if not b.get("is_winning_shot"):
            if R < 20: bi *= 0.60
            elif R < 30: bi *= 0.75
        b["final_impact"] = bi

    for name, bw in bowl.items():
        B, R_con = bw["balls"], bw["runs"]; if B == 0: continue
        E_bowl = (bw["balls_pp"] * 1.35 + bw["balls_mid"] * 1.25 + bw["balls_death"] * 1.70) / B
        boi = 1.10 * (B * E_bowl - R_con) + bw["wkt_value_total"] + (bw["dots_pp"] * 0.8 + bw["dots_mid"] * 0.6 + bw["dots_death"] * 1.2) + (bw["maidens"] * 8)
        if B >= 12:
            eco = (R_con / B) * 6
            if eco <= 5: boi += 10
            elif eco <= 6: boi += 7
            elif eco <= 7: boi += 4
            elif eco >= 13: boi -= 10
            elif eco >= 11: boi -= 5
        if bw["balls_death"] >= 12: boi += 6
        if bw["balls_pp"] >= 12: boi += 4
        if bw.get("is_final_over_bowler"): boi += 8
        bw["final_impact"] = boi

    combined = {}
    for n, s in bat.items(): combined[n] = combined.get(n, 0) + s.get("final_impact", 0)
    for n, s in bowl.items(): combined[n] = combined.get(n, 0) + s.get("final_impact", 0)
    for n, s in field.items(): combined[n] = combined.get(n, 0) + s.get("impact", 0)
    
    all_players = []
    for name, total in combined.items():
        rating = round(max(0.1, min(10.0, 10 * total / (total + 45))), 1)
        b, bw = bat.get(name, {}), bowl.get(name, {})
        runs, wkts = b.get("runs", 0), bw.get("wickets", 0)
        role = "all" if runs > 20 and wkts > 1 else ("bat" if runs > 15 else "bowl")
        stat = f"{runs}({b.get('balls',0)}) SR:{round(runs/max(b.get('balls',1),1)*100,1)}" if role == "bat" else (f"{wkts}/{bw.get('runs',0)} ({bw.get('balls',0)//6}.{bw.get('balls',0)%6} ov)" if role == "bowl" else f"{runs}r & {wkts}w")
        eco = round((bw.get("runs",0)/max(bw.get("balls",1),1))*6, 2) if bw.get("balls") else 0
        all_players.append({"name": name, "score": rating, "total_raw": total, "role": role, "stat": stat, "team": b.get("team") or bw.get("team") or "", "runs": runs, "balls": b.get("balls",0), "wkts": wkts, "fours": b.get("fours",0), "sixes": b.get("sixes",0), "eco": eco})
    
    all_players.sort(key=lambda x: x["total_raw"], reverse=True)
    innings_summary = [dict(team=i["team"], score=f"{i['runs']}/{i['wickets']}", overs=f"{i['balls']//6}.{i['balls']%6}") for i in innings_out]
    if by.get("runs"): result = f"{winner} won by {by['runs']} runs"
    elif by.get("wickets"): result = f"{winner} won by {by['wickets']} wickets"
    elif winner:
        i1, i2 = innings_out[0], innings_out[1]
        result = f"{winner} won by {i1['runs']-i2['runs']} runs" if winner == i1["team"] else f"{winner} won by {10-i2['wickets']} wickets"
    else: result = "No result"

    return dict(match_id=match_id, date=str(dates[0]) if dates else "", venue=info.get("venue",""), city=info.get("city",""), teams=teams, winner=winner, result=result, data_source=data.get("_data_source", "unknown"), pom=pom, innings=innings_summary, scorecards=compute_scorecards(innings_raw, info), top_players=all_players[:10], motm=all_players[0] if all_players else None, advanced=compute_advanced_analytics(innings_raw, info), worm=worm)
