#!/usr/bin/env python3
"""Effective H1.4 runner after pre-holdout current-market discovery.

No February 2026 historical data had been observed when the bookmaker selection
was amended. All H.1.4 thresholds, denominator, horizons and market contracts
remain inherited unchanged from THEODDSAPI_H_1.4_PRE_REGISTERED.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent

spec=importlib.util.spec_from_file_location('h14_v2', HERE/'run_h14_theoddsapi_v2.py')
v2=importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2)
r=v2.r

SELECTION=HERE/'current_discovery_selection_v1_1.json'
AMENDMENT=HERE/'theoddsapi_h14_preholdout_amendment_1_1.json'
if not SELECTION.exists() or not AMENDMENT.exists():
    raise SystemExit('FAIL_CLOSED: H1.4 pre-holdout selection/amendment missing')

selection=json.loads(SELECTION.read_text(encoding='utf-8'))
amendment=json.loads(AMENDMENT.read_text(encoding='utf-8'))
if selection.get('historical_holdout_observed_before_freeze') is not False:
    raise SystemExit('FAIL_CLOSED: selection temporal-integrity flag invalid')
if amendment.get('historical_holdout_observed_before_amendment') is not False:
    raise SystemExit('FAIL_CLOSED: amendment temporal-integrity flag invalid')
books=selection.get('selected_bookmakers')
if not isinstance(books,list) or len(books)!=10 or len(set(books))!=10:
    raise SystemExit('FAIL_CLOSED: expected exactly 10 unique frozen bookmakers')
if books != amendment['changed_fields_only']['candidate_bookmakers.keys']:
    raise SystemExit('FAIL_CLOSED: selection/amendment bookmaker mismatch')

# Verify byte content against the exact pre-holdout freeze commits.
subprocess.run(['git','diff','--exit-code','177559b62ef3efcc9d47d11284acbc808862452b','--','h14/current_discovery_selection_v1_1.json'],cwd=ROOT,check=True)
subprocess.run(['git','diff','--exit-code','d6965a2222493159614e4781ed4407ab3950d488','--','h14/theoddsapi_h14_preholdout_amendment_1_1.json'],cwd=ROOT,check=True)

# Apply only the explicitly amended candidate-bookmaker field to the already
# hardened v2 implementation. One batch of 10 remains one region-equivalent.
r.BOOKS=list(books)
r.BOOKS_PARAM=','.join(books)
r.protocol['candidate_bookmakers']['keys']=list(books)
r.protocol['candidate_bookmakers']['selection_basis']='H14_CURRENT_DISCOVERY_SELECTION_1.1 pre-holdout automatic ranking'
r.protocol['candidate_bookmakers']['manual_replacement_after_holdout_observation']=False

if __name__=='__main__':
    r.main()
