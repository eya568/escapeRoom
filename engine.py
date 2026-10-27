"""Moteur de simulation : états de l'instance Oracle + console SQL*Plus simulée.

La commande saisie n'est JAMAIS envoyée à Oracle : elle est analysée ici.
"""
import copy
import os
import random
import re
import time
from dataclasses import dataclass, field

STAGES = ["SHUTDOWN", "NOMOUNT", "MOUNT", "OPEN"]
STATUS_OF = {1: "STARTED", 2: "MOUNTED", 3: "OPEN"}
SPFILE_PATH = "/u01/app/oracle/dbs/spfileXE.ora"
DEFAULT_CACHE = "268435456"
SETTABLE = ("db_cache_size",)

_cache = {"t": 0.0, "data": None}


def fetch_real():
    """Lit V$SGA / V$CONTROLFILE / V$DATAFILE / V$PARAMETER avec un compte lecture seule."""
    if _cache["data"] is not None and time.time() - _cache["t"] < 300:
        return _cache["data"]
    user, pwd, dsn = (os.getenv(k) for k in ("ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_DSN"))
    if not (user and pwd and dsn):
        return None
    try:
        import oracledb
        with oracledb.connect(user=user, password=pwd, dsn=dsn) as conn:
            cur = conn.cursor()
            sga = {r[0]: int(r[1]) for r in cur.execute("SELECT name, value FROM v$sga")}
            ctl = [r[0] for r in cur.execute("SELECT name FROM v$controlfile")]
            dfs = [(r[0], int(r[1])) for r in cur.execute("SELECT name, bytes FROM v$datafile")]
            params = {r[0]: r[1] for r in cur.execute(
                "SELECT name, value FROM v$parameter WHERE name IN ('spfile','db_name','db_cache_size')")}
            inst = cur.execute("SELECT instance_name, host_name FROM v$instance").fetchone()
        params["control_files"] = ", ".join(ctl)
        if "db_cache_size" not in params:
            params["db_cache_size"] = DEFAULT_CACHE
        data = {"sga": sga, "control": ctl, "datafiles": dfs, "params": params,
                "spfile_params": params.copy(), "instance": inst[0], "host": inst[1], "source": "oracle"}
        _cache.update(t=time.time(), data=data)
        return data
    except Exception:
        return None


def fallback_data(team: str):
    r = random.Random(team)
    base = "/u01/app/oracle/oradata/XE"
    sga = {"Fixed Size": 2_200_000 + r.randint(0, 99_999) * 10,
           "Variable Size": 150_000_000 + r.randint(0, 9_999) * 1000,
           "Database Buffers": r.choice([100, 128, 144, 160, 192]) * 1024 * 1024,
           "Redo Buffers": 5_000_000 + r.randint(0, 999) * 1000}
    ctl = [f"{base}/control01_{r.randint(100, 999)}.ctl", f"{base}/control02.ctl"]
    dfs = [(f"{base}/system01.dbf", 880_803_840), (f"{base}/sysaux01.dbf", 576_716_800),
           (f"{base}/users01.dbf", 104_857_600)]
    params = {"spfile": SPFILE_PATH, "db_name": "XE",
              "control_files": ", ".join(ctl), "db_cache_size": DEFAULT_CACHE}
    return {"sga": sga, "control": ctl, "datafiles": dfs, "params": params,
            "spfile_params": dict(params), "instance": "XE", "host": "pay-db-01", "source": "simulé"}


@dataclass
class Sess:
    sid: int
    user: str
    leaves_after: int | None
    tx_order: int | None = None
    hung: bool = False
    serial: int = 1

    def tx(self):
        return self.tx_order is not None


@dataclass
class State:
    team: str
    data: dict
    stage: int = 0
    dirty: bool = False
    recovered: bool = False
    pending: str | None = None
    waits: int = 0
    orders: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    alert: list = field(default_factory=list)
    events: list = field(default_factory=list)
    lost_orders: int = 0
    history: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def new_state(team: str) -> State:
    return State(team=team, data=copy.deepcopy(fetch_real() or fallback_data(team)))


def log(st: State, msg: str):
    st.alert.append(f"{time.strftime('%H:%M:%S')}  {msg}")


def table(cols, rows):
    if not rows:
        return "aucune ligne sélectionnée"
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows)) for i, c in enumerate(cols)]
    head = "  ".join(str(c).ljust(w) for c, w in zip(cols, widths))
    sep = "  ".join("-" * w for w in widths)
    body = "\n".join("  ".join(str(v).ljust(w) for v, w in zip(r, widths)) for r in rows)
    n = len(rows)
    return f"{head}\n{sep}\n{body}\n\n{n} ligne{'s' if n > 1 else ''} sélectionnée{'s' if n > 1 else ''}."


def sga_banner(st: State):
    s = st.data["sga"]
    lines = [f"Total System Global Area {sum(s.values()):>14} octets"]
    lines += [f"{k:<26}{v:>14} octets" for k, v in s.items()]
    return "\n".join(lines)


def live_sessions(st: State):
    return [s for s in st.sessions if s.leaves_after is None or s.leaves_after > st.waits]


# --- vues simulées -------------------------------------------------------
def _v_instance(st):
    return [(st.data["instance"], STATUS_OF.get(st.stage, ""), time.strftime("%d-%b-%y"), st.data["host"])]


def _v_database(st):
    mode = "MOUNTED" if st.stage == 2 else ("READ WRITE" if st.stage == 3 else "")
    return [(st.data["params"].get("db_name", "XE"), mode, "NOARCHIVELOG")]


def _v_session(st):
    return [(s.sid, s.serial, s.user, "ACTIVE" if s.tx() else "INACTIVE", "pay_app") for s in live_sessions(st)]


def _v_transaction(st):
    return [(sum(1 for s in live_sessions(st) if s.tx()),)]


def _v_bg(st):
    return [("PMON", "process monitor"), ("SMON", "system monitor"), ("DBW0", "database writer"),
            ("LGWR", "log writer"), ("CKPT", "checkpoint")]


VIEWS = {
    "V$BGPROCESS":    (["NAME", "DESCRIPTION"], 1, _v_bg),
    "V$INSTANCE":     (["INSTANCE_NAME", "STATUS", "STARTUP_TIME", "HOST_NAME"], 1, _v_instance),
    "V$SGA":          (["NAME", "VALUE"], 1, lambda st: list(st.data["sga"].items())),
    "V$PARAMETER":    (["NAME", "VALUE"], 1, lambda st: list(st.data["params"].items())),
    "V$CONTROLFILE": (["NAME"], 1,
                  lambda st: [(n,) for n in st.data["control"]] if st.stage >= 2 else []),
    "V$DATABASE":     (["NAME", "OPEN_MODE", "LOG_MODE"], 2, _v_database),
    "V$DATAFILE":     (["FILE#", "NAME", "BYTES"], 2,
                       lambda st: [(i + 1, n, b) for i, (n, b) in enumerate(st.data["datafiles"])]),
    "V$SESSION":      (["SID", "SERIAL#", "USERNAME", "STATUS", "PROGRAM"], 2, _v_session),
    "V$TRANSACTION":  (["COUNT(*)"], 3, _v_transaction),
    "DBA_DATA_FILES": (["FILE_ID", "FILE_NAME", "BYTES"], 3,
                       lambda st: [(i + 1, n, b) for i, (n, b) in enumerate(st.data["datafiles"])]),
    "PAYMENTS":       (["ID", "MERCHANT"], 3,
                       lambda st: [(o["id"], o["produit"]) for o in st.orders if o["committed"]]),
}
ALIASES = {"NOVAPAY.PAYMENTS": "PAYMENTS", "SYS.V$INSTANCE": "V$INSTANCE"}


COND = re.compile(r"(?is)^\s*('[^']*'|[\w$#]+)\s*(=|!=|<>|<=|>=|<|>|LIKE)\s*('[^']*'|[\w$#]+)\s*$")
LITERAL = r"'[^']*'|\d+(?:\.\d+)?"


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _cmp(a, b, op):
    if op == "LIKE":
        pat = "^" + "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in str(b)) + "$"
        return re.match(pat, str(a), re.S) is not None
    if isinstance(a, str) != isinstance(b, str):
        a, b = _num(a), _num(b)
        if a is None or b is None:
            return False
    elif not isinstance(a, str):
        a, b = float(a), float(b)
    return {"=": a == b, "!=": a != b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]


def _filter(cols, rows, where):
    """WHERE valide uniquement : <col|littéral> <op> <col|littéral>, combinés par AND / OR (sensible à la casse)."""
    groups = []
    for g in re.split(r"(?i)\s+OR\s+", where.strip()):
        conds = []
        for c in re.split(r"(?i)\s+AND\s+", g):
            m = COND.match(c)
            if not m:
                return None, "ORA-00920: opérateur relationnel non valide"
            for tok in (m.group(1), m.group(3)):
                if not re.fullmatch(LITERAL, tok) and tok.upper() not in cols:
                    return None, f'ORA-00904: "{tok.upper()}" : identifiant invalide'
            conds.append((m.group(1), m.group(2).upper(), m.group(3)))
        groups.append(conds)

    def val(tok, row):
        if tok.startswith("'"):
            return tok[1:-1]
        n = _num(tok) if re.fullmatch(r"\d+(?:\.\d+)?", tok) else None
        return n if n is not None else row[cols.index(tok.upper())]

    keep = [r for r in rows if any(all(_cmp(val(l, r), val(rt, r), op) for l, op, rt in g) for g in groups)]
    return keep, None


def run_select(st: State, raw: str) -> str:
    m = re.match(r"(?is)^select\s+(.+?)\s+from\s+([\w$.]+)\s*(?:where\s+(.*))?$", raw.strip())
    if not m:
        return "ORA-00923: mot-clé FROM absent là où il est attendu"
    cols_txt, view, where = m.group(1), m.group(2).upper(), m.group(3)
    view = ALIASES.get(view, view)
    if view.startswith("SYS."):
        view = view[4:]
    if view not in VIEWS:
        return "ORA-00942: la table ou la vue n'existe pas"
    cols, min_stage, fn = VIEWS[view]
    if st.stage == 0:
        return "ORA-01034: ORACLE n'est pas disponible"
    if st.stage < min_stage:
        if view.startswith("V$"):
            return "ORA-01507: base de données non montée"
        return "ORA-01219: base de données non ouverte : requêtes autorisées uniquement sur les tables/vues fixes"
    rows = fn(st)
    if where is not None:
        rows, err = _filter(cols, rows, where)
        if err:
            return err
    if cols_txt.strip() == "*":
        return table(cols, rows)
    wanted = [c.strip().upper() for c in cols_txt.split(",")]
    for c in wanted:
        if c not in cols:
            return f'ORA-00904: "{c}" : identifiant invalide'
    idx = [cols.index(c) for c in wanted]
    return table(wanted, [tuple(r[i] for i in idx) for r in rows])


# --- démarrage / arrêt ---------------------------------------------------
def do_startup(st: State, target: int) -> str:
    out = ["Instance ORACLE démarrée.", "", sga_banner(st)]
    log(st, "Démarrage de l'instance ORACLE (normal)")
    st.data["params"] = st.data["spfile_params"].copy()  # l'instance relit le SPFILE
    st.stage = 1
    if target >= 2:
        out.append("Base de données montée.")
        log(st, "ALTER DATABASE MOUNT")
        st.stage = 2
    if target >= 3:
        out.append(do_open(st))
    st.events.append(f"STARTUP:{STAGES[target]}")
    return "\n".join(out)


def do_open(st: State) -> str:
    log(st, "ALTER DATABASE OPEN")
    msg = "Base de données ouverte."
    if st.dirty:
        log(st, "Début de la récupération d'instance après plantage")
        before = len(st.orders)
        st.orders = [o for o in st.orders if o["committed"]]
        st.lost_orders += before - len(st.orders)
        for s in st.sessions:
            s.tx_order = None
        log(st, "Fin de la récupération d'instance après plantage")
        st.dirty, st.recovered = False, True
        msg += ("\nRécupération d'instance (SMON) : roll forward des transactions validées, "
                "roll back des transactions non validées.")
    log(st, "Terminé : ALTER DATABASE OPEN")
    st.stage = 3
    st.pending = None
    st.events.append("OPEN")
    return msg


def _advance_sessions(st: State) -> str:
    out = []
    for s in list(st.sessions):
        if s.leaves_after is not None and s.leaves_after <= st.waits:
            if s.tx():
                for o in st.orders:
                    if o["id"] == s.tx_order:
                        o["committed"] = True
                out.append(f"Session {s.sid} ({s.user}) : COMMIT puis déconnexion.")
            else:
                out.append(f"Session {s.sid} ({s.user}) : déconnectée.")
            s.tx_order = None
    st.sessions = live_sessions(st)
    return "\n".join(out)


def do_shutdown(st: State, mode: str) -> str:
    adv = ""
    if st.pending:
        st.waits += 1
        adv = _advance_sessions(st)
    sess = live_sessions(st)
    blocked = False
    if st.stage == 3 and st.meta.get("unresponsive") and mode != "ABORT":
        blocked = True
    elif st.stage == 3:
        if mode == "NORMAL":
            blocked = bool(sess)
        elif mode == "TRANSACTIONAL":
            blocked = any(s.tx() for s in sess)
        elif mode == "IMMEDIATE":
            blocked = any(s.hung for s in sess)

    if blocked:
        st.pending = mode
        log(st, f"Arrêt de l'instance ({mode.lower()}) : en attente")
        msg = f"SHUTDOWN {mode} : en attente… l'instance ne rend pas la main."
        return (adv + "\n" + msg) if adv else msg

    log(st, f"Arrêt de l'instance ({mode.lower()})")
    st.meta["unresponsive"] = False
    if mode == "ABORT":
        st.dirty = True
        log(st, "Instance terminée par l'utilisateur")
        out = "Instance ORACLE arrêtée."
    else:
        if mode == "IMMEDIATE":
            before = len(st.orders)
            st.orders = [o for o in st.orders if o["committed"]]
            st.lost_orders += before - len(st.orders)
        st.dirty = False
        log(st, "Arrêt de l'instance terminé")
        out = "Base de données fermée.\nBase de données démontée.\nInstance ORACLE arrêtée."
    for s in st.sessions:
        s.tx_order = None
    st.sessions = []
    st.stage, st.pending = 0, None
    st.events.append(f"SHUTDOWN:{mode}")
    return (adv + "\n" + out) if adv else out


HELP = """Commandes disponibles :
  CONNECT / AS SYSDBA          SHOW USER          SHOW SGA
  SHOW PARAMETER spfile
  STARTUP [NOMOUNT|MOUNT]      ALTER DATABASE MOUNT | OPEN
  SHUTDOWN [NORMAL|TRANSACTIONAL|IMMEDIATE|ABORT]
  ALTER SYSTEM SET <param>=<val> [SCOPE=MEMORY|SPFILE|BOTH]
  SELECT ... FROM <vue>   (V$INSTANCE, V$DATABASE, V$SGA, V$PARAMETER, V$CONTROLFILE,
                           V$DATAFILE, V$SESSION, V$TRANSACTION, V$BGPROCESS, PAYMENTS)
  HOST tail alert              journal d'alerte"""


def execute(st: State, raw: str) -> str:
    cmd = raw.strip().rstrip(";").strip()
    up = " ".join(cmd.upper().split())
    if not up:
        return ""
    if up == "HELP":
        return HELP
    if up.split()[0] in ("CONNECT", "CONN"):
        return "Connecté à une instance inactive." if st.stage == 0 else "Connecté."
    if up == "SHOW USER":
        return 'USER est "SYS"'
    if up == "SHOW SGA":
        return "ORA-01034: ORACLE n'est pas disponible" if st.stage == 0 else sga_banner(st)
    if re.match(r"^(HOST )?(TAIL|CAT|TYPE)\b.*ALERT", up):
        return "\n".join(st.alert[-15:]) or "(journal vide)"

    if up.startswith("SHOW PARAMETER"):
        if st.stage == 0:
            return "ORA-01034: ORACLE n'est pas disponible"
        m = re.match(r"^SHOW PARAMETERS? (\w+)$", up)
        if not m:
            return "SP2-0226: Numéro de ligne invalide"
        name = m.group(1).lower()
        value = st.data["params"].get(name, "")
        rows = [(name, "string", value)] if value else []
        return table(["NAME", "TYPE", "VALUE"], rows)

    if up.startswith("SELECT"):
        return run_select(st, cmd)

    if up == "ALTER DATABASE MOUNT":
        if st.stage == 0:
            return "ORA-01034: ORACLE n'est pas disponible"
        if st.stage >= 2:
            return "ORA-01100: base de données déjà montée"
        st.stage = 2
        log(st, "ALTER DATABASE MOUNT")
        st.events.append("MOUNT")
        return "Base de données modifiée."
    if up in ("ALTER DATABASE OPEN", "ALTER DATABASE OPEN READ WRITE"):
        if st.stage == 0:
            return "ORA-01034: ORACLE n'est pas disponible"
        if st.stage == 1:
            return "ORA-01507: base de données non montée"
        if st.stage == 3:
            return "ORA-01531: une base de données est déjà ouverte par l'instance"
        return "Base de données modifiée.\n" + do_open(st)

    m = re.match(r"^STARTUP(?: (NOMOUNT|MOUNT))?$", up)
    if m:
        if st.stage > 0:
            return "ORA-01081: impossible de démarrer ORACLE déjà en cours - arrêtez-le d'abord"
        return do_startup(st, {None: 3, "NOMOUNT": 1, "MOUNT": 2}[m.group(1)])

    if up.startswith("ALTER SYSTEM SET"):
        if st.stage == 0:
            return "ORA-01034: ORACLE n'est pas disponible"
        m = re.match(r"^ALTER SYSTEM SET (\w+)\s*=\s*('?)([\w.]+)\2\s*(?:SCOPE\s*=\s*(MEMORY|SPFILE|BOTH))?$", up)
        if not m:
            return "ORA-00922: option manquante ou invalide"
        param, val, scope = m.group(1).lower(), m.group(3), m.group(4) or "BOTH"
        if param not in SETTABLE:
            return "ORA-02065: option non valide pour ALTER SYSTEM (paramètre non pris en charge dans cet atelier)"
        if not re.fullmatch(r"\d+[KMG]?", val):
            return "ORA-02017: valeur entière requise"
        if scope in ("MEMORY", "BOTH"):
            st.data["params"][param] = val
        if scope in ("SPFILE", "BOTH"):
            st.data["spfile_params"][param] = val
        log(st, f"ALTER SYSTEM SET {param} = {val} SCOPE = {scope}")
        return "Système modifié."

    m = re.match(r"^SHUTDOWN(?: (NORMAL|TRANSACTIONAL|IMMEDIATE|ABORT))?$", up)
    if m:
        if st.stage == 0:
            return "ORA-01034: ORACLE n'est pas disponible"
        return do_shutdown(st, m.group(1) or "NORMAL")

    if up.startswith(("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "GRANT")):
        return "Cet atelier ne couvre que le démarrage/arrêt : commande désactivée."
    return f'SP2-0734: commande inconnue commençant par "{cmd.split()[0]} ..." - fin de ligne ignorée.'