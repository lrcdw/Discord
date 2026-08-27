#!/usr/bin/env python3
import json, os, re, hashlib
from pathlib import Path
from datetime import datetime, timezone
import requests

KEY=os.environ.get('THE_ODDS_API_KEY','').strip()
if not KEY:
    raise SystemExit('NO_KEY')
API='https://api.the-odds-api.com/v4'
OUT=Path('h14_smoke_current'); OUT.mkdir(exist_ok=True)
BOOKS=['betonlineag','betmgm','betrivers','betus','bovada','draftkings','fanduel','hardrockbet','lowvig','mybookieag']
SPORTS=['soccer_epl','soccer_efl_champ','soccer_germany_bundesliga','soccer_spain_la_liga']
MARKETS=['alternate_totals_corners','alternate_team_totals_corners']
s=requests.Session()
rows=[]; raws=[]; usage=[]

def get(url,params,name):
    params=dict(params); params['apiKey']=KEY
    r=s.get(url,params=params,timeout=60)
    b=r.content
    p=OUT/f'{name}.json'; p.write_bytes(b)
    raws.append({'file':p.name,'sha256':hashlib.sha256(b).hexdigest(),'status':r.status_code})
    usage.append({'call':name,'status':r.status_code,'remaining':r.headers.get('x-requests-remaining',''),'used':r.headers.get('x-requests-used',''),'last':r.headers.get('x-requests-last','')})
    if r.status_code!=200:
        try: msg=r.json()
        except Exception: msg=r.text[:500]
        raise RuntimeError(f'{name} HTTP {r.status_code}: {msg}')
    return r.json()

sports=get(API+'/sports',{},'sports')
active={x['key'] for x in sports if x.get('active')}
for sport in SPORTS:
    if sport not in active:
        rows.append({'sport':sport,'event_id':'','home':'','away':'','market':'','bookmaker':'','status':'SPORT_NOT_ACTIVE','outcomes':0})
        continue
    events=get(f'{API}/sports/{sport}/events',{'dateFormat':'iso'},f'events_{sport}')
    events=sorted(events,key=lambda e:e.get('commence_time',''))[:3]
    if not events:
        rows.append({'sport':sport,'event_id':'','home':'','away':'','market':'','bookmaker':'','status':'NO_CURRENT_EVENTS','outcomes':0})
        continue
    for e in events:
        data=get(f"{API}/sports/{sport}/events/{e['id']}/odds",{
            'bookmakers':','.join(BOOKS),
            'markets':','.join(MARKETS),
            'oddsFormat':'decimal','dateFormat':'iso'
        },f"odds_{sport}_{e['id']}")
        seen=False
        for book in data.get('bookmakers',[]):
            for market in book.get('markets',[]):
                if market.get('key') not in MARKETS: continue
                seen=True
                outs=market.get('outcomes',[])
                half=sum(1 for o in outs if isinstance(o.get('point'),(int,float)) and abs(o['point']*2-round(o['point']*2))<1e-9 and int(round(o['point']*2))%2==1)
                rows.append({'sport':sport,'event_id':e['id'],'home':e.get('home_team',''),'away':e.get('away_team',''),'market':market.get('key',''),'bookmaker':book.get('key',''),'status':'PRESENT','outcomes':len(outs),'half_point_outcomes':half,'book_last_update':book.get('last_update',''),'market_last_update':market.get('last_update','')})
        if not seen:
            rows.append({'sport':sport,'event_id':e['id'],'home':e.get('home_team',''),'away':e.get('away_team',''),'market':'','bookmaker':'','status':'TARGET_MARKETS_ABSENT','outcomes':0})

summary={
    'timestamp_utc':datetime.now(timezone.utc).isoformat(),
    'purpose':'ENGINEERING_SMOKE_ONLY_NOT_HOLDOUT',
    'holdout_observed':False,
    'sports_tested':SPORTS,
    'fixed_bookmakers':BOOKS,
    'target_markets':MARKETS,
    'present_rows':sum(r.get('status')=='PRESENT' for r in rows),
    'events_with_target_markets':len({r['event_id'] for r in rows if r.get('status')=='PRESENT'}),
    'bookmakers_seen':sorted({r['bookmaker'] for r in rows if r.get('bookmaker')}),
    'markets_seen':sorted({r['market'] for r in rows if r.get('market')}),
    'usage':usage,
}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
(OUT/'rows.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
(OUT/'raw_manifest.json').write_text(json.dumps(raws,indent=2),encoding='utf-8')
secret=KEY.encode()
for p in OUT.rglob('*'):
    if p.is_file() and secret in p.read_bytes():
        raise RuntimeError(f'CREDENTIAL_LEAK:{p}')
print(json.dumps(summary,sort_keys=True))
