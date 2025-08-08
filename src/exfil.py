import os
import json
import datetime
from dotenv import load_dotenv
from sleeper_wrapper import League, Players

load_dotenv()

LEAGUE_ID = os.getenv("SLEEPER_LEAGUE_ID")
USER_ID = os.getenv("SLEEPER_USER_ID")
OMIT_CURRENT_YEAR_PICKS = True

if not LEAGUE_ID or not USER_ID:
    raise ValueError("Missing SLEEPER_LEAGUE_ID or SLEEPER_USER_ID environment variable")

league = League(LEAGUE_ID)
players_api = Players()

users = league.get_users()
user_map = {u["user_id"]: u["display_name"] for u in users}
rosters = league.get_rosters()
player_db = players_api.get_all_players()

def minimal_player_info_by_pid(pid):
    p = player_db.get(pid)
    if not p:
        return {"name": "Unknown", "team": None, "position": None, "age": None, "status": None}
    return {
        "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
        "team": p.get("team"),
        "position": p.get("position"),
        "age": p.get("age"),
        "status": p.get("status"),
    }

# --- Added for draft picks ---
def get_roster_draft_picks():
    current_year = datetime.datetime.now().year
    picks = league.get_traded_picks()
    roster_map = {r["roster_id"]: r for r in rosters}
    draft_picks_by_owner = {user_map.get(r["owner_id"], "Unknown"): [] for r in rosters}

    for pick in picks:
        # Skip current year if configured
        if OMIT_CURRENT_YEAR_PICKS and str(pick.get("season")) == str(current_year):
            continue

        owner_name = user_map.get(roster_map[pick["owner_id"]]["owner_id"], "Unknown")
        draft_picks_by_owner[owner_name].append({
            "season": pick.get("season"),
            "round": pick.get("round"),
            "original_owner": user_map.get(roster_map[pick["roster_id"]]["owner_id"], "Unknown")
        })

    # Sort picks by season then round
    for owner, pick_list in draft_picks_by_owner.items():
        pick_list.sort(key=lambda p: (p["season"], p["round"]))

    return draft_picks_by_owner
# --- End added for draft picks ---

# --- Build minimal rosters for snapshot ---
my_roster = next((r for r in rosters if r["owner_id"] == USER_ID), None)
if not my_roster:
    raise ValueError("Your roster not found. Check USER_ID.")

snapshot_data = {
    "export_date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "league_name": league.get_league()["name"],
    "my_team": [minimal_player_info_by_pid(pid) for pid in my_roster.get("players", [])]
}

for roster in rosters:
    if roster["owner_id"] == USER_ID:
        continue
    owner_name = user_map.get(roster["owner_id"], "Unknown")
    snapshot_data[owner_name] = [minimal_player_info_by_pid(pid) for pid in roster.get("players", [])]

# --- Add draft picks for all teams ---
snapshot_data["draft_picks"] = get_roster_draft_picks()

# Save snapshot
os.makedirs("./data", exist_ok=True)
date_prefix = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
snapshot_file = f"./data/{date_prefix}_sleeper_league_min.json"
with open(snapshot_file, "w") as f:
    json.dump(snapshot_data, f, separators=(",", ":"))

print(f"League snapshot saved to {snapshot_file}")

# --- Waiver analysis on fresh snapshot ---

# Collect all owned player names
owned_player_names = set()
for key, roster_players in snapshot_data.items():
    if key in ("export_date", "league_name", "my_team", "draft_picks"):
        continue
    for player in roster_players:
        owned_player_names.add(player["name"])
for player in snapshot_data.get("my_team", []):
    owned_player_names.add(player["name"])

# Filter waiver candidates
waiver_candidates = []
for pid, pdata in player_db.items():
    full_name = f"{pdata.get('first_name', '')} {pdata.get('last_name', '')}".strip()
    if full_name in owned_player_names:
        continue
    pos = pdata.get("position")
    if not pos or pos in ("K", "DEF", "LS"):
        continue
    waiver_candidates.append(pdata)

def rank_player(p):
    pos_score = {"RB": 2, "WR": 2, "QB": 1, "TE": 2}.get(p.get("position"), 0)
    status_score = 1 if p.get("status") == "active" else 0
    age = p.get("age")
    if age is None:
        age_score = 0
    elif age < 25:
        age_score = 2
    elif age <= 28:
        age_score = 1
    else:
        age_score = 0
    return pos_score * 10 + status_score * 5 + age_score * 3

waiver_candidates_sorted = sorted(waiver_candidates, key=rank_player, reverse=True)

top_waivers = []
for p in waiver_candidates_sorted[:30]:
    top_waivers.append({
        "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
        "team": p.get("team"),
        "position": p.get("position"),
        "age": p.get("age"),
        "status": p.get("status"),
    })

waiver_file = f"./data/{date_prefix}_sleeper_waivers.json"
with open(waiver_file, "w") as f:
    json.dump(top_waivers, f, separators=(",", ":"))

print(f"Top waiver targets saved to {waiver_file}")
