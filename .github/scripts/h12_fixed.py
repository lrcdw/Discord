from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter
import csv, gzip, hashlib, json, math, statistics, time
import requests

OUT=Path('h12_output'); RAW=OUT/'raw_historical'; OUT.mkdir(exist_ok=True); RAW.mkdir(exist_ok=True)
INPUT=Path('audit_input')
KEY=Path('/tmp/api_key.txt').read_text().strip()
if not KEY: raise SystemExit('EMPTY_KEY')
BASE='https://api.oddspapi.io/v4'
BOOKS=['pinnacle','bet365','singbet']
FAMILIES=['TOTAL_CORNERS','HOME_TEAM_TOTAL_CORNERS','AWAY_TEAM_TOTAL_CORNERS']
HORIZONS=[60,30,15]
MAX_AGE=300.0
PASS_RATE=0.90; PARTIAL_FLOOR=0.70; BOOK_ADMIT=0.80; MIN_N=25
EXPECTED_FIXTURE_SHA='daf4b7ae5cdadd0543667de0272545514c2a132f5a63ce7fc23439c6b41ff5e9'
EXPECTED_MARKET_SHA='89372de065291dc5d45768643d27a62cb635fa998b43a0d8318bbbc6cc26c6b0'

def sha(b): return hashlib.sha256(b).hexdigest()
def parsedt(x):
    if not x: return None
    try:
        d=datetime.fromisoformat(str(x).strip().replace('Z','+00:00'))
        if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception: return None
def fmt(d): return d.isoformat().replace('+00:00','Z') if d else None
def active_ok(v): return v not in (False,0,'false','False','0')
def write_csv(path, rows, fields=None):
    rows=list(rows)
    if fields is None: fields=list(rows[0].keys()) if rows else []
    with open(path,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows)
def quantile(xs,q):
    xs=sorted(xs)
    if not xs: return None
    pos=(len(xs)-1)*q; lo=math.floor(pos); hi=math.ceil(pos)
    if lo==hi: return xs[lo]
    return xs[lo]*(hi-pos)+xs[hi]*(pos-lo)

fixture_bytes=(INPUT/'eligible_fixtures.csv').read_bytes(); market_bytes=(INPUT/'market_catalog_target.csv').read_bytes()
if sha(fixture_bytes)!=EXPECTED_FIXTURE_SHA: raise SystemExit('FROZEN_FIXTURE_HASH_MISMATCH')
if sha(market_bytes)!=EXPECTED_MARKET_SHA: raise SystemExit('FROZEN_MARKET_HASH_MISMATCH')
fixtures=list(csv.DictReader((INPUT/'eligible_fixtures.csv').open(encoding='utf-8')))
markets=list(csv.DictReader((INPUT/'market_catalog_target.csv').open(encoding='utf-8')))
if len(fixtures)!=179: raise SystemExit(f'FROZEN_FIXTURE_COUNT_MISMATCH {len(fixtures)}')
if len(markets)!=50: raise SystemExit(f'FROZEN_MARKET_COUNT_MISMATCH {len(markets)}')
for f in fixtures:
    f['tournament_id']=int(f['tournament_id']); f['kickoff_dt']=parsedt(f['kickoff_utc'])
    if f['kickoff_dt'] is None: raise SystemExit('BAD_FROZEN_KICKOFF')
for m in markets:
    m['market_id']=int(m['market_id']); m['line']=float(m['line']); m['over_outcome_id']=int(m['over_outcome_id']); m['under_outcome_id']=int(m['under_outcome_id'])
    if abs((m['line'] % 1)-0.5)>1e-9: raise SystemExit('NON_HALF_POINT_IN_FROZEN_MARKET')
    if m['market_family'] not in FAMILIES: raise SystemExit('BAD_MARKET_FAMILY')
markets_by_family=defaultdict(list)
for m in markets: markets_by_family[m['market_family']].append(m)
for fam in FAMILIES:
    if not markets_by_family[fam]: raise SystemExit('MISSING_FROZEN_FAMILY '+fam)
comp_tid={}
for f in fixtures:
    c=f['competition']; tid=f['tournament_id']
    if c in comp_tid and comp_tid[c]!=tid: raise SystemExit('COMP_TOURNAMENT_COLLISION')
    comp_tid[c]=tid
eligible_counts={c:sum(1 for f in fixtures if f['competition']==c) for c in comp_tid}
if set(eligible_counts)!={'Premier League','Championship','Bundesliga','LaLiga'}: raise SystemExit('COMPETITION_SET_MISMATCH '+repr(eligible_counts))
if any(v<MIN_N for v in eligible_counts.values()): raise SystemExit('FROZEN_DENOMINATOR_BELOW_MIN '+repr(eligible_counts))

sess=requests.Session(); api_calls=0; retries=0; raws=[]; all_hist={}
for idx,f in enumerate(fixtures,1):
    params={'apiKey':KEY,'fixtureId':f['fixture_id'],'bookmakers':','.join(BOOKS)}
    response=None
    for attempt in range(8):
        api_calls+=1
        try: r=sess.get(BASE+'/historical-odds',params=params,timeout=120)
        except requests.RequestException:
            retries+=1
            if attempt==7: raise
            time.sleep(min(2**attempt,20)); continue
        if r.status_code==429 or 500<=r.status_code<600:
            retries+=1
            if attempt==7: response=r; break
            time.sleep(min(2**attempt,20)); continue
        response=r; break
    if response is None or response.status_code!=200:
        status=response.status_code if response is not None else 'NONE'; body=response.text[:300] if response is not None else ''
        raise RuntimeError(f'HISTORICAL_API_HARD_FAIL fixture={f["fixture_id"]} status={status} body={body}')
    body=response.content
    try: hist=response.json()
    except Exception: raise RuntimeError('HISTORICAL_NON_JSON '+f['fixture_id'])
    if not isinstance(hist,dict): raise RuntimeError('HISTORICAL_NOT_OBJECT '+f['fixture_id'])
    all_hist[f['fixture_id']]=hist
    rel=f'raw_historical/{f["fixture_id"]}.json.gz'; p=OUT/rel
    with gzip.open(p,'wb',compresslevel=9) as gz: gz.write(body)
    raws.append({'fixture_id':f['fixture_id'],'file':rel,'sha256_uncompressed':sha(body),'uncompressed_bytes':len(body),'http_status':response.status_code,'endpoint':'/historical-odds'})
    if idx%25==0: print('HISTORICAL_PROGRESS',idx,'/',len(fixtures),flush=True)
    time.sleep(0.02)

def lookup(d,k):
    if not isinstance(d,dict): return None
    if k in d: return d[k]
    sk=str(k)
    if sk in d: return d[sk]
    try:
        ik=int(k)
        if ik in d: return d[ik]
    except Exception: pass
    return None
def snapshots(hist,book,market_id,outcome_id):
    bm=lookup(hist.get('bookmakers') or {},book)
    if not isinstance(bm,dict): return []
    md=lookup(bm.get('markets') or {},market_id)
    if not isinstance(md,dict): return []
    od=lookup(md.get('outcomes') or {},outcome_id)
    if not isinstance(od,dict): return []
    arr=lookup(od.get('players') or {},'0')
    return arr if isinstance(arr,list) else []
def latest_at(arr,asof):
    best=None; bt=None
    for x in arr:
        if not isinstance(x,dict): continue
        t=parsedt(x.get('createdAt'))
        if t is None or t>asof: continue
        if bt is None or t>bt: best=x; bt=t
    return best,bt

snapshot_stats={}; duplicate_conflicts=[]
for f in fixtures:
    hist=all_hist[f['fixture_id']]
    for book in BOOKS:
        for m in markets:
            sides=[]
            for side,oid in [('OVER',m['over_outcome_id']),('UNDER',m['under_outcome_id'])]:
                arr=snapshots(hist,book,m['market_id'],oid); sides.extend(arr); seen={}
                for x in arr:
                    if not isinstance(x,dict): continue
                    t=x.get('createdAt'); price=x.get('price')
                    if t is not None and t in seen and seen[t]!=price:
                        duplicate_conflicts.append({'fixture_id':f['fixture_id'],'bookmaker':book,'market_id':m['market_id'],'outcome':side,'created_at':t,'price_a':seen[t],'price_b':price})
                    if t is not None: seen[t]=price
            with_ts=sum(1 for x in sides if isinstance(x,dict) and parsedt(x.get('createdAt')) is not None)
            snapshot_stats[(f['fixture_id'],book,m['market_id'])]=(len(sides),with_ts)

selected=[]; event_rows=[]
for f in fixtures:
    fid=f['fixture_id']; hist=all_hist[fid]; ko=f['kickoff_dt']
    for fam in FAMILIES:
        subject=None if fam=='TOTAL_CORNERS' else (f['home'] if fam=='HOME_TEAM_TOTAL_CORNERS' else f['away'])
        for book in BOOKS:
            for hm in HORIZONS:
                asof=ko-timedelta(minutes=hm); line_rows=[]; target=withts=0
                for m in markets_by_family[fam]:
                    a,b=snapshot_stats[(fid,book,m['market_id'])]; target+=a; withts+=b
                    oa=snapshots(hist,book,m['market_id'],m['over_outcome_id']); ua=snapshots(hist,book,m['market_id'],m['under_outcome_id'])
                    os,ot=latest_at(oa,asof); us,ut=latest_at(ua,asof)
                    if os is None or us is None or ot is None or ut is None: continue
                    try: op=float(os.get('price')); up=float(us.get('price'))
                    except Exception: continue
                    if op<=1 or up<=1 or not active_ok(os.get('active',True)) or not active_ok(us.get('active',True)): continue
                    oage=(asof-ot).total_seconds(); uage=(asof-ut).total_seconds()
                    if min(oage,uage)<0 or max(oage,uage)>MAX_AGE: continue
                    row={'competition':f['competition'],'tournament_id':f['tournament_id'],'fixture_id':fid,'kickoff_utc':fmt(ko),'home':f['home'],'away':f['away'],'market_family':fam,'subject':subject,'bookmaker':book,'horizon_minutes':hm,'prediction_as_of':fmt(asof),'market_id':m['market_id'],'line':m['line'],'over_price':op,'under_price':up,'over_created_at':fmt(ot),'under_created_at':fmt(ut),'over_age_seconds':oage,'under_age_seconds':uage,'side_timestamp_difference_seconds':abs((ot-ut).total_seconds())}
                    selected.append(row); line_rows.append(row)
                ages=[z for r in line_rows for z in (r['over_age_seconds'],r['under_age_seconds'])]
                event_rows.append({'competition':f['competition'],'tournament_id':f['tournament_id'],'fixture_id':fid,'kickoff_utc':fmt(ko),'home':f['home'],'away':f['away'],'market_family':fam,'subject':subject,'bookmaker':book,'horizon_minutes':hm,'prediction_as_of':fmt(asof),'covered':bool(line_rows),'valid_pair_count':len(line_rows),'freshest_age_seconds':min(ages) if ages else None,'max_selected_side_age_seconds':max(ages) if ages else None,'line_min':min((r['line'] for r in line_rows),default=None),'line_max':max((r['line'] for r in line_rows),default=None),'target_snapshots':target,'snapshots_with_timestamp':withts})

coverage=[]; bookcells=[]
for comp,tid in comp_tid.items():
    denom=eligible_counts[comp]
    for fam in FAMILIES:
        for hm in HORIZONS:
            rr=[r for r in event_rows if r['competition']==comp and r['market_family']==fam and r['horizon_minutes']==hm]; byfid=defaultdict(list)
            for r in rr: byfid[r['fixture_id']].append(r)
            cov=sum(any(x['covered'] for x in xs) for xs in byfid.values()); target=sum(r['target_snapshots'] for r in rr); ts=sum(r['snapshots_with_timestamp'] for r in rr)
            tcr=1.0 if target==0 else ts/target; rate=cov/denom; lines=[s['line'] for s in selected if s['competition']==comp and s['market_family']==fam and s['horizon_minutes']==hm]
            status='FAIL_TEMPORAL' if tcr<1.0 else ('PASS' if rate>=PASS_RATE else ('PARTIAL' if rate>=PARTIAL_FLOOR else 'FAIL_COVERAGE'))
            coverage.append({'competition':comp,'tournament_id':tid,'market_family':fam,'horizon_minutes':hm,'eligible_fixtures':denom,'covered_any_benchmark':cov,'coverage_rate':rate,'timestamp_complete_rate':tcr,'target_snapshots':target,'observed_line_min':min(lines) if lines else None,'observed_line_max':max(lines) if lines else None,'status':status})
            for book in BOOKS:
                br=[r for r in rr if r['bookmaker']==book]; bc=sum(r['covered'] for r in br); bt=sum(r['target_snapshots'] for r in br); bts=sum(r['snapshots_with_timestamp'] for r in br)
                bookcells.append({'competition':comp,'tournament_id':tid,'market_family':fam,'horizon_minutes':hm,'bookmaker':book,'eligible_fixtures':denom,'covered_fixtures':bc,'coverage_rate':bc/denom,'timestamp_complete_rate':1.0 if bt==0 else bts/bt,'target_snapshots':bt})
admissions=[]
for comp,tid in comp_tid.items():
    for fam in FAMILIES:
        for book in BOOKS:
            cells=[r for r in bookcells if r['competition']==comp and r['market_family']==fam and r['bookmaker']==book]
            admitted=len(cells)==3 and all(r['coverage_rate']>=BOOK_ADMIT and r['timestamp_complete_rate']==1.0 for r in cells)
            admissions.append({'competition':comp,'tournament_id':tid,'market_family':fam,'bookmaker':book,'admitted':admitted,'min_horizon_coverage_rate':min((r['coverage_rate'] for r in cells),default=0),'all_timestamp_complete':all(r['timestamp_complete_rate']==1.0 for r in cells) if cells else False})
cm=[]
for comp,tid in comp_tid.items():
    for fam in FAMILIES:
        cells=[r for r in coverage if r['competition']==comp and r['market_family']==fam]
        st='PASS' if len(cells)==3 and all(r['status']=='PASS' for r in cells) else ('FAIL' if any(r['status'].startswith('FAIL') for r in cells) else 'PARTIAL')
        cm.append({'competition':comp,'tournament_id':tid,'market_family':fam,'status':st,'min_coverage_rate':min(r['coverage_rate'] for r in cells),'max_coverage_rate':max(r['coverage_rate'] for r in cells),'admitted_bookmakers':';'.join(a['bookmaker'] for a in admissions if a['competition']==comp and a['market_family']==fam and a['admitted']) or None})

closing=[]
for f in fixtures:
    fid=f['fixture_id']; hist=all_hist[fid]; ko=f['kickoff_dt']
    for fam in FAMILIES:
        subject=None if fam=='TOTAL_CORNERS' else (f['home'] if fam=='HOME_TEAM_TOTAL_CORNERS' else f['away'])
        for book in BOOKS:
            cand=[]
            for m in markets_by_family[fam]:
                oa=snapshots(hist,book,m['market_id'],m['over_outcome_id']); ua=snapshots(hist,book,m['market_id'],m['under_outcome_id'])
                times=sorted({parsedt(x.get('createdAt')) for x in oa+ua if isinstance(x,dict) and parsedt(x.get('createdAt')) is not None and parsedt(x.get('createdAt'))<ko},reverse=True)
                for state_t in times:
                    os,ot=latest_at(oa,state_t); us,ut=latest_at(ua,state_t)
                    if os is None or us is None: continue
                    try: op=float(os.get('price')); up=float(us.get('price'))
                    except Exception: continue
                    if op<=1 or up<=1 or not active_ok(os.get('active',True)) or not active_ok(us.get('active',True)): continue
                    cand.append((state_t,m,op,up,ot,ut)); break
            if cand:
                state_t,m,op,up,ot,ut=max(cand,key=lambda z:z[0]); closing.append({'competition':f['competition'],'tournament_id':f['tournament_id'],'fixture_id':fid,'market_family':fam,'subject':subject,'bookmaker':book,'closing_covered':True,'market_id':m['market_id'],'line':m['line'],'over_price':op,'under_price':up,'state_time':fmt(state_t),'over_created_at':fmt(ot),'under_created_at':fmt(ut),'closing_age_seconds':(ko-state_t).total_seconds()})
            else: closing.append({'competition':f['competition'],'tournament_id':f['tournament_id'],'fixture_id':fid,'market_family':fam,'subject':subject,'bookmaker':book,'closing_covered':False,'market_id':None,'line':None,'over_price':None,'under_price':None,'state_time':None,'over_created_at':None,'under_created_at':None,'closing_age_seconds':None})
closing_summary=[]
for comp,tid in comp_tid.items():
    denom=eligible_counts[comp]
    for fam in FAMILIES:
        for book in BOOKS:
            rr=[r for r in closing if r['competition']==comp and r['market_family']==fam and r['bookmaker']==book]; ages=[r['closing_age_seconds'] for r in rr if r['closing_covered']]; n=len(ages)
            closing_summary.append({'competition':comp,'tournament_id':tid,'market_family':fam,'bookmaker':book,'eligible_fixtures':denom,'closing_covered_fixtures':n,'closing_coverage_rate':n/denom,'median_closing_age_seconds':statistics.median(ages) if ages else None,'p90_closing_age_seconds':quantile(ages,.90) if ages else None})

write_csv(OUT/'selected_contracts_H12.csv',selected); write_csv(OUT/'event_market_book_horizon_H12.csv',event_rows); write_csv(OUT/'coverage_cells_H12.csv',coverage); write_csv(OUT/'bookmaker_coverage_cells_H12.csv',bookcells); write_csv(OUT/'bookmaker_admissions_H12.csv',admissions); write_csv(OUT/'competition_market_status_H12.csv',cm); write_csv(OUT/'closing_event_book_H12.csv',closing); write_csv(OUT/'closing_summary_H12.csv',closing_summary); write_csv(OUT/'duplicate_timestamp_conflicts_H12.csv',duplicate_conflicts,fields=['fixture_id','bookmaker','market_id','outcome','created_at','price_a','price_b']); write_csv(OUT/'raw_manifest_H12.csv',raws)
(OUT/'eligible_fixtures_frozen.csv').write_bytes(fixture_bytes); (OUT/'market_catalog_target_frozen.csv').write_bytes(market_bytes)
if len(coverage)!=36 or len({(r['competition'],r['market_family'],r['horizon_minutes']) for r in coverage})!=36: raise SystemExit('COVERAGE_CELL_INVARIANT_FAIL')
if len(bookcells)!=108 or len(admissions)!=36 or len(cm)!=12 or len(raws)!=179: raise SystemExit('COUNT_INVARIANT_FAIL')
if any(r['max_selected_side_age_seconds'] is not None and float(r['max_selected_side_age_seconds'])>MAX_AGE+1e-9 for r in event_rows): raise SystemExit('FRESHNESS_INVARIANT_FAIL')
if any(r['subject'] in ('',None) for r in event_rows if r['market_family']!='TOTAL_CORNERS'): raise SystemExit('SUBJECT_INVARIANT_FAIL')
if any(abs((float(r['line'])%1)-.5)>1e-9 for r in selected): raise SystemExit('LINE_INVARIANT_FAIL')
summary={'audit_version':'ODDS_AUTH_COVERAGE_H_1.2_CORRECTED','provider':'ODDSPAPI','api_version':'v4','historical_endpoint':'/historical-odds','credential_persisted':False,'frozen_input_sha256':{'eligible_fixtures.csv':EXPECTED_FIXTURE_SHA,'market_catalog_target.csv':EXPECTED_MARKET_SHA},'frozen_window':{'start':'2026-04-01T00:00:00Z','end_exclusive':'2026-05-01T00:00:00Z','expanded':False},'benchmark_bookmakers':BOOKS,'horizons_minutes':HORIZONS,'max_odds_age_seconds':MAX_AGE,'minimum_eligible_fixtures':MIN_N,'coverage_pass_rate':PASS_RATE,'coverage_partial_floor':PARTIAL_FLOOR,'bookmaker_admission_min_rate':BOOK_ADMIT,'eligible_counts':eligible_counts,'coverage_cell_status_counts':dict(Counter(r['status'] for r in coverage)),'competition_market_status_counts':dict(Counter(r['status'] for r in cm)),'admitted_bookmaker_contracts':sum(bool(r['admitted']) for r in admissions),'coverage_cells_total':len(coverage),'bookmaker_cells_total':len(bookcells),'raw_historical_files':len(raws),'selected_contract_rows':len(selected),'duplicate_timestamp_price_conflicts':len(duplicate_conflicts),'historical_api_calls_including_retries':api_calls,'rate_limit_or_server_retries':retries,'process_complete':True,'step_h_process_status':'PASS'}
(OUT/'audit_summary_H12.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
manifest=[]
for p in sorted(OUT.rglob('*')):
    if p.is_file() and p.name!='artifact_sha256_manifest_H12.json': manifest.append({'file':str(p.relative_to(OUT)),'sha256':sha(p.read_bytes()),'bytes':p.stat().st_size})
(OUT/'artifact_sha256_manifest_H12.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
secret=KEY.encode()
for p in OUT.rglob('*'):
    if p.is_file() and secret in p.read_bytes(): raise SystemExit('CREDENTIAL_LEAK '+str(p))
print('CREDENTIAL_SCAN_PASS',flush=True); print(json.dumps(summary,indent=2),flush=True)
