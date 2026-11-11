"""Run the 198 synthetic policy and hotel boundary oracles without an LLM."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'backend'))
from app.enterprise_rules import evaluate
from app import policies,config

source=ROOT/'docs/test-evidence/enterprise-v2/test-cases.jsonl'
cases=[json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()]
config.COMPANY_TITLE='示例科技有限公司'
passed=0;failures=[]
for case in cases:
    if case['layer'] not in ['policy_and_llm','hotel_boundary']:continue
    actual=evaluate(case['input'],policies.current(),case.get('identity_fixture',{}).get('grade','staff'))
    hard=sorted(set(x['code'] for x in actual['hard']));manual=sorted(set(x['code'] for x in actual['manual']))
    if hard==case['expected']['hard_codes'] and manual==case['expected']['manual_codes']:passed+=1
    else:failures.append({'id':case['id'],'hard':hard,'manual':manual,'expected':case['expected']})
result={'suite':'enterprise-v2 deterministic policy','passed':passed,'failed':len(failures),'failures':failures,'llm_calls':0}
(ROOT/'work').mkdir(exist_ok=True);(ROOT/'work/enterprise-rule-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False));sys.exit(bool(failures))
