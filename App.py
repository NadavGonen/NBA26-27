import os
import streamlit as st
import pandas as pd
import altair as alt
from espn_api.basketball import League
import plotly.express as px


# --- PAGE CONFIG ---
st.set_page_config(page_title="NBA Fantasy Dashboard", layout="wide")

LEAGUE_ID = 258148190
YEAR = 2027


# ==========================================
# region 1: DATA FETCHING & COMPUTATION
# ==========================================

@st.cache_resource
def build_league() -> League:
    espn_s2 = os.getenv("ESPN_S2")
    swid = os.getenv("SWID")

    if espn_s2 and swid:
        return League(league_id=LEAGUE_ID, year=YEAR, espn_s2=espn_s2, swid=swid)
    return League(league_id=LEAGUE_ID, year=YEAR)


def load_league_data():
    league = build_league()
    settings = league.settings
    reg_season_limit = getattr(settings, "reg_season_count", 19)

    team_rows = []
    matchup_rows = []
    player_rows = []

    for team in league.teams:
        team_rows.append({
            "team_id": getattr(team, "team_id", None),
            "team_name": getattr(team, "team_name", "Unknown"),
            "wins": getattr(team, "wins", 0),
            "losses": getattr(team, "losses", 0),
            "ties": getattr(team, "ties", 0),
            "standing": getattr(team, "standing", 0)
        })

        for player in getattr(team, "roster", []):
            stats = getattr(player, "stats", {})
            stat_bucket = stats.get('2027_projected', {}) or stats.get('2027_total', {})
            avg_stats = stat_bucket.get('avg', {}) if isinstance(stat_bucket, dict) else {}

            player_rows.append({
                "team_name": team.team_name,
                "player_name": getattr(player, "name", "Unknown"),
                "position": getattr(player, "position", "N/A"),
                "injury_status": getattr(player, "injuryStatus", "ACTIVE"),
                "PTS": avg_stats.get('PTS', 0.0),
                "REB": avg_stats.get('REB', 0.0),
                "AST": avg_stats.get('AST', 0.0),
                "STL": avg_stats.get('STL', 0.0),
                "BLK": avg_stats.get('BLK', 0.0),
                "3PM": avg_stats.get('3PM', 0.0),
                "FG%": avg_stats.get('FG%', 0.0),
                "FT%": avg_stats.get('FT%', 0.0),
                "TO": avg_stats.get('TO', 0.0),
                "raw_stats": stats
            })

        for week_idx, game in enumerate(getattr(team, "schedule", []), start=1):
            is_playoff_game = week_idx > reg_season_limit
            h_score = getattr(game, "home_final_score", 0)
            a_score = getattr(game, "away_final_score", 0)

            if h_score is not None and h_score > 0:
                is_home = (getattr(game.home_team, "team_name", "") == team.team_name)
                team_score = h_score if is_home else a_score
                opp_score = a_score if is_home else h_score
                opponent = game.away_team if is_home else game.home_team

                matchup_rows.append({
                    "week": week_idx,
                    "team_name": team.team_name,
                    "opponent_name": getattr(opponent, "team_name", "BYE") if opponent else "BYE",
                    "win": int(team_score > opp_score),
                    "is_playoff": is_playoff_game
                })

    df_teams = pd.DataFrame(team_rows)
    df_matchups = pd.DataFrame(matchup_rows)
    df_players = pd.DataFrame(player_rows)

    categories = ["PTS", "REB", "AST", "STL", "BLK", "3PM", "FG%", "FT%", "TO"]
    team_cat_totals = df_players.groupby("team_name")[categories].mean().reset_index()

    num_teams = len(team_cat_totals)

    for cat in categories:
        # דירוג אמיתי בליגה (1 עד מספר הקבוצות). ב-TO הפוך: נמוך זה מקום 1 (טוב יותר)
        ascending_order = True if cat == "TO" else False
        team_cat_totals[f"{cat}_rank"] = team_cat_totals[cat].rank(ascending=ascending_order, method="min")

        min_val = team_cat_totals[cat].min()
        max_val = team_cat_totals[cat].max()
        val_range = max_val - min_val if max_val != min_val else 1.0

        padded_max = max_val + (val_range * 0.15)
        padded_min = max(0, min_val - (val_range * 0.05))
        padded_range = padded_max - padded_min if padded_max != padded_min else 1.0

        if cat == "TO":
            normalized = (padded_max - team_cat_totals[cat]) / padded_range
        else:
            normalized = (team_cat_totals[cat] - padded_min) / padded_range

        team_cat_totals[f"{cat}_score"] = (normalized * 9.0 + 1.0).clip(1.0, 9.6).round(1)

        # צבע לפי טופ 3 / בוטום 3 בליגה
        def get_tier_color(rank):
            if rank <= 3:
                return "#28a745"  # טופ 3 - ירוק
            elif rank > num_teams - 3:
                return "#dc3545"  # 3 אחרונות - אדום
            return "#6c757d"  # אמצע - אפור

        team_cat_totals[f"{cat}_color"] = team_cat_totals[f"{cat}_rank"].apply(get_tier_color)

    df_team_scores = team_cat_totals

    return df_teams, df_matchups, df_players, df_team_scores


if "df_teams" not in st.session_state:
    st.session_state["df_teams"] = None
    st.session_state["df_matchups"] = None
    st.session_state["df_players"] = None
    st.session_state["df_team_scores"] = None

col_title, col_btn = [st.columns([4, 1])[0], st.columns([4, 1])[1]]
with col_title:
    st.title("🏀 NBA Fantasy Dashboard - 2026/27 (9-Cat)")
with col_btn:
    st.write("")
    if st.button("🔄 Load / Refresh Data", use_container_width=True):
        with st.spinner("Fetching data from ESPN API..."):
            df_t, df_m, df_p, df_s = load_league_data()
            st.session_state["df_teams"] = df_t
            st.session_state["df_matchups"] = df_m
            st.session_state["df_players"] = df_p
            st.session_state["df_team_scores"] = df_s
        st.success("Data loaded successfully!")
        st.rerun()

if st.session_state["df_teams"] is None:
    try:
        df_t, df_m, df_p, df_s = load_league_data()
        st.session_state["df_teams"] = df_t
        st.session_state["df_matchups"] = df_m
        st.session_state["df_players"] = df_p
        st.session_state["df_team_scores"] = df_s
    except Exception as e:
        st.warning(f"Could not auto-load data yet. Please click 'Load / Refresh Data'. Error: {e}")

# endregion

# region 2: APP TABS & VIEWS

tabs = st.tabs(["Standing", "Matchup", "Players","testing"])

# ------------------------------------------
# region SUB-REGION: TAB 0 - STANDINGS
# ------------------------------------------
with tabs[0]:
    if st.session_state["df_teams"] is not None and not st.session_state["df_teams"].empty:
        df_teams_raw = st.session_state["df_teams"]
        df_m = st.session_state["df_matchups"]

        # Filter out playoff games for regular standings graph
        if not df_m.empty and 'is_playoff' in df_m.columns:
            df_m_reg = df_m[df_m['is_playoff'] == False].copy()
        else:
            df_m_reg = df_m.copy()

        # Clean display table (drop internal IDs)
        df_display = (
            df_teams_raw
            .drop(columns=["team_id"], errors="ignore")
            .sort_values("standing", ascending=True)
        )

        teams_list = df_display["team_name"].unique()
        color_palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#e377c2", "#9467bd", "#8c564b", "#7f7f7f", "#bcbd22",
                         "#17becf", "#d62728"]
        team_to_color = {team: color_palette[i % len(color_palette)] for i, team in enumerate(teams_list)}

        # Layout: Left = Table, Right = Wins Progression Chart
        col_left, col_right = st.columns([1, 1])

        with col_left:
            st.markdown("<h3 style='text-align: center;'>Teams Table Regular Season</h3>", unsafe_allow_html=True)
            st.dataframe(df_display, use_container_width=True, hide_index=True)

        with col_right:
            st.markdown("<h3 style='text-align: center;'>Weekly Wins Progression (Click on graph)</h3>",
                        unsafe_allow_html=True)

            if not df_m_reg.empty:
                df_m_plot = df_m_reg.sort_values(["team_name", "week"])
                week0 = pd.DataFrame(
                    {"week": [0] * len(teams_list), "team_name": teams_list, "win": [0] * len(teams_list)})
                df_plot = pd.concat([week0, df_m_plot], ignore_index=True)
                df_plot["wins"] = df_plot.groupby("team_name")["win"].cumsum()

                selection = alt.selection_point(fields=['team_name'], on='click', name="team_select")
                chart = alt.Chart(df_plot).mark_line().encode(
                    x=alt.X('week:O', title='Week', axis=alt.Axis(labelAngle=0)),
                    y=alt.Y('wins:Q', title='Total Wins'),
                    color=alt.Color('team_name:N',
                                    scale=alt.Scale(domain=list(team_to_color.keys()),
                                                    range=list(team_to_color.values())),
                                    legend=None),
                    opacity=alt.condition(selection, alt.value(1), alt.value(0.1)),
                    strokeWidth=alt.condition(selection, alt.value(5), alt.value(1.5)),
                    tooltip=['team_name', 'week', 'wins']
                ).add_params(selection).properties(height=400)

                st.altair_chart(chart, use_container_width=True)
            else:
                st.info("No matchup history available yet for the graph.")
    else:
        st.info("Please click the 'Load / Refresh Data' button above to load the league table.")
# endregion

# ------------------------------------------
# region SUB-REGION: TAB 1 - TEAM ROSTER
# ------------------------------------------
with tabs[1]:
    st.subheader("Team Roster & 9-Cat Stats")

    df_p = st.session_state.get("df_players")
    df_t = st.session_state.get("df_teams")
    df_s = st.session_state.get("df_team_scores")

    if df_p is not None and not df_p.empty and df_t is not None:
        teams_list = df_t["team_name"].unique().tolist()
        selected_team = st.selectbox("Select Team to View Roster:", teams_list)

        if selected_team:
            cols_to_show = ["player_name", "position", "injury_status", "PTS", "REB", "AST", "STL", "BLK", "3PM", "FG%",
                            "FT%", "TO"]
            team_roster = df_p[df_p["team_name"] == selected_team][cols_to_show].copy()

            # 1. המרת עמודות הסטטיסטיקה למספריות
            stat_cols = ["PTS", "REB", "AST", "STL", "BLK", "3PM", "FG%", "FT%", "TO"]
            for col in stat_cols:
                if col in team_roster.columns:
                    team_roster[col] = pd.to_numeric(team_roster[col], errors='coerce').fillna(0.0)


            # 2. פונקציית צבעים רכה ועדינה לטבלה
            def highlight_stats(val, col_name):
                if not isinstance(val, (int, float)) and col_name != "injury_status":
                    return ""

                c_elite = "background-color: #c3e6cb; color: #155724; font-weight: bold;"
                c_good = "background-color: #e2f0d9; color: #276a3c;"
                c_bad = "background-color: #fff3cd; color: #856404;"
                c_poor = "background-color: #f8d7da; color: #721c24; font-weight: bold;"

                if col_name == "FG%":
                    if val >= 0.55: return c_elite
                    if val >= 0.48: return c_good
                    if val >= 0.43: return c_bad
                    return c_poor
                elif col_name == "FT%":
                    if val >= 0.85: return c_elite
                    if val >= 0.78: return c_good
                    if val >= 0.70: return c_bad
                    return c_poor
                elif col_name == "TO":
                    if val >= 3.2: return c_poor
                    if val >= 2.3: return c_bad
                    if val >= 1.2: return c_good
                    if val > 0:   return c_elite
                elif col_name == "PTS":
                    if val >= 24.0: return c_elite
                    if val >= 16.0: return c_good
                    if val >= 10.0: return c_bad
                    return c_poor
                elif col_name == "REB":
                    if val >= 10.0: return c_elite
                    if val >= 6.5:  return c_good
                    if val >= 4.0:  return c_bad
                    return c_poor
                elif col_name == "AST":
                    if val >= 7.5:  return c_elite
                    if val >= 4.5:  return c_good
                    if val >= 2.5:  return c_bad
                    return c_poor
                elif col_name == "STL":
                    if val >= 1.5:  return c_elite
                    if val >= 1.0:  return c_good
                    if val >= 0.6:  return c_bad
                    return c_poor
                elif col_name == "BLK":
                    if val >= 1.5:  return c_elite
                    if val >= 0.9:  return c_good
                    if val >= 0.4:  return c_bad
                    return c_poor
                elif col_name == "3PM":
                    if val >= 3.0:  return c_elite
                    if val >= 1.8:  return c_good
                    if val >= 1.0:  return c_bad
                    return c_poor
                elif col_name == "injury_status":
                    if val in ["OUT", "INJURED_RESERVE", "SUSPENDED"]:
                        return c_poor
                    elif val in ["DAY_TO_DAY", "QUESTIONABLE"]:
                        return c_bad

                return ""


            # 3. פרמוט מדויק ל-2 ספרות עשרוניות בטבלה
            format_dict = {col: "{:.2f}" for col in stat_cols}

            styled_roster = (
                team_roster.style
                .apply(lambda col: [highlight_stats(v, col.name) for v in col], axis=0)
                .format(format_dict)
            )

            st.markdown(f"### Roster for: {selected_team}")
            st.dataframe(styled_roster, use_container_width=True, hide_index=True)

            # ----------------------------------------------------
            # דירוג קטגוריות וגרף (סדר יורד נקי: הגבוה ביותר למעלה)
            # ----------------------------------------------------
            st.write("---")
            st.subheader("📊 Team Category Power Ranking (1.0 - 10.0 Scale)")

            if df_s is not None and not df_s.empty:
                t_score_row = df_s[df_s["team_name"] == selected_team]

                if not t_score_row.empty:
                    categories = ["PTS", "REB", "AST", "STL", "BLK", "3PM", "FG%", "FT%", "TO"]
                    cat_scores = []

                    for cat in categories:
                        score_val = float(t_score_row[f"{cat}_score"].values[0])
                        color_val = str(t_score_row[f"{cat}_color"].values[0])
                        cat_scores.append({
                            "Category": cat,
                            "Score": round(score_val, 1),
                            "Color": color_val
                        })

                    # מיון יורד (מהגבוה לקטן)
                    df_chart = pd.DataFrame(cat_scores).sort_values(by="Score", ascending=False).reset_index(drop=True)

                    fig = px.bar(
                        df_chart,
                        x="Score",
                        y="Category",
                        orientation="h",
                        text="Score",
                        title=f"Category Ratings for {selected_team}",
                        labels={"Score": "Rating (1.0 - 10.0)", "Category": "Category"},
                        category_orders={"Category": df_chart["Category"].tolist()}
                    )

                    # צביעה אינדיבידואלית לעמודות
                    fig.update_traces(
                        marker_color=df_chart["Color"].tolist(),
                        texttemplate='%{text:.1f}',
                        textposition='outside'
                    )

                    fig.update_layout(
                        xaxis=dict(range=[0, 10.5]),
                        height=380,
                        margin=dict(l=20, r=20, t=40, b=20),
                        showlegend=False
                    )

                    st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Please load data first using the 'Load / Refresh Data' button at the top.")
# endregion

# ------------------------------------------
# region SUB-REGION: TAB 3 - TESTING
# ------------------------------------------
with tabs[3]:
    st.subheader("🧪 API & Data Structure Testing")

    df_p = st.session_state.get("df_players")

    if df_p is not None and not df_p.empty:
        st.write("Here you can inspect the raw data structure returned by the ESPN API for players:")

        player_names = df_p["player_name"].unique().tolist()
        selected_player = st.selectbox("Select a player to inspect stats structure:", player_names)

        if selected_player:
            player_row = df_p[df_p["player_name"] == selected_player].iloc[0]
            st.write(f"**Player:** {player_row['player_name']} ({player_row['position']})")
            st.write("**Raw Stats Object / Dictionary:**")
            st.json(player_row["raw_stats"])
    else:
        st.info("Please load league data first.")
# endregion