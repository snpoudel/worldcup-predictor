"""Fetch real World Cup 2026 knockout results from the free, open
openfootball/worldcup.json feed and sync them into our local bracket.

Source: https://github.com/openfootball/worldcup.json
Raw data: https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json
No API key required. Data is community-maintained, updated close to
real-time but by hand -- so don't expect second-by-second live scores.
"""
import requests

FEED_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"

# Map the feed's "round" string + match "num" to our internal (round, slot).
# Knockout match numbering in the feed is fixed: 73-88 = R32, 89-96 = R16,
# 97-100 = QF, 101-102 = SF, 104 = Final. (103 is third-place, which we don't
# track since this app is knockout-bracket-only, no 3rd place game.)
FEED_ROUND_TO_OURS = {
    "Round of 32": "R32",
    "Round of 16": "R16",
    "Quarter-final": "QF",
    "Semi-final": "SF",
    "Final": "F",
}

NUM_RANGES = {
    "R32": (73, 88),
    "R16": (89, 96),
    "QF": (97, 100),
    "SF": (101, 102),
    "F": (104, 104),
}


def fetch_feed():
    """Fetch and return the raw feed JSON. Raises on network/HTTP errors --
    caller should catch and show a friendly message rather than crashing."""
    resp = requests.get(FEED_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _iter_knockout_matches(feed_json):
    """Yield (our_round, slot, raw_match) for every knockout match in the feed."""
    for m in feed_json.get("matches", []):
        our_round = FEED_ROUND_TO_OURS.get(m.get("round"))
        if not our_round:
            continue
        num = m.get("num")
        if num is None:
            continue
        lo, hi = NUM_RANGES[our_round]
        if not (lo <= num <= hi):
            continue
        yield our_round, num - lo, m


def extract_all_knockout_info(feed_json):
    """Return team names + schedule dates for ALL knockout matches (played or not)."""
    info = []
    for our_round, slot, m in _iter_knockout_matches(feed_json):
        info.append({
            "round": our_round,
            "slot": slot,
            "team1": m.get("team1"),
            "team2": m.get("team2"),
            "date": m.get("date"),
            "time": m.get("time"),
        })
    return info


def extract_knockout_results(feed_json):
    """Return played knockout matches with final scores."""
    results = []
    for our_round, slot, m in _iter_knockout_matches(feed_json):
        score = m.get("score", {}).get("ft")
        if not score:
            continue
        results.append({
            "round": our_round,
            "slot": slot,
            "team1": m.get("team1"),
            "team2": m.get("team2"),
            "home": score[0],
            "away": score[1],
        })
    return results


def sync_results(group_id, db_module):
    """Pull live feed and apply to our bracket for the given group.
    Returns (num_updated, num_skipped_draws, error_message_or_None).

    First syncs team names + match dates for every knockout match (even
    unplayed ones — the schedule is known in advance). Then applies scores
    for played matches via db.set_result(), which auto-advances winners.
    Draws need a manual penalty-winner pick in the Admin tab.
    """
    try:
        feed = fetch_feed()
    except Exception as e:
        return 0, 0, f"Could not fetch live scores: {e}"

    existing = {(m["round"], m["slot"]): m for m in db_module.get_matches(group_id)}

    # Sync team names + dates for all knockout matches (played or not)
    all_info = extract_all_knockout_info(feed)
    if not all_info:
        return 0, 0, "No knockout data available in the feed yet."

    for info in all_info:
        local = existing.get((info["round"], info["slot"]))
        if not local:
            continue
        if info["team1"] and info["team2"]:
            db_module.update_team_names(
                local["id"], info["team1"], info["team2"],
                info.get("date"), info.get("time"),
            )
        elif info.get("date") or info.get("time"):
            db_module.update_match_date(local["id"], info.get("date"), info.get("time"))

    # Sync scores for played matches
    updated = 0
    skipped_draws = 0
    for r in extract_knockout_results(feed):
        local_match = existing.get((r["round"], r["slot"]))
        if not local_match:
            continue
        if (local_match["actual_home"] == r["home"]
                and local_match["actual_away"] == r["away"]):
            continue
        db_module.set_result(local_match["id"], r["home"], r["away"])
        if r["home"] == r["away"]:
            skipped_draws += 1
        else:
            updated += 1

    return updated, skipped_draws, None
