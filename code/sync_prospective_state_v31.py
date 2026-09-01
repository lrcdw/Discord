from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "db" / "corners_lab.sqlite3"
DEFAULT_ACTIVE = ROOT / "registries" / "prospective_active_cohort_v31.json"
DEFAULT_COLLECTIONS = ROOT / "raw" / "prospective_v3_1" / "collections"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sync(db_path: Path = DEFAULT_DB, active_path: Path = DEFAULT_ACTIVE, collections_root: Path = DEFAULT_COLLECTIONS) -> dict[str, int | str]:
    active = load(active_path)
    cohort_path = ROOT / active["cohort_path"]
    if sha256(cohort_path) != active["cohort_sha256"]:
        raise ValueError("active cohort hash mismatch")
    cohort = load(cohort_path)
    if cohort["cohort_id"] != active["active_cohort_id"]:
        raise ValueError("active cohort id mismatch")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
        connection.execute(
            """INSERT OR REPLACE INTO odds_prospective_v31_cohorts
               (cohort_id,protocol_version,enrolled_at_utc,source_url,source_sha256,eligible_fixtures,target_odds_observed_during_enrollment,immutable)
               VALUES (?,?,?,?,?,?,0,1)""",
            (cohort["cohort_id"], cohort["protocol_version"], cohort["enrolled_at_utc"], cohort["source"]["url"], cohort["source"]["sha256"], cohort["eligible_fixture_total"]),
        )
        connection.execute("DELETE FROM odds_prospective_v31_fixtures WHERE cohort_id=?", (cohort["cohort_id"],))
        connection.executemany(
            """INSERT INTO odds_prospective_v31_fixtures
               (cohort_id,fixture_id,competition,sport_key,kickoff_utc,home_team,away_team)
               VALUES (?,?,?,?,?,?,?)""",
            [(cohort["cohort_id"], row["fixture_id"], row["competition"], row["sport_key"], row["kickoff_utc"], row["home_team"], row["away_team"]) for row in cohort["fixtures"]],
        )
        run_count = 0
        for manifest_path in collections_root.rglob("execution_manifest.json") if collections_root.exists() else []:
            manifest = load(manifest_path)
            if manifest.get("cohort_id") != cohort["cohort_id"]:
                continue
            connection.execute(
                """INSERT OR REPLACE INTO odds_prospective_v31_collection_runs
                   (run_id,cohort_id,execution_timestamp_utc,due_tasks,odds_requests,status,paid_access_used,manifest_sha256)
                   VALUES (?,?,?,?,?,?,0,?)""",
                (manifest["run_id"], cohort["cohort_id"], manifest["execution_timestamp_utc"], manifest["due_tasks"], len(manifest["odds_requests"]), manifest["status"], sha256(manifest_path)),
            )
            run_count += 1
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        counts = {
            "sqlite_integrity": integrity,
            "foreign_key_violations": foreign_keys,
            "cohorts": connection.execute("SELECT COUNT(*) FROM odds_prospective_v31_cohorts").fetchone()[0],
            "fixtures": connection.execute("SELECT COUNT(*) FROM odds_prospective_v31_fixtures").fetchone()[0],
            "collection_runs": connection.execute("SELECT COUNT(*) FROM odds_prospective_v31_collection_runs").fetchone()[0],
            "collection_runs_synced_now": run_count,
        }
        if integrity != "ok" or foreign_keys:
            raise RuntimeError(f"database integrity failure: {counts}")
        return counts
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--active", type=Path, default=DEFAULT_ACTIVE)
    parser.add_argument("--collections", type=Path, default=DEFAULT_COLLECTIONS)
    args = parser.parse_args()
    result = sync(args.db, args.active, args.collections)
    for key, value in result.items():
        print(f"{key.upper()}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
