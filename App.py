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

    # 1. שליפת השבוע הנוכחי המדויק
    current_week = getattr(league, "currentMatchupPeriod", 1)

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

        # --- שליפת משחקי הלו"ז ---
        for week_idx, game in enumerate(getattr(team, "schedule", []), start=1):
            is_playoff_game = week_idx > reg_season_limit
            h_score = getattr(game, "home_final_score", 0) or 0
            a_score = getattr(game, "away_final_score", 0) or 0

            home_team_obj = getattr(game, "home_team", None)
            away_team_obj = getattr(game, "away_team", None)
            home_team_name = getattr(home_team_obj, "team_name", "") if home_team_obj else ""

            is_home = (home_team_name == team.team_name)
            team_score = h_score if is_home else a_score
            opp_score = a_score if is_home else h_score
            opponent = away_team_obj if is_home else home_team_obj

            matchup_rows.append({
                "week": week_idx,
                "team_name": team.team_name,
                "opponent_name": getattr(opponent, "team_name", "BYE") if opponent else "BYE",
                "team_score": team_score,
                "opponent_score": opp_score,
                "win": int(team_score > opp_score) if (team_score > 0 or opp_score > 0) else 0,
                "is_playoff": is_playoff_game
            })

    df_teams = pd.DataFrame(team_rows)
    df_matchups = pd.DataFrame(matchup_rows)
    df_players = pd.DataFrame(player_rows)

    # --- 2. בניית טבלת ניצחונות הקטגוריות השבועית (Week Score Matrix) ---
    score_records = []
    for w in range(1, current_week + 1):
        try:
            boxes = league.box_scores(w)
            for box in boxes:
                h_name = box.home_team.team_name.strip()
                a_name = box.away_team.team_name.strip()

                h_cat_wins = int(getattr(box, 'home_wins', 0))
                a_cat_wins = int(getattr(box, 'away_wins', 0))

                score_records.append({"Week": f"Week {w}", "Team": h_name, "Category Wins": h_cat_wins})
                score_records.append({"Week": f"Week {w}", "Team": a_name, "Category Wins": a_cat_wins})
        except Exception as err:
            pass

    df_scores_raw = pd.DataFrame(score_records)
    if not df_scores_raw.empty:
        df_week_scores = df_scores_raw.pivot(index="Team", columns="Week", values="Category Wins").fillna(0).astype(int)
        valid_cols = [f"Week {w}" for w in range(1, current_week + 1) if f"Week {w}" in df_week_scores.columns]
        df_week_scores = df_week_scores[valid_cols]
        df_week_scores.index.name = None
        df_week_scores.columns.name = None
    else:
        df_week_scores = pd.DataFrame()

    # סינון שבועות עתידיים ב-df_matchups
    if not df_matchups.empty:
        active_weeks = df_matchups.groupby("week").apply(
            lambda w: (w["team_score"].sum() > 0 or w["opponent_score"].sum() > 0)
        )
        valid_weeks = active_weeks[active_weeks].index.tolist()

        if not valid_weeks:
            valid_weeks = [1]

        df_matchups = df_matchups[df_matchups["week"].isin(valid_weeks)].reset_index(drop=True)

    categories = ["PTS", "REB", "AST", "STL", "BLK", "3PM", "FG%", "FT%", "TO"]
    team_cat_totals = df_players.groupby("team_name")[categories].mean().reset_index()

    num_teams = len(team_cat_totals)

    for cat in categories:
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

        def get_tier_color(rank):
            if rank <= 2:
                return "#28a745"  # טופ 3 - ירוק
            elif rank > num_teams - 2:
                return "#dc3545"  # 3 אחרונות - אדום
            return "#6c757d"  # אמצע - אפור

        team_cat_totals[f"{cat}_color"] = team_cat_totals[f"{cat}_rank"].apply(get_tier_color)

    df_team_scores = team_cat_totals

    return df_teams, df_matchups, df_players, df_team_scores, current_week, df_week_scores


# מילון צבעים קבוע לכל הקבוצות בליגה
TEAM_COLORS = {
    "Orpaz, Owner of Sachim": "#1f77b4",  # כחול
    "Nadav's Nifty Team": "#ff7f0e",  # כתום
    "Idan- Kendis's P1mp": "#2ca02c",  # ירוק
    "Alon’s Average Heroes": "#e377c2",  # ורוד
    "Eldad's Endless Excuses Team": "#9467bd",  # סגול
    "Ethan's Fat Homeboys": "#8c564b",  # חום
    "Team Galgal": "#17becf",  # טורקיז
    "Amit's Awesome Team": "#7f7f7f"  # אפור
}

if "TEAM_COLORS" not in st.session_state:
    st.session_state["TEAM_COLORS"] = TEAM_COLORS

if "df_teams" not in st.session_state:
    st.session_state["df_teams"] = None
    st.session_state["df_matchups"] = None
    st.session_state["df_players"] = None
    st.session_state["df_team_scores"] = None
    st.session_state["current_week"] = 1
    st.session_state["df_week_scores"] = None

col_title, col_btn = st.columns([4, 1])
with col_title:
    st.title("🏀 NBA Fantasy Dashboard - 2026/27 (9-Cat)")
with col_btn:
    st.write("")
    if st.button("🔄 Load / Refresh Data", use_container_width=True):
        with st.spinner("Fetching data from ESPN API..."):
            df_t, df_m, df_p, df_s, curr_w, df_ws = load_league_data()
            if df_t is not None and not df_t.empty:
                df_t["color"] = df_t["team_name"].map(st.session_state["TEAM_COLORS"]).fillna("#333333")
            st.session_state["df_teams"] = df_t
            st.session_state["df_matchups"] = df_m
            st.session_state["df_players"] = df_p
            st.session_state["df_team_scores"] = df_s
            st.session_state["current_week"] = curr_w
            st.session_state["df_week_scores"] = df_ws
        st.success("Data loaded successfully!")
        st.rerun()

if st.session_state["df_teams"] is None:
    try:
        df_t, df_m, df_p, df_s, curr_w, df_ws = load_league_data()
        if df_t is not None and not df_t.empty:
            df_t["color"] = df_t["team_name"].map(st.session_state["TEAM_COLORS"]).fillna("#333333")
        st.session_state["df_teams"] = df_t
        st.session_state["df_matchups"] = df_m
        st.session_state["df_players"] = df_p
        st.session_state["df_team_scores"] = df_s
        st.session_state["current_week"] = curr_w
        st.session_state["df_week_scores"] = df_ws
    except Exception as e:
        st.warning(f"Could not auto-load data yet. Please click 'Load / Refresh Data'. Error: {e}")

# endregion

# region 2: APP TABS & VIEWS

tabs = st.tabs(["Standing", "Roster", "Matchup","testing"])

# ------------------------------------------
# region SUB-REGION: TAB 0 - STANDINGS
# ------------------------------------------
with tabs[0]:
    if st.session_state.get("df_teams") is not None and not st.session_state["df_teams"].empty:
        df_teams_raw = st.session_state["df_teams"].copy()

        # ניקוי שמות
        df_teams_raw["team_name"] = df_teams_raw["team_name"].astype(str).str.strip()

        # שליפת מילון הצבעים מ-session_state
        team_colors = st.session_state.get("TEAM_COLORS", {})


        # עיגול צבעוני ממורכז
        def make_color_circle(color_hex):
            return f'<div style="display: flex; justify-content: center; align-items: center;"><span style="color:{color_hex}; font-size: 22px; line-height: 1;">●</span></div>'


        df_teams_raw["Color_Hex"] = df_teams_raw["team_name"].map(team_colors).fillna("#333333")
        df_teams_raw["Color"] = df_teams_raw["Color_Hex"].apply(make_color_circle)

        # מיון לפי standing מ-ESPN
        df_sorted = (
            df_teams_raw
            .drop(columns=["team_id", "abbrev", "owner"], errors="ignore")
            .sort_values(by="standing", ascending=True)
            .reset_index(drop=True)
        )

        # הוספת כתר וכדורסל למקום הראשון
        if not df_sorted.empty:
            min_standing = df_sorted["standing"].min()


            def add_first_place_icon(row):
                if row["standing"] == min_standing:
                    return f"{row['team_name']} 👑 🏀"
                return row["team_name"]


            df_sorted["team_name"] = df_sorted.apply(add_first_place_icon, axis=1)

        # סדר העמודות ושינוי שמות
        cols_order = ["team_name", "wins", "losses", "ties", "Color"]
        df_display = df_sorted[[col for col in cols_order if col in df_sorted.columns]].copy()
        df_display = df_display.rename(columns={
            "team_name": "Team",
            "wins": "Wins",
            "losses": "Losses",
            "ties": "Ties"
        })

        # מרכוז מלא של הטבלה
        styled_df = (
            df_display.style
            .hide(axis="index")
            .set_table_styles([
                {'selector': 'th',
                 'props': [('text-align', 'center !important'), ('vertical-align', 'middle !important'),
                           ('font-weight', 'bold')]},
                {'selector': 'td',
                 'props': [('text-align', 'center !important'), ('vertical-align', 'middle !important')]}
            ])
        )

        teams_list = df_teams_raw["team_name"].unique().tolist()

        # חלוקה לעמודות
        col_left, col_right = st.columns([1, 1])

        with col_left:
            st.markdown("<h3 style='text-align: center;'>Teams Table Regular Season</h3>", unsafe_allow_html=True)
            st.write(styled_df.to_html(escape=False), unsafe_allow_html=True)

        selected_from_chart = None

        with col_right:
            st.markdown("<h3 style='text-align: center;'>Weekly Wins Progression (Click line)</h3>",
                        unsafe_allow_html=True)

            df_week_scores = st.session_state.get("df_week_scores")
            curr_w = st.session_state.get("current_week", 1)

            if df_week_scores is not None and not df_week_scores.empty:
                try:
                    history_records = []
                    for t in teams_list:
                        history_records.append({"team_name": t, "week": 0, "weekly_cat_wins": 0})

                    for team_name in df_week_scores.index:
                        for col in df_week_scores.columns:
                            week_num = int(col.replace("Week ", "").strip())
                            cat_wins = int(df_week_scores.loc[team_name, col])
                            history_records.append({
                                "team_name": team_name,
                                "week": week_num,
                                "weekly_cat_wins": cat_wins
                            })

                    df_graph = pd.DataFrame(history_records)

                    if not df_graph.empty:
                        df_graph = df_graph.sort_values(['team_name', 'week'])
                        df_graph['wins'] = df_graph.groupby('team_name')['weekly_cat_wins'].cumsum()

                        domain_teams = [t for t in teams_list if t in team_colors]
                        range_colors = [team_colors[t] for t in domain_teams]

                        selection = alt.selection_point(fields=['team_name'], name="team_select")

                        chart = alt.Chart(df_graph).mark_line(point=True).encode(
                            x=alt.X('week:O', title='Week', axis=alt.Axis(labelAngle=0),
                                    scale=alt.Scale(domain=list(range(0, curr_w + 1)))),
                            y=alt.Y('wins:Q', title='Cumulative Category Wins', scale=alt.Scale(zero=True)),
                            color=alt.Color('team_name:N', scale=alt.Scale(domain=domain_teams, range=range_colors),
                                            legend=None),
                            opacity=alt.condition(selection, alt.value(1.0), alt.value(0.2)),
                            strokeWidth=alt.condition(selection, alt.value(4), alt.value(1.5)),
                            tooltip=['team_name', 'week', alt.Tooltip('wins:Q', title='Total Cat Wins')]
                        ).add_params(selection).properties(height=380)

                        event = st.altair_chart(chart, use_container_width=True, on_select="rerun")

                        if event and "selection" in event and "team_select" in event["selection"]:
                            points = event["selection"]["team_select"]
                            if points and len(points) > 0:
                                selected_from_chart = points[0].get("team_name")

                except Exception as e:
                    st.error(f"Could not build graph: {e}")
            else:
                st.info("Please click 'Load / Refresh Data' to display the graph.")

        # ----------------------------------------------------
        # 🎯 פירוט 9 הקטגוריות לגובה (רק לפי לחיצה בגרף)
        # ----------------------------------------------------
        st.divider()
        st.markdown("## 🔍 Detailed Category Breakdown & Total Rating")

        # קביעת הקבוצה הנבחרת: לפי הלחיצה בלבד (או הראשונה ברשימה ברירת מחדל)
        selected_team = selected_from_chart if (selected_from_chart and selected_from_chart in teams_list) else \
        teams_list[0]
        team_hex = team_colors.get(selected_team, "#1f77b4")

        # הצגת שם הקבוצה המודגש בצבע שלה
        st.markdown(
            f"<h2 style='text-align: center; color: {team_hex}; background-color: rgba(255,255,255,0.05); padding: 10px; border-radius: 8px; border: 2px solid {team_hex};'>"
            f"● {selected_team}"
            f"</h2>",
            unsafe_allow_html=True
        )

        df_cat_scores = st.session_state.get("df_team_scores")

        if df_cat_scores is not None and not df_cat_scores.empty:
            categories = ["PTS", "REB", "AST", "STL", "BLK", "3PM", "FG%", "FT%", "TO"]

            # צביעת הקבוצה הנבחרת בצבעה והשאר באפור
            df_cat_scores['display_color'] = df_cat_scores['team_name'].apply(
                lambda t: team_hex if t == selected_team else "#a6a6a6"
            )

            # --- 1. רשת 3x3 של 9 גרפים עומדים (ציר X = קבוצות, ציר Y = ערך הקטגוריה) ---
            st.markdown("### 📊 9-Category Rankings")

            for row_idx in range(0, 9, 3):
                cols = st.columns(3)
                for c_idx in range(3):
                    cat_name = categories[row_idx + c_idx]
                    with cols[c_idx]:
                        # עבור TO נמוך זה טוב יותר (ascending=True), לשאר גבוה זה טוב יותר (ascending=False)
                        asc = True if cat_name == "TO" else False
                        df_sub = df_cat_scores.sort_values(by=cat_name, ascending=asc).copy()

                        cat_chart = alt.Chart(df_sub).mark_bar().encode(
                            x=alt.X('team_name:N', sort=None, title=None,
                                    axis=alt.Axis(labelAngle=-45, labelLimit=100)),
                            y=alt.Y(f'{cat_name}:Q', title=cat_name),
                            color=alt.Color('display_color:N', scale=None),
                            tooltip=['team_name', alt.Tooltip(f'{cat_name}:Q', format='.2f')]
                        ).properties(title=f"Category: {cat_name}", height=280)

                        st.altair_chart(cat_chart, use_container_width=True)

            # --- 2. גרף שכלול כללי עומד ---
            st.divider()
            st.markdown("### 🏆 Overall League Power Ranking (Composite Score)")

            score_cols = [f"{c}_score" for c in categories if f"{c}_score" in df_cat_scores.columns]
            if score_cols:
                df_cat_scores['total_composite_score'] = df_cat_scores[score_cols].mean(axis=1).round(2)
                df_total = df_cat_scores.sort_values(by='total_composite_score', ascending=False).copy()

                total_chart = alt.Chart(df_total).mark_bar().encode(
                    x=alt.X('team_name:N', sort=None, title=None, axis=alt.Axis(labelAngle=-45, labelLimit=120)),
                    y=alt.Y('total_composite_score:Q', title='Composite Power Rating (1.0 - 10.0)'),
                    color=alt.Color('display_color:N', scale=None),
                    tooltip=['team_name', alt.Tooltip('total_composite_score:Q', title='Power Score')]
                ).properties(height=350)

                st.altair_chart(total_chart, use_container_width=True)

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
# region SUB-REGION: TAB 2 - MATCHUP ANALYSIS
# ------------------------------------------
with tabs[2]:
    st.markdown("<h2 style='text-align: center;'>Head-to-Head Weekly Matchup Analysis</h2>", unsafe_allow_html=True)

    try:
        league = build_league()
        curr_w = st.session_state.get("current_week", getattr(league, "currentMatchupPeriod", 2))

        # בחירת שבוע דינמית (משבוע 1 ועד השבוע האקטיבי)
        weeks_list = list(range(1, curr_w + 1))

        col_w, _ = st.columns([1, 2])
        with col_w:
            selected_week = st.selectbox("Select Week", weeks_list, index=len(weeks_list) - 1)

        boxes = league.box_scores(selected_week)

        if boxes:
            matchup_dict = {}
            for box in boxes:
                h_team = box.home_team.team_name.strip()
                a_team = box.away_team.team_name.strip()
                label = f"{h_team} vs {a_team}"
                matchup_dict[label] = box

            selected_matchup_label = st.selectbox("Select Matchup", list(matchup_dict.keys()))

            if selected_matchup_label:
                box = matchup_dict[selected_matchup_label]

                h_team = box.home_team.team_name.strip()
                a_team = box.away_team.team_name.strip()

                team_colors = st.session_state.get("TEAM_COLORS", {})
                h_color = team_colors.get(h_team, "#1f77b4")
                a_color = team_colors.get(a_team, "#e377c2")

                h_cat_wins = int(getattr(box, 'home_wins', 0))
                a_cat_wins = int(getattr(box, 'away_wins', 0))

                st.markdown("<br>", unsafe_allow_html=True)
                c1, c2, c3 = st.columns([2, 2, 2])
                with c1:
                    st.markdown(f"<h2 style='text-align: center; color: {h_color};'>{h_team}</h2>",
                                unsafe_allow_html=True)
                with c2:
                    st.markdown(f"<h2 style='text-align: center;'>{h_cat_wins} - {a_cat_wins}</h2>",
                                unsafe_allow_html=True)
                    st.markdown("<p style='text-align: center; color: #888;'>Category Wins</p>", unsafe_allow_html=True)
                with c3:
                    st.markdown(f"<h2 style='text-align: center; color: {a_color};'>{a_team}</h2>",
                                unsafe_allow_html=True)

                st.divider()
                st.markdown("<h3 style='text-align: center;'>9-Cat Live Breakdown</h3>", unsafe_allow_html=True)

                h_stats = getattr(box, 'home_stats', {}) or {}
                a_stats = getattr(box, 'away_stats', {}) or {}

                categories = ["PTS", "REB", "AST", "STL", "BLK", "3PM", "FG%", "FT%", "TO"]
                breakdown_rows = []

                for cat in categories:
                    h_raw = h_stats.get(cat, {}).get('value', 0) if isinstance(h_stats.get(cat), dict) else h_stats.get(
                        cat, 0)
                    a_raw = a_stats.get(cat, {}).get('value', 0) if isinstance(a_stats.get(cat), dict) else a_stats.get(
                        cat, 0)

                    h_val = float(h_raw)
                    a_val = float(a_raw)

                    if cat in ["FG%", "FT%"]:
                        h_str = f"{h_val:.3f}"
                        a_str = f"{a_val:.3f}"
                    else:
                        h_str = f"{int(h_val)}" if h_val.is_integer() else f"{h_val:.1f}"
                        a_str = f"{int(a_val)}" if a_val.is_integer() else f"{a_val:.1f}"

                    if cat == "TO":
                        h_lead = h_val < a_val and h_val > 0
                        a_lead = a_val < h_val and a_val > 0
                    else:
                        h_lead = h_val > a_val
                        a_lead = a_val > h_val

                    # עיצוב ללא אימוג'י - רק צבע והדגשה במידה ומוביל
                    h_style = f"color: {h_color}; font-weight: bold; text-align: center;" if h_lead else "text-align: center; color: #666;"
                    a_style = f"color: {a_color}; font-weight: bold; text-align: center;" if a_lead else "text-align: center; color: #666;"

                    breakdown_rows.append({
                        h_team: f"<span style='{h_style}'>{h_str}</span>",
                        "Category": f"<b>{cat}</b>",
                        a_team: f"<span style='{a_style}'>{a_str}</span>"
                    })

                df_bd = pd.DataFrame(breakdown_rows)

                # עיצוב ומרכוז מלא של הטבלה למרכז הדף
                styled_bd = (
                    df_bd.style
                    .hide(axis="index")
                    .set_table_styles([
                        {'selector': 'th',
                         'props': [('text-align', 'center !important'), ('vertical-align', 'middle !important'),
                                   ('font-weight', 'bold')]},
                        {'selector': 'td',
                         'props': [('text-align', 'center !important'), ('vertical-align', 'middle !important')]}
                    ])
                )

                # עטיפה ב-div ממורכז במרכז המסך
                st.markdown(
                    f"<div style='display: flex; justify-content: center; width: 100%;'>{styled_bd.to_html(escape=False)}</div>",
                    unsafe_allow_html=True
                )

        else:
            st.info(f"No matchups found for Week {selected_week}.")

    except Exception as e:
        st.error(f"Could not load Matchup Analysis: {e}")

# endregion


# ------------------------------------------
# region TAB 3 - LIVE MATCHUPS DETAILED BREAKDOWN
# ------------------------------------------
with tabs[3]:
    st.markdown("## ⚔️ Live Matchups Detailed 9-Cat Breakdown")
    st.write("תוצאות עומק בלייב לפי 9 קטגוריות עבור השבוע הפעיל הנוכחי.")

    try:
        league = build_league()
        curr_w = st.session_state.get("current_week", getattr(league, "currentMatchupPeriod", 2))

        st.info(f"📌 מציג פירוט קטגוריות בלייב עבור **Week {curr_w}**")

        boxes = league.box_scores(curr_w)
        categories = ["PTS", "REB", "AST", "STL", "BLK", "3PM", "FG%", "FT%", "TO"]

        if boxes:
            for idx, box in enumerate(boxes, start=1):
                h_team = box.home_team.team_name.strip()
                a_team = box.away_team.team_name.strip()

                h_cat_wins = int(getattr(box, 'home_wins', 0))
                a_cat_wins = int(getattr(box, 'away_wins', 0))
                ties = 9 - (h_cat_wins + a_cat_wins)

                # כותרת למאצ'אפ כולל תוצאה כללית
                st.markdown(
                    f"### 🏀 Matchup {idx}: {h_team} ({h_cat_wins}) vs ({a_cat_wins}) {a_team}"
                )

                # שליפת נתוני 9 הקטגוריות בלייב מתוך אובייקט ה-box
                h_stats = getattr(box, 'home_stats', {}) or {}
                a_stats = getattr(box, 'away_stats', {}) or {}

                rows = []
                for cat in categories:
                    # שליפת הערך של הקבוצה הביתית והאורחת
                    h_val = h_stats.get(cat, {}).get('value', 0) if isinstance(h_stats.get(cat), dict) else h_stats.get(cat, 0)
                    a_val = a_stats.get(cat, {}).get('value', 0) if isinstance(a_stats.get(cat), dict) else a_stats.get(cat, 0)

                    # עיגול אחוזים ל-3 ספרות ושאר הסטטיסטיקות ל-1/0 ספרות
                    if cat in ["FG%", "FT%"]:
                        h_str = f"{float(h_val):.3f}"
                        a_str = f"{float(a_val):.3f}"
                    else:
                        h_str = f"{float(h_val):.1f}".rstrip('0').rstrip('.')
                        a_str = f"{float(a_val):.1f}".rstrip('0').rstrip('.')

                    # סימון מוביל בקטגוריה (ב-TO נמוך זה מוביל)
                    if cat == "TO":
                        h_win = float(h_val) < float(a_val)
                        a_win = float(a_val) < float(h_val)
                    else:
                        h_win = float(h_val) > float(a_val)
                        a_win = float(a_val) > float(h_val)

                    h_disp = f"<b>{h_str} 👈</b>" if h_win else h_str
                    a_disp = f"<b>👉 {a_str}</b>" if a_win else a_str

                    rows.append({
                        "Category": cat,
                        h_team: h_disp,
                        a_team: a_disp
                    })

                df_matchup_detail = pd.DataFrame(rows)

                # עיצוב ומרכוז הטבלה
                styled_detail = (
                    df_matchup_detail.style
                    .hide(axis="index")
                    .set_table_styles([
                        {'selector': 'th', 'props': [('text-align', 'center !important'), ('vertical-align', 'middle !important'), ('font-weight', 'bold')]},
                        {'selector': 'td', 'props': [('text-align', 'center !important'), ('vertical-align', 'middle !important')]}
                    ])
                )

                st.write(styled_detail.to_html(escape=False), unsafe_allow_html=True)
                st.markdown("<br>", unsafe_allow_html=True)

        else:
            st.info("No box scores found for current week.")

        st.divider()

        # ----------------------------------------------------
        # טבלת Week Score Matrix
        # ----------------------------------------------------
        st.markdown("### 📊 Week Score Matrix (Category Wins per Team)")
        df_week_scores = st.session_state.get("df_week_scores")

        if df_week_scores is not None and not df_week_scores.empty:
            styled_pivot = (
                df_week_scores.style
                .set_table_styles([
                    {'selector': 'th', 'props': [('text-align', 'center !important'), ('vertical-align', 'middle !important'), ('font-weight', 'bold')]},
                    {'selector': 'td', 'props': [('text-align', 'center !important'), ('vertical-align', 'middle !important')]}
                ])
            )
            st.write(styled_pivot.to_html(escape=False), unsafe_allow_html=True)
        else:
            st.info("No score data available. Please click 'Load / Refresh Data' above.")

    except Exception as e:
        st.error(f"Error loading Live Matchup details: {e}")

# endregion



# region no copyright
# --- Footer - Legal & Educational Disclaimer ---
# יצירת מרווח משמעותי מהתוכן שמעל
st.write("<br>" * 5, unsafe_allow_html=True)

st.divider() # קו מפריד דק נוסף לפני הכיתוב

st.markdown(
    """
    <div style="text-align: center; color: gray; font-size: 0.8em;">
        <strong>Disclaimer & Copyright Information:</strong><br>
        This application is developed strictly for <strong>educational and non-commercial purposes</strong>.<br>
        All NBA-related data, team names, and statistics are the property of the <strong>National Basketball Association (NBA)</strong> and/or <strong>ESPN</strong>.<br>
        This tool does not claim any ownership over the retrieved data and is intended for personal use and learning only.
    </div>
    """,
    unsafe_allow_html=True
)
# endregion

