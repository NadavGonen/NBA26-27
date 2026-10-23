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

    # סינון שבועות עתידיים שבהם לא שוחקו משחקים בכלל (לכל הליגה)
    if not df_matchups.empty:
        active_weeks = df_matchups.groupby("week").apply(
            lambda w: (w["team_score"].sum() > 0 or w["opponent_score"].sum() > 0)
        )
        valid_weeks = active_weeks[active_weeks].index.tolist()

        # אם הליגה עוד לפני שבוע 1, נציג לפחות את שבוע 1 לבחירה
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
            if rank <= 3:
                return "#28a745"  # טופ 3 - ירוק
            elif rank > num_teams - 3:
                return "#dc3545"  # 3 אחרונות - אדום
            return "#6c757d"  # אמצע - אפור

        team_cat_totals[f"{cat}_color"] = team_cat_totals[f"{cat}_rank"].apply(get_tier_color)

    df_team_scores = team_cat_totals

    return df_teams, df_matchups, df_players, df_team_scores


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

col_title, col_btn = st.columns([4, 1])
with col_title:
    st.title("🏀 NBA Fantasy Dashboard - 2026/27 (9-Cat)")
with col_btn:
    st.write("")
    if st.button("🔄 Load / Refresh Data", use_container_width=True):
        with st.spinner("Fetching data from ESPN API..."):
            df_t, df_m, df_p, df_s = load_league_data()
            if df_t is not None and not df_t.empty:
                df_t["color"] = df_t["team_name"].map(st.session_state["TEAM_COLORS"]).fillna("#333333")
            st.session_state["df_teams"] = df_t
            st.session_state["df_matchups"] = df_m
            st.session_state["df_players"] = df_p
            st.session_state["df_team_scores"] = df_s
        st.success("Data loaded successfully!")
        st.rerun()

if st.session_state["df_teams"] is None:
    try:
        df_t, df_m, df_p, df_s = load_league_data()
        if df_t is not None and not df_t.empty:
            df_t["color"] = df_t["team_name"].map(st.session_state["TEAM_COLORS"]).fillna("#333333")
        st.session_state["df_teams"] = df_t
        st.session_state["df_matchups"] = df_m
        st.session_state["df_players"] = df_p
        st.session_state["df_team_scores"] = df_s
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
        df_m = st.session_state.get("df_matchups", pd.DataFrame())

        # שליפת מילון הצבעים מה-session_state
        team_colors = st.session_state.get("TEAM_COLORS", {})

        # סינון משחקי פלייאוף עבור גרף העונה הסדירה
        if not df_m.empty and 'is_playoff' in df_m.columns:
            df_m_reg = df_m[df_m['is_playoff'] == False].copy()
        else:
            df_m_reg = df_m.copy()


        # הכנת עמודת צבע ויזואלית (עיגול צבעוני HTML) לטבלה
        def make_color_circle(color_hex):
            return f'<span style="color:{color_hex}; font-size: 26px;">●</span>'


        df_teams_raw["Color_Hex"] = df_teams_raw["team_name"].map(team_colors).fillna("#333333")
        df_teams_raw["Color"] = df_teams_raw["Color_Hex"].apply(make_color_circle)

        # מיון לפי עמודת standing הרשמית מ-ESPN API (1, 2, 3...)
        df_sorted = (
            df_teams_raw
            .drop(columns=["team_id", "abbrev", "owner"], errors="ignore")
            .sort_values(by="standing", ascending=True)
            .reset_index(drop=True)
        )

        # הוספת כתר וכדורסל למי שבמקום 1 הרשמי (או שוויון במקום הראשון לפי ניצחונות ותיקו)
        if not df_sorted.empty:
            min_standing = df_sorted["standing"].min()


            def add_first_place_icon(row):
                if row["standing"] == min_standing:
                    return f" {row['team_name']} 👑 🏀"
                return row["team_name"]


            df_sorted["team_name"] = df_sorted.apply(add_first_place_icon, axis=1)

        # סדר העמודות בטבלה
        cols_order = ["team_name", "wins", "losses", "ties", "standing", "Color"]
        df_display = df_sorted[[col for col in cols_order if col in df_sorted.columns]]

        teams_list = df_teams_raw["team_name"].unique().tolist()

        # חלוקה לעמודות: שמאל = טבלה, ימין = גרף ניצחונות
        col_left, col_right = st.columns([1, 1])

        with col_left:
            st.markdown("<h3 style='text-align: center;'>Teams Table Regular Season</h3>", unsafe_allow_html=True)
            st.write(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)

        with col_right:
            st.markdown("<h3 style='text-align: center;'>Weekly Wins Progression (Click on graph)</h3>",
                        unsafe_allow_html=True)

            if not df_m_reg.empty:
                df_m_plot = df_m_reg.sort_values(["team_name", "week"])
                week0 = pd.DataFrame({
                    "week": [0] * len(teams_list),
                    "team_name": teams_list,
                    "win": [0] * len(teams_list)
                })
                df_plot = pd.concat([week0, df_m_plot], ignore_index=True)
                df_plot["wins"] = df_plot.groupby("team_name")["win"].cumsum()

                # יצירת דומיין וטווח צבעים עבור Altair
                domain_teams = [t for t in teams_list if t in team_colors]
                range_colors = [team_colors[t] for t in domain_teams]

                selection = alt.selection_point(fields=['team_name'], on='click', name="team_select")

                chart = alt.Chart(df_plot).mark_line().encode(
                    x=alt.X('week:O', title='Week', axis=alt.Axis(labelAngle=0)),
                    y=alt.Y('wins:Q', title='Total Wins'),
                    color=alt.Color(
                        'team_name:N',
                        scale=alt.Scale(domain=domain_teams, range=range_colors),
                        legend=None
                    ),
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
# region SUB-REGION: TAB 2 - MATCHUP H2H
# ------------------------------------------
with tabs[2]:
    st.markdown("<h3 style='text-align: center;'>Head-to-Head Weekly Matchup Analysis</h3>", unsafe_allow_html=True)

    if st.session_state.get("df_teams") is not None and not st.session_state.get("df_matchups", pd.DataFrame()).empty:
        df_m = st.session_state["df_matchups"]
        team_colors = st.session_state.get("TEAM_COLORS", {})

        # בחירת שבוע
        available_weeks = sorted(df_m["week"].unique())
        selected_week = st.selectbox("Select Week", available_weeks,
                                     index=len(available_weeks) - 1 if available_weeks else 0)

        # סינון משחקים לשבוע הנבחר
        df_week_matches = df_m[df_m["week"] == selected_week]

        # יצירת רשימת קרבות ייחודית לשבוע (כדי להימנע מכפילויות של הבית/חוץ)
        matchup_pairs = []
        seen_pairs = set()
        for _, row in df_week_matches.iterrows():
            t1, t2 = row["team_name"], row["opponent_name"]
            if t2 != "BYE" and tuple(sorted([t1, t2])) not in seen_pairs:
                seen_pairs.add(tuple(sorted([t1, t2])))
                matchup_pairs.append((t1, t2))

        if matchup_pairs:
            matchup_labels = [f"{t1}  vs  {t2}" for t1, t2 in matchup_pairs]
            selected_matchup_label = st.selectbox("Select Matchup", matchup_labels)

            # חילוץ שתי הקבוצות הנבחרות
            idx = matchup_labels.index(selected_matchup_label)
            team_a, team_b = matchup_pairs[idx]

            color_a = team_colors.get(team_a, "#1f77b4")
            color_b = team_colors.get(team_b, "#ff7f0e")

            # --- חישוב סיכויי זכייה דינמיים והשוואת קטגוריות ---
            categories = ["PTS", "REB", "AST", "STL", "BLK", "3PM", "FG%", "FT%", "TO"]

            # שליפת שחקני שתי הקבוצות לצורך חישוב תחזית/חוזק
            df_players = st.session_state.get("df_players", pd.DataFrame())
            team_a_players = df_players[df_players["team_name"] == team_a] if not df_players.empty else pd.DataFrame()
            team_b_players = df_players[df_players["team_name"] == team_b] if not df_players.empty else pd.DataFrame()

            # חישוב משוקלל לכל קבוצה לפי ממוצעי השחקנים (או ציוני הקטגוריות)
            scores_a = {cat: team_a_players[cat].mean() if not team_a_players.empty and cat in team_a_players else 5.0
                        for cat in categories}
            scores_b = {cat: team_b_players[cat].mean() if not team_b_players.empty and cat in team_b_players else 5.0
                        for cat in categories}

            # חישוב סיכוי נצח משוער בהתבסס על יחס הנתונים המשוקלל
            total_score_a = sum(scores_a.values()) if scores_a else 1
            total_score_b = sum(scores_b.values()) if scores_b else 1
            prob_a = round((total_score_a / (total_score_a + total_score_b)) * 100, 1)
            prob_b = round(100 - prob_a, 1)

            # --- תצוגה ראש בראש: צד ימין קבוצה א', צד שמאל קבוצה ב' ---
            col_team1, col_win_prob, col_team2 = st.columns([2, 3, 2])

            with col_team1:
                st.markdown(f"<h2 style='text-align: center; color: {color_a};'>{team_a}</h2>", unsafe_allow_html=True)

            with col_win_prob:
                st.markdown("<h4 style='text-align: center;'>Win Probability (Live + Projections)</h4>",
                            unsafe_allow_html=True)
                st.markdown(f"""
                    <div style='display: flex; justify-content: space-around; align-items: center; font-size: 20px; font-weight: bold;'>
                        <span style='color: {color_a};'>{prob_a}%</span>
                        <span>VS</span>
                        <span style='color: {color_b};'>{prob_b}%</span>
                    </div>
                """, unsafe_allow_html=True)
                st.progress(prob_a / 100.0)

            with col_team2:
                st.markdown(f"<h2 style='text-align: center; color: {color_b};'>{team_b}</h2>", unsafe_allow_html=True)

            st.markdown("---")

            # --- השוואת 9 הקטגוריות באמצע ---
            st.markdown("<h4 style='text-align: center;'>9-Cat Breakdown</h4>", unsafe_allow_html=True)

            for cat in categories:
                val_a = scores_a.get(cat, 0.0)
                val_b = scores_b.get(cat, 0.0)

                c1, c_cat, c2 = st.columns([3, 2, 3])
                with c1:
                    st.markdown(
                        f"<div style='text-align: right; color: {color_a}; font-weight: bold;'>{val_a:.2f}</div>",
                        unsafe_allow_html=True)
                with c_cat:
                    st.markdown(
                        f"<div style='text-align: center; background-color: #f0f2f6; border-radius: 5px; font-weight: bold;'>{cat}</div>",
                        unsafe_allow_html=True)
                with c2:
                    st.markdown(
                        f"<div style='text-align: left; color: {color_b}; font-weight: bold;'>{val_b:.2f}</div>",
                        unsafe_allow_html=True)
        else:
            st.info("No matchups found for this week.")
    else:
        st.info("Please load league data first using the sidebar or refresh button.")
# endregion


# ------------------------------------------
# region SUB-REGION: TAB 3 - TESTING
# ------------------------------------------
with tabs[3]:
    st.subheader("🧪 API & Data Structure Testing")

    df_p = st.session_state.get("df_players")
    df_m = st.session_state.get("df_matchups")

    # 1. בדיקת נתוני מצ'אפים וקרבות
    st.markdown("### 🏆 Matchups Raw Data Inspection")
    if df_m is not None and not df_m.empty:
        st.success(f"Matchups data loaded successfully! Total rows: {len(df_m)}")

        # בחירת שבוע לבדיקה
        test_weeks = sorted(df_m["week"].unique()) if "week" in df_m.columns else []
        if test_weeks:
            inspect_week = st.selectbox("Select Week to Inspect Matchups:", test_weeks, key="test_week_select")
            week_data = df_m[df_m["week"] == inspect_week]
            st.write(f"**Matchups for Week {inspect_week}:**")
            st.dataframe(week_data, use_container_width=True)

        with st.expander("View Full Raw Matchups DataFrame"):
            st.dataframe(df_m, use_container_width=True)
    else:
        st.warning("No matchups data found in `df_matchups`. Make sure to load league data.")

    st.markdown("---")

    # 2. בדיקת נתוני שחקנים
    st.markdown("### 🏀 Players Raw Data Inspection")
    if df_p is not None and not df_p.empty:
        st.write("Here you can inspect the raw data structure returned by the ESPN API for players:")

        player_names = df_p["player_name"].unique().tolist()
        selected_player = st.selectbox("Select a player to inspect stats structure:", player_names,
                                       key="test_player_select")

        if selected_player:
            player_row = df_p[df_p["player_name"] == selected_player].iloc[0]
            st.write(f"**Player:** {player_row['player_name']} ({player_row['position']})")
            st.write("**Raw Stats Object / Dictionary:**")
            st.json(player_row["raw_stats"])
    else:
        st.info("Please load league data first.")
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

