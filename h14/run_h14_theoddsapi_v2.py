#!/usr/bin/env python3
"""Pre-observation hardening wrapper for H1.4.

This file changes only provider-team normalization and event-ID resolution robustness.
It does not change the frozen H1.4 denominator, bookmakers, markets, horizons,
coverage thresholds, pair rules, or promotion criteria.
"""
import importlib.util
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).with_name("run_h14_theoddsapi.py")
spec = importlib.util.spec_from_file_location("h14_base_runner", BASE)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def norm(s):
    raw = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    raw = raw.replace("&", " and ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw).strip()

    aliases = {
        # England
        "man united": "manchester united",
        "manchester utd": "manchester united",
        "man city": "manchester city",
        "nott m forest": "nottingham forest",
        "nottm forest": "nottingham forest",
        "newcastle": "newcastle united",
        "tottenham": "tottenham hotspur",
        "wolves": "wolverhampton wanderers",
        "west ham": "west ham united",
        "brighton": "brighton and hove albion",
        "sheff united": "sheffield united",
        "sheffield utd": "sheffield united",
        "sheff wed": "sheffield wednesday",
        "sheff weds": "sheffield wednesday",
        "sheffield wed": "sheffield wednesday",
        "sheffield weds": "sheffield wednesday",
        "qpr": "queens park rangers",
        "west brom": "west bromwich albion",
        "leicester": "leicester city",
        "norwich": "norwich city",
        "coventry": "coventry city",
        "birmingham": "birmingham city",
        "swansea": "swansea city",
        "stoke": "stoke city",
        "hull": "hull city",
        "ipswich": "ipswich town",
        "preston": "preston north end",
        "blackburn": "blackburn rovers",
        "derby": "derby county",
        "charlton": "charlton athletic",
        "oxford": "oxford united",
        # Germany
        "dortmund": "borussia dortmund",
        "borussia dortmund": "borussia dortmund",
        "leverkusen": "bayer leverkusen",
        "bayer 04 leverkusen": "bayer leverkusen",
        "bayer leverkusen": "bayer leverkusen",
        "m gladbach": "borussia monchengladbach",
        "monchengladbach": "borussia monchengladbach",
        "borussia monchengladbach": "borussia monchengladbach",
        "frankfurt": "eintracht frankfurt",
        "ein frankfurt": "eintracht frankfurt",
        "eintracht frankfurt": "eintracht frankfurt",
        "stuttgart": "vfb stuttgart",
        "vfb stuttgart": "vfb stuttgart",
        "bayern": "bayern munich",
        "bayern munich": "bayern munich",
        "bayern munchen": "bayern munich",
        "koln": "koln",
        "cologne": "koln",
        "1 fc koln": "koln",
        "fc koln": "koln",
        "1 fc cologne": "koln",
        "fc cologne": "koln",
        "mainz": "mainz",
        "mainz 05": "mainz",
        "fsv mainz 05": "mainz",
        "1 fsv mainz 05": "mainz",
        "freiburg": "freiburg",
        "sc freiburg": "freiburg",
        "werder": "werder bremen",
        "werder bremen": "werder bremen",
        "sv werder bremen": "werder bremen",
        "union berlin": "union berlin",
        "1 fc union berlin": "union berlin",
        "augsburg": "augsburg",
        "fc augsburg": "augsburg",
        "wolfsburg": "wolfsburg",
        "vfl wolfsburg": "wolfsburg",
        "hoffenheim": "hoffenheim",
        "tsg hoffenheim": "hoffenheim",
        "tsg 1899 hoffenheim": "hoffenheim",
        "st pauli": "st pauli",
        "fc st pauli": "st pauli",
        "heidenheim": "heidenheim",
        "heidenheim 1846": "heidenheim",
        "1 fc heidenheim 1846": "heidenheim",
        "hamburg": "hamburg",
        "hamburger sv": "hamburg",
        "rb leipzig": "rb leipzig",
        "leipzig": "rb leipzig",
        # Spain
        "ath bilbao": "athletic club",
        "athletic bilbao": "athletic club",
        "athletic club": "athletic club",
        "ath madrid": "atletico madrid",
        "atletico madrid": "atletico madrid",
        "betis": "real betis",
        "real betis": "real betis",
        "sociedad": "real sociedad",
        "real sociedad": "real sociedad",
        "mallorca": "mallorca",
        "real mallorca": "mallorca",
        "rcd mallorca": "mallorca",
        "oviedo": "real oviedo",
        "real oviedo": "real oviedo",
        "celta": "celta vigo",
        "celta vigo": "celta vigo",
        "celta de vigo": "celta vigo",
        "rc celta de vigo": "celta vigo",
        "alaves": "alaves",
        "deportivo alaves": "alaves",
        "rayo vallecano": "rayo vallecano",
        "espanyol": "espanyol",
        "rcd espanyol": "espanyol",
    }
    if raw in aliases:
        return aliases[raw]

    # Provider prefixes/suffixes are not football identity. Clean them only for matching,
    # then apply the aliases again. Numbers are preserved unless explicitly aliased above.
    toks = [t for t in raw.split() if t not in {"fc", "cf", "afc", "club", "de", "the"}]
    cleaned = " ".join(toks)
    return aliases.get(cleaned, cleaned)


r.norm = norm


def resolve_events(fixtures):
    """Resolve IDs without odds information and without removing unresolved fixtures.

    Two fixed same-day historical event snapshots (06:00 and 12:00 UTC) are used.
    This remains below the pre-registered conservative event-resolution quota ceiling.
    """
    by_cd = defaultdict(list)
    for fx in fixtures:
        by_cd[(fx["competition"], fx["date"])].append(fx)

    event_pool = defaultdict(dict)
    for (comp, day), fxs in sorted(by_cd.items()):
        sport = r.SOURCES[comp][1]
        for hour in (6, 12):
            dt = f"{day}T{hour:02d}:00:00Z"
            data, _ = r.api_get(
                f"/historical/sports/{sport}/events",
                {"date": dt},
                f"events_{sport}_{day}_{hour:02d}",
            )
            for e in data.get("data", []):
                key = (norm(e.get("home_team")), norm(e.get("away_team")))
                event_pool[(comp, day)][key] = e

    unresolved = []
    for fx in fixtures:
        e = event_pool[(fx["competition"], fx["date"])].get(
            (norm(fx["home_team"]), norm(fx["away_team"]))
        )
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

    with (r.OUT / "event_resolution.csv").open("w", newline="", encoding="utf-8") as f:
        fields = list(fixtures[0].keys())
        w = r.csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(fixtures)
    return unresolved


r.resolve_events = resolve_events

if __name__ == "__main__":
    r.main()
