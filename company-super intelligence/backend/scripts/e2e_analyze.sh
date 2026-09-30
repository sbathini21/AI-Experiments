#!/usr/bin/env bash
# End-to-end smoke test: resolve a company by natural language, run analysis, print the report.
# Usage: scripts/e2e_analyze.sh "Kinetic Engineering" [window_days] [mode]
set -euo pipefail
API=${API:-http://localhost:8000}
Q=${1:-Kinetic Engineering}; W=${2:-30}; MODE=${3:-quick}
CID=$(curl -s -X POST "$API/api/company/resolve" -H 'content-type: application/json' -d "{\"query\":\"$Q\"}" \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['selected']['company_id'] if d['status']=='resolved' else '')")
[ -z "$CID" ] && { echo "Not uniquely resolved — pick a candidate via /api/company/resolve"; exit 1; }
R=$(curl -s -X POST "$API/api/company/analyze" -H 'content-type: application/json' -d "{\"company_id\":\"$CID\",\"window_days\":$W,\"mode\":\"$MODE\",\"force\":true}")
JOB=$(echo "$R" | python3 -c "import json,sys;print(json.load(sys.stdin).get('job_id',''))")
for i in $(seq 1 120); do
  S=$(curl -s "$API/api/jobs/$JOB"); ST=$(echo "$S" | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])")
  [ "$ST" = done ] || [ "$ST" = failed ] && break; sleep 3
done
echo "$S" | python3 -c "
import json,sys;d=json.load(sys.stdin);print('JOB',d['status'],d['error'] or '')
for p in d['progress']: print('  ',p[:150])
r=d.get('report') or {}
c=r.get('company',{});print('\n##',c.get('legal_name'),'|',c.get('country'),'|',', '.join(l['exchange']+':'+l['ticker'] for l in c.get('listings',[])),'|',c.get('industry'),'(',c.get('industry_provenance'),')')
print('HEADLINE:',r.get('headline'))
for it in r.get('summary',[]): print(' -',it['date'],f\"[{it['materiality']}/{it['verification']}/{it['event_type']}]\",it['text'][:240],it['source_refs'])
for k,v in (r.get('sections') or {}).items():
  if v: print(f'\n  {k}:'); [print('    *',x['text'][:200],x.get('source_refs')) for x in v]
print('\nGUIDANCE:');[print('  ',g['metric'],g['target'],g['deadline'],g['status'],'|',(g['statement'] or '')[:180]) for g in r.get('management_guidance',[])]
print('\nSOURCES:');[print('  [%d] T%d %s | %s | %s'%(s['ref'],s['tier'],(s['published_at'] or '')[:10],s['title'][:90],s['url'])) for s in r.get('sources',[])]
print('\nGAPS:');[print('  ',g[:140]) for g in r.get('coverage',{}).get('gaps',[])]
print('\nVOICE (%d words):'%len(r.get('voice_script','').split()), r.get('voice_script'))
"
