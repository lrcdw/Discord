from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter
import csv, gzip, hashlib, json, os, time, requests

ROOT=Path('.')
OUT=ROOT/'h13_holdout_v3'
RES=ROOT/'resume_h13'
IN=ROOT/'h13'
OUT.mkdir(exist_ok=True)
(OUT/'raw_historical').mkdir(parents=True,exist_ok=True)
key=Path('/tmp/h13_api_key.txt').read_text(encoding='utf-8').strip()
if not key: raise SystemExit('EMPTY_KEY')

BASE='https://api.oddspapi.io/v4'
PROTOCOL_SHA='63cdc0b6151082cae262dded74b397fd6872d97c162731153f8b50bf2316a068'
CATALOG_SHA='e5a94b1eb282c7df2a547ac3c0425f2016cca10d183b278daeea62679c267a56'
SELECTION_SHA='d752d11a8e71d16c6a347e41a9f3ed7aec6c0b99b29d337680c6b961a7089178'
DISCOVERY_SHA='2681911c9455696275e74a245a22d670d03bb2d1eb69da375bceb1cf2905bc55'
SELECTOR_SHA='6d34e0514910f1a5fb6a904ed2a8883eae67872973d0e511c4060ebf8ec167f8'
DENOM_SHA='3f5ac36e524f0c8b91d4d10f29c99d336fae6ef7a3c64add8bc2d3a2b872289f'
EXPECTED_COUNTS={'Premier League':32,'Championship':50,'Bundesliga':31,'LaLiga':36}
EXPECTED_TIDS={'Premier League':17,'Championship':18,'Bundesliga':35,'LaLiga':8}
FAMILIES=['TOTAL_CORNERS','HOME_TEAM_TOTAL_CORNERS','AWAY_TEAM_TOTAL_CORNERS']
HORIZONS=[60,30,15]
MAX_AGE=300.0
PASS_RATE=.90
PARTIAL=.70
BOOK_ADMIT=.80
MIN_N=25
START_S='2026-03-01T00:00:00Z'; END_S='2026-03-31T23:59:59Z'


def sha_bytes(b): return hashlib.sha256(b).hexdigest()
def sha_file(p): return sha_bytes(Path(p).read_bytes())
def dt(x):
    if not x: return None
    try: d=datetime.fromisoformat(str(x).replace('Z','+00:00'))
    except Exception: return None
    if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)
def fmt(d): return d.isoformat().replace('+00:00','Z') if d else None
def write_csv(path,rows,fields=None):
    rows=list(rows)
    if fields is None: fields=list(rows[0].keys()) if rows else []
    with open(path,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        if fields: w.writeheader()
        w.writerows(rows)
def safe_get(session,url,params,timeout=120,max_attempts=8):
    calls=retries=0; last=None
    for attempt in range(max_attempts):
        calls+=1
        try: r=session.get(url,params=params,timeout=timeout); last=r
        except requests.RequestException:
            retries+=1
            if attempt==max_attempts-1: return None,calls,retries
            time.sleep(min(2**attempt,20)); continue
        if r.status_code in (200,404): return r,calls,retries
        if r.status_code==429 or 500<=r.status_code<600:
            retries+=1; wait=min(2**attempt,20)
            if r.status_code==429:
                try:
                    p=r.json(); e=p.get('error') if isinstance(p,dict) else None
                    if isinstance(e,dict) and e.get('retryMs') is not None: wait=max(wait,float(e['retryMs'])/1000+.5)
                except Exception: pass
            if attempt==max_attempts-1: return r,calls,retries
            time.sleep(wait); continue
        return r,calls,retries
    return last,calls,retries

# Freeze validation: no criterion is allowed to drift.
protocol_path=IN/'oddspapi_h13_protocol_pre_registered.json'
manifest_path=IN/'selection_frozen/candidate_selection_manifest.json'
checks={
 'protocol':(sha_file(protocol_path),PROTOCOL_SHA),
 'catalog':(sha_file(IN/'market_catalog_target_H13.csv'),CATALOG_SHA),
 'selection':(sha_file(IN/'selection_frozen/candidate_selection.csv'),SELECTION_SHA),
 'discovery':(sha_file(IN/'selection_frozen/discovery_book_stats.csv'),DISCOVERY_SHA),
 'selector':(sha_file(IN/'select_h13_candidates.py'),SELECTOR_SHA),
}
for n,(a,e) in checks.items():
    if a!=e: raise SystemExit('FREEZE_HASH_FAIL_'+n.upper())
protocol=json.loads(protocol_path.read_text(encoding='utf-8'))
sel_manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
if sel_manifest.get('selection_protocol_version')!='ODDSPAPI_H_1.3_PRE_REGISTERED': raise SystemExit('PROTOCOL_VERSION_FAIL')
if sel_manifest.get('manual_override_used') is not False: raise SystemExit('MANUAL_OVERRIDE_FAIL')
if sel_manifest.get('holdout_observed_before_freeze') is not False: raise SystemExit('HOLDOUT_PREOBSERVATION_FAIL')
if protocol['holdout']['period_start_utc']!=START_S or protocol['holdout']['period_end_utc']!=END_S: raise SystemExit('HOLDOUT_PERIOD_CHANGED')
if protocol['holdout']['competitions']!=EXPECTED_TIDS: raise SystemExit('COMPETITIONS_CHANGED')
if int(protocol['holdout']['minimum_fixtures_per_competition'])!=MIN_N: raise SystemExit('MIN_SAMPLE_CHANGED')
if protocol['market_contract']['horizons_minutes']!=HORIZONS: raise SystemExit('HORIZONS_CHANGED')
if float(protocol['market_contract']['max_odds_age_seconds'])!=MAX_AGE: raise SystemExit('MAX_AGE_CHANGED')
if float(protocol['gates']['coverage_pass_rate'])!=PASS_RATE or float(protocol['gates']['coverage_partial_floor'])!=PARTIAL or float(protocol['gates']['bookmaker_admission_min_rate_across_all_horizons'])!=BOOK_ADMIT: raise SystemExit('THRESHOLDS_CHANGED')
if protocol['gates']['timestamp_complete_rate_required']!=1.0: raise SystemExit('TIMESTAMP_GATE_CHANGED')
if protocol['gates']['duplicate_same_timestamp_different_price_allowed'] is not False: raise SystemExit('DUPLICATE_GATE_CHANGED')

# Restore the denominator and partial raw checkpoint produced before the 404 handling fix.
elig=RES/'eligible_fixtures_H13.csv'
if sha_file(elig)!=DENOM_SHA: raise SystemExit('DENOMINATOR_HASH_FAIL')
fixtures=list(csv.DictReader(elig.open(encoding='utf-8')))
counts=dict(Counter(r['competition'] for r in fixtures))
if counts!=EXPECTED_COUNTS: raise SystemExit('DENOMINATOR_COUNTS_FAIL:'+repr(counts))
start=dt(START_S); end=dt(END_S)
for f in fixtures:
    if int(f['tournament_id'])!=EXPECTED_TIDS[f['competition']]: raise SystemExit('DENOMINATOR_TOURNAMENT_FAIL')
    ko=dt(f['kickoff_utc'])
    if ko is None or not(start<=ko<=end): raise SystemExit('DENOMINATOR_TIME_FAIL')
    if int(f['status_id'])!=2 or int(f['sport_id'])!=10: raise SystemExit('DENOMINATOR_STATUS_SPORT_FAIL')
if len({f['fixture_id'] for f in fixtures})!=len(fixtures): raise SystemExit('DENOMINATOR_DUPLICATE_FAIL')
fixtures.sort(key=lambda r:(r['kickoff_utc'],str(r['fixture_id'])))

# Verify and preserve the four structural census raw responses.
fc_rows=list(csv.DictReader((RES/'fixture_census_manifest.csv').open(encoding='utf-8')))
if len(fc_rows)!=4: raise SystemExit('FIXTURE_CENSUS_MANIFEST_COUNT_FAIL')
for x in fc_rows:
    p=RES/x['file']; body=gzip.decompress(p.read_bytes())
    if sha_bytes(body)!=x['sha256_uncompressed']: raise SystemExit('FIXTURE_CENSUS_RAW_HASH_FAIL')
    dest=OUT/x['file']; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(p.read_bytes())
write_csv(OUT/'fixture_census_manifest.csv',fc_rows)
(OUT/'eligible_fixtures_H13.csv').write_bytes(elig.read_bytes())

cats=list(csv.DictReader((IN/'market_catalog_target_H13.csv').open(encoding='utf-8')))
cats_by_family=defaultdict(list)
for c in cats:
    c['market_id']=int(c['market_id']); c['line']=float(c['line']); c['over_outcome_id']=int(c['over_outcome_id']); c['under_outcome_id']=int(c['under_outcome_id'])
    if c['market_family'] not in FAMILIES: raise SystemExit('UNEXPECTED_MARKET_FAMILY')
    if abs((c['line']-.5)-round(c['line']-.5))>1e-9: raise SystemExit('NON_HALF_POINT_LINE')
    cats_by_family[c['market_family']].append(c)
selected=sel_manifest['selected']
union_books={comp:sorted({b for fam in FAMILIES for b in selected[comp][fam]}) for comp in EXPECTED_TIDS}

# Restore already acquired historical raws.
raw_manifest=[]
prior_manifest=RES/'raw_manifest.csv'
if prior_manifest.exists():
    for x in csv.DictReader(prior_manifest.open(encoding='utf-8')):
        p=RES/x['file']; body=gzip.decompress(p.read_bytes())
        if sha_bytes(body)!=x['sha256_uncompressed']: raise SystemExit('PRIOR_RAW_HASH_FAIL')
        dest=OUT/x['file']; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(p.read_bytes())
        x=dict(x); x['availability_status']='AVAILABLE'; x['error_code']=''; raw_manifest.append(x)
existing={(x['fixture_id'],int(x['group_index'])):x for x in raw_manifest}

expected_requests=[]
for f in fixtures:
    books=union_books[f['competition']]
    for gi in range(0,len(books),3):
        grp=books[gi:gi+3]
        expected_requests.append((f,gi//3+1,grp))
for f,gi,grp in expected_requests:
    if (f['fixture_id'],gi) in existing and existing[(f['fixture_id'],gi)]['bookmakers']!=','.join(grp): raise SystemExit('RESUME_GROUP_MISMATCH')

sess=requests.Session(); total_calls=0; total_retries=0; newly_collected=0; not_found_count=0
fields=['file','competition','fixture_id','group_index','bookmakers','sha256_uncompressed','uncompressed_bytes','http_status','endpoint','availability_status','error_code']
write_csv(OUT/'raw_manifest.csv',raw_manifest,fields)
for req_i,(f,gi,grp) in enumerate(expected_requests,1):
    k=(f['fixture_id'],gi)
    if k in existing: continue
    params={'apiKey':key,'fixtureId':f['fixture_id'],'bookmakers':','.join(grp)}
    r,calls,retries=safe_get(sess,BASE+'/historical-odds',params,timeout=120)
    total_calls+=calls; total_retries+=retries
    if r is None:
        err={'fixture_id':f['fixture_id'],'competition':f['competition'],'group_index':gi,'bookmakers':grp,'http_status':None,'kind':'NETWORK_OR_TIMEOUT'}
        (OUT/'collection_status.json').write_text(json.dumps({'status':'PARTIAL','phase':'HISTORICAL_ODDS','error':err,'api_calls_this_run':total_calls,'retries_this_run':total_retries,'completed_request_groups':len(raw_manifest),'expected_request_groups':len(expected_requests),'credential_persisted':False},indent=2),encoding='utf-8')
        raise SystemExit('H13_COLLECTION_INTERRUPTED')
    body=r.content
    try: payload=r.json()
    except Exception: payload=None
    if r.status_code==404:
        code=None
        if isinstance(payload,dict):
            e=payload.get('error'); code=e.get('code') if isinstance(e,dict) else None
        if code!='NOT_FOUND':
            err={'fixture_id':f['fixture_id'],'competition':f['competition'],'group_index':gi,'bookmakers':grp,'http_status':404,'error_code':code,'kind':'UNEXPECTED_404'}
            (OUT/'collection_status.json').write_text(json.dumps({'status':'PARTIAL','phase':'HISTORICAL_ODDS','error':err,'api_calls_this_run':total_calls,'retries_this_run':total_retries,'completed_request_groups':len(raw_manifest),'expected_request_groups':len(expected_requests),'credential_persisted':False},indent=2),encoding='utf-8')
            raise SystemExit('H13_UNEXPECTED_404')
        rel=f'raw_historical/{f["fixture_id"]}_g{gi}.not_found.json.gz'; status='NOT_FOUND'; not_found_count+=1
    elif r.status_code==200:
        if not isinstance(payload,dict): raise SystemExit('HISTORICAL_200_SHAPE_FAIL')
        rel=f'raw_historical/{f["fixture_id"]}_g{gi}.json.gz'; status='AVAILABLE'
    else:
        excerpt=None
        try: excerpt=r.text[:300]
        except Exception: pass
        err={'fixture_id':f['fixture_id'],'competition':f['competition'],'group_index':gi,'bookmakers':grp,'http_status':r.status_code,'body_excerpt':excerpt,'kind':'CREDENTIAL_LIMIT_OR_HTTP_FAILURE'}
        (OUT/'collection_status.json').write_text(json.dumps({'status':'PARTIAL','phase':'HISTORICAL_ODDS','error':err,'api_calls_this_run':total_calls,'retries_this_run':total_retries,'completed_request_groups':len(raw_manifest),'expected_request_groups':len(expected_requests),'credential_persisted':False},indent=2),encoding='utf-8')
        raise SystemExit('H13_COLLECTION_INTERRUPTED')
    (OUT/rel).write_bytes(gzip.compress(body,compresslevel=9))
    row={'file':rel,'competition':f['competition'],'fixture_id':f['fixture_id'],'group_index':gi,'bookmakers':','.join(grp),'sha256_uncompressed':sha_bytes(body),'uncompressed_bytes':len(body),'http_status':r.status_code,'endpoint':'/historical-odds','availability_status':status,'error_code':'NOT_FOUND' if status=='NOT_FOUND' else ''}
    raw_manifest.append(row); existing[k]=row; newly_collected+=1
    write_csv(OUT/'raw_manifest.csv',raw_manifest,fields)
    if newly_collected%10==0:
        (OUT/'collection_status.json').write_text(json.dumps({'status':'RUNNING','phase':'HISTORICAL_ODDS','api_calls_this_run':total_calls,'retries_this_run':total_retries,'completed_request_groups':len(raw_manifest),'expected_request_groups':len(expected_requests),'newly_collected':newly_collected,'not_found_groups_this_run':not_found_count,'credential_persisted':False},indent=2),encoding='utf-8')
        print('H13_REQUEST_GROUPS',len(raw_manifest),'OF',len(expected_requests),flush=True)

if len(raw_manifest)!=len(expected_requests): raise SystemExit('RAW_REQUEST_COMPLETENESS_FAIL')

# Rebuild feed from immutable raw request bodies. NOT_FOUND = empty bookmaker coverage.
rows_by_fixture=defaultdict(list)
for x in raw_manifest: rows_by_fixture[x['fixture_id']].append(x)
all_hist={}
for f in fixtures:
    merged={'bookmakers':{}}
    for x in sorted(rows_by_fixture[f['fixture_id']],key=lambda z:int(z['group_index'])):
        body=gzip.decompress((OUT/x['file']).read_bytes())
        if sha_bytes(body)!=x['sha256_uncompressed']: raise SystemExit('RAW_HASH_FAIL')
        if x['availability_status']=='NOT_FOUND': continue
        p=json.loads(body); b=p.get('bookmakers',{}) if isinstance(p,dict) else {}
        if not isinstance(b,dict): raise SystemExit('RAW_BOOKMAKERS_SHAPE_FAIL')
        if set(merged['bookmakers']).intersection(b): raise SystemExit('RAW_BOOKMAKER_OVERLAP')
        merged['bookmakers'].update(b)
    all_hist[f['fixture_id']]=merged

def snaps(hist,book,mid,oid):
    try: arr=hist['bookmakers'][book]['markets'][str(mid)]['outcomes'][str(oid)]['players']['0']
    except (KeyError,TypeError): return []
    return arr if isinstance(arr,list) else []
def latest_at(arr,asof):
    best=None; best_t=None
    for x in arr:
        if not isinstance(x,dict): continue
        t=dt(x.get('createdAt'))
        if t is None or t>asof: continue
        if best_t is None or t>best_t: best=x; best_t=t
    return best,best_t

snapshot_stats={}; duplicate_conflicts_total=0
for f in fixtures:
    hist=all_hist[f['fixture_id']]; comp=f['competition']
    for fam in FAMILIES:
        for book in selected[comp][fam]:
            for c in cats_by_family[fam]:
                ov=snaps(hist,book,c['market_id'],c['over_outcome_id']); un=snaps(hist,book,c['market_id'],c['under_outcome_id']); target=ov+un
                with_ts=sum(1 for x in target if isinstance(x,dict) and dt(x.get('createdAt')) is not None)
                conflicts=0
                for arr in (ov,un):
                    seen={}
                    for x in arr:
                        if not isinstance(x,dict): continue
                        t=x.get('createdAt')
                        if not t: continue
                        price=x.get('price')
                        if t in seen and seen[t]!=price: conflicts+=1
                        seen[t]=price
                duplicate_conflicts_total+=conflicts
                snapshot_stats[(f['fixture_id'],fam,book,c['market_id'])]=(len(target),with_ts,conflicts)

event_rows=[]
for f in fixtures:
    hist=all_hist[f['fixture_id']]; ko=dt(f['kickoff_utc']); comp=f['competition']
    for fam in FAMILIES:
        subject=None if fam=='TOTAL_CORNERS' else (f['home'] if fam=='HOME_TEAM_TOTAL_CORNERS' else f['away'])
        for book in selected[comp][fam]:
            for hm in HORIZONS:
                asof=ko-timedelta(minutes=hm); valid=[]; ages=[]; ts_target=0; ts_with=0; confs=0
                for c in cats_by_family[fam]:
                    ov=snaps(hist,book,c['market_id'],c['over_outcome_id']); un=snaps(hist,book,c['market_id'],c['under_outcome_id'])
                    a,b,cc=snapshot_stats[(f['fixture_id'],fam,book,c['market_id'])]; ts_target+=a; ts_with+=b; confs+=cc
                    osnap,ot=latest_at(ov,asof); usnap,ut=latest_at(un,asof)
                    if not osnap or not usnap or ot is None or ut is None: continue
                    try: op=float(osnap.get('price')); up=float(usnap.get('price'))
                    except Exception: continue
                    if op<=1 or up<=1 or osnap.get('active') is False or usnap.get('active') is False: continue
                    oa=(asof-ot).total_seconds(); ua=(asof-ut).total_seconds()
                    if oa<0 or ua<0 or oa>MAX_AGE or ua>MAX_AGE: continue
                    valid.append(c['line']); ages.extend([oa,ua])
                event_rows.append({'competition':comp,'tournament_id':f['tournament_id'],'fixture_id':f['fixture_id'],'kickoff_utc':f['kickoff_utc'],'home':f['home'],'away':f['away'],'market_family':fam,'subject':subject,'bookmaker':book,'horizon_minutes':hm,'prediction_as_of':fmt(asof),'covered':bool(valid),'valid_pair_count':len(valid),'freshest_age_seconds':min(ages) if ages else None,'max_selected_side_age_seconds':max(ages) if ages else None,'line_min':min(valid) if valid else None,'line_max':max(valid) if valid else None,'valid_lines':';'.join(str(x) for x in sorted(valid)) if valid else None,'target_snapshots':ts_target,'snapshots_with_timestamp':ts_with,'duplicate_timestamp_price_conflicts':confs})
write_csv(OUT/'event_coverage.csv',event_rows)

coverage=[]; bookmaker_cells=[]
for comp,tid in EXPECTED_TIDS.items():
    denom=EXPECTED_COUNTS[comp]
    for fam in FAMILIES:
        for hm in HORIZONS:
            subset=[r for r in event_rows if r['competition']==comp and r['market_family']==fam and r['horizon_minutes']==hm]
            byfix=defaultdict(list)
            for r in subset: byfix[r['fixture_id']].append(r)
            covered=sum(1 for f in fixtures if f['competition']==comp and any(x['covered'] for x in byfix.get(f['fixture_id'],[])))
            target=sum(r['target_snapshots'] for r in subset); withts=sum(r['snapshots_with_timestamp'] for r in subset); confs=sum(r['duplicate_timestamp_price_conflicts'] for r in subset)
            tcr=1.0 if target==0 else withts/target; rate=covered/denom
            valid_lines=[]
            for r in subset:
                if r['valid_lines']: valid_lines.extend(float(x) for x in r['valid_lines'].split(';'))
            if denom<MIN_N: status='FAIL_SAMPLE'
            elif confs>0 or tcr<1.0: status='FAIL_TEMPORAL'
            elif rate>=PASS_RATE: status='PASS'
            elif rate>=PARTIAL: status='PARTIAL'
            else: status='FAIL_COVERAGE'
            coverage.append({'competition':comp,'tournament_id':tid,'market_family':fam,'horizon_minutes':hm,'eligible_fixtures':denom,'covered_any_selected_bookmaker':covered,'coverage_rate':rate,'timestamp_complete_rate':tcr,'target_snapshots':target,'duplicate_timestamp_price_conflicts':confs,'status':status,'observed_line_min':min(valid_lines) if valid_lines else None,'observed_line_max':max(valid_lines) if valid_lines else None})
            for book in selected[comp][fam]:
                bs=[r for r in subset if r['bookmaker']==book]; bc=sum(1 for r in bs if r['covered']); bt=sum(r['target_snapshots'] for r in bs); bw=sum(r['snapshots_with_timestamp'] for r in bs); bconf=sum(r['duplicate_timestamp_price_conflicts'] for r in bs)
                bookmaker_cells.append({'competition':comp,'tournament_id':tid,'market_family':fam,'horizon_minutes':hm,'bookmaker':book,'eligible_fixtures':denom,'covered_fixtures':bc,'coverage_rate':bc/denom,'timestamp_complete_rate':1.0 if bt==0 else bw/bt,'target_snapshots':bt,'duplicate_timestamp_price_conflicts':bconf})
write_csv(OUT/'coverage_36_cells.csv',coverage); write_csv(OUT/'bookmaker_horizon_coverage.csv',bookmaker_cells)

admissions=[]
for comp,tid in EXPECTED_TIDS.items():
    for fam in FAMILIES:
        for book in selected[comp][fam]:
            cells=[r for r in bookmaker_cells if r['competition']==comp and r['market_family']==fam and r['bookmaker']==book]
            admitted=len(cells)==3 and all(r['coverage_rate']>=BOOK_ADMIT and r['timestamp_complete_rate']==1.0 and r['duplicate_timestamp_price_conflicts']==0 for r in cells)
            admissions.append({'competition':comp,'tournament_id':tid,'market_family':fam,'bookmaker':book,'admitted':admitted,'min_horizon_coverage_rate':min((r['coverage_rate'] for r in cells),default=0),'all_timestamp_complete':all(r['timestamp_complete_rate']==1.0 for r in cells) if cells else False,'duplicate_timestamp_price_conflicts':sum(r['duplicate_timestamp_price_conflicts'] for r in cells)})
write_csv(OUT/'bookmaker_admissions.csv',admissions)

cm_status=[]
for comp,tid in EXPECTED_TIDS.items():
    for fam in FAMILIES:
        cs=[r for r in coverage if r['competition']==comp and r['market_family']==fam]; admitted=[a['bookmaker'] for a in admissions if a['competition']==comp and a['market_family']==fam and a['admitted']]
        if len(cs)==3 and all(r['status']=='PASS' for r in cs) and admitted: st='PASS'
        elif any(r['status'].startswith('FAIL') for r in cs): st='FAIL'
        elif len(cs)==3 and all(r['status']=='PASS' for r in cs) and not admitted: st='FAIL_BOOKMAKER_ADMISSION'
        else: st='PARTIAL'
        cm_status.append({'competition':comp,'tournament_id':tid,'market_family':fam,'status':st,'min_coverage_rate':min((r['coverage_rate'] for r in cs),default=0),'max_coverage_rate':max((r['coverage_rate'] for r in cs),default=0),'admitted_bookmakers':';'.join(admitted) if admitted else None})
write_csv(OUT/'competition_market_status.csv',cm_status)

closing=[]
for f in fixtures:
    hist=all_hist[f['fixture_id']]; ko=dt(f['kickoff_utc']); comp=f['competition']
    for fam in FAMILIES:
        for book in selected[comp][fam]:
            for c in cats_by_family[fam]:
                ov=snaps(hist,book,c['market_id'],c['over_outcome_id']); un=snaps(hist,book,c['market_id'],c['under_outcome_id'])
                times=sorted({dt(x.get('createdAt')) for x in ov+un if isinstance(x,dict) and dt(x.get('createdAt')) is not None and dt(x.get('createdAt'))<ko},reverse=True)
                chosen=None
                for state_t in times:
                    osnap,ot=latest_at(ov,state_t); usnap,ut=latest_at(un,state_t)
                    if not osnap or not usnap or ot is None or ut is None: continue
                    try: op=float(osnap.get('price')); up=float(usnap.get('price'))
                    except Exception: continue
                    if op<=1 or up<=1 or osnap.get('active') is False or usnap.get('active') is False: continue
                    chosen=(state_t,ot,ut,op,up); break
                if chosen:
                    state_t,ot,ut,op,up=chosen
                    closing.append({'competition':comp,'fixture_id':f['fixture_id'],'kickoff_utc':f['kickoff_utc'],'market_family':fam,'bookmaker':book,'line':c['line'],'closing_state_time':fmt(state_t),'over_snapshot_time':fmt(ot),'under_snapshot_time':fmt(ut),'over_odds':op,'under_odds':up,'strictly_pre_kickoff':state_t<ko})
write_csv(OUT/'closing_pairs.csv',closing)

pass_contracts=[r for r in cm_status if r['status']=='PASS']
summary={'protocol_version':'ODDSPAPI_H_1.3_PRE_REGISTERED','runner_version':'H13_RESUME_V3','operating_mode':'SHADOW','holdout_period':[START_S,END_S],'eligible_counts':EXPECTED_COUNTS,'eligible_fixtures_sha256':DENOM_SHA,'coverage_cells':len(coverage),'expected_coverage_cells':36,'pass_cells':sum(r['status']=='PASS' for r in coverage),'partial_cells':sum(r['status']=='PARTIAL' for r in coverage),'fail_cells':sum(r['status'].startswith('FAIL') for r in coverage),'competition_market_contracts':len(cm_status),'pass_contract_count':len(pass_contracts),'pass_contracts':pass_contracts,'h13_market_gate_pass':len(pass_contracts)>=1,'duplicate_timestamp_price_conflicts':duplicate_conflicts_total,'expected_historical_request_groups':len(expected_requests),'raw_historical_request_groups':len(raw_manifest),'not_found_request_groups':sum(x['availability_status']=='NOT_FOUND' for x in raw_manifest),'api_calls_this_run_including_retries':total_calls,'retries_this_run':total_retries,'reused_request_groups':len(raw_manifest)-newly_collected,'newly_collected_request_groups':newly_collected,'closing_pairs':len(closing),'credential_persisted':False,'step_H_status':'OPEN_PENDING_INTEGRATION_AND_FINAL_REVIEW','step_I_allowed':False}
if len(coverage)!=36: raise SystemExit('COVERAGE_CELL_COUNT_FAIL')
(OUT/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True),encoding='utf-8')
(OUT/'freeze_validation.json').write_text(json.dumps({'freeze_gate':'PASS','protocol_sha256':PROTOCOL_SHA,'catalog_sha256':CATALOG_SHA,'selection_sha256':SELECTION_SHA,'discovery_sha256':DISCOVERY_SHA,'selector_sha256':SELECTOR_SHA,'denominator_sha256':DENOM_SHA,'eligible_counts':EXPECTED_COUNTS,'not_found_semantics':'Observed provider absence; fixture retained in denominator; no bookmaker substitution; counts as uncovered.'},indent=2,sort_keys=True),encoding='utf-8')
(OUT/'collection_status.json').write_text(json.dumps({'status':'COMPLETE','phase':'HISTORICAL_ODDS','completed_request_groups':len(raw_manifest),'expected_request_groups':len(expected_requests),'api_calls_this_run':total_calls,'retries_this_run':total_retries,'credential_persisted':False},indent=2),encoding='utf-8')

secret=key.encode(); files=[]
for p in sorted(OUT.rglob('*')):
    if p.is_file() and p.name!='artifact_manifest.csv':
        b=p.read_bytes()
        if secret and secret in b: raise SystemExit('CREDENTIAL_LEAK:'+str(p))
        files.append({'file':str(p.relative_to(OUT)),'sha256':sha_bytes(b),'bytes':len(b)})
write_csv(OUT/'artifact_manifest.csv',files)
print('H13_COMPLETE',json.dumps(summary,sort_keys=True),flush=True)
