#!/usr/bin/env python3
"""SM3 Field Wars server: one small Python file, no packages to install.

    python3 server.py                # PIN defaults to "tdsm3", port 8000
    PIN=cardinal PORT=8080 python3 server.py

Pages:
    /display   projector screen (no PIN, never sees hidden strategies)
    /admin     organizer console (PIN)
    /entry     host entry form for phones/laptops (PIN)

Every page checks /api/version every 1.5 s and refetches when it changes, so all
devices stay in sync without holding connections open. State is saved to
data/state.json after every change, so a restart picks up where you left off.
"""

import csv
import io
import json
import os
import random
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import scoring as S

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
PORT = int(os.environ.get("PORT", "8000"))
PIN = os.environ.get("PIN", "tdsm3")
DATA_FILE = os.environ.get("DATA_FILE", os.path.join(ROOT, "data", "state.json"))
DEFAULT_DURATION = 180  # seconds per round

lock = threading.RLock()
DB = {}


class ApiError(Exception):
    pass


def now_ms():
    return int(time.time() * 1000)


def uid():
    return secrets.token_hex(4)


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def fresh_state(name="SM3 Field Wars", version=1, teams=None):
    return {
        "version": version,
        "tournament": {"id": uid(), "name": name, "createdAt": now_ms()},
        "teams": teams or [],
        "rounds": [],
        "currentRoundId": None,
        "display": {"view": "lobby", "roundId": None},
    }


def load():
    try:
        with open(DATA_FILE) as f:
            DB["state"] = json.load(f)
        print(f"Loaded saved tournament from {DATA_FILE}")
    except (OSError, ValueError):
        DB["state"] = fresh_state()


def save():
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(DB["state"], f, indent=1)
    os.replace(tmp, DATA_FILE)


def find_round(st, rid):
    for r in st["rounds"]:
        if r["id"] == rid:
            return r
    raise ApiError("Round not found")


def find_team(st, tid):
    for t in st["teams"]:
        if t["id"] == tid:
            return t
    raise ApiError("Team not found")


def timer_remaining(t):
    rem = t["remaining"]
    if t.get("startedAt"):
        rem -= (now_ms() - t["startedAt"]) / 1000
    return max(0, rem)


def sanitize_round(c, base=None):
    r = dict(base or {})
    mode = c.get("mode", r.get("mode", "classic"))
    r["mode"] = mode if mode in S.MODES else "classic"
    events = c.get("events", r.get("events", []))
    r["events"] = [e for e in S.EVENTS if e in (events or [])]
    try:
        budget = int(c.get("budget") or 0)
    except (TypeError, ValueError):
        budget = 0
    if budget <= 0:
        budget = 40 if "supply" in r["events"] else 100
    r["budget"] = budget
    try:
        duration = int(c.get("duration") or DEFAULT_DURATION)
    except (TypeError, ValueError):
        duration = DEFAULT_DURATION
    duration = max(10, min(3600, duration))
    title = str(c.get("title") or "").strip()[:80]
    if not title:
        title = S.MODES[r["mode"]]["name"]
        if r["events"]:
            title += " + " + " + ".join(S.EVENTS[e]["name"] for e in r["events"])
    r["title"] = title
    r["tag"] = str(c.get("tag") or "").strip()[:40]
    # Hidden Treasure stays secret until results unless the organizer chooses to announce it.
    r["crownHidden"] = bool(c.get("crownHidden", r.get("crownHidden", True)))
    r["notes"] = str(c.get("notes") or "").strip()[:400]
    if base is None:
        r.update({
            "id": uid(), "status": "draft", "revealed": False, "strategiesRevealed": False,
            "draws": {}, "submissions": {},
            "timer": {"duration": duration, "remaining": duration, "startedAt": None},
        })
    else:
        t = r["timer"]
        if not t.get("startedAt") and t["remaining"] == t["duration"]:
            t["remaining"] = duration
        t["duration"] = duration
    ensure_draws(r)
    return r


def ensure_draws(r):
    if r["status"] == "draft":
        return
    for ev in ("crown", "volcano", "frozen"):
        if ev in r["events"] and not r["draws"].get(DRAW_KEY[ev]):
            r["draws"][DRAW_KEY[ev]] = random_draw(DRAW_KEY[ev])
    if "teleport" in r["events"]:
        # Each team gets its own pair of fields to swap, drawn once when its entry arrives.
        swaps = r["draws"].setdefault("teleport", {})
        for tid in r["submissions"]:
            if tid not in swaps:
                swaps[tid] = sorted(random.sample(range(1, S.FIELDS + 1), 2))


DRAW_KEY = {"crown": "crown", "volcano": "volcano", "frozen": "thaw"}


def random_draw(which):
    # Frozen Fortress thaws one neighbour of Field 3; the others can land anywhere.
    return random.choice((2, 4)) if which == "thaw" else random.randint(1, S.FIELDS)


def pick_field(x):
    try:
        x = int(x)
    except (TypeError, ValueError):
        return None
    return x if 1 <= x <= S.FIELDS else None


def validate_submission(r, alloc, capital, sabotage=None):
    """Return (alloc, capital, sabotage, problem) with problem=None when valid."""
    if not isinstance(alloc, list) or len(alloc) != S.FIELDS:
        return alloc, capital, sabotage, "Need exactly 5 fields"
    try:
        alloc = [int(x) for x in alloc]
    except (TypeError, ValueError):
        return alloc, capital, sabotage, "Soldier counts must be whole numbers"
    if any(x < 0 or x > r["budget"] for x in alloc):
        return alloc, capital, sabotage, f"Each field must be between 0 and {r['budget']}"
    if sum(alloc) != r["budget"]:
        return alloc, capital, sabotage, f"Total is {sum(alloc)}, must be exactly {r['budget']}"
    problem = S.submission_problem(r, alloc)
    if problem:
        return alloc, capital, sabotage, problem
    capital = pick_field(capital) if r["mode"] == "capital" else None
    if r["mode"] == "capital" and not capital:
        return alloc, None, sabotage, "Pick a capital field (1-5)"
    sabotage = pick_field(sabotage) if "sabotage" in r["events"] else None
    if "sabotage" in r["events"] and not sabotage:
        return alloc, capital, None, "Pick a field to sabotage (1-5)"
    return alloc, capital, sabotage, None


# --------------------------------------------------------------------------
# Views (what each kind of client is allowed to see)
# --------------------------------------------------------------------------

META = {"modes": S.MODES, "events": S.EVENTS, "points": S.POINTS, "fields": S.FIELDS}


def all_results(st):
    teams_by_id = {t["id"]: t for t in st["teams"]}
    return {r["id"]: S.compute_round(r, teams_by_id) for r in st["rounds"] if r["status"] != "draft"}


def admin_view(st):
    res = all_results(st)
    rounds = []
    for i, r in enumerate(st["rounds"]):
        rr = dict(r)
        rr["number"] = i + 1
        if r["id"] in res:
            rr["results"] = res[r["id"]]
        rr["invalid"] = {
            tid: problem
            for tid, s in r["submissions"].items()
            for *_, problem in [validate_submission(r, s["alloc"], s.get("capital"), s.get("sabotage"))]
            if problem
        }
        rounds.append(rr)
    revealed = [r for r in st["rounds"] if r["revealed"]]
    scored = [r for r in st["rounds"] if r["status"] != "draft"]
    return {
        **st, "role": "admin", "rounds": rounds, "meta": META, "serverNow": now_ms(),
        "overall": S.compute_overall(scored, st["teams"], res),
        "overallPublic": S.compute_overall(revealed, st["teams"], res),
    }


def public_view(st):
    res = all_results(st)
    team_ids = {t["id"] for t in st["teams"]}
    rounds = []
    for i, r in enumerate(st["rounds"]):
        if r["status"] == "draft":
            continue
        pr = {k: r.get(k) for k in ("id", "title", "tag", "mode", "events", "budget", "status",
                                    "revealed", "strategiesRevealed", "timer", "notes")}
        pr["number"] = i + 1
        pr["submitted"] = [tid for tid in r["submissions"] if tid in team_ids]
        draws = {}
        if "crown" in r["events"] and (r["revealed"] or not r.get("crownHidden")):
            draws["crown"] = r["draws"].get("crown")
        if r["revealed"]:
            draws["volcano"] = r["draws"].get("volcano")
            draws["thaw"] = r["draws"].get("thaw")
            x = res[r["id"]]
            pr["results"] = {k: x[k] for k in ("values", "notes", "unstableFields", "standings",
                                               "stats", "capitalCounts", "sabotageCounts", "underdogCaptures",
                                               "exactWins", "participants")}
        if r["strategiesRevealed"]:
            swaps = r["draws"].get("teleport") or {} if "teleport" in r["events"] else {}
            pr["allocations"] = {
                tid: {"alloc": s["alloc"], "capital": s.get("capital"),
                      "sabotage": s.get("sabotage"), "swap": swaps.get(tid)}
                for tid, s in r["submissions"].items() if tid in team_ids
            }
        pr["draws"] = draws
        rounds.append(pr)
    revealed = [r for r in st["rounds"] if r["revealed"]]
    return {
        "role": "public", "version": st["version"], "tournament": st["tournament"],
        "teams": [{"id": t["id"], "name": t["name"], "active": t.get("active", True)} for t in st["teams"]],
        "rounds": rounds, "totalRounds": len(st["rounds"]),
        "currentRoundId": st["currentRoundId"], "display": st["display"],
        "overall": S.compute_overall(revealed, st["teams"], res),
        "meta": META, "serverNow": now_ms(),
    }


# --------------------------------------------------------------------------
# Actions (every write goes through here)
# --------------------------------------------------------------------------

NOCHANGE = "_nochange"


def a_tournament_new(st, p):
    teams = []
    if p.get("keepTeams"):
        teams = [dict(t, active=True) for t in st["teams"] if t.get("active", True)]
    name = str(p.get("name") or "SM3 Field Wars").strip()[:80]
    DB["state"] = fresh_state(name, st["version"], teams)


def a_tournament_rename(st, p):
    st["tournament"]["name"] = str(p.get("name") or "").strip()[:80] or st["tournament"]["name"]


def a_team_add(st, p):
    existing = {t["name"].lower() for t in st["teams"]}
    added, skipped = [], []
    for name in p.get("names") or []:
        name = str(name).strip()[:60]
        if not name:
            continue
        if name.lower() in existing:
            skipped.append(name)
            continue
        existing.add(name.lower())
        st["teams"].append({"id": uid(), "name": name, "active": True, "createdAt": now_ms()})
        added.append(name)
    if not added:
        raise ApiError("No new teams added" + (f" (already exists: {', '.join(skipped)})" if skipped else ""))
    return {"added": added, "skipped": skipped}


def a_team_rename(st, p):
    t = find_team(st, p.get("id"))
    name = str(p.get("name") or "").strip()[:60]
    if not name:
        raise ApiError("Name can't be empty")
    if any(o["name"].lower() == name.lower() and o["id"] != t["id"] for o in st["teams"]):
        raise ApiError("Another team already has that name")
    t["name"] = name


def a_team_withdraw(st, p):
    find_team(st, p.get("id"))["active"] = False


def a_team_restore(st, p):
    find_team(st, p.get("id"))["active"] = True


def a_team_delete(st, p):
    t = find_team(st, p.get("id"))
    st["teams"] = [o for o in st["teams"] if o["id"] != t["id"]]
    for r in st["rounds"]:
        r["submissions"].pop(t["id"], None)


def a_round_create(st, p):
    r = sanitize_round(p.get("config") or {})
    st["rounds"].append(r)
    return {"id": r["id"]}


def a_round_update(st, p):
    r = find_round(st, p.get("id"))
    idx = st["rounds"].index(r)
    st["rounds"][idx] = sanitize_round(p.get("config") or {}, r)


def a_round_delete(st, p):
    r = find_round(st, p.get("id"))
    st["rounds"].remove(r)
    if st["currentRoundId"] == r["id"]:
        st["currentRoundId"] = None
    if st["display"].get("roundId") == r["id"]:
        st["display"] = {"view": "lobby", "roundId": None}


def a_round_move(st, p):
    r = find_round(st, p.get("id"))
    i = st["rounds"].index(r)
    j = i + (1 if p.get("dir") == "down" else -1)
    if 0 <= j < len(st["rounds"]):
        st["rounds"][i], st["rounds"][j] = st["rounds"][j], st["rounds"][i]


def a_round_open(st, p):
    r = find_round(st, p.get("id"))
    r["status"] = "open"
    ensure_draws(r)
    st["currentRoundId"] = r["id"]
    st["display"] = {"view": "round", "roundId": r["id"]}


def a_round_lock(st, p):
    r = find_round(st, p.get("id"))
    r["status"] = "locked"
    ensure_draws(r)
    t = r["timer"]
    t["remaining"], t["startedAt"] = timer_remaining(t), None


def a_round_unlock(st, p):
    r = find_round(st, p.get("id"))
    r["status"] = "open"


def a_round_reveal(st, p):
    a_round_lock(st, p)
    r = find_round(st, p.get("id"))
    r["revealed"] = True
    st["display"] = {"view": "results", "roundId": r["id"]}


def a_round_strategies(st, p):
    r = find_round(st, p.get("id"))
    show = bool(p.get("value", True))
    if show:
        a_round_reveal(st, p)
        st["display"] = {"view": "strategies", "roundId": r["id"]}
    r["strategiesRevealed"] = show


def a_round_hide(st, p):
    r = find_round(st, p.get("id"))
    r["revealed"] = r["strategiesRevealed"] = False
    if st["display"].get("roundId") == r["id"] and st["display"]["view"] in ("results", "strategies"):
        st["display"]["view"] = "round"


def a_round_redraw(st, p):
    r = find_round(st, p.get("id"))
    which = p.get("which")
    if which not in ("crown", "volcano", "thaw"):
        raise ApiError("Unknown draw")
    r["draws"][which] = random_draw(which)


def a_timer(st, p):
    r = find_round(st, p.get("id"))
    t = r["timer"]
    op = p.get("op")
    if op == "start":
        if not t.get("startedAt") and t["remaining"] > 0:
            t["startedAt"] = now_ms()
    elif op == "pause":
        t["remaining"], t["startedAt"] = timer_remaining(t), None
    elif op == "reset":
        t["remaining"], t["startedAt"] = t["duration"], None
    elif op == "add":
        t["remaining"] = max(0, t["remaining"] + int(p.get("seconds") or 60))
    else:
        raise ApiError("Unknown timer op")


def a_submit(st, p):
    r = find_round(st, p.get("roundId"))
    team = find_team(st, p.get("teamId"))
    if r["status"] == "draft":
        raise ApiError("This round hasn't been opened yet")
    if r["status"] != "open" and not p.get("force"):
        raise ApiError("This round is locked. Ask the organizer to unlock it.")
    if not team.get("active", True):
        raise ApiError(f"{team['name']} has been withdrawn")
    alloc, capital, sabotage, problem = validate_submission(r, p.get("alloc"), p.get("capital"), p.get("sabotage"))
    if problem:
        raise ApiError(problem)
    existing = r["submissions"].get(team["id"])
    if existing and not p.get("overwrite"):
        if existing["alloc"] == alloc and existing.get("capital") == capital and existing.get("sabotage") == sabotage:
            return {NOCHANGE: True, "same": True}
        return {NOCHANGE: True, "conflict": True, "existing": existing}
    r["submissions"][team["id"]] = {
        "alloc": alloc, "capital": capital, "sabotage": sabotage,
        "enteredBy": str(p.get("enteredBy") or "")[:40], "at": now_ms(),
    }
    ensure_draws(r)


def a_submission_delete(st, p):
    r = find_round(st, p.get("roundId"))
    r["submissions"].pop(p.get("teamId"), None)


def a_display(st, p):
    view = p.get("view")
    if view not in ("lobby", "round", "results", "strategies", "leaderboard", "standby"):
        raise ApiError("Unknown view")
    st["display"] = {"view": view, "roundId": p.get("roundId") or st["display"].get("roundId") or st["currentRoundId"]}


SUGGESTED = [
    {"mode": "classic"},
    {"mode": "weighted"},
    {"mode": "capital"},
    {"mode": "capital", "events": ["unstable"]},
    {"mode": "weighted", "events": ["volcano"]},
    {"mode": "classic", "events": ["crown"]},
    {"mode": "capital", "events": ["sabotage"]},
    {"mode": "classic", "events": ["underdog"]},
    {"mode": "weighted", "events": ["supply"]},
    {"mode": "classic", "events": ["frozen"]},
    {"mode": "weighted", "events": ["siege"]},
    {"mode": "classic", "events": ["teleport"]},
    {"mode": "capital", "events": ["exact"]},
    {"mode": "classic", "events": ["nomans"]},
    {"mode": "capital", "events": ["staircase"]},
]


def lightning_series():
    return [{"mode": "weighted", "title": f"Lightning {i}", "tag": f"⚡ Lightning {i}/3", "duration": 120}
            for i in (1, 2, 3)]


def a_schedule(st, p):
    configs = lightning_series() if p.get("kind") == "lightning" else SUGGESTED
    for c in configs:
        st["rounds"].append(sanitize_round(c))


def a_import(st, p):
    new = p.get("state")
    if not isinstance(new, dict) or not all(k in new for k in ("teams", "rounds", "tournament")):
        raise ApiError("That doesn't look like an SM3 Field Wars backup")
    new.setdefault("display", {"view": "lobby", "roundId": None})
    new.setdefault("currentRoundId", None)
    new["version"] = st["version"]
    DB["state"] = new


ACTIONS = {
    "tournament.new": a_tournament_new,
    "tournament.rename": a_tournament_rename,
    "team.add": a_team_add,
    "team.rename": a_team_rename,
    "team.withdraw": a_team_withdraw,
    "team.restore": a_team_restore,
    "team.delete": a_team_delete,
    "round.create": a_round_create,
    "round.update": a_round_update,
    "round.delete": a_round_delete,
    "round.move": a_round_move,
    "round.open": a_round_open,
    "round.lock": a_round_lock,
    "round.unlock": a_round_unlock,
    "round.reveal": a_round_reveal,
    "round.strategies": a_round_strategies,
    "round.hide": a_round_hide,
    "round.redraw": a_round_redraw,
    "timer": a_timer,
    "submit": a_submit,
    "submission.delete": a_submission_delete,
    "display": a_display,
    "schedule": a_schedule,
    "state.import": a_import,
}


def run_action(kind, payload):
    fn = ACTIONS.get(kind)
    if not fn:
        raise ApiError(f"Unknown action {kind}")
    with lock:
        out = fn(DB["state"], payload) or {}
        if not out.pop(NOCHANGE, False):
            DB["state"]["version"] += 1
            save()
        return out


def export_csv(st):
    res = all_results(st)
    names = {t["id"]: t["name"] for t in st["teams"]}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["round", "title", "mode", "events", "budget", "team",
                "f1", "f2", "f3", "f4", "f5", "capital", "sabotage", "teleport_swap",
                "wins", "ties", "losses", "round_points", "round_rank"])
    for i, r in enumerate(st["rounds"]):
        rr = {s["teamId"]: s for s in res.get(r["id"], {}).get("standings", [])}
        swaps = r["draws"].get("teleport") or {} if "teleport" in r["events"] else {}
        for tid, s in r["submissions"].items():
            x = rr.get(tid, {})
            swap = swaps.get(tid)
            w.writerow([i + 1, r["title"], r["mode"], " ".join(r["events"]), r["budget"], names.get(tid, tid),
                        *s["alloc"], s.get("capital") or "", s.get("sabotage") or "",
                        f"F{swap[0]}<->F{swap[1]}" if swap else "", x.get("wins"), x.get("ties"),
                        x.get("losses"), x.get("points"), x.get("rank")])
    w.writerow([])
    w.writerow(["overall_rank", "team", "total_points", "wins", "ties", "losses", "rounds_played"])
    for o in S.compute_overall([r for r in st["rounds"] if r["status"] != "draft"], st["teams"], res):
        w.writerow([o["rank"], o["name"], o["points"], o["wins"], o["ties"], o["losses"], o["roundsPlayed"]])
    return buf.getvalue()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

PAGES = {"/": "index.html", "/display": "display.html", "/admin": "admin.html", "/entry": "entry.html", "/sheet": "sheet.html"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".ico": "image/x-icon", ".json": "application/json"}


class Handler(BaseHTTPRequestHandler):
    server_version = "SM3FieldWars/1.0"

    def log_message(self, fmt, *args):
        pass

    def is_admin(self, query):
        given = self.headers.get("X-Pin") or (query.get("pin") or [""])[0]
        return secrets.compare_digest(given.encode(), PIN.encode())

    def send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/api/state":
            with lock:
                st = DB["state"]
                return self.send(200, admin_view(st) if self.is_admin(q) else public_view(st))
        if url.path == "/api/version":
            return self.send(200, {"version": DB["state"]["version"]})
        if url.path in ("/api/export.csv", "/api/export.json"):
            if not self.is_admin(q):
                return self.send(403, {"error": "Wrong PIN"})
            with lock:
                st = DB["state"]
                stamp = time.strftime("%Y%m%d-%H%M")
                if url.path.endswith(".csv"):
                    return self.send(200, export_csv(st), "text/csv; charset=utf-8",
                                     {"Content-Disposition": f'attachment; filename="sm3-field-wars-{stamp}.csv"'})
                return self.send(200, json.dumps(st, indent=1), "application/json",
                                 {"Content-Disposition": f'attachment; filename="sm3-field-wars-{stamp}.json"'})
        self.static(url.path)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/api/action":
            return self.send(404, {"error": "Not found"})
        if not self.is_admin(parse_qs(url.query)):
            return self.send(403, {"error": "Wrong PIN"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 10_000_000:
                raise ApiError("Request too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            out = run_action(body.get("type"), body.get("payload") or {})
            self.send(200, {"ok": True, **out})
        except ApiError as e:
            self.send(400, {"ok": False, "error": str(e)})
        except ValueError:
            self.send(400, {"ok": False, "error": "Bad JSON"})

    def static(self, path):
        rel = PAGES.get(path, path.lstrip("/"))
        full = os.path.normpath(os.path.join(PUBLIC, rel))
        if not full.startswith(PUBLIC + os.sep) or not os.path.isfile(full):
            return self.send(404, "Not found", "text/plain")
        with open(full, "rb") as f:
            data = f.read()
        self.send(200, data, TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the outbound interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    load()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True
    ip = lan_ip()
    print("\n  SM3 Field Wars is running\n")
    print(f"  Projector  http://{ip}:{PORT}/display")
    print(f"  Organizer  http://{ip}:{PORT}/admin")
    print(f"  Hosts      http://{ip}:{PORT}/entry")
    print(f"\n  PIN for organizer + hosts: {PIN}")
    print(f"  (on this laptop you can also use http://localhost:{PORT})\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Your data is saved in", DATA_FILE)


if __name__ == "__main__":
    main()
