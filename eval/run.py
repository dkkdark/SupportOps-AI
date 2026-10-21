import argparse
import hashlib
import json
import logging
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from supportops.config import Settings
from supportops.main import create_app
from supportops.workflow import PROMPT_VERSION

ROOT=Path(__file__).resolve().parents[1]


def run(split='holdout',live=False,output=None):
    dataset=json.loads((ROOT/'eval/dataset.json').read_text())
    cases=[c for c in dataset['cases'] if c['split']==split]
    orders=json.loads((ROOT/'data/orders.json').read_text())
    for order in orders:
        if order.get('delivered_at'):
            order['delivered_at']=(date.today()-timedelta(days=2)).isoformat()
    def handle(request):
        if request.url.path=='/health/live':return httpx.Response(200,json={'status':'ok'})
        order=next((o for o in orders if request.url.path=='/orders/'+o['id']),None)
        return httpx.Response(200 if order else 404,json=order or {})
    results=[]
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpx2').setLevel(logging.WARNING)
    logging.getLogger('supportops').setLevel(logging.WARNING)
    config=Settings()
    if live and (not config.llm_api_key or not config.llm_model):
        raise SystemExit('--live requires LLM_API_KEY and LLM_MODEL')
    with tempfile.TemporaryDirectory() as temp:
        config=config.model_copy(update={'database_url':f'sqlite:///{temp}/eval.db','model_provider':'openai_compatible' if live else 'baseline','app_env':'evaluation','app_api_key':'','fault_injection_enabled':True})
        with TestClient(create_app(config,orders_transport=httpx.MockTransport(handle))) as client:
            for case in cases:
                t=client.post('/api/tickets',json=case['input']).json()
                response=client.post(f"/api/tickets/{t['id']}/analyze",json={'fault':case['fault']})
                result=response.json().get('result',{})
                predicted=result.get('categories',[])
                expected=set(case['expected_categories'])
                expected_manual=case['expected_reason'] is not None
                fact_ids=set(result.get('selection',{}).get('fact_ids',[]))
                good_facts=not result.get('draft') if expected_manual else bool(result.get('draft')) and set(case['required_fact_ids']) <= fact_ids and all(c['passed'] for c in result.get('checks',[]))
                good_sources=all(d['id']!='returns-v0' for d in result.get('sources',[]))
                if result.get('draft'):
                    good_facts=good_facts and all(result['facts'][fid] in result['draft'] for fid in fact_ids) and '14 календарных' not in result['draft']
                reason_ok=result.get('reason')==case['expected_reason']
                passed=set(predicted)==expected and reason_ok and good_facts and good_sources
                results.append({'id':case['id'],'description':case['description'],'passed':passed,'expected_categories':sorted(expected),'predicted_categories':predicted,'expected_reason':case['expected_reason'],'reason':result.get('reason'),'fact_check':good_facts and good_sources,'duration_ms':result.get('duration_ms',0),'cost_usd':result.get('usage',{}).get('cost_usd',0),'input_tokens':result.get('usage',{}).get('input_tokens',0),'output_tokens':result.get('usage',{}).get('output_tokens',0),'manifest':result.get('manifest',{})})
    f1s=[]
    for label in ['delivery','cancellation','returns','damage','payment','other']:
        tp=sum(label in r['expected_categories'] and label in r['predicted_categories'] for r in results)
        fp=sum(label not in r['expected_categories'] and label in r['predicted_categories'] for r in results)
        fn=sum(label in r['expected_categories'] and label not in r['predicted_categories'] for r in results)
        f1s.append(2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 1)
    manual=[r for r in results if r['expected_reason']]
    durations=sorted(r['duration_ms'] for r in results)
    report={'status':'passed' if all(r['passed'] for r in results) else 'failed','created_at':datetime.now(timezone.utc).isoformat(),'dataset_version':dataset['version'],'dataset_sha256':hashlib.sha256((ROOT/'eval/dataset.json').read_bytes()).hexdigest(),'seed':dataset['seed'],'split':split,'cases':len(cases),'passed':sum(r['passed'] for r in results),'live':live,'model':config.llm_model if live else 'deterministic-baseline-v1','prompt_version':PROMPT_VERSION,'code_version':config.code_version,'metrics':{'classification_accuracy':sum(set(r['expected_categories'])==set(r['predicted_categories']) for r in results)/len(results),'macro_f1':sum(f1s)/len(f1s),'factual_accuracy':sum(r['fact_check'] for r in results)/len(results),'escalation_recall':sum(bool(r['reason']) for r in manual)/len(manual) if manual else 1,'p50_ms':durations[len(durations)//2],'p95_ms':durations[min(len(durations)-1,int(len(durations)*.95))],'cost_usd':sum(r['cost_usd'] for r in results),'input_tokens':sum(r['input_tokens'] for r in results),'output_tokens':sum(r['output_tokens'] for r in results)},'thresholds':{'case_pass_rate':1.0,'factual_accuracy':1.0,'escalation_recall':1.0,'macro_f1':0.95},'results':results}
    destination=Path(output or ROOT/'reports/evaluation.json')
    destination.parent.mkdir(exist_ok=True,parents=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(f"{report['status'].upper()}: {report['passed']}/{len(cases)} {split} cases; macro-F1={report['metrics']['macro_f1']:.3f}; report={destination}")
    for r in results:
        if not r['passed']:print(f"  FAIL {r['id']}: category={r['predicted_categories']} reason={r['reason']} expected={r['expected_reason']} facts={r['fact_check']}")
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--split',choices=['dev','holdout'],default='holdout')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--output')
    args=parser.parse_args()
    report=run(args.split,args.live,args.output)
    raise SystemExit(0 if report['status']=='passed' else 1)
