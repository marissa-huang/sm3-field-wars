"""Field Wars scoring engine for SM3.

Everything here is pure: it takes a round (as stored in state.json) plus the
team list and returns results. The server calls it on every request, so
editing an allocation or a round's rules re-scores instantly.
"""

FIELDS = 5
POINTS = {"win": 10, "tie": 5, "loss": 0}

MODES = {
    "classic": {
        "name": "Classic",
        "rule": "Every field is worth 1 point. Win more fields than your opponent to win the matchup.",
    },
    "weighted": {
        "name": "Weighted",
        "rule": "Field n is worth n points (Field 1 = 1 pt ... Field 5 = 5 pts). Win more field points than your opponent.",
    },
    "capital": {
        "name": "Capital",
        "rule": "Like Classic, but you also name one field as your capital. If your opponent out-soldiers you on your capital, you lose that matchup automatically.",
    },
}

EVENTS = {
    "supply": {
        "name": "Supply Shortage",
        "icon": "📦",
        "text": "Your budget drops to 40 soldiers.",
    },
    # Stored as "crown" so older saved rounds keep working.
    "crown": {
        "name": "Hidden Treasure",
        "icon": "💎",
        "text": "One randomly chosen field is worth double points.",
    },
    "unstable": {
        "name": "Unstable Terrain",
        "icon": "⛰️",
        "text": "The field with the most total soldiers (all teams combined) is worth double points.",
    },
    "volcano": {
        "name": "Volcanic Eruption",
        "icon": "🌋",
        "text": "One random field (revealed after the round) becomes worthless; no one earns points for winning it.",
    },
    "frozen": {
        "name": "Frozen Fortress",
        "icon": "🧊",
        "text": "Field 3 is worth 0. Field 2 or Field 4 (picked at random, revealed after the round) is worth double.",
    },
    "underdog": {
        "name": "Underdog Victory",
        "icon": "🐭",
        "text": "Every field that you capture using 10 or fewer soldiers earns 2 extra field points in that matchup.",
    },
    "sabotage": {
        "name": "Sabotage",
        "icon": "💣",
        "text": "Secretly pick one field to sabotage. Against you, every opponent has up to 10 fewer soldiers there (25 becomes 15).",
    },
    "siege": {
        "name": "Siege Warfare",
        "icon": "🏹",
        "text": "To win a field you need at least 5 more soldiers at that field than your opponent.",
    },
    "teleport": {
        "name": "Random Teleportation",
        "icon": "🌀",
        "text": "After submissions, two random fields of every team's allocation swap their soldiers.",
    },
    "exact": {
        "name": "Exact Victory",
        "icon": "🎯",
        "text": "Winning a field by exactly 1 soldier gives you one extra field point.",
    },
    "nomans": {
        "name": "No Man's Land",
        "icon": "🚫",
        "text": "You must leave exactly one field completely empty (0 soldiers).",
    },
    "staircase": {
        "name": "Staircase",
        "icon": "🪜",
        "text": "Soldiers must go in nondecreasing order: Field 1 ≤ Field 2 ≤ Field 3 ≤ Field 4 ≤ Field 5.",
    },
}

# Multiplier used by Hidden Treasure, Unstable Terrain and Frozen Fortress.
BONUS_MULTIPLIER = 2
SABOTAGE_LOSS = 10     # soldiers an opponent loses on the field you sabotage
SIEGE_MARGIN = 5       # Siege Warfare: lead needed to take a field
UNDERDOG_MAX = 10      # Underdog Victory: capture with this many soldiers or fewer...
UNDERDOG_BONUS = 2     # ...for this many extra field points
EXACT_BONUS = 1        # Exact Victory: extra field points for winning a field by exactly 1


def submission_problem(rnd, alloc):
    """Event rules that limit which allocations are legal (budget is checked by the caller)."""
    events = rnd.get("events") or []
    if "nomans" in events and alloc.count(0) != 1:
        return "No Man's Land: exactly one field must be 0 (the other four need at least 1 soldier)"
    if "staircase" in events and any(alloc[i] > alloc[i + 1] for i in range(FIELDS - 1)):
        return "Staircase: Field 1 ≤ Field 2 ≤ Field 3 ≤ Field 4 ≤ Field 5"
    return None


def teleported(alloc, swap):
    """Allocation after Random Teleportation swaps fields swap = [a, b] (1-based)."""
    out = list(alloc)
    if swap:
        i, j = swap[0] - 1, swap[1] - 1
        out[i], out[j] = out[j], out[i]
    return out


def field_values(rnd, entries):
    """Return (values, notes) for the five fields of a round.

    notes maps field index -> list of short tags ("crown", "volcano", ...)
    so the UI can explain why a field is worth what it is.
    """
    values = [(i + 1) if rnd.get("mode") == "weighted" else 1 for i in range(FIELDS)]
    notes = {i: [] for i in range(FIELDS)}
    events = set(rnd.get("events") or [])
    draws = rnd.get("draws") or {}

    if "frozen" in events:
        values[2] = 0
        notes[2].append("frozen")
        thaw = (draws.get("thaw") or 4) - 1
        values[thaw] *= BONUS_MULTIPLIER
        notes[thaw].append("thaw")

    crown = draws.get("crown")
    if "crown" in events and crown:
        values[crown - 1] *= BONUS_MULTIPLIER
        notes[crown - 1].append("crown")

    unstable_fields = []
    if "unstable" in events and entries:
        totals = [sum(e["alloc"][i] for e in entries) for i in range(FIELDS)]
        top = max(totals)
        if top > 0:
            unstable_fields = [i for i in range(FIELDS) if totals[i] == top]
            for i in unstable_fields:
                values[i] *= BONUS_MULTIPLIER
                notes[i].append("unstable")

    volcano = draws.get("volcano")
    if "volcano" in events and volcano:
        values[volcano - 1] = 0
        notes[volcano - 1].append("volcano")

    return values, notes, [i + 1 for i in unstable_fields]


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def rank_rows(rows, key):
    """Competition ranking (1, 1, 3, ...) on rows already sorted best-first."""
    prev, rank = None, 0
    for i, r in enumerate(rows):
        k = key(r)
        if k != prev:
            rank = i + 1
            prev = k
        r["rank"] = rank
    return rows


def compute_round(rnd, teams_by_id):
    """Play every submitted team against every other submitted team."""
    subs = rnd.get("submissions") or {}
    events = set(rnd.get("events") or [])
    swaps = (rnd.get("draws") or {}).get("teleport") or {} if "teleport" in events else {}
    entries = [
        {
            "teamId": tid,
            "alloc": teleported(s["alloc"], swaps.get(tid)),
            "capital": s.get("capital"),
            "sabotage": s.get("sabotage") if "sabotage" in events else None,
        }
        for tid, s in subs.items()
        if tid in teams_by_id
    ]
    values, notes, unstable_fields = field_values(rnd, entries)
    capital_mode = rnd.get("mode") == "capital"
    margin = SIEGE_MARGIN if "siege" in events else 1

    def bonus(f, placed, lead):
        # Worthless fields (frozen, erupted) earn nothing, bonuses included.
        if not values[f]:
            return 0, 0, 0
        under = "underdog" in events and placed <= UNDERDOG_MAX
        exact = "exact" in events and lead == 1
        return UNDERDOG_BONUS * under + EXACT_BONUS * exact, int(under), int(exact)

    rec = {
        e["teamId"]: {
            "teamId": e["teamId"],
            "name": teams_by_id[e["teamId"]]["name"],
            "wins": 0, "ties": 0, "losses": 0, "points": 0,
            "fieldsWon": 0, "capitalLosses": 0,
        }
        for e in entries
    }
    matrix = {e["teamId"]: {} for e in entries}
    underdog_caps = exact_wins = 0

    for i in range(len(entries)):
        a = entries[i]
        for j in range(i + 1, len(entries)):
            b = entries[j]
            # Effective strength: each side loses soldiers on the field the other sabotaged.
            ea, eb = list(a["alloc"]), list(b["alloc"])
            if b["sabotage"]:
                ea[b["sabotage"] - 1] = max(0, ea[b["sabotage"] - 1] - SABOTAGE_LOSS)
            if a["sabotage"]:
                eb[a["sabotage"] - 1] = max(0, eb[a["sabotage"] - 1] - SABOTAGE_LOSS)
            sa = sb = fa = fb = 0
            for f in range(FIELDS):
                lead = ea[f] - eb[f]
                if lead >= margin:
                    extra, u, x = bonus(f, a["alloc"][f], lead)
                    sa += values[f] + extra
                    fa += 1
                elif -lead >= margin:
                    extra, u, x = bonus(f, b["alloc"][f], -lead)
                    sb += values[f] + extra
                    fb += 1
                else:
                    continue
                underdog_caps += u
                exact_wins += x

            reason = "points"
            if capital_mode:
                a_fell = bool(a["capital"]) and eb[a["capital"] - 1] - ea[a["capital"] - 1] >= margin
                b_fell = bool(b["capital"]) and ea[b["capital"] - 1] - eb[b["capital"] - 1] >= margin
            else:
                a_fell = b_fell = False

            if a_fell and b_fell:
                outcome, reason = 0, "both capitals fell"
            elif a_fell:
                outcome, reason = -1, "capital"
            elif b_fell:
                outcome, reason = 1, "capital"
            else:
                outcome = (sa > sb) - (sa < sb)

            ra, rb = rec[a["teamId"]], rec[b["teamId"]]
            ra["fieldsWon"] += fa
            rb["fieldsWon"] += fb
            if outcome > 0:
                ra["wins"] += 1; rb["losses"] += 1
                ra["points"] += POINTS["win"]; rb["points"] += POINTS["loss"]
                if reason == "capital":
                    rb["capitalLosses"] += 1
            elif outcome < 0:
                rb["wins"] += 1; ra["losses"] += 1
                rb["points"] += POINTS["win"]; ra["points"] += POINTS["loss"]
                if reason == "capital":
                    ra["capitalLosses"] += 1
            else:
                ra["ties"] += 1; rb["ties"] += 1
                ra["points"] += POINTS["tie"]; rb["points"] += POINTS["tie"]

            res = {1: "W", 0: "T", -1: "L"}
            matrix[a["teamId"]][b["teamId"]] = {"r": res[outcome], "for": sa, "against": sb, "reason": reason}
            matrix[b["teamId"]][a["teamId"]] = {"r": res[-outcome], "for": sb, "against": sa, "reason": reason}

    standings = sorted(rec.values(), key=lambda r: (-r["points"], -r["wins"], r["name"].lower()))
    rank_rows(standings, lambda r: r["points"])

    stats = []
    for f in range(FIELDS):
        col = [e["alloc"][f] for e in entries]
        stats.append({
            "field": f + 1,
            "mean": round(sum(col) / len(col), 2) if col else 0,
            "median": _median(col),
            "min": min(col) if col else 0,
            "max": max(col) if col else 0,
            "total": sum(col),
        })

    capitals, sabotaged = {}, {}
    for e in entries:
        if capital_mode and e["capital"]:
            capitals[e["capital"]] = capitals.get(e["capital"], 0) + 1
        if e["sabotage"]:
            sabotaged[e["sabotage"]] = sabotaged.get(e["sabotage"], 0) + 1

    return {
        "values": values,
        "notes": {str(k + 1): v for k, v in notes.items() if v},
        "unstableFields": unstable_fields,
        "standings": standings,
        "matrix": matrix,
        "stats": stats,
        "capitalCounts": capitals,
        "sabotageCounts": sabotaged,
        "underdogCaptures": underdog_caps,
        "exactWins": exact_wins,
        "participants": len(entries),
    }


def compute_overall(rounds, teams, results_by_round):
    """Sum round points across the given rounds for every team."""
    rows = {
        t["id"]: {
            "teamId": t["id"], "name": t["name"], "active": t.get("active", True),
            "points": 0, "wins": 0, "ties": 0, "losses": 0,
            "roundsPlayed": 0, "lastRoundPoints": None,
        }
        for t in teams
    }
    for rnd in rounds:
        res = results_by_round.get(rnd["id"])
        if not res:
            continue
        for s in res["standings"]:
            r = rows.get(s["teamId"])
            if not r:
                continue
            r["points"] += s["points"]
            r["wins"] += s["wins"]
            r["ties"] += s["ties"]
            r["losses"] += s["losses"]
            r["roundsPlayed"] += 1
            r["lastRoundPoints"] = s["points"]
    out = sorted(
        (r for r in rows.values() if r["active"]),
        key=lambda r: (-r["points"], -r["wins"], r["name"].lower()),
    )
    return rank_rows(out, lambda r: r["points"])
