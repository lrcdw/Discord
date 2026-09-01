from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
from prospective_total_corners_v31 import attempted_cells, due_tasks, load_json, parse_utc  # noqa: E402
from prospective_total_corners_v313 import DEFAULT_PROTOCOL, collect, load_protocol  # noqa: E402


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(cohort_path: Path, protocol_path: Path, output_root: Path, horizon_seconds: int) -> int:
    if not 0 < horizon_seconds <= 19_800:
        raise ValueError("horizon_seconds must be between 1 and 19800")
    protocol = load_protocol(protocol_path)
    cohort = load_json(cohort_path)
    secret_name = protocol["collection"]["api_key_environment_variable"]
    if not os.environ.get(secret_name, "").strip():
        print("FAIL_CLOSED_MISSING_CREDENTIAL")
        return 2

    started = datetime.now(timezone.utc)
    deadline = started + timedelta(seconds=horizon_seconds)
    target_lead = protocol["scheduler"]["target_seconds_before_prediction_as_of"]
    predictions: set[datetime] = set()
    for fixture in cohort["fixtures"]:
        kickoff = parse_utc(fixture["kickoff_utc"])
        for horizon in protocol["collection"]["horizons_minutes"]:
            predictions.add(kickoff - timedelta(minutes=horizon))

    attempted = attempted_cells(output_root, cohort["cohort_id"])
    eligible_targets = [
        prediction
        for prediction in sorted(predictions)
        if started <= prediction <= deadline + timedelta(seconds=target_lead)
    ]
    if not eligible_targets:
        print(f"NO_TARGETS_IN_HORIZON start={iso(started)} deadline={iso(deadline)} attempted={len(attempted)}")
        return 0

    collected_runs = 0
    for prediction_as_of in eligible_targets:
        attempted = attempted_cells(output_root, cohort["cohort_id"])
        if not any(
            task["prediction_as_of_utc"] == iso(prediction_as_of)
            for task in due_tasks(cohort, prediction_as_of - timedelta(seconds=target_lead), protocol, attempted)
        ):
            continue
        target = prediction_as_of - timedelta(seconds=target_lead)
        while True:
            now = datetime.now(timezone.utc)
            remaining = (target - now).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(30, remaining))
        now = datetime.now(timezone.utc)
        if now > prediction_as_of:
            print(f"MISSED prediction_as_of={iso(prediction_as_of)} rule=UNCOVERED_NO_RETRY")
            continue
        path = collect(cohort_path, protocol_path, output_root, iso(now))
        manifest = load_json(path)
        print(
            f"ATTEMPT run_id={manifest['run_id']} prediction_as_of={iso(prediction_as_of)} "
            f"due={manifest['due_tasks']} status={manifest['status']}"
        )
        collected_runs += 1
        if manifest["status"] not in {"NO_DUE_TASKS", "COLLECTION_COMPLETE"}:
            return 2
    print(f"CLOUD_WINDOW_COMPLETE collected_runs={collected_runs} attempted_cells={len(attempted_cells(output_root, cohort['cohort_id']))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--output-root", type=Path, default=ROOT / "raw" / "prospective_v3_1_3" / "collections")
    parser.add_argument("--horizon-seconds", type=int, default=1800)
    args = parser.parse_args()
    return run(args.cohort, args.protocol, args.output_root, args.horizon_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
