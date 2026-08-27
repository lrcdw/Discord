#!/usr/bin/env python3
import csv, gzip, hashlib, json, os, re, time, unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "h14_runtime"
RAW = OUT / "raw"
FDRAW = OUT / "football_data_raw"
RAW.mkdir(parents=True, exist_ok=True)
FDRAW.mkdir(parents=True, exist_ok=True)

API = "https://api.the-odds-api.com/v4"
KEY = os.environ.get("THE_ODDS_API_KEY", "").strip()
if not KEY:
    raise SystemExit("FAIL_CLOSED: THE_ODDS_API_KEY missing")

PROTOCOL = ROOT / "h14" / "theoddsapi_h14_protocol_pre_registered.json"
PRECHECK = ROOT / "h14" / "denominator_precheck_202602.json"
if not PROTOCOL.exists() or not PRECHECK.exists():
    raise SystemExit("FAIL_CLOSED: frozen H1.4 inputs missing")
protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
precheck = json.loads(PRECHECK.read_text(encoding="utf-8"))

EXPECTED = {
    "Premier League": 42,
    "Championship": 62,
    "Bundesliga": 35,
    "LaLiga": 40,
}
SOURCES = {
    "Premier League": ("E0", "soccer_epl"),
    "Championship": ("E1", "soccer_efl_champ"),
    "Bundesliga": ("D1", "soccer_germany_bundesliga"),
    "LaLiga": ("SP1", "soccer_spain_la_liga"),
}
BOOKS = protocol["candidate_bookmakers"]["keys"]
BOOKS_PARAM = ",".join(BOOKS)
MARKETS_PARAM = "alternate_totals_corners,alternate_team_totals_corners"
HORIZONS = [60, 30, 15]
START = datetime(2026, 2, 1, tzinfo=timezone.utc)
END = datetime(2026, 3, 1, tzinfo=timezone.utc)

sess = requests.Session()
usage_rows = []
raw_rows = []


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def save_raw(name, body, meta):
    p = RAW / f"{name}.json.gz"
    p.write_bytes(gzip.compress(body, compresslevel=9))
    raw_rows.append({"file": str(p.relative_to(OUT)), "sha256_uncompressed": sha256_bytes(body), **meta})


def api_get(path, params, raw_name, max_retries=6):
    params = dict(params)
    params["apiKey"] = KEY
    for attempt in range(max_retries):
        r = sess.get(API + path, params=params, timeout=90)
        usage_rows.append({
            "raw_name": raw_name,
            "status": r.status_code,
            "x_requests_remaining": r.headers.get("x-requests-remaining", ""),
            "x_requests_used": r.headers.get("x-requests-used", ""),
            "x_requests_last": r.headers.get("x-requests-last", ""),
        })
        save_raw(raw_name, r.content, {"http_status": r.status_code, "endpoint": path})
        if r.status_code == 429:
            time.sleep(min(30, 2 ** attempt))
            continue
        if r.status_code != 200:
            try:
                msg = r.json()
            except Exception:
                msg = r.text[:500]
            raise RuntimeError(f"API_FAIL {r.status_code} {path}: {msg}")
        return r.json(), r.headers
    raise RuntimeError(f"API_FAIL repeated 429: {path}")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    aliases = {
        "man united": "manchester united", "man city": "manchester city",
        "nott m forest": "nottingham forest", "newcastle": "newcastle united",
        "tottenham": "tottenham hotspur", "wolves": "wolverhampton wanderers",
        "west ham": "west ham united", "brighton": "brighton and hove albion",
        "bournemouth": "bournemouth", "sheff united": "sheffield united",
        "sheff wed": "sheffield wednesday", "qpr": "queens park rangers",
        "west brom": "west bromwich albion", "leicester": "leicester city",
        "norwich": "norwich city", "coventry": "coventry city",
        "birmingham": "birmingham city", "swansea": "swansea city",
        "bristol city": "bristol city", "stoke": "stoke city",
        "hull": "hull city", "ipswich": "ipswich town",
        "preston": "preston north end", "blackburn": "blackburn rovers",
        "derby": "derby county", "charlton": "charlton athletic",
        "middlesbrough": "middlesbrough", "millwall": "millwall",
        "portsmouth": "portsmouth", "watford": "watford", "wrexham": "wrexham",
        "oxford": "oxford united", "southampton": "southampton",
        "dortmund": "borussia dortmund", "leverkusen": "bayer leverkusen",
        "m gladbach": "borussia monchengladbach", "monchengladbach": "borussia monchengladbach",
        "frankfurt": "eintracht frankfurt", "stuttgart": "vfb stuttgart",
        "bayern munich": "bayern munich", "bayern": "bayern munich",
        "koln": "koln", "cologne": "koln", "mainz": "mainz",
        "freiburg": "freiburg", "werder": "werder bremen", "werder bremen": "werder bremen",
        "union berlin": "union berlin", "augsburg": "augsburg",
        "wolfsburg": "wolfsburg", "hoffenheim": "hoffenheim",
        "st pauli": "st pauli", "heidenheim": "heidenheim",
        "hamburg": "hamburg", "rb leipzig": "rb leipzig", "leipzig": "rb leipzig",
        "ath bilbao": "athletic club", "athletic bilbao": "athletic club", "athletic club": "athletic club",
        "ath madrid": "atletico madrid", "atletico madrid": "atletico madrid",
        "betis": "real betis", "real betis": "real betis",
        "sociedad": "real sociedad", "real sociedad": "real sociedad",
        "mallorca": "mallorca", "real mallorca": "mallorca",
        "oviedo": "real oviedo", "real oviedo": "real oviedo",
        "celta": "celta vigo", "celta vigo": "celta vigo",
        "alaves": "alaves", "rayo vallecano": "rayo vallecano",
        "real madrid": "real madrid", "barcelona": "barcelona", "sevilla": "sevilla",
        "valencia": "valencia", "villarreal": "villarreal", "espanyol": "espanyol",
        "getafe": "getafe", "girona": "girona", "levante": "levante", "elche": "elche", "osasuna": "osasuna",
    }
    if s in aliases:
        return aliases[s]
    toks = [t for t in s.split() if t not in {"fc", "cf", "afc", "club", "de", "the"}]
    return " ".join(toks)


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError(s)


def download_denominator():
    fixtures = []
    for comp, (code, sport) in SOURCES.items():
        url = f"https://www.football-data.co.uk/mmz4281/2526/{code}.csv"
        r = sess.get(url, timeout=90)
        if r.status_code != 200:
            raise RuntimeError(f"FOOTBALL_DATA_FAIL {comp} {r.status_code}")
        body = r.content
        p = FDRAW / f"{code}_2526.csv"
        p.write_bytes(body)
        text = body.decode("utf-8-sig", errors="replace")
        rows = list(csv.DictReader(text.splitlines()))
        n = 0
        for row in rows:
            if not row.get("Date") or row.get("FTHG", "") == "" or row.get("FTAG", "") == "":
                continue
            d = parse_date(row["Date"])
            if START <= d < END:
                n += 1
                fixtures.append({
                    "competition": comp, "sport_key": sport, "date": d.date().isoformat(),
                    "home_team": row["HomeTeam"], "away_team": row["AwayTeam"],
                    "home_norm": norm(row["HomeTeam"]), "away_norm": norm(row["AwayTeam"]),
                    "fthg": row["FTHG"], "ftag": row["FTAG"],
                    "source_file": p.name, "source_sha256": sha256_bytes(body),
                })
        if n != EXPECTED[comp]:
            raise RuntimeError(f"DENOMINATOR_COUNT_MISMATCH {comp}: expected {EXPECTED[comp]} got {n}")
    if len(fixtures) != 179:
        raise RuntimeError(f"DENOMINATOR_TOTAL_MISMATCH {len(fixtures)}")
    fixtures.sort(key=lambda x: (x["competition"], x["date"], x["home_norm"], x["away_norm"]))
    with (OUT / "eligible_fixtures_H14.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fixtures[0].keys()); w.writeheader(); w.writerows(fixtures)
    return fixtures


def resolve_events(fixtures):
    by_cd = defaultdict(list)
    for fx in fixtures:
        by_cd[(fx["competition"], fx["date"])].append(fx)
    event_pool = defaultdict(dict)
    for (comp, day), fxs in sorted(by_cd.items()):
        sport = SOURCES[comp][1]
        for hour in (0, 6, 12):
            dt = f"{day}T{hour:02d}:00:00Z"
            data, _ = api_get(f"/historical/sports/{sport}/events", {"date": dt}, f"events_{sport}_{day}_{hour:02d}")
            for e in data.get("data", []):
                key = (norm(e.get("home_team")), norm(e.get("away_team")))
                event_pool[(comp, day)][key] = e
    unresolved = []
    for fx in fixtures:
        e = event_pool[(fx["competition"], fx["date"])].get((fx["home_norm"], fx["away_norm"]))
        if e:
            fx["event_id"] = e["id"]
            fx["commence_time"] = e["commence_time"]
            fx["provider_home_team"] = e.get("home_team", "")
            fx["provider_away_team"] = e.get("away_team", "")
        else:
            fx["event_id"] = ""
            fx["commence_time"] = ""
            fx["provider_home_team"] = ""
            fx["provider_away_team"] = ""
            unresolved.append(fx)
    with (OUT / "event_resolution.csv").open("w", newline="", encoding="utf-8") as f:
        fields = list(fixtures[0].keys())
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(fixtures)
    return unresolved


def valid_pairs_for_book(book, market_key, team_norm, snapshot_time, asof):
    markets = {m.get("key"): m for m in book.get("markets", [])}
    m = markets.get(market_key)
    if not m:
        return False
    obs_time = snapshot_time
    if obs_time > asof or (asof - obs_time).total_seconds() > 300:
        return False
    sides = defaultdict(set)
    for o in m.get("outcomes", []):
        name = (o.get("name") or "").lower()
        if name not in {"over", "under"}:
            continue
        try:
            point = float(o.get("point")); price = float(o.get("price"))
        except Exception:
            continue
        if price <= 1 or abs(point * 2 - round(point * 2)) > 1e-9 or int(round(point * 2)) % 2 == 0:
            continue
        if team_norm is not None:
            desc = norm(o.get("description", ""))
            if desc != team_norm:
                continue
        sides[point].add(name)
    return any(v == {"over", "under"} for v in sides.values())


def run_odds(fixtures):
    cell = defaultdict(lambda: {"den": 0, "covered": 0})
    bookcell = defaultdict(lambda: {"den": 0, "covered": 0})
    observation_rows = []
    for idx, fx in enumerate(fixtures, 1):
        comp = fx["competition"]
        for market in ("TOTAL_CORNERS", "HOME_TEAM_TOTAL_CORNERS", "AWAY_TEAM_TOTAL_CORNERS"):
            for h in HORIZONS:
                cell[(comp, market, h)]["den"] += 1
                for b in BOOKS:
                    bookcell[(comp, market, h, b)]["den"] += 1
        if not fx["event_id"]:
            continue
        kickoff = datetime.fromisoformat(fx["commence_time"].replace("Z", "+00:00"))
        for h in HORIZONS:
            asof = kickoff - timedelta(minutes=h)
            stamp = asof.strftime("%Y-%m-%dT%H:%M:%SZ")
            data, headers = api_get(
                f"/historical/sports/{fx['sport_key']}/events/{fx['event_id']}/odds",
                {"date": stamp, "bookmakers": BOOKS_PARAM, "markets": MARKETS_PARAM, "oddsFormat": "decimal", "dateFormat": "iso"},
                f"odds_{fx['sport_key']}_{fx['event_id']}_T{h}",
            )
            snap = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
            payload = data.get("data") or {}
            books = {b.get("key"): b for b in payload.get("bookmakers", [])}
            contract_any = {"TOTAL_CORNERS": False, "HOME_TEAM_TOTAL_CORNERS": False, "AWAY_TEAM_TOTAL_CORNERS": False}
            for bkey in BOOKS:
                book = books.get(bkey, {})
                vals = {
                    "TOTAL_CORNERS": valid_pairs_for_book(book, "alternate_totals_corners", None, snap, asof),
                    "HOME_TEAM_TOTAL_CORNERS": valid_pairs_for_book(book, "alternate_team_totals_corners", fx["home_norm"], snap, asof),
                    "AWAY_TEAM_TOTAL_CORNERS": valid_pairs_for_book(book, "alternate_team_totals_corners", fx["away_norm"], snap, asof),
                }
                for market, ok in vals.items():
                    if ok:
                        bookcell[(comp, market, h, bkey)]["covered"] += 1
                        contract_any[market] = True
                    observation_rows.append({"competition": comp, "date": fx["date"], "event_id": fx["event_id"], "horizon": h, "market": market, "bookmaker": bkey, "covered": int(ok), "snapshot_timestamp": data["timestamp"], "prediction_as_of": stamp})
            for market, ok in contract_any.items():
                if ok:
                    cell[(comp, market, h)]["covered"] += 1
        print(f"PROGRESS {idx}/{len(fixtures)}", flush=True)
    with (OUT / "bookmaker_fixture_observations.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=observation_rows[0].keys()); w.writeheader(); w.writerows(observation_rows)
    return cell, bookcell


def summarize(fixtures, unresolved, cell, bookcell):
    cells = []
    for (comp, market, h), x in sorted(cell.items()):
        rate = x["covered"] / x["den"] if x["den"] else 0
        status = "PASS" if rate >= 0.90 else ("PARTIAL" if rate >= 0.70 else "FAIL_COVERAGE")
        cells.append({"competition": comp, "market": market, "horizon": h, "denominator": x["den"], "covered": x["covered"], "coverage_rate": f"{rate:.6f}", "status": status})
    bstats = []
    admitted = defaultdict(list)
    for (comp, market, h, b), x in sorted(bookcell.items()):
        rate = x["covered"] / x["den"] if x["den"] else 0
        bstats.append({"competition": comp, "market": market, "horizon": h, "bookmaker": b, "denominator": x["den"], "covered": x["covered"], "coverage_rate": f"{rate:.6f}"})
    for comp in EXPECTED:
        for market in ("TOTAL_CORNERS", "HOME_TEAM_TOTAL_CORNERS", "AWAY_TEAM_TOTAL_CORNERS"):
            for b in BOOKS:
                rates = [next(float(r["coverage_rate"]) for r in bstats if r["competition"] == comp and r["market"] == market and r["horizon"] == h and r["bookmaker"] == b) for h in HORIZONS]
                if min(rates) >= 0.80:
                    admitted[(comp, market)].append(b)
    contracts = []
    for comp in EXPECTED:
        for market in ("TOTAL_CORNERS", "HOME_TEAM_TOTAL_CORNERS", "AWAY_TEAM_TOTAL_CORNERS"):
            crows = [r for r in cells if r["competition"] == comp and r["market"] == market]
            passed = all(r["status"] == "PASS" for r in crows) and bool(admitted[(comp, market)])
            contracts.append({"competition": comp, "market": market, "pass": passed, "admitted_bookmakers": ";".join(admitted[(comp, market)])})
    for name, rows in (("coverage_cells.csv", cells), ("bookmaker_stats.csv", bstats), ("contract_gate.csv", contracts)):
        with (OUT / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    final_pass = any(r["pass"] for r in contracts)
    summary = {
        "protocol_version": protocol["protocol_version"],
        "denominator": len(fixtures),
        "unresolved_provider_events": len(unresolved),
        "pass_cells": sum(r["status"] == "PASS" for r in cells),
        "partial_cells": sum(r["status"] == "PARTIAL" for r in cells),
        "fail_cells": sum(r["status"] == "FAIL_COVERAGE" for r in cells),
        "pass_contracts": sum(bool(r["pass"]) for r in contracts),
        "H14_MARKET_GATE": "PASS" if final_pass else "FAIL",
        "STEP_I_ALLOWED": bool(final_pass),
        "thresholds_unchanged": True,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def write_manifests():
    with (OUT / "raw_manifest.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["file", "sha256_uncompressed", "http_status", "endpoint"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(raw_rows)
    with (OUT / "usage.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["raw_name", "status", "x_requests_remaining", "x_requests_used", "x_requests_last"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(usage_rows)
    secret = KEY.encode()
    for p in OUT.rglob("*"):
        if p.is_file() and secret in p.read_bytes():
            raise RuntimeError(f"CREDENTIAL_LEAK {p}")


def main():
    # Verify the pre-odds protocol and denominator-precheck have not been edited after their freeze commits.
    import subprocess
    subprocess.run(["git", "diff", "--exit-code", "3db986b2c8c92124fa923ac9cec68fc01c59bf33", "--", "h14/theoddsapi_h14_protocol_pre_registered.json"], cwd=ROOT, check=True)
    subprocess.run(["git", "diff", "--exit-code", "5ad2fb66f12376218b9046c431a3dccdfc3297e7", "--", "h14/denominator_precheck_202602.json"], cwd=ROOT, check=True)

    # Cheap authentication/quota check. /sports does not consume quota.
    _, hdr = api_get("/sports", {}, "sports_auth_check")
    rem = hdr.get("x-requests-remaining")
    if rem not in (None, "") and int(rem) < 10919:
        raise RuntimeError(f"FAIL_CLOSED_INSUFFICIENT_QUOTA remaining={rem} required_ceiling=10919")

    fixtures = download_denominator()
    unresolved = resolve_events(fixtures)
    cell, bookcell = run_odds(fixtures)
    summary = summarize(fixtures, unresolved, cell, bookcell)
    write_manifests()
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        # Never persist the credential.
        KEY = ""
