#!/usr/bin/env python3
import json, os, hashlib
from collections import defaultdict
from pathlib import Path
import requests

KEY=os.environ.get('THE_ODDS_API_KEY','').strip()
if not KEY: raise SystemExit('NO_KEY')
API='https://api.the-odds-api.com/v4'
OUT=Path('h14_current_discovery'); OUT.mkdir(exist_ok=True)
SPORTS={
 'Premier League':'soccer_epl',
 'Championship':'soccer_efl_champ',
 'Bundesliga':'soccer_germany_bundesliga',
 'LaLiga':'soccer_spain_la_liga',
}
REGIONS='us,us2,uk,eu,au'
MARKETS='alternate_totals_corners,alternate_team_totals_corners'
s=requests.Session(); usage=[]; raw=[]

def get(path,params,name):
    q=dict(params); q['apiKey']=KEY
    r=s.get(API+path,params=q,timeout=90)
    body=r.content; p=OUT/f'{name}.json'; p.write_bytes(body)
    raw.append({'file':p.name,'sha256':hashlib.sha256(body).hexdigest(),'status':r.status_code})
    usage.append({'call':name,'status':r.status_code,'remaining':r.headers.get('x-requests-remaining',''),'used':r.headers.get('x-requests-used',''),'last':r.headers.get('x-requests-last','')})
    if r.status_code!=200:
        try: msg=r.json()
        except Exception: msg=r.text[:500]
        raise RuntimeError(f'{name} HTTP {r.status_code}: {msg}')
    return r.json()

def line_pairs(market):
    sides=defaultdict(set)
    for o in market.get('outcomes',[]):
        name=(o.get('name') or '').lower()
        if name not in ('over','under'): continue
        try: pt=float(o.get('point')); price=float(o.get('price'))
        except Exception: continue
        if price<=1: continue
        if abs(pt*2-round(pt*2))>1e-9 or int(round(pt*2))%2==0: continue
        desc=o.get('description','') if market.get('key')=='alternate_team_totals_corners' else ''
        sides[(desc,pt)].add(name)
    return sum(v=={'over','under'} for v in sides.values())

stats=defaultdict(lambda:{'events_present':set(),'paired_lines':0,'outcomes':0})
event_counts={}
for comp,sport in SPORTS.items():
    events=get(f'/sports/{sport}/events',{'dateFormat':'iso'},f'events_{sport}')
    events=sorted(events,key=lambda e:e.get('commence_time',''))[:5]
    event_counts[comp]=len(events)
    for e in events:
        data=get(f"/sports/{sport}/events/{e['id']}/odds",{'regions':REGIONS,'markets':MARKETS,'oddsFormat':'decimal','dateFormat':'iso'},f"odds_{sport}_{e['id']}")
        for b in data.get('bookmakers',[]):
            for m in b.get('markets',[]):
                if m.get('key') not in MARKETS.split(','): continue
                pairs=line_pairs(m)
                if pairs:
                    k=(comp,m['key'],b.get('key',''),b.get('title',''))
                    stats[k]['events_present'].add(e['id'])
                    stats[k]['paired_lines']+=pairs
                    stats[k]['outcomes']+=len(m.get('outcomes',[]))

rows=[]
for (comp,market,key,title),x in stats.items():
    n=event_counts[comp]
    rows.append({'competition':comp,'market':market,'bookmaker_key':key,'bookmaker_title':title,'events_sampled':n,'events_present':len(x['events_present']),'sample_coverage':len(x['events_present'])/n if n else 0,'paired_half_point_lines':x['paired_lines'],'outcomes':x['outcomes']})
rows.sort(key=lambda r:(r['competition'],r['market'],-r['events_present'],-r['paired_half_point_lines'],r['bookmaker_key']))
summary={
 'purpose':'PRE_HOLDOUT_CURRENT_DISCOVERY_ONLY',
 'historical_holdout_observed':False,
 'regions':REGIONS.split(','),
 'markets':MARKETS.split(','),
 'events_per_competition_target':5,
 'events_sampled':event_counts,
 'rows':rows,
 'usage':usage,
}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
(OUT/'raw_manifest.json').write_text(json.dumps(raw,indent=2),encoding='utf-8')
secret=KEY.encode()
for p in OUT.rglob('*'):
    if p.is_file() and secret in p.read_bytes(): raise RuntimeError(f'CREDENTIAL_LEAK:{p}')
print(json.dumps({'events_sampled':event_counts,'discovery_rows':len(rows),'remaining':usage[-1]['remaining'] if usage else None},sort_keys=True))
