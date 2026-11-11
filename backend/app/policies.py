"""Versioned policy snapshots. Only server configuration can publish a version."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timezone
import json,os
from .models import PolicyVersion

DEFAULT_PATH=Path(__file__).parent/'data/enterprise-policy.json'

def current():
    path=Path(os.getenv('ENTERPRISE_POLICY_FILE',str(DEFAULT_PATH)))
    policy=json.loads(path.read_text(encoding='utf-8'))
    from decimal import Decimal
    p=policy['parameters']
    if not policy['version'] or not 0<Decimal(p['auto_limit'])<=Decimal(p['claim_limit'])<=5000:
        raise ValueError('制度阈值无效')
    for rule in policy['rules']:
        if rule['id']=='GEN-03':rule['text']=f"无风险且申报不超{p['auto_limit']}元、有效模型意见为通过时可自动初审；超阈值交人工。"
        if rule['id']=='GEN-02':rule['text']=f"申报金额大于0且不超过{p['claim_limit']}元，最多两位小数，票面总额独立保存。"
    return policy

def register(db):
    policy=current()
    prior=db.get(PolicyVersion,policy['version'])
    if prior and prior.document!=policy:raise ValueError('制度内容变化必须发布新的版本号')
    if not prior:
        db.add(PolicyVersion(id=policy['version'],document=policy,created_at=datetime.now(timezone.utc).isoformat()))
        db.commit()
    return deepcopy(policy)

def for_task(db,task):
    version=db.get(PolicyVersion,task.policy_version)
    if not version:raise ValueError('任务制度快照缺失')
    return deepcopy(version.document)

def retrieve(policy,category):
    return [{**r,'source':policy['title'],'version':policy['version'],'retrieved_at':datetime.now(timezone.utc).isoformat()}
            for r in policy['rules'] if '*' in r['categories'] or category in r['categories']]
