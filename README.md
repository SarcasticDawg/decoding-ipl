# 🏏 Decoding IPL: Ball-by-Ball Impact Analytics

[![Live Demo](https://img.shields.io/badge/demo-live-green.svg)](#) 
[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/flask-v2.0+-lightgrey.svg)](https://flask.palletsprojects.com/)

A cinematic, high-performance web application designed to analyze the **real impact** of players in every IPL match from 2008 to 2026. Unlike traditional scorecards, this engine uses pressure indices, phase-wise dominance, and win-probability shifts to identify the true "game-changers."

## 🚀 Key Features

*   **Impact Scoring Engine:** Advanced algorithm calculating player ratings based on pressure, phase (Powerplay/Middle/Death), and momentum.
*   **Worm Charts & Win Probability:** Interactive visualizations of run progression and live win-probability shifts.
*   **Turning Points:** AI-driven identification of the specific balls that changed the course of the match.
*   **Seasonal Leaderboards:** Track the Orange Cap, Purple Cap, and Impact leaders across any IPL season.
*   **Cinematic Experience:** Immersive UI with background video hype and a professional design.

## 🛠️ Installation & Setup

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/SarcasticDawg/decoding-ipl.git
    cd decoding-ipl
    ```

2.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Prepare Data**
    *   Ensure the `IPL-DATASET-main/csv` directory is populated with match data.
    *   For the ball-by-ball engine, ensure Cricsheet YAML files are in `ipl_app/data/`.

4.  **Run the Application**
    ```bash
    cd ipl_app
    python app.py
    ```
    Open `http://localhost:5000` in your browser.

## 📊 The Impact Formula

The application goes beyond runs and wickets:
*   **Batting:** `runs × pressure_boost × phase_multiplier + boundary_bonus − dot_penalty`
*   **Bowling:** `wickets × 15 × pressure × phase + dot_bonus − runs_conceded_penalty`
*   **Fielding:** Points for catches, stumpings, and run-outs.
*   **Normalization:** All scores are scaled to a 0–10 "Impact Rating."

## 📂 Project Structure

*   `ipl_app/`: Core Flask application and frontend.
*   `ipl_points_tables/`: Historical points tables for all seasons.
*   `IPL/`: Processed batting and bowling statistics.
*   `IPL-DATASET-main/`: Raw CSV data for matches and players.
*   `Procfile`: Configuration for production deployment (Gunicorn).

## 🤝 Contributing

Contributions are welcome! Please open an issue or submit a pull request for any improvements or bug fixes.

---
*Created by [SarcasticDawg](https://github.com/SarcasticDawg)*
