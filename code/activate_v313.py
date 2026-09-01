from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
from prospective_total_corners_v31 import canonical_bytes, http_get, load_json, parse_utc, quota_remaining, sha256_bytes  # noqa: E402
from prospective_total_corners_v313 import DEFAULT_PROTOCOL, enroll, load_protocol  # noqa: E402


DEFAULT_ACTIVE = ROOT / "registries" / "prospective_active_cohort_v313.json"
DEFAULT_COHORTS = ROOT / "raw" / "prospective_v3_1_3" / "cohorts"


def activate(protocol_path: Path, cohorts_root: Path, active_path: Path) -> Path:
    protocol = load_protocol(protocol_path)
    now = datetime.now(timezone.utc)
    authorized_until = parse_utc(protocol["activation_requirements"]["api_key_authorized_through_utc"])
    if now > authorized_until:
        raise RuntimeError("FAIL_CLOSED_API_KEY_AUTHORIZATION_EXPIRED")
    if active_path.exists():
        raise FileExistsError(f"immutable active cohort registry already exists: {active_path}")

    key_name = protocol["collection"]["api_key_environment_variable"]
    secret = os.environ.get(key_name, "").strip()
    if not secret:
        raise RuntimeError("FAIL_CLOSED_MISSING_CREDENTIAL")

    sport = next(iter(protocol["denominator"]["competition_codes"].values()))["sport_key"]
    api = protocol["collection"]["base_url"].rstrip("/")
    _, status, headers = http_get(f"{api}/sports/{sport}/events", {"apiKey": secret, "dateFormat": "iso"})
    if status != 200:
        raise RuntimeError(f"FAIL_CLOSED_QUOTA_PREFLIGHT_HTTP_{status}")
    remaining = quota_remaining(headers)
    if remaining is None:
        raise RuntimeError("FAIL_CLOSED_MISSING_QUOTA_HEADER")

    cohort_dir = enroll(protocol_path, cohorts_root)
    cohort_path = cohort_dir / "cohort.json"
    manifest_path = cohort_dir / "manifest.json"
    cohort = load_json(cohort_path)
    projected_cells = cohort["eligible_fixture_total"] * len(protocol["collection"]["horizons_minutes"])
    reserve = protocol["no_cost_control"]["minimum_quota_reserve"]
    if remaining - projected_cells < reserve:
        raise RuntimeError(
            f"FAIL_CLOSED_INSUFFICIENT_FREE_QUOTA remaining={remaining} projected={projected_cells} reserve={reserve}"
        )
    if not cohort["fixtures"]:
        raise RuntimeError("FAIL_CLOSED_EMPTY_COHORT")

    registry = {
        "registry_version": "PROSPECTIVE_ACTIVE_COHORT_V313_1.0",
        "activated_at_utc": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "active_cohort_id": cohort["cohort_id"],
        "cohort_path": cohort_path.relative_to(ROOT).as_posix(),
        "cohort_sha256": sha256_bytes(cohort_path.read_bytes()),
        "cohort_manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "source_sha256": cohort["source"]["sha256"],
        "eligible_fixtures": cohort["eligible_fixture_total"],
        "projected_maximum_credits": projected_cells,
        "quota_remaining_at_activation": remaining,
        "minimum_quota_reserve": reserve,
        "activation_authorized": True,
        "public_repository_publication_authorized": True,
        "authorized_public_repository": "lrcdw/Discord",
        "api_key_window_authorized": True,
        "api_key_authorized_through_utc": protocol["activation_requirements"]["api_key_authorized_through_utc"],
        "credential_persisted": False,
        "target_odds_observed_before_activation": False,
        "operating_mode": "SHADOW",
        "step_i_allowed": False,
    }
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_bytes(canonical_bytes(registry))
    return active_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--cohorts-root", type=Path, default=DEFAULT_COHORTS)
    parser.add_argument("--active", type=Path, default=DEFAULT_ACTIVE)
    args = parser.parse_args()
    path = activate(args.protocol, args.cohorts_root, args.active)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
