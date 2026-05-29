# 🏏 IPL Impact Analyzer

Ball-by-ball impact scoring for all 1212 IPL matches (2008–2026).

## Setup

1. **Install dependencies**
   ```
   pip install -r requirements.txt
   ```

2. **Add your data**
   Put all the Cricsheet `.yaml` files inside the `data/` folder.
   Also copy `README.txt` from the Cricsheet zip into the same directory as `app.py`.

3. **Run**
   ```
   python app.py
   ```

4. Open **http://localhost:5000** in your browser.

## How it works

### Impact Formula
- **Batting:** `runs × pressure_boost × phase_multiplier + boundary_bonus − dot_penalty`
- **Bowling:** `wickets × 15 × pressure × phase + dot_bonus − runs_conceded_penalty`
- **Fielding:** catches (+5), stumpings (+6), run-outs (+4)
- **Phases:** Powerplay (ov 1–6) 1.1×/1.2×, Middle (7–15) 1.0×, Death (16–20) 1.3×
- **Pressure:** scales with wickets fallen in current innings
- **Normalization:** √(min–max) spread to 0–10

### Features
- Year selector → match dropdown (all 1212 matches)
- Ball-by-ball engine runs locally, no API calls
- Top 5 impact players with score bars
- Run progression worm chart (both innings)
- Batting & bowling stat tables
