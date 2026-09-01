from __future__ import annotations

import argparse
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from prospective_total_corners_v31 import (
    TEAM_ALIAS_REGISTRY,
    build_cohort,
    canonical_bytes,
    collect as collect_base,
    http_get,
    iso_utc,
    load_json,
    load_protocol as load_protocol_base,
    parse_utc,
    sha256_bytes,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / "config" / "odds_prospective_protocol_v3_1_3_total_corners.json"
ACTIVATION_AMENDMENT = ROOT / "config" / "odds_prospective_protocol_v3_1_3a_activation.json"


def load_protocol(path: Path = DEFAULT_PROTOCOL) -> dict[str, Any]:
    protocol = load_protocol_base(path)
    assert protocol["protocol_version"] == "ODDS_PROSPECTIVE_V3_1_3_TOTAL_CORNERS"
    if path.resolve() == DEFAULT_PROTOCOL.resolve() and ACTIVATION_AMENDMENT.exists():
        amendment = load_json(ACTIVATION_AMENDMENT)
        assert amendment["parent_protocol_version"] == protocol["protocol_version"]
        assert amendment["thresholds_changed"] is False
        assert amendment["target_odds_observed_before_amendment"] is False
        protocol["protocol_version"] = amendment["amended_protocol_version"]
        protocol["activation_state"] = amendment["changes"]["activation_state"]["new"]
        reserve = amendment["changes"]["minimum_quota_reserve"]["new"]
        protocol["no_cost_control"]["minimum_quota_reserve"] = reserve
        protocol["activation_requirements"]["minimum_quota_reserve_after_collection"] = reserve
        protocol["activation_requirements"]["network_calls_authorized_now"] = amendment["changes"]["network_calls_authorized_now"]["new"]
        protocol["activation_requirements"]["authorization_amendment"] = amendment["amendment_version"]
        protocol["activation_requirements"]["authorized_public_repository"] = amendment["scope"]["public_repository"]
        protocol["activation_requirements"]["api_key_authorized_through_utc"] = amendment["scope"]["authorized_through_utc"]
    assert protocol["activation_state"] == "AUTHORIZED_PENDING_COHORT"
    assert protocol["no_cost_control"]["minimum_quota_reserve"] == 150
    assert protocol["activation_requirements"]["minimum_quota_reserve_after_collection"] == 150
    assert protocol["activation_requirements"]["network_calls_authorized_now"] is True
    assert protocol["bookmaker_snapshot_quality"]["unit_of_quarantine"] == "BOOKMAKER_FIXTURE_HORIZON_SNAPSHOT"
    assert protocol["bookmaker_snapshot_quality"]["conflicting_duplicate_price_selection"] == "PROHIBITED"
    assert protocol["gates"]["duplicate_same_timestamp_different_price_allowed"] is False
    assert protocol["gates"]["thresholds_changed"] is False
    return protocol


def collect(
    cohort_path: Path,
    protocol_path: Path,
    output_root: Path,
    execution_timestamp: str | None = None,
) -> Path:
    """Delegate to the frozen base collector with the v3.1.3A view materialized in memory."""
    protocol = load_protocol(protocol_path)
    with tempfile.TemporaryDirectory(prefix="corners-v313-") as directory:
        resolved = Path(directory) / "protocol.json"
        resolved.write_bytes(canonical_bytes(protocol))
        return collect_base(cohort_path, resolved, output_root, execution_timestamp)


def enroll(
    protocol_path: Path,
    output_root: Path,
    fetched_at_utc: str | None = None,
    source_file: Path | None = None,
) -> Path:
    protocol = load_protocol(protocol_path)
    fetched_at = parse_utc(fetched_at_utc) if fetched_at_utc else datetime.now(timezone.utc)
    if source_file:
        raw, status, headers = source_file.read_bytes(), 200, {"reused-source": str(source_file)}
    else:
        raw, status, headers = http_get(protocol["denominator"]["url"])
        if status != 200:
            raise RuntimeError(f"fixture source HTTP {status}")
    cohort = build_cohort(raw, iso_utc(fetched_at), protocol)
    if not cohort["cohort_id"].startswith("V31P-"):
        raise ValueError("unexpected base cohort id")
    cohort["cohort_id"] = "V313P-" + cohort["cohort_id"][5:]
    cohort_dir = output_root / cohort["cohort_id"]
    if cohort_dir.exists():
        raise FileExistsError(f"immutable cohort already exists: {cohort_dir}")
    cohort_dir.mkdir(parents=True)
    source_path = cohort_dir / "fixtures_source.csv"
    source_path.write_bytes(raw)
    cohort_path = cohort_dir / "cohort.json"
    cohort_path.write_bytes(canonical_bytes(cohort))
    manifest = {
        "cohort_id": cohort["cohort_id"],
        "protocol_version": protocol["protocol_version"],
        "protocol_artifacts": [
            {"path": protocol_path.relative_to(ROOT).as_posix(), "sha256": sha256_bytes(protocol_path.read_bytes())},
            {"path": ACTIVATION_AMENDMENT.relative_to(ROOT).as_posix(), "sha256": sha256_bytes(ACTIVATION_AMENDMENT.read_bytes())},
            {"path": TEAM_ALIAS_REGISTRY.relative_to(ROOT).as_posix(), "sha256": sha256_bytes(TEAM_ALIAS_REGISTRY.read_bytes())},
        ],
        "source_reused_from_prior_cohort": str(source_file) if source_file else None,
        "http_status": status,
        "http_metadata": {key: headers[key] for key in ("date", "last-modified", "etag", "content-type") if key in headers},
        "files": [
            {"path": source_path.name, "bytes": len(raw), "sha256": sha256_bytes(raw)},
            {"path": cohort_path.name, "bytes": cohort_path.stat().st_size, "sha256": sha256_bytes(cohort_path.read_bytes())},
        ],
        "immutable": True,
        "target_odds_observed": False,
        "step_i_allowed": False,
    }
    (cohort_dir / "manifest.json").write_bytes(canonical_bytes(manifest))
    return cohort_dir


def evaluate_bookmakers(
    payload: dict[str, Any],
    prediction_as_of: datetime,
    collected_at: datetime,
    protocol: dict[str, Any],
) -> tuple[set[str], dict[str, list[str]], list[str]]:
    """Evaluate each bookmaker independently and never select an ambiguous price.

    Returns valid bookmaker keys, bookmaker-local quarantine reasons and systemic
    integrity issues. Any conflicting price invalidates the complete bookmaker
    snapshot for the cell while independent bookmakers remain evaluable.
    """
    systemic: list[str] = []
    valid: set[str] = set()
    quarantined: dict[str, list[str]] = defaultdict(list)
    age_from_collection = (prediction_as_of - collected_at).total_seconds()
    if age_from_collection < 0 or age_from_collection > protocol["collection"]["collection_window_seconds_before_prediction_as_of"]:
        return valid, {}, ["collection_outside_frozen_window"]

    candidates = set(protocol["collection"]["candidate_bookmakers"])
    objects_by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bookmaker in payload.get("bookmakers", []):
        book = str(bookmaker.get("key", ""))
        if book in candidates:
            objects_by_book[book].append(bookmaker)

    for book, bookmaker_objects in objects_by_book.items():
        if len(bookmaker_objects) != 1:
            quarantined[book].append(f"duplicate_bookmaker_object:{len(bookmaker_objects)}")
            continue
        markets = [
            market
            for market in bookmaker_objects[0].get("markets", [])
            if market.get("key") == protocol["domain"]["provider_market_key"]
        ]
        if not markets:
            continue
        if len(markets) != 1:
            quarantined[book].append(f"duplicate_market_object:{len(markets)}")
            continue
        market = markets[0]
        try:
            updated = parse_utc(market["last_update"])
        except (KeyError, TypeError, ValueError):
            quarantined[book].append("missing_or_invalid_market_timestamp")
            continue
        market_age = (prediction_as_of - updated).total_seconds()
        if market_age < 0:
            quarantined[book].append("future_market_timestamp")
            continue
        if market_age > protocol["collection"]["max_odds_age_seconds"]:
            quarantined[book].append("stale_market_timestamp")
            continue

        prices: dict[tuple[float, str], list[float]] = defaultdict(list)
        malformed = False
        for outcome in market.get("outcomes", []):
            side = str(outcome.get("name", "")).upper()
            if side not in {"OVER", "UNDER"}:
                continue
            try:
                point = float(outcome["point"])
                price = float(outcome["price"])
            except (KeyError, TypeError, ValueError):
                malformed = True
                continue
            doubled = round(point * 2)
            if price <= 1 or abs(point * 2 - doubled) > 1e-9 or doubled % 2 == 0:
                continue
            prices[(point, side)].append(price)
        if malformed:
            quarantined[book].append("malformed_relevant_outcome")
            continue

        conflicts = []
        canonical: dict[tuple[float, str], float] = {}
        for key, values in prices.items():
            distinct = sorted(set(values))
            if len(distinct) > 1:
                conflicts.append(f"conflicting_duplicate:{key[0]}:{key[1]}")
            elif distinct:
                canonical[key] = distinct[0]
        if conflicts:
            quarantined[book].extend(sorted(conflicts))
            continue

        points = {point for point, _ in canonical}
        if any((point, "OVER") in canonical and (point, "UNDER") in canonical for point in points):
            valid.add(book)

    return valid, {book: reasons for book, reasons in sorted(quarantined.items())}, systemic


def audit(
    cohort_path: Path,
    collections_root: Path,
    protocol_path: Path,
    output_path: Path,
    audited_at: str | None = None,
    evidence_role: str = "PROSPECTIVE_V313",
) -> dict[str, Any]:
    protocol = load_protocol(protocol_path)
    cohort = load_json(cohort_path)
    audited_time = parse_utc(audited_at) if audited_at else datetime.now(timezone.utc)
    if evidence_role not in {"PROSPECTIVE_V313", "ENGINEERING_REPLAY_ONLY"}:
        raise ValueError(f"unsupported evidence role: {evidence_role}")

    candidates: dict[tuple[str, int], list[tuple[datetime, Path, dict[str, Any]]]] = defaultdict(list)
    systemic_issues: list[str] = []
    for manifest_path in collections_root.rglob("execution_manifest.json") if collections_root.exists() else []:
        manifest = load_json(manifest_path)
        if manifest.get("cohort_id") != cohort["cohort_id"]:
            continue
        try:
            collected_at = parse_utc(manifest["execution_timestamp_utc"])
        except (KeyError, TypeError, ValueError):
            systemic_issues.append(f"invalid_manifest_timestamp:{manifest_path}")
            continue
        for request in manifest.get("odds_requests", []):
            if request.get("status") != "COLLECTED":
                continue
            raw_path = manifest_path.parent / request["raw_path"]
            if not raw_path.exists() or sha256_bytes(raw_path.read_bytes()) != request.get("raw_sha256"):
                systemic_issues.append(f"raw_hash_mismatch:{raw_path}")
                continue
            key = (request["fixture_id"], int(request["horizon_minutes"]))
            candidates[key].append((collected_at, raw_path, request))

    cells: list[dict[str, Any]] = []
    quality_issues: list[str] = []
    quarantined_snapshots: set[tuple[str, int, str]] = set()
    book_covered: dict[tuple[str, int, str], int] = defaultdict(int)
    denominators: dict[tuple[str, int], int] = defaultdict(int)
    for fixture in cohort["fixtures"]:
        kickoff = parse_utc(fixture["kickoff_utc"])
        for horizon in protocol["collection"]["horizons_minutes"]:
            prediction_as_of = kickoff - timedelta(minutes=horizon)
            key = (fixture["fixture_id"], horizon)
            eligible = [item for item in candidates.get(key, []) if item[0] <= prediction_as_of]
            selected = max(eligible, key=lambda item: item[0]) if eligible else None
            books: set[str] = set()
            quarantines: dict[str, list[str]] = {}
            local_systemic: list[str] = []
            if selected:
                request_prediction = selected[2].get("prediction_as_of_utc")
                if request_prediction and parse_utc(request_prediction) != prediction_as_of:
                    local_systemic.append("request_prediction_as_of_mismatch")
                payload = load_json(selected[1])
                books, quarantines, evaluation_systemic = evaluate_bookmakers(
                    payload, prediction_as_of, selected[0], protocol
                )
                local_systemic.extend(evaluation_systemic)
                for book, reasons in quarantines.items():
                    quarantined_snapshots.add((fixture["fixture_id"], horizon, book))
                    quality_issues.extend(
                        f"{fixture['fixture_id']}:T{horizon}:{book}:{reason}" for reason in reasons
                    )
                systemic_issues.extend(
                    f"{fixture['fixture_id']}:T{horizon}:{issue}" for issue in local_systemic
                )
            is_due = audited_time >= prediction_as_of
            if is_due:
                denominators[(fixture["competition"], horizon)] += 1
                for book in books:
                    book_covered[(fixture["competition"], horizon, book)] += 1
            cells.append({
                "fixture_id": fixture["fixture_id"],
                "competition": fixture["competition"],
                "horizon_minutes": horizon,
                "prediction_as_of_utc": iso_utc(prediction_as_of),
                "snapshot_found": selected is not None,
                "valid_bookmakers": sorted(books),
                "quarantined_bookmakers": quarantines,
                "covered": bool(books) if is_due else None,
                "evaluation_status": "COVERED" if is_due and books else "UNCOVERED" if is_due else "NOT_YET_DUE",
                "systemic_issues": local_systemic,
            })

    summaries: list[dict[str, Any]] = []
    admitted: dict[str, list[str]] = defaultdict(list)
    competitions = sorted({fixture["competition"] for fixture in cohort["fixtures"]})
    for competition in competitions:
        for book in protocol["collection"]["candidate_bookmakers"]:
            rates = []
            for horizon in protocol["collection"]["horizons_minutes"]:
                denominator = denominators[(competition, horizon)]
                rates.append(book_covered[(competition, horizon, book)] / denominator if denominator else 0.0)
            if rates and min(rates) >= protocol["gates"]["bookmaker_admission_min_rate_across_all_horizons"]:
                admitted[competition].append(book)
        for horizon in protocol["collection"]["horizons_minutes"]:
            relevant = [cell for cell in cells if cell["competition"] == competition and cell["horizon_minutes"] == horizon]
            due = [cell for cell in relevant if cell["evaluation_status"] != "NOT_YET_DUE"]
            covered = sum(cell["covered"] is True for cell in due)
            rate = covered / len(due) if due else None
            status = "NOT_YET_EVALUABLE" if rate is None else "PASS" if rate >= protocol["gates"]["coverage_pass_rate"] else "PARTIAL" if rate >= protocol["gates"]["coverage_partial_floor"] else "FAIL"
            summaries.append({
                "competition": competition,
                "horizon_minutes": horizon,
                "registered": len(relevant),
                "eligible_due": len(due),
                "covered": covered,
                "coverage_rate": rate,
                "gate_status": status,
            })

    minimum = protocol["denominator"]["minimum_completed_fixtures_per_competition_for_final_review"]
    collection_complete = bool(cohort["fixtures"]) and audited_time > max(parse_utc(fixture["kickoff_utc"]) for fixture in cohort["fixtures"])
    minimum_ready = bool(summaries) and collection_complete and all(row["registered"] >= minimum for row in summaries)
    systemic_issues = sorted(set(systemic_issues))
    quality_issues = sorted(set(quality_issues))
    gates_pass = (
        evidence_role == "PROSPECTIVE_V313"
        and minimum_ready
        and not systemic_issues
        and all(row["gate_status"] == "PASS" for row in summaries)
        and all(admitted.get(competition) for competition in competitions)
    )
    result = {
        "protocol_version": protocol["protocol_version"],
        "cohort_id": cohort["cohort_id"],
        "audited_at_utc": iso_utc(audited_time),
        "evidence_role": evidence_role,
        "operating_mode": "SHADOW",
        "eligible_fixtures": cohort["eligible_fixture_total"],
        "cells": cells,
        "coverage_summary": summaries,
        "admitted_bookmakers_diagnostic": dict(admitted),
        "minimum_denominator_ready": minimum_ready,
        "collection_window_complete": collection_complete,
        "systemic_integrity_status": "PASS" if not systemic_issues else "FAIL",
        "systemic_integrity_issues": systemic_issues,
        "bookmaker_quality_status": "PASS" if not quality_issues else "FAIL_QUARANTINED",
        "bookmaker_quality_issue_count": len(quality_issues),
        "bookmaker_quality_issues": quality_issues,
        "quarantined_bookmaker_snapshot_count": len(quarantined_snapshots),
        "step_h_status": "ELIGIBLE_FOR_FORMAL_REVIEW" if gates_pass else "ENGINEERING_REPLAY_ONLY" if evidence_role == "ENGINEERING_REPLAY_ONLY" else "OPEN_ACCUMULATING_PROSPECTIVE_EVIDENCE",
        "step_i_allowed": False,
        "paid_access_used": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(canonical_bytes(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    commands = parser.add_subparsers(dest="command", required=True)
    enroll_parser = commands.add_parser("enroll")
    enroll_parser.add_argument("--output-root", type=Path, default=ROOT / "raw" / "prospective_v3_1_3" / "cohorts")
    enroll_parser.add_argument("--fetched-at")
    enroll_parser.add_argument("--source-file", type=Path)
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--cohort", type=Path, required=True)
    collect_parser.add_argument("--output-root", type=Path, default=ROOT / "raw" / "prospective_v3_1_3" / "collections")
    collect_parser.add_argument("--execution-timestamp")
    audit_parser = commands.add_parser("audit")
    audit_parser.add_argument("--cohort", type=Path, required=True)
    audit_parser.add_argument("--collections-root", type=Path, default=ROOT / "raw" / "prospective_v3_1_3" / "collections")
    audit_parser.add_argument("--output", type=Path, default=ROOT / "reports" / "odds_prospective_v3_1_3_result.json")
    audit_parser.add_argument("--audited-at")
    audit_parser.add_argument("--evidence-role", choices=["PROSPECTIVE_V313", "ENGINEERING_REPLAY_ONLY"], default="PROSPECTIVE_V313")
    args = parser.parse_args()
    if args.command == "enroll":
        path = enroll(args.protocol, args.output_root, args.fetched_at, args.source_file)
        print(path)
        return 0
    if args.command == "collect":
        path = collect(args.cohort, args.protocol, args.output_root, args.execution_timestamp)
        print(path)
        return 0
    result = audit(args.cohort, args.collections_root, args.protocol, args.output, args.audited_at, args.evidence_role)
    print(f"COHORT_ID={result['cohort_id']}")
    print(f"SYSTEMIC_INTEGRITY={result['systemic_integrity_status']}")
    print(f"BOOKMAKER_QUALITY={result['bookmaker_quality_status']}")
    print(f"STEP_H={result['step_h_status']}")
    print("STEP_I_ALLOWED=FALSE")
    return 0 if result["systemic_integrity_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
