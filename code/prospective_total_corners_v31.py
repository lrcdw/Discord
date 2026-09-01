from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / "config" / "odds_prospective_protocol_v3_1_2_total_corners.json"
SOURCE_ROUTING_AMENDMENT = ROOT / "config" / "odds_prospective_protocol_v3_1_2a_source_routing.json"
FIXTURE_TIME_AMENDMENT = ROOT / "config" / "odds_prospective_protocol_v3_1_2b_fixture_time.json"
EVENT_ALIAS_AMENDMENT = ROOT / "config" / "odds_prospective_protocol_v3_1_2c_event_aliases.json"
SCHEDULER_AMENDMENT = ROOT / "config" / "odds_prospective_protocol_v3_1_2d_scheduler.json"
TEAM_ALIAS_REGISTRY = ROOT / "registries" / "prospective_team_aliases_v31_1.0.json"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone required: {value}")
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_protocol(path: Path = DEFAULT_PROTOCOL) -> dict[str, Any]:
    protocol = load_json(path)
    if path.resolve() == DEFAULT_PROTOCOL.resolve() and SOURCE_ROUTING_AMENDMENT.exists():
        amendment = load_json(SOURCE_ROUTING_AMENDMENT)
        assert amendment["parent_protocol_version"] == protocol["protocol_version"]
        change = amendment["changes"]["denominator.url"]
        assert protocol["denominator"]["url"] == change["old"]
        protocol["denominator"]["url"] = change["new"]
        protocol["protocol_version"] = amendment["amended_protocol_version"]
        protocol["source_routing_amendment"] = amendment["amendment_version"]
    if path.resolve() == DEFAULT_PROTOCOL.resolve() and FIXTURE_TIME_AMENDMENT.exists():
        amendment = load_json(FIXTURE_TIME_AMENDMENT)
        assert amendment["parent_protocol_version"] == protocol["protocol_version"]
        for competition in protocol["denominator"]["competition_codes"].values():
            competition["timezone"] = "Europe/London"
        protocol["protocol_version"] = amendment["amended_protocol_version"]
        protocol["fixture_time_amendment"] = amendment["amendment_version"]
    if path.resolve() == DEFAULT_PROTOCOL.resolve() and EVENT_ALIAS_AMENDMENT.exists():
        amendment = load_json(EVENT_ALIAS_AMENDMENT)
        assert amendment["parent_protocol_version"] == protocol["protocol_version"]
        protocol["protocol_version"] = amendment["amended_protocol_version"]
        protocol["event_alias_amendment"] = amendment["amendment_version"]
    if path.resolve() == DEFAULT_PROTOCOL.resolve() and SCHEDULER_AMENDMENT.exists():
        amendment = load_json(SCHEDULER_AMENDMENT)
        assert amendment["parent_protocol_version"] == protocol["protocol_version"]
        protocol["protocol_version"] = amendment["amended_protocol_version"]
        protocol["scheduler_amendment"] = amendment["amendment_version"]
        protocol["scheduler"] = amendment["scheduler"]
    assert protocol["domain"]["operational_market"] == "TOTAL_CORNERS"
    assert protocol["collection"]["horizons_minutes"] == [60, 30, 15]
    assert protocol["collection"]["max_odds_age_seconds"] == 300
    assert protocol["gates"]["thresholds_changed"] is False
    return protocol


def decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("fixture CSV encoding is not UTF-8/CP1252")


def parse_local_kickoff(row: dict[str, str], timezone_name: str) -> datetime:
    date_text = (row.get("Date") or "").strip()
    time_text = (row.get("Time") or "").strip()
    if not date_text or not time_text:
        raise ValueError("missing Date/Time")
    local = datetime.strptime(f"{date_text} {time_text}", "%d/%m/%Y %H:%M").replace(tzinfo=ZoneInfo(timezone_name))
    return local.astimezone(timezone.utc)


def fixture_key(division: str, kickoff: str, home: str, away: str) -> str:
    material = "|".join((division, kickoff, home.strip(), away.strip())).encode("utf-8")
    return "FX-" + hashlib.sha256(material).hexdigest()[:20]


def build_cohort(raw: bytes, fetched_at_utc: str, protocol: dict[str, Any]) -> dict[str, Any]:
    fetched_at = parse_utc(fetched_at_utc)
    minimum = fetched_at + timedelta(minutes=protocol["denominator"]["minimum_lead_time_minutes"])
    maximum = fetched_at + timedelta(days=protocol["denominator"]["maximum_cohort_window_days"])
    reader = csv.DictReader(io.StringIO(decode_csv(raw)))
    required = {"Div", "Date", "Time", "HomeTeam", "AwayTeam"}
    if not required.issubset(set(reader.fieldnames or [])):
        raise ValueError(f"fixture CSV missing required columns: {sorted(required - set(reader.fieldnames or []))}")
    fixtures: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    seen: set[str] = set()
    competitions = protocol["denominator"]["competition_codes"]
    for line_number, row in enumerate(reader, 2):
        division = (row.get("Div") or "").strip()
        if division not in competitions:
            continue
        try:
            kickoff = parse_local_kickoff(row, competitions[division]["timezone"])
        except ValueError as exc:
            exclusions.append({"line": line_number, "division": division, "reason": "UNPARSEABLE_KICKOFF", "detail": str(exc)})
            continue
        if kickoff < minimum or kickoff > maximum:
            exclusions.append({"line": line_number, "division": division, "reason": "OUTSIDE_FROZEN_TIME_WINDOW", "kickoff_utc": iso_utc(kickoff)})
            continue
        home = (row.get("HomeTeam") or "").strip()
        away = (row.get("AwayTeam") or "").strip()
        if not home or not away:
            exclusions.append({"line": line_number, "division": division, "reason": "MISSING_TEAM"})
            continue
        kickoff_text = iso_utc(kickoff)
        key = fixture_key(division, kickoff_text, home, away)
        if key in seen:
            exclusions.append({"line": line_number, "division": division, "reason": "EXACT_DUPLICATE", "fixture_id": key})
            continue
        seen.add(key)
        fixtures.append({
            "fixture_id": key,
            "competition_code": division,
            "competition": competitions[division]["competition"],
            "sport_key": competitions[division]["sport_key"],
            "kickoff_utc": kickoff_text,
            "home_team": home,
            "away_team": away,
            "source_line": line_number,
        })
    fixtures.sort(key=lambda row: (row["kickoff_utc"], row["competition_code"], row["home_team"], row["away_team"]))
    source_hash = sha256_bytes(raw)
    protocol_hash = sha256_bytes(protocol["protocol_version"].encode("utf-8"))[:8].upper()
    cohort_id = "V31P-" + fetched_at.strftime("%Y%m%dT%H%M%SZ") + "-" + source_hash[:10].upper() + "-" + protocol_hash
    counts = {item["competition"]: 0 for item in competitions.values()}
    for fixture in fixtures:
        counts[fixture["competition"]] += 1
    return {
        "cohort_id": cohort_id,
        "protocol_version": protocol["protocol_version"],
        "enrolled_at_utc": iso_utc(fetched_at),
        "enrollment_state": "FROZEN_BEFORE_TARGET_ODDS_OBSERVATION",
        "source": {
            "url": protocol["denominator"]["url"],
            "sha256": source_hash,
            "bytes": len(raw),
        },
        "window": {"minimum_kickoff_utc": iso_utc(minimum), "maximum_kickoff_utc": iso_utc(maximum)},
        "eligible_fixture_counts": counts,
        "eligible_fixture_total": len(fixtures),
        "fixtures": fixtures,
        "exclusions": exclusions,
        "target_odds_requests_executed_during_enrollment": 0,
        "step_i_allowed": False,
    }


def http_get(url: str, params: dict[str, str] | None = None, timeout: int = 90) -> tuple[bytes, int, dict[str, str]]:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "corners-lab-v3.1-prospective/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        headers = {key.lower(): value for key, value in response.headers.items()}
        return body, response.status, headers


def enroll(protocol_path: Path, output_root: Path, fetched_at_utc: str | None = None, source_file: Path | None = None) -> Path:
    protocol = load_protocol(protocol_path)
    fetched_at = parse_utc(fetched_at_utc) if fetched_at_utc else datetime.now(timezone.utc)
    if source_file:
        raw, status, headers = source_file.read_bytes(), 200, {"reused-source": str(source_file)}
    else:
        raw, status, headers = http_get(protocol["denominator"]["url"])
        if status != 200:
            raise RuntimeError(f"fixture source HTTP {status}")
    cohort = build_cohort(raw, iso_utc(fetched_at), protocol)
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
        "protocol_path": protocol_path.relative_to(ROOT).as_posix() if protocol_path.is_relative_to(ROOT) else str(protocol_path),
        "protocol_artifacts": [
            {"path": protocol_path.relative_to(ROOT).as_posix(), "sha256": sha256_bytes(protocol_path.read_bytes())},
            {"path": SOURCE_ROUTING_AMENDMENT.relative_to(ROOT).as_posix(), "sha256": sha256_bytes(SOURCE_ROUTING_AMENDMENT.read_bytes())},
            {"path": FIXTURE_TIME_AMENDMENT.relative_to(ROOT).as_posix(), "sha256": sha256_bytes(FIXTURE_TIME_AMENDMENT.read_bytes())},
            {"path": EVENT_ALIAS_AMENDMENT.relative_to(ROOT).as_posix(), "sha256": sha256_bytes(EVENT_ALIAS_AMENDMENT.read_bytes())},
            {"path": TEAM_ALIAS_REGISTRY.relative_to(ROOT).as_posix(), "sha256": sha256_bytes(TEAM_ALIAS_REGISTRY.read_bytes())},
            {"path": SCHEDULER_AMENDMENT.relative_to(ROOT).as_posix(), "sha256": sha256_bytes(SCHEDULER_AMENDMENT.read_bytes())},
        ],
        "source_reused_from_invalidated_pre_odds_cohort": str(source_file) if source_file else None,
        "http_status": status,
        "http_metadata": {key: headers[key] for key in ("date", "last-modified", "etag", "content-type") if key in headers},
        "files": [
            {"path": source_path.name, "bytes": len(raw), "sha256": sha256_bytes(raw)},
            {"path": cohort_path.name, "bytes": cohort_path.stat().st_size, "sha256": sha256_bytes(cohort_path.read_bytes())},
        ],
        "immutable": True,
        "target_odds_observed": False,
    }
    (cohort_dir / "manifest.json").write_bytes(canonical_bytes(manifest))
    return cohort_dir


def normalize_team(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", folded)


def load_team_aliases(path: Path = TEAM_ALIAS_REGISTRY) -> dict[tuple[str, str], str]:
    registry = load_json(path)
    assert registry["fuzzy_resolution_allowed"] is False
    aliases: dict[tuple[str, str], str] = {}
    for row in registry["aliases"]:
        key = (row["competition"], normalize_team(row["source"]))
        if key in aliases:
            raise ValueError(f"duplicate team alias: {key}")
        aliases[key] = normalize_team(row["provider"])
    return aliases


def resolve_event(fixture: dict[str, Any], events: list[dict[str, Any]], aliases: dict[tuple[str, str], str] | None = None) -> tuple[str | None, str]:
    kickoff = parse_utc(fixture["kickoff_utc"])
    aliases = load_team_aliases() if aliases is None else aliases
    competition = fixture.get("competition", "")
    raw_home = normalize_team(fixture["home_team"])
    raw_away = normalize_team(fixture["away_team"])
    home = aliases.get((competition, raw_home), raw_home)
    away = aliases.get((competition, raw_away), raw_away)
    matches = []
    for event in events:
        try:
            delta = abs((parse_utc(event["commence_time"]) - kickoff).total_seconds())
        except (KeyError, ValueError):
            continue
        if delta <= 900 and normalize_team(event.get("home_team", "")) == home and normalize_team(event.get("away_team", "")) == away:
            matches.append(event)
    if len(matches) == 1:
        return str(matches[0]["id"]), "EXACT_NORMALIZED"
    if len(matches) > 1:
        return None, "AMBIGUOUS"
    return None, "UNRESOLVED"


def attempted_cells(collections_root: Path, cohort_id: str) -> set[tuple[str, int]]:
    """Return every cell whose single prospective attempt has already begun.

    New manifests persist ``scheduled_tasks`` before any provider request.  The
    odds-request fallback keeps the already-observed v3.1.2D manifests
    compatible with cloud failover without rewriting them.
    """
    attempted: set[tuple[str, int]] = set()
    if not collections_root.exists():
        return attempted
    for manifest_path in collections_root.rglob("execution_manifest.json"):
        manifest = load_json(manifest_path)
        if manifest.get("cohort_id") != cohort_id:
            continue
        rows = manifest.get("scheduled_tasks")
        if rows is None:
            rows = manifest.get("odds_requests", [])
        for row in rows:
            try:
                attempted.add((str(row["fixture_id"]), int(row["horizon_minutes"])))
            except (KeyError, TypeError, ValueError):
                continue
    return attempted


def due_tasks(
    cohort: dict[str, Any],
    execution_time: datetime,
    protocol: dict[str, Any],
    attempted: set[tuple[str, int]] | None = None,
) -> list[dict[str, Any]]:
    window = protocol["collection"]["collection_window_seconds_before_prediction_as_of"]
    attempted = attempted or set()
    tasks = []
    for fixture in cohort["fixtures"]:
        kickoff = parse_utc(fixture["kickoff_utc"])
        for horizon in protocol["collection"]["horizons_minutes"]:
            if (fixture["fixture_id"], horizon) in attempted:
                continue
            prediction_as_of = kickoff - timedelta(minutes=horizon)
            seconds_before = (prediction_as_of - execution_time).total_seconds()
            if 0 <= seconds_before <= window:
                tasks.append({**fixture, "horizon_minutes": horizon, "prediction_as_of_utc": iso_utc(prediction_as_of)})
    return sorted(tasks, key=lambda item: (item["prediction_as_of_utc"], item["fixture_id"], -item["horizon_minutes"]))


def quota_remaining(headers: dict[str, str]) -> int | None:
    value = headers.get("x-requests-remaining")
    return int(value) if value is not None and value.isdigit() else None


def credential_leaked(directory: Path, secret: bytes) -> list[str]:
    return [str(path) for path in directory.rglob("*") if path.is_file() and secret in path.read_bytes()]


def collect(cohort_path: Path, protocol_path: Path, output_root: Path, execution_timestamp: str | None = None) -> Path:
    protocol = load_protocol(protocol_path)
    cohort = load_json(cohort_path)
    execution_time = parse_utc(execution_timestamp) if execution_timestamp else datetime.now(timezone.utc)
    previous_attempts = attempted_cells(output_root, cohort["cohort_id"])
    tasks = due_tasks(cohort, execution_time, protocol, previous_attempts)
    run_id = "V31C-" + execution_time.strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / cohort["cohort_id"] / run_id
    if run_dir.exists():
        raise FileExistsError(f"immutable collection run already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    key_name = protocol["collection"]["api_key_environment_variable"]
    secret = os.environ.get(key_name, "").strip()
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "cohort_id": cohort["cohort_id"],
        "protocol_version": protocol["protocol_version"],
        "execution_timestamp_utc": iso_utc(execution_time),
        "due_tasks": len(tasks),
        "scheduled_tasks": [
            {
                "fixture_id": task["fixture_id"],
                "horizon_minutes": task["horizon_minutes"],
                "prediction_as_of_utc": task["prediction_as_of_utc"],
            }
            for task in tasks
        ],
        "previously_attempted_cells": len(previous_attempts),
        "odds_requests": [],
        "event_catalogue_requests": [],
        "status": "NO_DUE_TASKS" if not tasks else "PENDING",
        "paid_access_used": False,
        "step_i_allowed": False,
        "team_alias_registry": {
            "path": TEAM_ALIAS_REGISTRY.relative_to(ROOT).as_posix(),
            "sha256": sha256_bytes(TEAM_ALIAS_REGISTRY.read_bytes()),
        },
    }
    if tasks and not secret:
        manifest["status"] = "FAIL_CLOSED_MISSING_CREDENTIAL"
    elif tasks:
        api = protocol["collection"]["base_url"].rstrip("/")
        by_sport: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for task in tasks:
            by_sport[task["sport_key"]].append(task)
        events_by_sport: dict[str, list[dict[str, Any]]] = {}
        remaining_values = []
        for sport in sorted(by_sport):
            body, status, headers = http_get(f"{api}/sports/{sport}/events", {"apiKey": secret, "dateFormat": "iso"})
            raw_path = run_dir / f"events_{sport}.json"
            raw_path.write_bytes(body)
            remaining = quota_remaining(headers)
            if remaining is not None:
                remaining_values.append(remaining)
            manifest["event_catalogue_requests"].append({
                "sport_key": sport, "status": status, "raw_path": raw_path.name,
                "raw_sha256": sha256_bytes(body), "remaining": remaining,
            })
            if status != 200:
                manifest["status"] = "FAIL_CLOSED_EVENT_CATALOGUE_HTTP"
                break
            events_by_sport[sport] = json.loads(body)
        if manifest["status"] == "PENDING":
            if not remaining_values:
                manifest["status"] = "FAIL_CLOSED_MISSING_QUOTA_HEADER"
            elif min(remaining_values) - len(tasks) < protocol["no_cost_control"]["minimum_quota_reserve"]:
                manifest["status"] = "FAIL_CLOSED_INSUFFICIENT_FREE_QUOTA"
                manifest["quota_remaining_before_odds"] = min(remaining_values)
            else:
                manifest["quota_remaining_before_odds"] = min(remaining_values)
                for task in tasks:
                    event_id, resolution = resolve_event(task, events_by_sport[task["sport_key"]])
                    entry: dict[str, Any] = {
                        "fixture_id": task["fixture_id"],
                        "horizon_minutes": task["horizon_minutes"],
                        "prediction_as_of_utc": task["prediction_as_of_utc"],
                        "event_resolution": resolution,
                        "event_id": event_id,
                    }
                    if event_id is None:
                        entry["status"] = "UNCOVERED_UNRESOLVED_EVENT"
                        manifest["odds_requests"].append(entry)
                        continue
                    params = {
                        "apiKey": secret,
                        "bookmakers": ",".join(protocol["collection"]["candidate_bookmakers"]),
                        "markets": protocol["domain"]["provider_market_key"],
                        "oddsFormat": "decimal",
                        "dateFormat": "iso",
                    }
                    body, status, headers = http_get(f"{api}/sports/{task['sport_key']}/events/{event_id}/odds", params)
                    raw_name = f"odds_{task['fixture_id']}_T{task['horizon_minutes']}.json"
                    (run_dir / raw_name).write_bytes(body)
                    entry.update({
                        "status": "COLLECTED" if status == 200 else "HTTP_ERROR",
                        "http_status": status,
                        "raw_path": raw_name,
                        "raw_sha256": sha256_bytes(body),
                        "quota_remaining": quota_remaining(headers),
                        "quota_used": headers.get("x-requests-used"),
                        "quota_last": headers.get("x-requests-last"),
                    })
                    manifest["odds_requests"].append(entry)
                    if status != 200:
                        manifest["status"] = "FAIL_CLOSED_ODDS_HTTP"
                        break
                if manifest["status"] == "PENDING":
                    manifest["status"] = "COLLECTION_COMPLETE"
    manifest_path = run_dir / "execution_manifest.json"
    manifest_path.write_bytes(canonical_bytes(manifest))
    if secret:
        leaks = credential_leaked(run_dir, secret.encode())
        if leaks:
            raise RuntimeError(f"credential leak detected: {leaks}")
    return manifest_path


def valid_books(payload: dict[str, Any], prediction_as_of: datetime, collected_at: datetime, protocol: dict[str, Any]) -> tuple[set[str], list[str]]:
    issues: list[str] = []
    valid: set[str] = set()
    if collected_at > prediction_as_of or (prediction_as_of - collected_at).total_seconds() > 300:
        return valid, ["collection_outside_frozen_window"]
    candidates = set(protocol["collection"]["candidate_bookmakers"])
    for bookmaker in payload.get("bookmakers", []):
        book = bookmaker.get("key")
        if book not in candidates:
            continue
        for market in bookmaker.get("markets", []):
            if market.get("key") != protocol["domain"]["provider_market_key"]:
                continue
            try:
                updated = parse_utc(market["last_update"])
            except (KeyError, ValueError):
                issues.append(f"missing_or_invalid_timestamp:{book}")
                continue
            age = (prediction_as_of - updated).total_seconds()
            if age < 0 or age > protocol["collection"]["max_odds_age_seconds"]:
                continue
            sides: dict[float, dict[str, list[tuple[float, str]]]] = defaultdict(lambda: defaultdict(list))
            for outcome in market.get("outcomes", []):
                side = str(outcome.get("name", "")).upper()
                try:
                    point = float(outcome["point"])
                    price = float(outcome["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                if side not in {"OVER", "UNDER"} or price <= 1:
                    continue
                doubled = round(point * 2)
                if abs(point * 2 - doubled) > 1e-9 or doubled % 2 == 0:
                    continue
                sides[point][side].append((price, iso_utc(updated)))
            for point, selections in sides.items():
                point_conflict = False
                for side, values in selections.items():
                    prices_by_time: dict[str, set[float]] = defaultdict(set)
                    for price, timestamp in values:
                        prices_by_time[timestamp].add(price)
                    if any(len(prices) > 1 for prices in prices_by_time.values()):
                        issues.append(f"conflicting_duplicate:{book}:{point}:{side}")
                        point_conflict = True
                if not point_conflict and selections.get("OVER") and selections.get("UNDER"):
                    valid.add(book)
    return valid, issues


def audit(cohort_path: Path, collections_root: Path, protocol_path: Path, output_path: Path, audited_at: str | None = None) -> dict[str, Any]:
    protocol = load_protocol(protocol_path)
    cohort = load_json(cohort_path)
    audited_time = parse_utc(audited_at) if audited_at else datetime.now(timezone.utc)
    candidates: dict[tuple[str, int], list[tuple[datetime, Path, dict[str, Any]]]] = defaultdict(list)
    integrity_issues: list[str] = []
    for manifest_path in collections_root.rglob("execution_manifest.json") if collections_root.exists() else []:
        manifest = load_json(manifest_path)
        if manifest.get("cohort_id") != cohort["cohort_id"]:
            continue
        collected_at = parse_utc(manifest["execution_timestamp_utc"])
        for request in manifest.get("odds_requests", []):
            if request.get("status") != "COLLECTED":
                continue
            raw_path = manifest_path.parent / request["raw_path"]
            if not raw_path.exists() or sha256_bytes(raw_path.read_bytes()) != request["raw_sha256"]:
                integrity_issues.append(f"raw_hash_mismatch:{raw_path}")
                continue
            candidates[(request["fixture_id"], int(request["horizon_minutes"]))].append((collected_at, raw_path, request))
    cells = []
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
            issues = []
            if selected:
                payload = json.loads(selected[1].read_bytes())
                books, issues = valid_books(payload, prediction_as_of, selected[0], protocol)
                integrity_issues.extend(f"{fixture['fixture_id']}:T{horizon}:{issue}" for issue in issues)
            is_due = audited_time >= prediction_as_of
            if is_due:
                denominators[(fixture["competition"], horizon)] += 1
                for book in books:
                    book_covered[(fixture["competition"], horizon, book)] += 1
            cells.append({
                "fixture_id": fixture["fixture_id"], "competition": fixture["competition"],
                "horizon_minutes": horizon, "prediction_as_of_utc": iso_utc(prediction_as_of),
                "snapshot_found": selected is not None, "valid_bookmakers": sorted(books),
                "covered": bool(books) if is_due else None,
                "evaluation_status": "COVERED" if is_due and books else "UNCOVERED" if is_due else "NOT_YET_DUE",
                "issues": issues,
            })
    summaries = []
    admitted: dict[str, list[str]] = defaultdict(list)
    for competition in sorted({fixture["competition"] for fixture in cohort["fixtures"]}):
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
            status = "NOT_YET_EVALUABLE" if rate is None else "PASS" if rate >= 0.9 else "PARTIAL" if rate >= 0.7 else "FAIL"
            summaries.append({"competition": competition, "horizon_minutes": horizon, "registered": len(relevant), "eligible_due": len(due), "covered": covered, "coverage_rate": rate, "gate_status": status})
    minimum = protocol["denominator"]["minimum_completed_fixtures_per_competition_for_final_review"]
    collection_complete = bool(cohort["fixtures"]) and audited_time > max(parse_utc(fixture["kickoff_utc"]) for fixture in cohort["fixtures"])
    minimum_ready = bool(summaries) and collection_complete and all(row["registered"] >= minimum for row in summaries)
    gates_pass = minimum_ready and all(row["gate_status"] == "PASS" for row in summaries) and all(admitted.get(comp) for comp in {row["competition"] for row in summaries})
    result = {
        "protocol_version": protocol["protocol_version"], "cohort_id": cohort["cohort_id"],
        "audited_at_utc": iso_utc(audited_time), "operating_mode": "SHADOW",
        "eligible_fixtures": cohort["eligible_fixture_total"], "cells": cells,
        "coverage_summary": summaries, "admitted_bookmakers_diagnostic": dict(admitted),
        "minimum_denominator_ready": minimum_ready, "collection_window_complete": collection_complete,
        "integrity_status": "PASS" if not integrity_issues else "FAIL", "integrity_issues": sorted(set(integrity_issues)),
        "step_h_status": "ELIGIBLE_FOR_FORMAL_REVIEW" if gates_pass and not integrity_issues else "OPEN_ACCUMULATING_PROSPECTIVE_EVIDENCE",
        "step_i_allowed": False, "paid_access_used": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(canonical_bytes(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    commands = parser.add_subparsers(dest="command", required=True)
    enroll_parser = commands.add_parser("enroll")
    enroll_parser.add_argument("--output-root", type=Path, default=ROOT / "raw" / "prospective_v3_1" / "cohorts")
    enroll_parser.add_argument("--fetched-at")
    enroll_parser.add_argument("--source-file", type=Path)
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--cohort", type=Path, required=True)
    collect_parser.add_argument("--output-root", type=Path, default=ROOT / "raw" / "prospective_v3_1" / "collections")
    collect_parser.add_argument("--execution-timestamp")
    audit_parser = commands.add_parser("audit")
    audit_parser.add_argument("--cohort", type=Path, required=True)
    audit_parser.add_argument("--collections-root", type=Path, default=ROOT / "raw" / "prospective_v3_1" / "collections")
    audit_parser.add_argument("--output", type=Path, default=ROOT / "reports" / "odds_prospective_v3_1_2_result.json")
    audit_parser.add_argument("--audited-at")
    args = parser.parse_args()
    if args.command == "enroll":
        path = enroll(args.protocol, args.output_root, args.fetched_at, args.source_file)
        cohort = load_json(path / "cohort.json")
        print(f"COHORT_ID={cohort['cohort_id']}")
        print(f"ELIGIBLE_FIXTURES={cohort['eligible_fixture_total']}")
        print("TARGET_ODDS_OBSERVED=FALSE")
        return 0
    if args.command == "collect":
        path = collect(args.cohort, args.protocol, args.output_root, args.execution_timestamp)
        manifest = load_json(path)
        print(f"RUN_ID={manifest['run_id']}")
        print(f"DUE_TASKS={manifest['due_tasks']}")
        print(f"STATUS={manifest['status']}")
        return 0 if manifest["status"] in {"NO_DUE_TASKS", "COLLECTION_COMPLETE"} else 2
    result = audit(args.cohort, args.collections_root, args.protocol, args.output, args.audited_at)
    print(f"COHORT_ID={result['cohort_id']}")
    print(f"INTEGRITY={result['integrity_status']}")
    print(f"STEP_H={result['step_h_status']}")
    print("STEP_I_ALLOWED=FALSE")
    return 0 if result["integrity_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
