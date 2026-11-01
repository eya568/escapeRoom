
# Oracle Escape Room — Solutions & Answer Key

> **Instructor note:** This file contains the complete solutions for all three incidents. Do not read it before attempting the challenges.

---

# Level 1 — Startup Steps (NOMOUNT → MOUNT → OPEN)

## Story

The Oracle database is down and returns `ORA-01034`. Bring it back online by following the three startup stages in the correct order.

Each stage requires a SQL query proving that the corresponding component is accessible.

## Unlock requirements

| Stage | Required proof | Expected result |
|---|---|---|
| NOMOUNT | Query `v$instance` | `STATUS = STARTED` |
| MOUNT | Query `v$controlfile` | Rows returned |
| MOUNT | Query `v$datafile` | Rows returned |
| OPEN | Execute `ALTER DATABASE OPEN` | Database opens successfully |

All three SELECT proofs are mandatory. The database reaching OPEN alone is not sufficient to unlock the level.

## Correct solution

```sql
STARTUP NOMOUNT;

SELECT * FROM v$instance;
-- STATUS = STARTED

ALTER DATABASE MOUNT;

SELECT * FROM v$controlfile;
-- Controlfile information returned

SELECT * FROM v$datafile;
-- Datafile information returned

ALTER DATABASE OPEN;
```

The `v$controlfile` and `v$datafile` queries can be executed in either order after MOUNT.

## Accepted alternative queries

The following queries also count as valid proofs:

| Stage | Accepted query |
|---|---|
| NOMOUNT | `SELECT status FROM v$instance` |
| MOUNT | `SELECT * FROM v$controlfile` |
| MOUNT | `SELECT name FROM v$datafile` |

A query only counts if it returns rows at the correct database stage.

## Wrong turns

| You type | You get | Why it doesn't unlock |
|---|---|---|
| Any `SELECT` before `STARTUP` | `ORA-01034` | The database instance is down |
| `STARTUP` (bare) | Database opens, no unlock | Skips the required staged proof |
| `STARTUP NOMOUNT` → `MOUNT` → `OPEN` without SELECTs | Database opens, no unlock | Required evidence is missing |
| `v$datafile` at NOMOUNT | `ORA-01507` | Datafiles are not accessible yet |
| `v$database` at NOMOUNT | `ORA-01507` | Database is not mounted |
| `v$session` at NOMOUNT | `ORA-01507` | Database is not open |
| `v$controlfile` at NOMOUNT | 0 rows | Controlfile has not been mounted |
| Missing any of the three SELECT proofs | No unlock | Evidence is incomplete |

## Recovery after a mistake

If the player skips a stage or starts the database incorrectly, the following sequence restores the required state:

```sql
SHUTDOWN IMMEDIATE;

STARTUP NOMOUNT;

SELECT * FROM v$instance;

ALTER DATABASE MOUNT;

SELECT * FROM v$controlfile;

SELECT * FROM v$datafile;

ALTER DATABASE OPEN;
```

If the database is already open, `SHUTDOWN IMMEDIATE` safely shuts it down before restarting it.

## Concept explanation

Oracle startup consists of three distinct stages:

### 1. NOMOUNT

- Oracle reads the initialization parameter file (SPFILE or PFILE).
- The System Global Area (SGA) is allocated.
- Background processes are started.
- The instance becomes available.

At this stage, the instance exists, but the database has not been mounted.

### 2. MOUNT

- Oracle reads the control files.
- The database structure becomes known to the instance.
- Datafiles and redo log files can be identified.

The database is mounted but not yet open.

### 3. OPEN

- Oracle opens the datafiles.
- Online redo log files are opened.
- Database consistency is checked.
- The database becomes available for normal operations.

**Important distinction:**

- `V$` views provide information about the instance and database state. Their availability depends on the current startup stage.
- `DBA_` views generally rely on the data dictionary and require the database to be OPEN.

---

# Level 2 — Parameter Configuration

## Story

The database configuration needs to be corrected. Diagnose the current configuration and apply the required memory parameter change.

## Unlock requirements

Two conditions must be satisfied:

1. Diagnose the current SPFILE configuration.
2. Update `db_cache_size` to `500M` using `SCOPE=BOTH`.

## Correct solution

```sql
SHOW PARAMETER spfile;

ALTER SYSTEM SET db_cache_size = 500M SCOPE=BOTH;
```

## Explanation

### Step 1 — Diagnose the SPFILE

```sql
SHOW PARAMETER spfile;
```

This command displays the initialization parameter related to the server parameter file.

It helps determine whether the instance is using an SPFILE.

### Step 2 — Update the parameter

```sql
ALTER SYSTEM SET db_cache_size = 500M SCOPE=BOTH;
```

This changes the database buffer cache size to 500 MB.

`SCOPE=BOTH` means:

- **MEMORY:** The parameter is changed immediately in the running instance.
- **SPFILE:** The parameter is saved persistently for future restarts.

## Wrong turns

| You type | Why it doesn't unlock |
|---|---|
| Only `SHOW PARAMETER spfile` | Diagnosis is complete, but configuration is not updated |
| `ALTER SYSTEM SET db_cache_size = 500M` | Missing the required scope |
| `ALTER SYSTEM SET db_cache_size = 500M SCOPE=MEMORY` | Change is not persistent after restart |
| `ALTER SYSTEM SET db_cache_size = 500M SCOPE=SPFILE` | Change is not applied immediately |
| Change a different parameter | Wrong parameter |
| Set a different value | Incorrect configuration |

## Concept explanation

Oracle initialization parameters control how the database instance operates.

There are two main parameter file types:

- **PFILE:** A text-based initialization file, traditionally named `init.ora`.
- **SPFILE:** A server-managed binary initialization file that supports persistent parameter changes through SQL.

The `SCOPE` option determines where a parameter change is applied:

| Scope | Memory | SPFILE | Effect |
|---|---|---|---|
| MEMORY | Yes | No | Current instance only |
| SPFILE | No | Yes | Next restart |
| BOTH | Yes | Yes | Immediate and persistent |

**Note:** `SCOPE=BOTH` requires the instance to be using an SPFILE. Some parameters may also have restrictions on which scopes are supported.

---

# Level 3 — Emergency Recovery

## Story

An incident has affected the database. The recovery procedure requires an immediate shutdown, a restart, and verification that the payment records are accessible.

## Unlock requirements

The player must:

1. Execute `SHUTDOWN ABORT`.
2. Restart the database using `STARTUP`.
3. Retrieve all records from the `PAYMENTS` table using `SELECT *`.

The final query must return rows.

## Correct solution

```sql
SHUTDOWN ABORT;

STARTUP;

SELECT * FROM payments;
```

## Explanation

### Step 1 — Emergency shutdown

```sql
SHUTDOWN ABORT;
```

This immediately terminates the Oracle instance without performing a normal shutdown sequence.

Unlike `SHUTDOWN IMMEDIATE`, it does not wait for active transactions to finish or perform a clean database shutdown.

Oracle performs instance recovery during the next startup if necessary.

### Step 2 — Restart

```sql
STARTUP;
```

This starts the instance, mounts the database, and opens it.

The database becomes available for normal operations.

### Step 3 — Verify payment records

```sql
SELECT * FROM payments;
```

This retrieves every column and every row from the `PAYMENTS` table.

The query must return rows to satisfy the final proof requirement.

## Wrong turns

| You type | Why it doesn't unlock |
|---|---|
| `SHUTDOWN IMMEDIATE` | The required shutdown mode is ABORT |
| `SHUTDOWN NORMAL` | Wrong shutdown mode |
| `SHUTDOWN ABORT` without restarting | Database remains unavailable |
| `SHUTDOWN ABORT` → `STARTUP` without SELECT | Recovery verification is missing |
| `SELECT * FROM payments` before restart | Does not follow the required recovery sequence |
| `SELECT amount FROM payments` | Does not satisfy the required `SELECT *` proof |
| `SELECT * FROM another_table` | Wrong table |
| `SELECT * FROM payments` returning no rows | Required evidence is missing |

## Concept explanation

Oracle provides several shutdown modes:

| Shutdown mode | Behaviour |
|---|---|
| NORMAL | Waits for users to disconnect |
| TRANSACTIONAL | Waits for active transactions to finish |
| IMMEDIATE | Terminates active SQL statements and rolls back uncommitted transactions |
| ABORT | Immediately terminates the instance |

`SHUTDOWN ABORT` is generally reserved for emergency situations because it does not perform a clean shutdown.

During the next startup, Oracle uses instance recovery to restore database consistency when required.

The final SELECT verifies that the payment records are accessible after recovery.

---

# Cross-Cutting Behaviours

These rules apply to all three levels.

## Team names

Team names are case-insensitive.

| Input | Result |
|---|---|
| `Test1` | Same team |
| `TEST1` | Same team |
| `test1` | Same team |

All three names identify the same team.

## Validation and errors

| Condition | Expected HTTP response |
|---|---|
| Unknown team | `404 Not Found` |
| Team name too short | `400 Bad Request` |
| Command exceeds 200 characters | `400 Bad Request` |

## Team state isolation

Each team has its own independent game state.

For example:

- Team A executes `STARTUP NOMOUNT`.
- Team B remains unaffected.
- Team B must perform its own startup sequence.

One team's progress must never modify another team's state.

## Hint persistence

Hints reduce the team's score.

Calling:

```http
POST /api/reset
```

resets the level state but does not restore points already deducted for hints.

The reduced score therefore survives a reset.

## Admin countdown

The administrator can start the countdown using:

```http
GET /api/admin/start?key=<ADMIN_KEY>
```

The administrator key is printed when the application launches.

Only a request containing the correct key should start the countdown.

---

# Quick Reference

| Level | Required command sequence | Final proof |
|---|---|---|
| 1 | `STARTUP NOMOUNT` → `SELECT v$instance` → `ALTER DATABASE MOUNT` → `SELECT v$controlfile` → `SELECT v$datafile` → `ALTER DATABASE OPEN` | All three SELECT proofs return rows at the correct stage |
| 2 | `SHOW PARAMETER spfile` → `ALTER SYSTEM SET db_cache_size=500M SCOPE=BOTH` | Correct diagnosis and parameter configuration |
| 3 | `SHUTDOWN ABORT` → `STARTUP` → `SELECT * FROM payments` | All payment records are returned |

---

# Running the Automated Tests

The project includes an automated playthrough test suite covering the correct solutions, invalid commands, wrong turns, and cross-cutting behaviours.

## 1. Install the test dependency

```bash
python -m pip install httpx
```

## 2. Run the tests

```bash
python test_playthrough.py
```

## 3. Expected result

```text
ALL OK
```

If the test suite passes, the implementation matches the documented behaviours and unlocking conditions covered by the tests.

---

# Instructor Checklist

Before presenting the escape room, verify the following:

- [ ] All three levels unlock using the documented command sequences.
- [ ] Alternative accepted queries work in Level 1.
- [ ] Queries executed at the wrong startup stage do not unlock the level.
- [ ] Incorrect commands produce the expected errors.
- [ ] Each team has an independent state.
- [ ] Team names are case-insensitive.
- [ ] Invalid team names return HTTP 400 or 404 as appropriate.
- [ ] Commands longer than 200 characters are rejected.
- [ ] Hint penalties survive `POST /api/reset`.
- [ ] The admin countdown starts with the correct key.
- [ ] The automated test suite finishes with `ALL OK`.

---

**End of Answer Key**
