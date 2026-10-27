"""3 incidents NovaPay (processeur de paiements) : démarrage, paramètres, crash recovery."""
from engine import DEFAULT_CACHE, Sess

OK_CACHE = ("524288000", "500M")


def _reset(st, stage=0):
    st.stage, st.dirty, st.recovered, st.pending, st.waits = stage, False, False, None, 0
    st.sessions, st.alert, st.events, st.lost_orders = [], [], [], 0
    st.meta.clear()
    st.data["params"]["db_cache_size"] = DEFAULT_CACHE
    st.data["spfile_params"]["db_cache_size"] = DEFAULT_CACHE
    st.orders = [{"id": i, "produit": p, "committed": True}
                 for i, p in enumerate(["ACME Ltd", "Bistro 21", "Taxi Co"], 1)]


def _ok(o):
    return not o.startswith(("ORA-", "SP2-"))


def _committed(st):
    return sum(o["committed"] for o in st.orders)


# --- Niveau 1 : NOMOUNT / MOUNT / OPEN -----------------------------------
def setup1(st):
    _reset(st)
    return ""


def check1(st, L):
    seq = []
    for c, b, o in L:
        if not _ok(o):
            continue
        if c.startswith("SHUTDOWN"):
            seq = []
        elif c.startswith(("STARTUP", "ALTER DATABASE")):
            seq.append(("ddl", c, b))
        elif c.startswith(("SELECT", "DESC")):
            seq.append(("sel", c, b, o))

    want = ["STARTUP NOMOUNT", "ALTER DATABASE MOUNT", "ALTER DATABASE OPEN"]
    di = 0
    saw_instance_proof = False
    saw_controlfile_proof = False
    saw_datafile_proof = False

    for entry in seq:
        kind = entry[0]
        if kind == "ddl":
            _, c, b = entry
            if di < len(want) and c == want[di]:
                di += 1
        elif kind == "sel":
            _, c, b, o = entry
            # A query only counts as "proof" if it actually returned rows
            no_rows = ("aucune ligne" in o) or ("no rows selected" in o)
            if no_rows:
                continue
            # Stage 1 (NOMOUNT): must prove the instance is alive via v$instance
            if di == 1 and b == 1 and "V$INSTANCE" in c:
                saw_instance_proof = True
            # Stage 2 (MOUNT): must SEE the control file paths AND the datafile paths
            elif di == 2 and b == 2:
                if "V$CONTROLFILE" in c:
                    saw_controlfile_proof = True
                if "V$DATAFILE" in c:
                    saw_datafile_proof = True

    return (st.stage == 3
            and di == 3
            and saw_instance_proof
            and saw_controlfile_proof
            and saw_datafile_proof)


# --- Niveau 2 : PFILE / SPFILE + SCOPE -----------------------------------
def setup2(st):
    _reset(st, 3)
    return ""


def check2(st, L):
    saw_show = any(
        _ok(o) and "aucune ligne" not in o
        and (("SPFILE" in c and c.startswith("SHOW PARAMETER"))
             or (c.startswith("SELECT") and "V$PARAMETER" in c and "SPFILE" in c))
        for c, _, o in L)
    saw_set = any(
        c.startswith("ALTER SYSTEM SET DB_CACHE_SIZE") and _ok(o) and "SCOPE=MEMORY" not in c.replace(" ", "")
        for c, _, o in L)
    return (saw_show and saw_set
            and st.data["params"].get("db_cache_size") in OK_CACHE
            and st.data["spfile_params"].get("db_cache_size") in OK_CACHE)


# --- Niveau 3 : SHUTDOWN ABORT + Crash Recovery --------------------------
def setup3(st):
    _reset(st, 3)
    st.sessions = [Sess(31, "PAY_APP", None, tx_order=4, hung=True),
                   Sess(47, "BATCH_SETTLE", None, tx_order=5, hung=True)]
    st.orders += [{"id": 4, "produit": "Hotel Lux", "committed": False},
                  {"id": 5, "produit": "Kiosk 9", "committed": False}]
    st.meta["unresponsive"] = True
    return ""


def check3(st, L):
    if st.stage != 3 or not st.recovered or _committed(st) < 3:
        return False
    if "SHUTDOWN:ABORT" not in st.events:
        return False
    last = max((i for i, (c, _, o) in enumerate(L)
                if _ok(o) and (c.startswith("STARTUP") or c == "ALTER DATABASE OPEN")), default=-1)
    return any(c.startswith("SELECT") and "PAYMENTS" in c and _ok(o) and "aucune ligne" not in o
               for c, _, o in L[last + 1:])


LEVELS = [
    {
        "sev": "SEV-2", "from": "NOC Datacenter",
        "title": "Le courant est revenu, pas la base",
        "concept": "Étapes de démarrage : NOMOUNT / MOUNT / OPEN",
        "story": (
            "Une coupure de courant a fait tomber pay-db-01. Le courant est rétabli, "
            "l'instance Oracle est arrêtée.\n\n"
            "Le ticket CHG-4471 impose de vérifier chaque couche séparément avant de laisser "
            "entrer les utilisateurs : l'instance est-elle saine ? Le fichier de contrôle est-il "
            "là ? Les fichiers de données sont-ils là ?\n\nEnsuite, ouvrez la base."
        ),
        "hints": [
            "STARTUP seul enchaîne tout d'un coup. Ici, on veut s'arrêter entre chaque étape "
            "pour vérifier une couche avant de passer à la suivante.",
            "Chaque étape a son état et ses vues qui répondent. Demandez-vous quelle vue dit "
            "« l'instance est saine » avant de monter la base, et à quel moment le fichier de "
            "contrôle est lu. Si vous êtes allé trop loin, il faudra repartir de zéro.",
        ],
        "debrief": (
            "(diapo p. 19-23)\n"
            "STARTUP fait trois choses en interne ; vous les avez faites une par une :\n\n"
            "  NOMOUNT : alloue la SGA et lance les processus d'arrière-plan (PMON, SMON, DBW0, "
            "LGWR, CKPT). L'instance est STARTED. On l'interroge avec v$instance.\n"
            "  MOUNT   : lit le fichier de contrôle. L'instance est MOUNTED. v$database, "
            "v$controlfile et v$datafile répondent.\n"
            "  OPEN    : ouvre les datafiles et les redo logs. La base est en READ WRITE, les "
            "utilisateurs peuvent se connecter.\n\n"
            "À retenir :\n"
            "  - STARTUP seul = NOMOUNT + MOUNT + OPEN.\n"
            "  - STARTUP NOMOUNT : recréer un fichier de contrôle.\n"
            "  - STARTUP MOUNT : renommer des datafiles, restaurer/récupérer la base.\n"
            "  - SELECT status FROM v$instance donne l'étape courante."
        ),
        "setup": setup1, "check": check1,
    },
    {
        "sev": "SEV-3", "from": "Manager d'astreinte",
        "title": "Le paramètre qui n'a pas survécu au redémarrage",
        "concept": "PFILE vs SPFILE et ALTER SYSTEM SET SCOPE",
        "story": (
            "Hier soir, un junior DBA a réglé db_cache_size à 500M. Ça fonctionnait à 3h du "
            "matin, mais ce matin l'instance affiche l'ancienne valeur (256 Mo) : le changement "
            "a été perdu au redémarrage.\n\n"
            "Déterminez comment l'instance charge ses paramètres (PFILE ou SPFILE), puis réglez "
            "db_cache_size à 500M pour que cela prenne effet MAINTENANT et soit conservé après "
            "le prochain redémarrage."
        ),
        "hints": [
            "Avant de modifier quoi que ce soit : quel type de fichier de paramètres l'instance "
            "a-t-elle utilisé au démarrage ? Une commande SHOW permet de le savoir.",
            "ALTER SYSTEM SET accepte une clause SCOPE à trois valeurs. Réfléchissez à ce que "
            "fait chacune : tout de suite, au prochain redémarrage, ou les deux ?",
        ],
        "debrief": (
            "(diapo p. 10-17)\n"
            "PFILE (init<SID>.ora) : fichier texte édité à la main, pris en compte au redémarrage.\n"
            "SPFILE (spfile<SID>.ora) : fichier binaire modifié uniquement par ALTER SYSTEM. "
            "C'est le format recommandé par Oracle.\n\n"
            "SHOW PARAMETER spfile indique ce que l'instance utilise :\n"
            "  - la valeur est un chemin -> SPFILE\n"
            "  - la valeur est vide       -> PFILE\n\n"
            "ALTER SYSTEM SET nom=valeur SCOPE = MEMORY | SPFILE | BOTH\n"
            "  MEMORY : s'applique maintenant, perdu au redémarrage (c'était le bug)\n"
            "  SPFILE : prend effet au prochain redémarrage (obligatoire pour les paramètres statiques)\n"
            "  BOTH   : s'applique maintenant ET est conservé (valeur par défaut avec un SPFILE)\n\n"
            "db_cache_size est dynamique : SCOPE=BOTH (ou pas de SCOPE) règle l'incident durablement."
        ),
        "setup": setup2, "check": check2,
    },
    {
        "sev": "SEV-1", "from": "NOC Datacenter",
        "title": "L'urgence de 4h du matin",
        "concept": "SHUTDOWN ABORT et Crash Recovery par SMON",
        "story": (
            "Il est 4h du matin. L'instance de pay-db-01 est bloquée : deux sessions applicatives "
            "sont figées avec des transactions ouvertes, et les tentatives d'arrêt normales "
            "restent suspendues au prompt. Vous devez l'arrêter maintenant.\n\n"
            "Prouvez ensuite à l'équipe du matin que les données validées ont survécu "
            "(table PAYMENTS)."
        ),
        "hints": [
            "Les trois modes d'arrêt que vous avez essayés attendent tous quelque chose de "
            "l'instance. Il en existe un quatrième qui n'attend rien.",
            "Ce mode laisse la base incohérente. Réfléchissez à ce qui se passe au démarrage "
            "suivant, puis vérifiez que les paiements validés sont toujours là.",
        ],
        "debrief": (
            "SHUTDOWN ABORT est le seul mode capable d'interrompre une instance bloquée "
            "(diapo p. 41). Il n'annule pas les transactions et n'écrit pas la SGA sur disque : "
            "il coupe tout. La base reste dans un état incohérent.\n\n"
            "Le STARTUP suivant déclenche le Crash Recovery par SMON (diapo p. 42) :\n"
            "  1. Roll forward : rejoue les transactions validées depuis les redo logs.\n"
            "  2. Roll back    : annule les transactions jamais validées.\n"
            "Les données validées survivent ; les non validées sont écartées.\n\n"
            "Les quatre modes :\n"
            "  NORMAL        attend la fin de toutes les sessions\n"
            "  TRANSACTIONAL attend la fin des transactions en cours\n"
            "  IMMEDIATE     annule les transactions et déconnecte (pas de récupération)\n"
            "  ABORT         coupe tout, Crash Recovery au prochain démarrage (dernier recours)"
        ),
        "setup": setup3, "check": check3,
    },
]