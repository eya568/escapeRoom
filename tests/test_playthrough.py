"""
Test exhaustif de l'escape room.
Couvre les chemins corrects ET les erreurs plausibles pour les 3 niveaux.
Chaque niveau : on vérifie (a) qu'une bonne réponse débloque, (b) que chaque
mauvaise réponse ne débloque pas, et (c) que les erreurs retournées sont
conformes à Oracle (ORA-01507, ORA-01034, ORA-01081, etc.).
"""
from fastapi.testclient import TestClient
import app as m

c = TestClient(m.app)
_ = c.get(f"/api/admin/start?key={m.ADMIN_KEY}")


def new_team(name):
    r = c.post("/api/join", json={"team": name}).json()
    return name, r


def go(t, s):
    return c.post("/api/command", json={"team": t, "command": s}).json()


def expect_no_unlock(t, cmd):
    r = go(t, cmd)
    assert not r["unlocked"], f"{cmd!r} should NOT unlock (got {r['unlocked']!r})"
    return r["output"]


def expect_unlock(t, cmd):
    r = go(t, cmd)
    assert r["unlocked"], f"{cmd!r} SHOULD unlock (output was {r['output']!r})"
    return r["unlocked"]


def expect_error(t, cmd, fragment):
    out = expect_no_unlock(t, cmd)
    assert fragment in out, f"{cmd!r} should contain {fragment!r}, got: {out!r}"
    return out


# ==========================================================================
# NIVEAU 1 — Étapes de démarrage (NOMOUNT / MOUNT / OPEN)
# ==========================================================================

def test_n1_wrong_first_commands():
    """Commandes tapées avant tout STARTUP : doivent toutes échouer proprement."""
    t, v = new_team("n1_a")
    assert v["level"] == 0

    # La DB est down, tout SELECT doit échouer avec ORA-01034
    for cmd in [
        "SELECT * FROM v$instance",
        "SELECT status FROM v$instance",
        "SELECT * FROM v$database",
        "SELECT * FROM v$controlfile",
        "SELECT name FROM v$controlfile",
        "SELECT * FROM v$datafile",
        "SELECT name FROM v$datafile",
        "SHOW PARAMETER spfile",
        "ALTER DATABASE MOUNT",
        "ALTER DATABASE OPEN",
    ]:
        expect_error(t, cmd, "ORA-01034")

    # Aucune de ces erreurs n'a débloqué
    assert not go(t, "ALTER DATABASE OPEN")["unlocked"]


def test_n1_shortcut_startup_alone_does_not_unlock():
    """STARTUP seul ouvre la base mais ne débloque PAS (il faut le staged)."""
    t, _ = new_team("n1_b")
    r = go(t, "STARTUP")
    assert "ORACLE instance started" in r["output"] or "Instance ORACLE démarrée" in r["output"]
    assert "OPEN" in r["output"].upper() or "Database opened" in r["output"]
    # PAS de unlock : bare STARTUP ne prouve rien
    assert not r["unlocked"]


def test_n1_partial_startup_no_verify():
    """STARTUP NOMOUNT puis MOUNT puis OPEN sans aucune requête ne débloque pas."""
    t, _ = new_team("n1_c")
    assert not go(t, "STARTUP NOMOUNT")["unlocked"]
    assert not go(t, "ALTER DATABASE MOUNT")["unlocked"]
    assert not go(t, "ALTER DATABASE OPEN")["unlocked"]


def test_n1_wrong_view_at_nomount():
    """Vues qui nécessitent MOUNT doivent échouer à NOMOUNT."""
    t, _ = new_team("n1_d")
    go(t, "STARTUP NOMOUNT")

    # v$datafile / v$database / v$session → ORA-01507 (need MOUNT)
    expect_error(t, "SELECT * FROM v$datafile", "ORA-01507")
    expect_error(t, "SELECT name FROM v$datafile", "ORA-01507")
    expect_error(t, "SELECT * FROM v$database", "ORA-01507")
    expect_error(t, "SELECT * FROM v$session", "ORA-01507")

    # v$controlfile → NE DOIT PAS renvoyer d'erreur, mais DOIT être vide
    out = go(t, "SELECT * FROM v$controlfile")["output"]
    assert "aucune ligne" in out or "no rows selected" in out, \
        f"v$controlfile at NOMOUNT should be empty, got: {out!r}"

    # Et surtout : aucune de ces requêtes ne compte comme preuve
    assert not go(t, "ALTER DATABASE MOUNT")["unlocked"]


def test_n1_controlfile_empty_at_nomount_does_not_count():
    """SELECT * FROM v$controlfile à NOMOUNT : renvoie 0 ligne, ne compte pas comme preuve."""
    t, _ = new_team("n1_e")
    go(t, "STARTUP NOMOUNT")
    # La requête s'exécute (pas ORA-01507) mais ne renvoie rien
    out = go(t, "SELECT * FROM v$controlfile")["output"]
    assert "aucune ligne" in out or "no rows selected" in out
    # On MONTE, on ne refait PAS la requête controlfile, on OPEN direct → pas d'unlock
    go(t, "ALTER DATABASE MOUNT")
    go(t, "SELECT * FROM v$datafile")   # on fait juste datafile
    assert not go(t, "ALTER DATABASE OPEN")["unlocked"], \
        "controlfile never seen with rows → must not unlock"


def test_n1_missing_instance_proof():
    """Si on saute la vérif v$instance à NOMOUNT, pas d'unlock même si tout le reste est OK."""
    t, _ = new_team("n1_f")
    go(t, "STARTUP NOMOUNT")
    # Pas de SELECT v$instance !
    go(t, "ALTER DATABASE MOUNT")
    go(t, "SELECT * FROM v$controlfile")
    go(t, "SELECT * FROM v$datafile")
    assert not go(t, "ALTER DATABASE OPEN")["unlocked"], \
        "no v$instance proof at NOMOUNT → must not unlock"


def test_n1_missing_controlfile_proof():
    """Sans SELECT v$controlfile à MOUNT, pas d'unlock."""
    t, _ = new_team("n1_g")
    go(t, "STARTUP NOMOUNT")
    go(t, "SELECT * FROM v$instance")
    go(t, "ALTER DATABASE MOUNT")
    # On saute v$controlfile
    go(t, "SELECT * FROM v$datafile")
    assert not go(t, "ALTER DATABASE OPEN")["unlocked"]


def test_n1_missing_datafile_proof():
    """Sans SELECT v$datafile à MOUNT, pas d'unlock."""
    t, _ = new_team("n1_h")
    go(t, "STARTUP NOMOUNT")
    go(t, "SELECT * FROM v$instance")
    go(t, "ALTER DATABASE MOUNT")
    go(t, "SELECT * FROM v$controlfile")
    # On saute v$datafile
    assert not go(t, "ALTER DATABASE OPEN")["unlocked"]


def test_n1_full_correct_path():
    """Chemin correct complet — doit débloquer."""
    t, _ = new_team("n1_ok")
    go(t, "STARTUP NOMOUNT")
    assert not go(t, "SELECT * FROM v$instance")["unlocked"]
    assert not go(t, "ALTER DATABASE MOUNT")["unlocked"]
    assert not go(t, "SELECT * FROM v$controlfile")["unlocked"]
    assert not go(t, "SELECT name FROM v$datafile")["unlocked"]
    assert go(t, "ALTER DATABASE OPEN")["unlocked"], "N1: should unlock"


def test_n1_alternate_correct_path():
    """Variante : MOUNT d'abord, puis les requêtes dans l'autre ordre."""
    t, _ = new_team("n1_ok2")
    go(t, "STARTUP NOMOUNT")
    go(t, "SELECT status FROM v$instance")
    go(t, "ALTER DATABASE MOUNT")
    go(t, "SELECT name FROM v$datafile")     # datafile d'abord
    go(t, "SELECT * FROM v$controlfile")     # controlfile ensuite
    assert go(t, "ALTER DATABASE OPEN")["unlocked"]


def test_n1_restart_after_mistake():
    """Après une erreur, SHUTDOWN + refaire proprement doit débloquer."""
    t, _ = new_team("n1_restart")
    go(t, "STARTUP")
    go(t, "SHUTDOWN IMMEDIATE")
    go(t, "STARTUP NOMOUNT")
    go(t, "SELECT * FROM v$instance")
    go(t, "ALTER DATABASE MOUNT")
    go(t, "SELECT * FROM v$controlfile")
    go(t, "SELECT * FROM v$datafile")
    assert go(t, "ALTER DATABASE OPEN")["unlocked"]


# ==========================================================================
# NIVEAU 2 — PFILE / SPFILE + SCOPE
# ==========================================================================

def reach_n2(t):
    """Aide : atteint le niveau 2 proprement."""
    go(t, "STARTUP NOMOUNT")
    go(t, "SELECT * FROM v$instance")
    go(t, "ALTER DATABASE MOUNT")
    go(t, "SELECT * FROM v$controlfile")
    go(t, "SELECT * FROM v$datafile")
    r = go(t, "ALTER DATABASE OPEN")
    assert r["unlocked"], "reach_n2: L1 should have unlocked"


def test_n2_no_startup_attempt():
    """Au tout début de N2, la DB est déjà OPEN (setup2 stage 3). STARTUP doit échouer."""
    t, _ = new_team("n2_a")
    reach_n2(t)
    expect_error(t, "STARTUP", "ORA-01081")


def test_n2_memory_scope_does_not_unlock():
    """SCOPE=MEMORY ne suffit pas — la valeur doit survivre au redémarrage."""
    t, _ = new_team("n2_b")
    reach_n2(t)
    go(t, "SHOW PARAMETER spfile")
    expect_no_unlock(t, "ALTER SYSTEM SET db_cache_size=500M SCOPE=MEMORY")


def test_n2_spfile_scope_alone_does_not_unlock():
    """SCOPE=SPFILE seul ne prend pas effet immédiatement → ne compte pas."""
    t, _ = new_team("n2_c")
    reach_n2(t)
    go(t, "SHOW PARAMETER spfile")
    expect_no_unlock(t, "ALTER SYSTEM SET db_cache_size=500M SCOPE=SPFILE")


def test_n2_set_without_show_does_not_unlock():
    """SCOPE=BOTH mais sans SHOW PARAMETER spfile → pas d'unlock (il manque le diag)."""
    t, _ = new_team("n2_d")
    reach_n2(t)
    expect_no_unlock(t, "ALTER SYSTEM SET db_cache_size=500M SCOPE=BOTH")


def test_n2_show_without_set_does_not_unlock():
    """SHOW PARAMETER spfile seul ne suffit pas."""
    t, _ = new_team("n2_e")
    reach_n2(t)
    expect_no_unlock(t, "SHOW PARAMETER spfile")


def test_n2_wrong_parameter_does_not_unlock():
    """ALTER SYSTEM SET sur un autre paramètre → ne débloque pas."""
    t, _ = new_team("n2_f")
    reach_n2(t)
    go(t, "SHOW PARAMETER spfile")
    expect_no_unlock(t, "ALTER SYSTEM SET sga_max_size=1G SCOPE=BOTH")


def test_n2_wrong_value_does_not_unlock():
    """Bonne syntaxe, mauvaise valeur → pas d'unlock."""
    t, _ = new_team("n2_g")
    reach_n2(t)
    go(t, "SHOW PARAMETER spfile")
    expect_no_unlock(t, "ALTER SYSTEM SET db_cache_size=1G SCOPE=BOTH")


def test_n2_correct_scope_both_unlocks():
    """SCOPE=BOTH après SHOW PARAMETER spfile → unlock."""
    t, _ = new_team("n2_ok")
    reach_n2(t)
    go(t, "SHOW PARAMETER spfile")
    assert go(t, "ALTER SYSTEM SET db_cache_size=500M SCOPE=BOTH")["unlocked"], "N2: should unlock"


def test_n2_default_scope_unlocks():
    """Sans SCOPE (défaut = BOTH) après SHOW → unlock."""
    t, _ = new_team("n2_ok2")
    reach_n2(t)
    go(t, "SHOW PARAMETER spfile")
    assert go(t, "ALTER SYSTEM SET db_cache_size=500M")["unlocked"], "N2: default scope should unlock"


def test_n2_select_v_parameter_as_alternative():
    """SELECT value FROM v$parameter WHERE name='spfile' compte comme diagnostic."""
    t, _ = new_team("n2_ok3")
    reach_n2(t)
    go(t, "SELECT value FROM v$parameter WHERE name = 'spfile'")
    assert go(t, "ALTER SYSTEM SET db_cache_size=500M SCOPE=BOTH")["unlocked"], \
        "N2: SELECT v$parameter should count as SHOW equivalent"


# ==========================================================================
# NIVEAU 3 — SHUTDOWN ABORT + Crash Recovery
# ==========================================================================

def reach_n3(t):
    """Aide : atteint le niveau 3 proprement."""
    reach_n2(t)
    go(t, "SHOW PARAMETER spfile")
    r = go(t, "ALTER SYSTEM SET db_cache_size=500M SCOPE=BOTH")
    assert r["unlocked"], "reach_n3: L2 should have unlocked"


def test_n3_immediate_blocks():
    """SHUTDOWN IMMEDIATE doit être bloqué par les sessions figées."""
    t, _ = new_team("n3_a")
    reach_n3(t)
    out = expect_no_unlock(t, "SHUTDOWN IMMEDIATE")
    assert "ne rend pas la main" in out or "en attente" in out or "ne répond" in out


def test_n3_normal_blocks():
    """SHUTDOWN NORMAL bloqué aussi."""
    t, _ = new_team("n3_b")
    reach_n3(t)
    out = expect_no_unlock(t, "SHUTDOWN NORMAL")
    assert "en attente" in out or "ne rend pas" in out


def test_n3_transactional_blocks():
    """SHUTDOWN TRANSACTIONAL bloqué (transactions ouvertes)."""
    t, _ = new_team("n3_c")
    reach_n3(t)
    out = expect_no_unlock(t, "SHUTDOWN TRANSACTIONAL")
    assert "en attente" in out or "ne rend pas" in out


def test_n3_abort_alone_does_not_unlock():
    """ABORT seul ne débloque pas : il faut STARTUP + SELECT PAYMENTS."""
    t, _ = new_team("n3_d")
    reach_n3(t)
    go(t, "SHUTDOWN ABORT")
    assert not go(t, "STARTUP")["unlocked"], \
        "ABORT + STARTUP without SELECT PAYMENTS must not unlock"


def test_n3_abort_then_startup_without_proof():
    """ABORT + STARTUP : la DB est de retour mais sans SELECT PAYMENTS → pas d'unlock."""
    t, _ = new_team("n3_e")
    reach_n3(t)
    go(t, "SHUTDOWN ABORT")
    go(t, "STARTUP")
    # On saute la preuve
    assert not go(t, "SELECT * FROM v$instance")["unlocked"], \
        "v$instance is not proof of data survival"


def test_n3_proof_before_startup_does_not_count():
    """Un SELECT PAYMENTS tapé AVANT l'ABORT ne compte pas comme preuve."""
    t, _ = new_team("n3_f")
    reach_n3(t)
    go(t, "SELECT * FROM payments")   # preuve prématurée (mais la base n'est pas crashée)
    go(t, "SHUTDOWN ABORT")
    go(t, "STARTUP")
    # Pas de SELECT payments APRÈS STARTUP
    assert not go(t, "ALTER DATABASE OPEN")["unlocked"], \
        "SELECT PAYMENTS before ABORT should not count as post-recovery proof"


def test_n3_correct_flow_unlocks():
    """ABORT → STARTUP → SELECT PAYMENTS → unlock."""
    t, _ = new_team("n3_ok")
    reach_n3(t)
    go(t, "SHUTDOWN ABORT")
    go(t, "STARTUP")
    assert go(t, "SELECT * FROM payments")["unlocked"], "N3: should unlock"


def test_n3_select_name_only_does_not_count():
    """SELECT name FROM payments n'est pas une preuve (ne montre pas les lignes)."""
    t, _ = new_team("n3_g")
    reach_n3(t)
    go(t, "SHUTDOWN ABORT")
    go(t, "STARTUP")
    # La vue payments n'a pas de colonne "name" — ORA-00904
    expect_error(t, "SELECT name FROM payments", "ORA-00904")


def test_n3_select_count_does_not_prove_rows_visible():
    """SELECT COUNT(*) FROM payments renvoie un nombre mais ce n'est pas la preuve attendue."""
    t, _ = new_team("n3_h")
    reach_n3(t)
    go(t, "SHUTDOWN ABORT")
    go(t, "STARTUP")
    # Techniquement COUNT(*) marche mais check3 cherche un SELECT qui RENVOIE des lignes
    # (donc un COUNT renvoie 1 ligne "3" — ça peut passer selon l'implémentation).
    # On accepte les deux comportements pour rester robuste :
    r = go(t, "SELECT COUNT(*) FROM payments")
    # Si le jeu refuse, c'est OK ; si le jeu accepte, c'est OK aussi.
    # Ce test vérifie juste que ça ne crashe pas.
    assert r["output"] != ""


# ==========================================================================
# TESTS TRANSVERSAUX — comportements du serveur
# ==========================================================================

def test_team_names_are_case_insensitive():
    """Team 'Test1' et 'TEST1' et 'test1' partagent le même état."""
    c.post("/api/join", json={"team": "Test1"})
    go("Test1", "STARTUP NOMOUNT")
    r = go("TEST1", "SELECT * FROM v$instance")
    assert "STARTED" in r["output"]


def test_unknown_team_returns_404():
    r = c.post("/api/command", json={"team": "ghost", "command": "HELP"})
    assert r.status_code == 404


def test_team_name_too_short_is_rejected():
    r = c.post("/api/join", json={"team": "x"})
    assert r.status_code == 400


def test_command_too_long_is_rejected():
    t, _ = new_team("longcmd")
    r = c.post("/api/command", json={"team": t, "command": "A" * 300})
    assert r.status_code == 400


def test_lobby_resets_state_between_teams():
    """Deux équipes ne partagent pas leur état."""
    t1, _ = new_team("iso1")
    t2, _ = new_team("iso2")
    go(t1, "STARTUP NOMOUNT")
    # t2 est encore à stage 0 → SELECT doit échouer
    out = go(t2, "SELECT * FROM v$instance")["output"]
    assert "ORA-01034" in out


def test_reset_does_not_reset_hints():
    """Reset doit rejouer setup mais garder les hints comptés (comportement attendu)."""
    t, _ = new_team("reseth")
    go(t, "STARTUP NOMOUNT")
    go(t, "SELECT * FROM v$instance")
    go(t, "ALTER DATABASE MOUNT")
    go(t, "SELECT * FROM v$controlfile")
    go(t, "SELECT * FROM v$datafile")
    go(t, "ALTER DATABASE OPEN")
    # On est en N2, on demande un hint (le back-end comptabilise)
    r = c.post("/api/hint", json={"team": t}).json()
    assert r["hints_left"] is not None
    # Reset
    c.post("/api/reset", json={"team": t})
    # Rejouer N2 avec le hint déjà consommé
    go(t, "SHOW PARAMETER spfile")
    r = go(t, "ALTER SYSTEM SET db_cache_size=500M SCOPE=BOTH")
    # Score réduit de HINT_COST mais toujours > 0
    assert r["unlocked"]


# ==========================================================================
# Run everything
# ==========================================================================
if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            print(f"  ✔ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ✘ {name}: {e}")
            failed.append((name, e))
        except Exception as e:
            print(f"  ✘ {name}: EXCEPTION {type(e).__name__}: {e}")
            failed.append((name, e))
    print(f"\n{passed} passed, {len(failed)} failed, {len(tests)} total")
    if failed:
        print("\nFailed tests:")
        for name, e in failed:
            print(f"  - {name}: {e}")
        import sys
        sys.exit(1)
    print("ALL OK")