import os
import secrets
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

import engine  # noqa: E402
from levels import LEVELS  # noqa: E402

GAME_SECONDS = 15 * 60
BASE_POINTS, HINT_COST = 10, 2
BONUS = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1} 
MAX_TEAMS = int(os.getenv("MAX_TEAMS", "12"))
ADMIN_KEY = os.getenv("ADMIN_KEY") or secrets.token_urlsafe(6)

app = FastAPI(title="Oracle Escape Room", docs_url=None, redoc_url=None, openapi_url=None)
BASE = Path(__file__).parent
lock = threading.RLock()
teams: dict[str, dict] = {}
SOLVES: list[list[dict]] = [[] for _ in LEVELS]
game = {"start": None}

print(f"\n[admin] Lancer la partie : /api/admin/start?key={ADMIN_KEY}")
print(f"[admin] Tout réinitialiser : /api/admin/reset?key={ADMIN_KEY}\n")


class Join(BaseModel):
    team: str


class Retry(Join):
    level: int | None = None


class Cmd(Join):
    command: str


# --- helpers -------------------------------------------------------------
def get_team(name: str) -> dict:
    t = teams.get(name.strip().lower())
    if not t:
        raise HTTPException(404, "Équipe inconnue : rejoignez la partie d'abord.")
    return t


def over() -> bool:
    return bool(game["start"]) and time.time() - game["start"] >= GAME_SECONDS


def left(t) -> int:
    end = t.get("end") or time.time()
    return max(0, GAME_SECONDS - int(end - game["start"]))


def elapsed_now() -> int:
    return int(time.time() - game["start"]) if game["start"] else 0


def start_level(t, i):
    t["level"], t["log"] = i, []
    t["extra"] = LEVELS[i]["setup"](t["st"])


def advance(t):
    i = next((i for i, r in enumerate(t["results"]) if r is None), None)
    if i is None:
        t["end"] = time.time()
    else:
        start_level(t, i)


def expire(t):
    if game["start"] and not t.get("end") and over():
        t["results"] = [False if r is None else r for r in t["results"]]
        t["end"] = game["start"] + GAME_SECONDS


def team_score(name: str) -> int:
    k = name.lower()
    return sum(s["pts"] for lvl in SOLVES for s in lvl if s["team"].lower() == k)


def view(t):
    expire(t)
    base = {"team": t["name"]}
    if not game["start"]:
        return {**base, "lobby": True, "done": False, "teams": [x["name"] for x in teams.values()]}
    if t.get("end"):
        return {**base, "lobby": False, "done": True, "time": int(t["end"] - game["start"]),
                "score": team_score(t["name"]),
                "can_retry": (not over()) and any(r is False for r in t["results"]),
                "results": [{"concept": lv["concept"], "ok": r is True}
                            for lv, r in zip(LEVELS, t["results"])]}
    lv, n = LEVELS[t["level"]], t["hints"][t["level"]]
    return {**base, "lobby": False, "done": False, "level": t["level"], "total": len(LEVELS),
            "title": lv["title"], "sev": lv["sev"], "from": lv["from"],
            "story": lv["story"] + ("\n\n" + t["extra"] if t["extra"] else ""),
            "hints": lv["hints"][:n], "hints_left": len(lv["hints"]) - n,
            "hint_cost": HINT_COST, "time_left": left(t), "results": t["results"]}


def _admin(key: str):
    if not secrets.compare_digest(key, ADMIN_KEY):
        raise HTTPException(403, "Clé admin invalide.")


# --- routes --------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/admin/start")
def admin_start(key: str):
    _admin(key)
    with lock:
        if not game["start"]:
            game["start"] = time.time()
    return {"started": True, "teams": len(teams)}


@app.get("/api/admin/reset")
def admin_reset(key: str):
    _admin(key)
    with lock:
        teams.clear()
        for s in SOLVES:
            s.clear()
        game["start"] = None
    return {"reset": True}


@app.post("/api/join")
def join(j: Join):
    with lock:
        name = j.team.strip()
        if not 2 <= len(name) <= 24:
            raise HTTPException(400, "Nom d'équipe : 2 à 24 caractères.")
        key = name.lower()
        t = teams.get(key)
        if t is None:
            if len(teams) >= MAX_TEAMS:
                raise HTTPException(429, "Partie complète.")
            t = {"name": name, "st": engine.new_state(name),
                 "results": [None] * len(LEVELS), "hints": [0] * len(LEVELS)}
            teams[key] = t
            start_level(t, 0)
        return view(t)


@app.get("/api/state")
def state(team: str):
    with lock:
        return view(get_team(team))


@app.post("/api/command")
def command(c: Cmd):
    with lock:
        t = get_team(c.team)
        v = view(t)
        if v["lobby"] or v["done"]:
            return {"output": "", "unlocked": None, "view": v}
        if len(c.command) > 200:
            raise HTTPException(400, "Commande trop longue.")
        st, before = t["st"], t["st"].stage
        st.history.append(c.command)
        out = engine.execute(st, c.command)
        up = " ".join(c.command.strip().rstrip(";").upper().split())
        if up == "ALTER DATABASE OPEN READ WRITE":
            up = "ALTER DATABASE OPEN"
        t["log"].append((up, before, out))

        lv, unlocked = LEVELS[t["level"]], None
        if lv["check"](st, t["log"]):
            idx = t["level"]
            t["results"][idx] = True
            unlocked = lv["debrief"]
            if not any(s["team"].lower() == t["name"].lower() for s in SOLVES[idx]):
                place = len(SOLVES[idx]) + 1
                pts = max(1, BASE_POINTS + BONUS.get(place, 0) - HINT_COST * t["hints"][idx])
                SOLVES[idx].append({"team": t["name"], "elapsed": elapsed_now(), "hints": t["hints"][idx],
                                    "place": place, "pts": pts})
            advance(t)
        return {"output": out, "unlocked": unlocked, "view": view(t)}


@app.post("/api/hint")
def hint(j: Join):
    with lock:
        t = get_team(j.team)
        v = view(t)
        if v["lobby"] or v["done"]:
            return v
        i = t["level"]
        if t["hints"][i] < len(LEVELS[i]["hints"]):
            t["hints"][i] += 1
        return view(t)


@app.post("/api/skip")
def skip(j: Join):
    with lock:
        t = get_team(j.team)
        v = view(t)
        if v["lobby"] or v["done"]:
            return v
        t["results"][t["level"]] = False
        advance(t)
        return view(t)


@app.post("/api/retry")
def retry(j: Retry):
    with lock:
        t = get_team(j.team)
        expire(t)
        if not game["start"] or over() or not t.get("end"):
            return view(t)
        failed = [i for i, r in enumerate(t["results"]) if r is False]
        idx = j.level if j.level in failed else (failed[0] if failed else None)
        if idx is not None:
            t["results"][idx] = None
            t["end"] = None
            start_level(t, idx)
        return view(t)


@app.post("/api/reset")
def reset(j: Join):
    with lock:
        t = get_team(j.team)
        v = view(t)
        if not (v["lobby"] or v["done"]):
            start_level(t, t["level"])
        return view(t)


@app.get("/api/debriefs")
def get_debriefs(team: str):
    with lock:
        t = get_team(team)
        return [{"title": lv["title"], "debrief": lv["debrief"]} if r is True else None
                for lv, r in zip(LEVELS, t["results"])]


@app.get("/api/leaderboard")
def leaderboard():
    with lock:
        rows = {k: {"team": t["name"], "score": 0, "levels": 0, "firsts": 0, "time": 0}
                for k, t in teams.items()}
        for lvl in SOLVES:
            for s in lvl:
                r = rows.get(s["team"].lower())
                if r:
                    r["score"] += s["pts"]
                    r["levels"] += 1
                    r["firsts"] += 1 if s["place"] == 1 else 0
                    r["time"] += s["elapsed"]
        ranked = sorted(rows.values(), key=lambda x: (-x["score"], -x["firsts"], x["time"], x["team"].lower()))
        levels_view = [{"index": i, "title": lv["title"],
                        "solves": [{"team": s["team"], "elapsed": s["elapsed"]} for s in SOLVES[i]]}
                       for i, lv in enumerate(LEVELS)]
        return {"levels": levels_view, "teams": ranked, "started": bool(game["start"])}