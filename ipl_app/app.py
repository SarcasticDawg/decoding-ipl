#!/usr/bin/env python3
"""
IPL Impact Analyzer — Flask backend
"""
import os, re, json, math, yaml, zipfile, csv
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
from pathlib import Path
from flask import Flask, jsonify, send_from_directory, abort
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from urllib.parse import quote_plus, urlparse, parse_qs

app = Flask(__name__, static_folder="static", static_url_path="")
DATA_DIR = Path(__file__).parent / "data"
README   = Path(__file__).parent / "README.txt"

# Dataset Paths
DATASET_BASE = Path(__file__).parent.parent / "IPL-DATASET-main" / "csv"
BBB_DATA_CSV = DATASET_BASE / "Ball_By_Ball_Match_Data.csv"
MATCH_INFO_CSV = DATASET_BASE / "Match_Info.csv"
PLAYER_DETAILS_CSV = DATASET_BASE / "2024_players_details.csv"
TEAMS_INFO_CSV = DATASET_BASE / "teams_info.csv"

JSON_ZIP_CANDIDATES = [Path(__file__).parent / "ipl_json.zip", Path.home() / "Downloads" / "ipl_json.zip"]
PLAYER_FILE_CANDIDATES = [Path(__file__).parent / "ipl_players.json", Path(__file__).parent / "ipl_players.csv"]

# ── Helpers ───────────────────────────────────────────────────────────────────
def _name_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())

def _player_aliases(name):
    parts = [p for p in re.split(r"\s+", str(name).strip()) if p]
    if not parts: return set()
    aliases = {name}
    surname = parts[-1]
    given = parts[:-1]
    if given:
        aliases.add(f"{given[0][0]} {surname}")
        aliases.add(f"{''.join(p[0] for p in given)} {surname}")
    return {_name_key(alias) for alias in aliases}

def _load_player_profiles():
    profiles = {}
    all_rows = []
    for fp in PLAYER_FILE_CANDIDATES:
        if not fp.exists(): continue
        try:
            if fp.suffix.lower() == ".json": all_rows.extend(json.loads(fp.read_text(encoding="utf-8")))
            else:
                with open(fp, newline="", encoding="utf-8") as f: all_rows.extend(list(csv.DictReader(f)))
        except: continue
    for row in all_rows:
        name, url = str(row.get("name", "")).strip(), str(row.get("profile_url", "")).strip()
        if name and url:
            for alias in _player_aliases(name): profiles.setdefault(alias, url)
    return profiles

PLAYER_PROFILES = _load_player_profiles()

def _profile_url(name):
    direct = PLAYER_PROFILES.get(_name_key(name), "")
    if direct: return direct
    return f"https://www.iplt20.com/search?query={name.replace(' ', '+')}"

def _load_player_assets():
    assets = {}
    if not PLAYER_DETAILS_CSV.exists(): return assets
    try:
        with open(PLAYER_DETAILS_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name, img = row.get("longName") or row.get("Name", ""), row.get("imgUrl", "")
                if name and img: assets[_name_key(name)] = {"img": img, "role": row.get("playingRoles", ""), "style": row.get("battingStyles", "")}
    except Exception: pass
    return assets

def _load_team_assets():
    assets = {}
    if not TEAMS_INFO_CSV.exists(): return assets
    try:
        with open(TEAMS_INFO_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name, logo = row.get("team_name", ""), row.get("url", "")
                if name and logo: assets[_name_key(name)] = logo
    except Exception: pass
    return assets

PLAYER_ASSETS = _load_player_assets()
TEAM_ASSETS = _load_team_assets()

# ── Data Loading ──────────────────────────────────────────────────────────────
def _load_match_data_csv(match_id):
    if not BBB_DATA_CSV.exists(): return None
    match_id = str(match_id)
    deliveries_by_inning = defaultdict(list)
    team_per_inning = {}
    info = {"players": defaultdict(list)}
    try:
        with open(BBB_DATA_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("ID") != match_id: continue
                inning, over, ball_num = int(row.get("Innings", 1)), int(row.get("Overs", 0)), int(row.get("BallNumber", 1))
                ball_id, batting_team = f"{over}.{ball_num}", row.get("BattingTeam", "")
                team_per_inning[inning] = batting_team
                batter, bowler = row.get("Batter", ""), row.get("Bowler", "")
                total_runs, bat_runs, extra_runs = int(row.get("TotalRun", 0)), int(row.get("BatsmanRun", 0)), int(row.get("ExtrasRun", 0))
                extra_type = row.get("ExtraType", "")
                ball_data = {"batsman": batter, "bowler": bowler, "non_striker": row.get("NonStriker", ""), "runs": {"batsman": bat_runs, "total": total_runs, "extras": extra_runs}, "extras": {extra_type: extra_runs} if extra_type and extra_type != "NA" else {}}
                if row.get("IsWicketDelivery") == "1":
                    ball_data["wicket"] = {"player_out": row.get("PlayerOut", ""), "kind": row.get("Kind", ""), "fielders": [f.strip() for f in row.get("FieldersInvolved", "").split(",") if f.strip() and f.strip() != "NA"]}
                deliveries_by_inning[inning].append({ball_id: ball_data})
                if batter and batter not in info["players"][batting_team]: info["players"][batting_team].append(batter)
        if not deliveries_by_inning: return None
        if MATCH_INFO_CSV.exists():
            with open(MATCH_INFO_CSV, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("match_number") == match_id:
                        info.update({"venue": row.get("venue", ""), "city": row.get("city", ""), "dates": [row.get("match_date", "")]})
                        info["outcome"] = {"winner": row.get("winner", ""), "by": {}}
                        info["player_of_match"] = [p.strip() for p in row.get("player_of_match", "").split(",") if p.strip()]
                        info["teams"] = [row.get("team1", ""), row.get("team2", "")]
                        p1, p2 = [p.strip() for p in row.get("team1_players", "").split(",") if p.strip()], [p.strip() for p in row.get("team2_players", "").split(",") if p.strip()]
                        if p1 and p2: info["players"] = {row.get("team1", ""): p1, row.get("team2", ""): p2}
                        break
        innings_out = []
        for inn_idx in sorted(deliveries_by_inning.keys()):
            innings_out.append({f"{inn_idx} innings": {"team": team_per_inning.get(inn_idx, f"Team {inn_idx}"), "deliveries": deliveries_by_inning[inn_idx]}})
        return {"info": info, "innings": innings_out, "_data_source": "csv_dataset"}
    except Exception: return None

def _load_match_data(match_id):
    return _load_match_data_csv(match_id)

def _build_index():
    index = []
    if MATCH_INFO_CSV.exists():
        try:
            with open(MATCH_INFO_CSV, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    date, mid, t1, t2 = row.get("match_date", ""), row.get("match_number", ""), row.get("team1", ""), row.get("team2", "")
                    # Skip 2026 as requested
                    if mid and date and t1 and t2 and date[:4] != "2026":
                        index.append({"id": mid, "date": date, "year": date[:4], "team1": t1, "team2": t2, "t1_logo": TEAM_ASSETS.get(_name_key(t1), ""), "t2_logo": TEAM_ASSETS.get(_name_key(t2), ""), "venue": row.get("venue", ""), "city": row.get("city", ""), "source": "csv_dataset"})
            if index: return sorted(index, key=lambda x: x["date"], reverse=True)
        except Exception: pass
    return index

MATCH_INDEX = _build_index()
BY_YEAR = {}
for m in MATCH_INDEX: BY_YEAR.setdefault(m["year"], []).append(m)

# ── Math & Logic ──────────────────────────────────────────────────────────────
@dataclass
class MatchState:
    innings: int
    batting_team: str
    bowling_team: str
    runs: int
    wickets: int
    balls: int
    target: int | None = None
    @property
    def balls_remaining(self): return max(0, 120 - self.balls)
    @property
    def wickets_in_hand(self): return max(0, 10 - self.wickets)
    @property
    def crr(self): return self.runs * 6 / self.balls if self.balls else 0.0
    @property
    def runs_remaining(self): return max(0, self.target - self.runs) if self.target is not None else None
    @property
    def rrr(self):
        if self.target is None or self.runs >= self.target: return 0.0
        return self.runs_remaining * 6 / self.balls_remaining if self.balls_remaining else 99.0

def _sigmoid(x): return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))

def _phase(ball_id):
    try:
        over_num = int(float(str(ball_id)))
        if over_num < 6: return "powerplay"
        if over_num < 15: return "middle"
        return "death"
    except: return "middle"

def _is_legal_ball(ball): return "wides" not in ball.get("extras", {})
def _wicket_count(ball):
    wickets = ball.get("wickets")
    return len(wickets) if isinstance(wickets, list) else (1 if ball.get("wicket") else 0)
def _wicket_entries(ball):
    wickets = ball.get("wickets")
    if isinstance(wickets, list): return wickets
    wicket = ball.get("wicket")
    return [wicket] if wicket else []

def _bowler_wicket(kind): return kind in {"bowled", "caught", "caught and bowled", "lbw", "stumped", "hit wicket", "hit the ball twice"}
def _format_overs(balls): return f"{balls // 6}.{balls % 6}"

def _dismissal_text(wicket, bowler):
    if not wicket: return "not out"
    kind, fielders = wicket.get("kind", "out"), wicket.get("fielders", [])
    fielder_names = [n for n in [(f if isinstance(f, str) else f.get("name", "")) for f in fielders] if n]
    if kind == "bowled": return f"b {bowler}"
    if kind == "caught and bowled": return f"c & b {bowler}"
    if kind == "caught": return f"c {', '.join(fielder_names) or 'fielder'} b {bowler}"
    if kind == "stumped": return f"st {', '.join(fielder_names) or 'keeper'} b {bowler}"
    if kind == "run out": return f"run out ({', '.join(fielder_names) or 'fielder'})"
    if kind == "lbw": return f"lbw b {bowler}"
    return kind

def pressure_index(state):
    wicket_stress, ball_stress = 1 - (state.wickets_in_hand / 10), 1 - (state.balls_remaining / 120)
    scoring_stress = max(0.0, min(2.0, (8.5 - state.crr) / 8.5)) if state.target is None else max(0.0, min(3.0, state.rrr / max(state.crr, 1.0) - 1))
    return round(_sigmoid(1.30 * scoring_stress + 0.85 * wicket_stress + 0.55 * ball_stress - 0.75), 4)

def win_probability(state):
    if state.wickets >= 10: return 0.01
    if state.target is not None:
        if state.runs >= state.target: return 0.99
        if state.balls_remaining <= 0: return 0.01
        rr_gap, wickets, resource, runs_req = state.crr - state.rrr, state.wickets_in_hand, state.balls_remaining / 120, state.runs_remaining
        z = -0.75 + 0.30 * rr_gap + 0.32 * (wickets - 5) - 0.014 * runs_req + 0.008 * state.balls_remaining + 0.10 * (rr_gap * resource) + 0.08 * (wickets - 5) * resource
        return round(max(0.01, min(0.99, _sigmoid(z))), 4)
    else:
        proj = _projected_score(state)
        z = (proj - 175) / 18.0
        return round(max(0.05, min(0.95, _sigmoid(z))), 4)

def _projected_score(state):
    if state.wickets >= 10 or state.balls_remaining <= 0: return float(state.runs)
    exp_rr = max(5.0, min(15.0, (state.crr * 0.4) + (8.5 * 0.6) - (state.wickets * 0.6)))
    return round(state.runs + (exp_rr * state.balls_remaining / 6), 2)

# ── Core Functions ────────────────────────────────────────────────────────────
def compute_scorecards(innings_raw, info):
    players_map, teams, cards = info.get("players", {}), info.get("teams", []), []
    for inn_block in innings_raw:
        for _, inn_data in inn_block.items():
            batting_team = inn_data.get("team", "")
            bowling_team = next((team for team in teams if team != batting_team), "")
            batters, bowlers, extras_breakdown, total, wickets, legal_balls = {}, {}, defaultdict(int), 0, 0, 0
            for delivery in inn_data.get("deliveries", []):
                for ball_id, ball in delivery.items():
                    batsman, bowler = ball.get("batsman", ball.get("batter", "")), ball.get("bowler", "")
                    runs, extras_obj = ball.get("runs", {}), ball.get("extras", {})
                    bat_runs, total_runs = int(runs.get("batsman", 0)), int(runs.get("total", 0))
                    legal = _is_legal_ball(ball) and "noballs" not in extras_obj
                    total += total_runs
                    for k, v in extras_obj.items(): extras_breakdown[k] += int(v)
                    if batsman:
                        b = batters.setdefault(batsman, {"name": batsman, "dismissal": "not out", "runs": 0, "balls": 0, "fours": 0, "sixes": 0, "sr": 0.0, "profile_url": _profile_url(batsman)})
                        b["runs"] += bat_runs
                        if legal: b["balls"] += 1
                        if bat_runs == 4: b["fours"] += 1
                        if bat_runs == 6: b["sixes"] += 1
                    if bowler:
                        bw = bowlers.setdefault(bowler, {"name": bowler, "balls": 0, "runs": 0, "wickets": 0, "eco": 0.0, "profile_url": _profile_url(bowler)})
                        if legal: bw["balls"] += 1
                        bw["runs"] += total_runs - int(extras_obj.get("byes", 0)) - int(extras_obj.get("legbyes", 0))
                    if ball.get("wicket"):
                        wickets += 1
                        w = ball["wicket"]
                        p_out = w.get("player_out", batsman)
                        if p_out:
                            out_b = batters.setdefault(p_out, {"name": p_out, "dismissal": "not out", "runs": 0, "balls": 0, "fours": 0, "sixes": 0, "sr": 0.0, "profile_url": _profile_url(p_out)})
                            out_b["dismissal"] = _dismissal_text(w, bowler)
                        if bowler and _bowler_wicket(w.get("kind", "")): bowlers[bowler]["wickets"] += 1
                    if legal: legal_balls += 1
            for b in batters.values(): b["sr"] = round(b["runs"] * 100 / b["balls"], 2) if b["balls"] else 0.0
            for bw in bowlers.values(): bw["overs"] = _format_overs(bw["balls"]); bw["eco"] = round(bw["runs"] / (bw["balls"] / 6), 2) if bw["balls"] else 0.0
            bat_order = [p for p in players_map.get(batting_team, []) if p in batters]
            ordered_batters = [batters[p] for p in bat_order + [p for p in batters if p not in bat_order]]
            for b in ordered_batters:
                asset = PLAYER_ASSETS.get(_name_key(b["name"]), {})
                b["img"], b["role"] = asset.get("img", ""), asset.get("role", "")
            for bw in bowlers.values():
                asset = PLAYER_ASSETS.get(_name_key(bw["name"]), {})
                bw["img"], bw["role"] = asset.get("img", ""), asset.get("role", "")
            cards.append({"team": batting_team, "team_logo": TEAM_ASSETS.get(_name_key(batting_team), ""), "opponent": bowling_team, "opponent_logo": TEAM_ASSETS.get(_name_key(bowling_team), ""), "score": f"{total}-{wickets}", "runs": total, "wickets": wickets, "overs": _format_overs(legal_balls), "run_rate": round(total / (legal_balls / 6), 2) if legal_balls else 0.0, "batting": ordered_batters, "bowling": sorted(bowlers.values(), key=lambda b: b["balls"], reverse=True), "extras": {"total": sum(extras_breakdown.values())}, "did_not_bat": []})
    return cards

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
            total_r, bat_r, wicket_n, legal = int(runs_obj.get("total", 0)), int(runs_obj.get("batsman", 0)), _wicket_count(ball), _is_legal_ball(ball)
            phase = _phase(raw_ball_id)
            batsman, bowler = ball.get("batsman", ball.get("batter", "")), ball.get("bowler", "")
            runs += total_r
            wickets += wicket_n
            if legal: legal_balls += 1
            ps = phase_stats[batting_team][phase]
            ps["runs"] += total_r
            ps["wickets"] += wicket_n
            if legal:
                ps["balls"] += 1
                if total_r == 0: ps["dots"] += 1
            after = MatchState(inn["index"], batting_team, bowling_team, runs, wickets, legal_balls, target)
            after_wp = win_probability(after)
            delta = round(after_wp - before_wp, 4)
            pressure = pressure_index(after)
            if batsman: player_delta[batsman].update({"team": batting_team, "batting_wp": player_delta[batsman]["batting_wp"] + delta, "events": player_delta[batsman]["events"] + 1})
            if bowler: player_delta[bowler].update({"team": bowling_team, "bowling_wp": player_delta[bowler]["bowling_wp"] - delta, "events": player_delta[bowler]["events"] + 1})
            timeline.append({"ball": str(raw_ball_id), "innings": inn["index"], "batting_team": batting_team, "bowling_team": bowling_team, "batter": batsman, "bowler": bowler, "runs": total_r, "bat_runs": bat_r, "wickets": wicket_n, "phase": phase, "state": asdict(after), "pressure": pressure, "win_probability_before": before_wp, "win_probability_after": after_wp, "win_probability_delta": delta})
            pressure_curve.append([len(timeline), pressure])
            win_curve.append([len(timeline), after_wp, batting_team])
        if first_total is None: first_total = runs
    p_dom = []
    for t, phs in phase_stats.items():
        for p, s in phs.items():
            rr = s["runs"] * 6 / (s["balls"] or 1)
            dot_pct = s["dots"] / (s["balls"] or 1)
            p_dom.append({"team": t, "phase": p, "run_rate": round(rr, 2), "performance": f"{s['runs']}-{s['wickets']}", "wickets": s["wickets"], "dot_ball_pct": round(dot_pct * 100, 1), "dominance_score": round(rr * 0.55 + s["wickets"] * 1.45 - dot_pct * 2.0, 2)})
    p_dom.sort(key=lambda x: (x["phase"]=="powerplay", x["phase"]=="middle", x["phase"]=="death"), reverse=True)
    p_impact = sorted([{"name": n, "team": s["team"], "batting_wp": round(s["batting_wp"], 4), "bowling_wp": round(s["bowling_wp"], 4), "total_wp": round(s["batting_wp"] + s["bowling_wp"], 4)} for n, s in player_delta.items()], key=lambda x: abs(x["total_wp"]), reverse=True)
    cands = []
    for e in timeline:
        d_wp, a_wp, pr = e["win_probability_delta"], abs(e["win_probability_delta"]), e["pressure"]
        tp_s = a_wp * 100 * (1 + 0.25 * pr)
        if a_wp >= 0.05 or tp_s >= 6.0: cands.append({"ball": e["ball"], "innings": e["innings"], "team": e["batting_team"], "batter": e["batter"], "bowler": e["bowler"], "event": f"{e['runs']} run(s), {e['wickets']} wicket(s)", "wp_delta": d_wp, "pressure": pr, "score": round(tp_s, 2), "benefited_team": (e["batting_team"] if d_wp > 0 else e["bowling_team"]), "wp_before": round(e["win_probability_before"] * 100, 1), "wp_after": round(e["win_probability_after"] * 100, 1)})
    return {"summary": {"model": "hybrid_v1", "balls_processed": len(timeline), "final_pressure": pressure_curve[-1][1] if pressure_curve else 0, "final_win_probability": win_curve[-1][1] if win_curve else 0.5}, "win_probability_curve": win_curve, "pressure_curve": pressure_curve, "turning_points": sorted(cands, key=lambda x: x["score"], reverse=True)[:8], "phase_dominance": p_dom, "player_impact_decomposition": p_impact[:10]}

def compute_impact(match_id: str):
    data = _load_match_data(match_id)
    if data is None: return None
    info = data.get("info", {})
    inn_raw, teams, winner, by, players_map = data.get("innings", []), info.get("teams", []), info.get("outcome", {}).get("winner", ""), info.get("outcome", {}).get("by", {}), info.get("players", {})
    bat, bowl, field, innings_out, first_total, worm = {}, {}, {}, [], None, {}
    bat_pos = {(team, p): i + 1 for team, p_list in players_map.items() for i, p in enumerate(p_list)}
    PHASE_E = {"powerplay": 1.35, "middle": 1.25, "death": 1.70, "default": 1.40}
    for inn_idx, inn_block in enumerate(inn_raw):
        for _, inn_data in inn_block.items():
            team, deliveries = inn_data.get("team", ""), inn_data.get("deliveries", [])
            inn_runs, inn_wkts, inn_balls, target = 0, 0, 0, (first_total + 1 if first_total is not None else None)
            batter_entry_wkts, cur_over, curve = {}, {"num": -1, "runs": 0, "balls": 0}, []
            for delivery in deliveries:
                for ball_id, ball in delivery.items():
                    ov_num = int(float(str(ball_id)))
                    phase = _phase(ball_id)
                    batsman, non_striker, bowler = ball.get("batsman", ball.get("batter", "")), ball.get("non_striker", ""), ball.get("bowler", "")
                    runs_obj = ball.get("runs", {})
                    bat_r, total_r, extras_r = int(runs_obj.get("batsman", 0)), int(runs_obj.get("total", 0)), int(runs_obj.get("extras", 0))
                    wicket, legal = ball.get("wicket"), (_is_legal_ball(ball) and "noballs" not in ball.get("extras", {}))
                    if batsman and batsman not in batter_entry_wkts: batter_entry_wkts[batsman] = inn_wkts
                    if non_striker and non_striker not in batter_entry_wkts: batter_entry_wkts[non_striker] = inn_wkts
                    state_before = MatchState(inn_idx+1, team, "", inn_runs, inn_wkts, inn_balls, target)
                    val_before = (_projected_score(state_before) if target is None else win_probability(state_before)*100)
                    inn_runs += total_r
                    if wicket: inn_wkts += 1
                    if legal: inn_balls += 1
                    state_after = MatchState(inn_idx+1, team, "", inn_runs, inn_wkts, inn_balls, target)
                    val_after = (_projected_score(state_after) if target is None else win_probability(state_after)*100)
                    try:
                        p1, p2 = map(int, str(ball_id).split('.'))
                        exact_over = p1 + (p2/6.0)
                    except: exact_over = inn_balls/6.0
                    curve.append([round(exact_over, 3), inn_runs, inn_wkts])
                    if batsman:
                        b = bat.setdefault(batsman, {"runs":0, "balls":0, "fours":0, "sixes":0, "dots":0, "team":team, "balls_pp":0, "balls_mid":0, "balls_death":0, "is_winning_shot": False, "entry_wkts": batter_entry_wkts[batsman]})
                        b["runs"] += bat_r
                        if legal: b["balls"] += 1
                        if bat_r == 4: b["fours"] += 1
                        if bat_r == 6: b["sixes"] += 1
                        if bat_r == 0: b["dots"] += 1
                        if phase == "powerplay": b["balls_pp"] += (1 if legal else 0)
                        elif phase == "middle": b["balls_mid"] += (1 if legal else 0)
                        else: b["balls_death"] += (1 if legal else 0)
                    if bowler:
                        bw = bowl.setdefault(bowler, {"balls":0, "runs":0, "wickets":0, "dots":0, "maidens":0, "team":"", "balls_pp":0, "balls_mid":0, "balls_death":0, "dots_pp":0, "dots_mid":0, "dots_death":0, "wkt_value_total": 0.0, "is_final_over_bowler": False})
                        if legal: bw["balls"] += 1; bw["runs"] += (total_r - extras_r)
                        if bat_r == 0 and extras_r == 0:
                            bw["dots"] += 1
                            if phase == "powerplay": bw["dots_pp"] += 1
                            elif phase == "middle": bw["dots_mid"] += 1
                            else: bw["dots_death"] += 1
                        if phase == "powerplay": bw["balls_pp"] += (1 if legal else 0)
                        elif phase == "middle": bw["balls_mid"] += (1 if legal else 0)
                        else: bw["balls_death"] += (1 if legal else 0)
                        if not bw["team"]: bw["team"] = next((t for t, pl in players_map.items() if bowler in pl), "")
                        if cur_over["num"] != ov_num: cur_over = {"num": ov_num, "runs": 0, "balls": 0}
                        cur_over["runs"] += total_r
                        if legal: cur_over["balls"] += 1
                        if cur_over["balls"] == 6 and cur_over["runs"] == 0: bw["maidens"] += 1
                        if wicket and wicket.get("kind") not in ["run out", "retired hurt"]:
                            bw["wickets"] += 1; opp_team = next((t for t in teams if t != team), ""); pos = bat_pos.get((opp_team, wicket.get("player_out", "")), 0)
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
        B, R_con = bw["balls"], bw["runs"]
        if B == 0: continue
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
    comb = {}
    for n, s in bat.items(): comb[n] = comb.get(n, 0) + s.get("final_impact", 0)
    for n, s in bowl.items(): comb[n] = comb.get(n, 0) + s.get("final_impact", 0)
    for n, s in field.items(): comb[n] = comb.get(n, 0) + s.get("impact", 0)
    all_p = []
    for name, total in comb.items():
        rating = round(max(0.1, min(10.0, 10 * total / (total + 45))), 1)
        b, bw = bat.get(name, {}), bowl.get(name, {})
        runs, wkts = b.get("runs", 0), bw.get("wickets", 0)
        role = "all" if runs > 20 and wkts > 1 else ("bat" if runs > 15 else "bowl")
        stat = f"{runs}({b.get('balls',0)}) SR:{round(runs/max(b.get('balls',1),1)*100,1)}" if role == "bat" else (f"{wkts}/{bw.get('runs',0)} ({bw.get('balls',0)//6}.{bw.get('balls',0)%6} ov)" if role == "bowl" else f"{runs}r & {wkts}w")
        all_p.append({"name": name, "score": rating, "total_raw": total, "role": role, "stat": stat, "team": b.get("team") or bw.get("team") or "", "runs": runs, "balls": b.get("balls",0), "wkts": wkts, "fours": b.get("fours",0), "sixes": b.get("sixes",0), "eco": round((bw.get("runs",0)/max(bw.get("balls",1),1))*6, 2) if bw.get("balls") else 0})
    all_p.sort(key=lambda x: x["total_raw"], reverse=True)
    summary = [dict(team=i["team"], score=f"{i['runs']}/{i['wickets']}", overs=f"{i['balls']//6}.{i['balls']%6}") for i in innings_out]
    if by.get("runs"): res = f"{winner} won by {by['runs']} runs"
    elif by.get("wickets"): res = f"{winner} won by {by['wickets']} wickets"
    elif winner:
        i1, i2 = innings_out[0], innings_out[1]
        res = f"{winner} won by {i1['runs']-i2['runs']} runs" if winner == i1["team"] else f"{winner} won by {10-i2['wickets']} wickets"
    else: res = "No result"
    return dict(match_id=match_id, date=str(info.get("dates", [""])[0]), venue=info.get("venue",""), city=info.get("city",""), teams=teams, winner=winner, result=res, data_source=data.get("_data_source", "unknown"), pom=info.get("player_of_match", []), innings=summary, scorecards=compute_scorecards(inn_raw, info), top_players=all_p[:10], motm=all_p[0] if all_p else None, advanced=compute_advanced_analytics(inn_raw, info), worm=worm)

def _get_match_number_for_id(match_id):
    match_id = str(match_id)
    for yr, matches in BY_YEAR.items():
        yr_matches = sorted(matches, key=lambda x: x["date"])
        for i, m in enumerate(yr_matches):
            if str(m["id"]) == match_id: return i + 1, yr
    return None, None

@app.route("/api/highlights/<match_id>")
def api_highlights(match_id):
    data = _load_match_data(match_id)
    if not data: abort(404)

    info = data.get("info", {})
    t1, t2 = info.get("teams", ["", ""])
    m_no, year = _get_match_number_for_id(match_id)

    # THE EXACT QUERY FORMAT REQUESTED - Now the primary method
    if m_no:
        query = f"site:iplt20.com/video IPL {year} Match {m_no} {t1} vs {t2} Highlights"
    else:
        query = f"site:iplt20.com/video IPL {info.get('dates',['2024'])[0][:4]} {t1} vs {t2} Highlights"

    # Direct redirection to Google for 100% reliability and speed
    url = f"https://www.google.com/search?q={quote_plus(query)}"
    return jsonify({"url": url})

@app.route("/api/points-table/<year>")
def api_points_table(year):
    pt_file = Path(__file__).parent.parent / "ipl_points_tables" / f"ipl_{year}.csv"
    if not pt_file.exists():
        return jsonify([])
    
    try:
        with open(pt_file, newline="", encoding="utf-8") as f:
            data = list(csv.DictReader(f))
            for row in data:
                team_name = row.get("TEAM", "")
                row["LOGO"] = TEAM_ASSETS.get(_name_key(team_name), "")
            return jsonify(data)
    except Exception:
        return jsonify([])

@app.route("/api/stats/<year>")
def api_stats(year):
    base_dir = Path(__file__).parent.parent / "IPL" / str(year)
    if not base_dir.exists():
        return jsonify({"bat": {}, "bowl": {}})
    
    results = {"bat": {}, "bowl": {}}
    
    # Load Batting Stats
    bat_dir = base_dir / "Batting"
    if bat_dir.exists():
        for csv_file in bat_dir.glob("*.csv"):
            category = csv_file.stem.replace("_", " ").title()
            try:
                with open(csv_file, newline="", encoding="utf-8") as f:
                    results["bat"][category] = list(csv.DictReader(f))
            except: continue
            
    # Load Bowling Stats
    bowl_dir = base_dir / "Bowling"
    if bowl_dir.exists():
        for csv_file in bowl_dir.glob("*.csv"):
            category = csv_file.stem.replace("_", " ").title()
            try:
                with open(csv_file, newline="", encoding="utf-8") as f:
                    results["bowl"][category] = list(csv.DictReader(f))
            except: continue
            
    return jsonify(results)

@app.route("/")
def index(): return send_from_directory("static", "index.html")
@app.route("/api/years")
def api_years(): return jsonify(sorted(BY_YEAR.keys(), reverse=True))
@app.route("/api/matches/<year>")
def api_matches(year): return jsonify(BY_YEAR.get(year, []))
@app.route("/api/analyze/<match_id>")
def api_analyze(match_id):
    if not re.match(r"^\d+$", match_id): abort(400)
    result = compute_impact(match_id)
    if result is None: abort(404)
    return jsonify(result)

if __name__ == "__main__":
    print("🏏  IPL Impact Analyzer running at http://localhost:5000")
    app.run(debug=False, port=5000)
