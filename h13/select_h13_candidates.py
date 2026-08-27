#!/usr/bin/env python3
from pathlib import Path
import argparse, csv, gzip, hashlib, json
from collections import defaultdict
from datetime import datetime, timezone

FAMILIES = ("TOTAL_CORNERS","HOME_TEAM_TOTAL_CORNERS","AWAY_TEAM_TOTAL_CORNERS")

def parse_dt(x):
    if not x:
        return None
    try:
        d=datetime.fromisoformat(str(x).strip().replace("Z","+00:00"))
        if d.tzinfo is None:
            d=d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

def lookup(d,k):
    if not isinstance(d,dict):
        return None
    if k in d: return d[k]
    s=str(k)
    if s in d: return d[s]
    try:
        i=int(k)
        if i in d: return d[i]
    except Exception:
        pass
    return None

def snaps(hist, book, market_id, outcome_id):
    bm=lookup(hist.get("bookmakers") or {},book)
    if not isinstance(bm,dict): return []
    md=lookup(bm.get("markets") or {},market_id)
    if not isinstance(md,dict): return []
    od=lookup(md.get("outcomes") or {},outcome_id)
    if not isinstance(od,dict): return []
    arr=lookup(od.get("players") or {},"0")
    return arr if isinstance(arr,list) else []

def write_csv(path, rows, fields):
    with open(path,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

def sha256_file(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--discovery-dir",required=True)
    ap.add_argument("--market-catalog",required=True)
    ap.add_argument("--protocol",required=True)
    ap.add_argument("--out-dir",required=True)
    args=ap.parse_args()

    root=Path(args.discovery_dir)
    out=Path(args.out_dir); out.mkdir(parents=True,exist_ok=True)
    proto=json.loads(Path(args.protocol).read_text(encoding="utf-8"))
    allowed=set(proto["discovery_data"]["allowed_candidate_slugs"])
    groups=proto["candidate_selection"]["correlation_groups"]
    max_books=int(proto["candidate_selection"]["max_selected_bookmakers_per_competition_market"])

    group_of={}
    for g,books in groups.items():
        for b in books:
            if b in group_of:
                raise SystemExit(f"BOOK_IN_MULTIPLE_GROUPS:{b}")
            group_of[b]=g
    if not allowed.issubset(group_of):
        raise SystemExit("ALLOWED_BOOK_WITHOUT_CORRELATION_GROUP")

    markets=[]
    with open(args.market_catalog,encoding="utf-8") as f:
        for r in csv.DictReader(f):
            fam=r["market_family"]
            if fam not in FAMILIES: continue
            line=float(r["line"])
            if abs((line%1)-0.5)>1e-9:
                raise SystemExit("NON_HALF_POINT_MARKET")
            markets.append({
                "market_id":int(r["market_id"]),
                "market_family":fam,
                "line":line,
                "over_outcome_id":int(r["over_outcome_id"]),
                "under_outcome_id":int(r["under_outcome_id"]),
            })
    byfam=defaultdict(list)
    for m in markets: byfam[m["market_family"]].append(m)
    if any(not byfam[f] for f in FAMILIES):
        raise SystemExit("MISSING_TARGET_MARKET_FAMILY")

    manifest_path=root/"manifest.csv"
    manifest=list(csv.DictReader(manifest_path.open(encoding="utf-8")))
    if not manifest:
        raise SystemExit("EMPTY_DISCOVERY_MANIFEST")

    hist_by_comp_book={}
    raw_hashes=[]
    for r in manifest:
        comp=r["competition"]
        p=root/r["file"]
        body=gzip.open(p,"rb").read()
        raw_hashes.append({"competition":comp,"file":r["file"],"sha256_uncompressed":hashlib.sha256(body).hexdigest()})
        hist=json.loads(body)
        for book,bobj in (hist.get("bookmakers") or {}).items():
            if book in allowed:
                key=(comp,book)
                if key in hist_by_comp_book:
                    raise SystemExit(f"DUPLICATE_DISCOVERY_BOOK_PAYLOAD:{comp}:{book}")
                hist_by_comp_book[key]={"bookmakers":{book:bobj}}

    comps=sorted(proto["discovery_data"]["fixed_fixture_ids"])
    stats=[]
    for comp in comps:
        for book in sorted(allowed):
            hist=hist_by_comp_book.get((comp,book),{"bookmakers":{}})
            for fam in FAMILIES:
                two_sided=0; target=0; with_ts=0
                line_details=[]
                for m in byfam[fam]:
                    oa=snaps(hist,book,m["market_id"],m["over_outcome_id"])
                    ua=snaps(hist,book,m["market_id"],m["under_outcome_id"])
                    target += len(oa)+len(ua)
                    ots=[x for x in oa if isinstance(x,dict) and parse_dt(x.get("createdAt")) is not None]
                    uts=[x for x in ua if isinstance(x,dict) and parse_dt(x.get("createdAt")) is not None]
                    with_ts += len(ots)+len(uts)
                    if ots and uts:
                        two_sided += 1
                        line_details.append(m["line"])
                stats.append({
                    "competition":comp,
                    "market_family":fam,
                    "bookmaker":book,
                    "correlation_group":group_of[book],
                    "two_sided_line_count":two_sided,
                    "target_snapshot_count":target,
                    "timestamped_snapshot_count":with_ts,
                    "timestamp_complete_rate":1.0 if target==0 else with_ts/target,
                    "eligible":bool(two_sided>=1),
                    "two_sided_lines":";".join(str(x) for x in sorted(line_details)) if line_details else None,
                })

    selected=[]
    for comp in comps:
        for fam in FAMILIES:
            pool=[r for r in stats if r["competition"]==comp and r["market_family"]==fam and r["eligible"]]
            reps=[]
            for g in groups:
                gp=[r for r in pool if r["correlation_group"]==g]
                if not gp: continue
                gp.sort(key=lambda r:(-r["two_sided_line_count"],-r["target_snapshot_count"],r["bookmaker"]))
                reps.append(gp[0])
            reps.sort(key=lambda r:(-r["two_sided_line_count"],-r["target_snapshot_count"],r["bookmaker"]))
            chosen=reps[:max_books]
            for rank,r in enumerate(chosen,1):
                selected.append({
                    "competition":comp,
                    "market_family":fam,
                    "rank":rank,
                    "bookmaker":r["bookmaker"],
                    "correlation_group":r["correlation_group"],
                    "discovery_two_sided_line_count":r["two_sided_line_count"],
                    "discovery_target_snapshot_count":r["target_snapshot_count"],
                })

    stats_fields=["competition","market_family","bookmaker","correlation_group","two_sided_line_count","target_snapshot_count","timestamped_snapshot_count","timestamp_complete_rate","eligible","two_sided_lines"]
    sel_fields=["competition","market_family","rank","bookmaker","correlation_group","discovery_two_sided_line_count","discovery_target_snapshot_count"]
    write_csv(out/"discovery_book_stats.csv",stats,stats_fields)
    write_csv(out/"candidate_selection.csv",selected,sel_fields)

    summary={}
    for comp in comps:
        summary[comp]={}
        for fam in FAMILIES:
            summary[comp][fam]=[r["bookmaker"] for r in selected if r["competition"]==comp and r["market_family"]==fam]

    manifest_out={
        "selection_protocol_version":proto["protocol_version"],
        "protocol_sha256":sha256_file(args.protocol),
        "market_catalog_sha256":sha256_file(args.market_catalog),
        "discovery_manifest_sha256":sha256_file(manifest_path),
        "raw_files":raw_hashes,
        "selected":summary,
        "manual_override_used":False,
    }
    (out/"candidate_selection_manifest.json").write_text(json.dumps(manifest_out,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))

if __name__=="__main__":
    main()
