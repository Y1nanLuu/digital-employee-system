"""Deterministic checks; the LLM cannot waive these findings."""
from decimal import Decimal
from datetime import date,datetime
from zoneinfo import ZoneInfo
import re
from . import config

def evaluate(payload,policy,grade='staff'):
    p=policy['parameters'];amount=Decimal(payload['amount']);category=payload['category']
    inv=payload.get('invoice') or {};d=payload.get('details') or {}
    hard=[];manual=[];facts={}
    def flag(code,message,reject=False):
        (hard if reject else manual).append({'code':code,'message':message})
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if amount>Decimal(p['claim_limit']):flag('GEN-02','申报超过制度受理上限',True)
    if amount>Decimal(p['auto_limit']):flag('GEN-03',f"金额超过自动阈值{p['auto_limit']}元")
    if (today-date.fromisoformat(payload['expense_date'])).days>p['expense_days']:flag('GEN-04','费用超30日，须补充逾期说明')
    if not payload.get('department','').strip():flag('GEN-01','请补充归属部门')
    if re.search(r'私人|个人消费|无关业务|非业务',payload.get('description','')):flag('GEN-01','费用业务用途需要核对')
    if category!='出差餐补':
        missing=[name for name in ['invoice_no','invoice_date','buyer','seller','total','item_name'] if not inv.get(name)]
        if missing:flag('INV-01','票面字段待补充：'+','.join(missing))
        number=inv.get('invoice_no','')
        code=inv.get('invoice_code','')
        if not (re.fullmatch(r'\d{20}',number) or (re.fullmatch(r'\d{8}',number) and re.fullmatch(r'\d{10}|\d{12}',code))):flag('INV-02','票号/代码格式需要核对')
        if inv.get('buyer')!=config.COMPANY_TITLE:flag('INV-03','购买方与配置企业抬头不符或缺失')
        if inv.get('invoice_date') and date.fromisoformat(inv['invoice_date'])>today:flag('INV-01','开票日期晚于今天')
        if inv.get('total'):
            total=Decimal(inv['total'])
            if total<=0:flag('INV-04','票面为零或负额，须走冲销核对',True)
            if amount>total:flag('INV-07','申报金额超过票面含税总额',True)
            elif amount<total:flag('INV-07','部分报销须核对差异说明，一票仍只能报销一次')
    if category=='交通费':
        if d.get('seat') not in ['二等座','硬座','硬卧','经济舱','市内交通']:flag('TRV-01','请核对交通席位标准')
        if not d.get('route'):flag('TRV-02','请补充因公路线或行程说明')
    elif category=='住宿费':
        city=d.get('city','').removesuffix('市').strip()
        if not city:flag('TRV-03','请补充住宿城市')
        tier=next((k for k,v in p['cities'].items() if city in v),None)
        if city and tier is None:flag('TRV-03','城市不在当前分档表，请人工确认适用标准')
        if d.get('check_in') and d.get('check_out'):
            nights=(date.fromisoformat(d['check_out'])-date.fromisoformat(d['check_in'])).days
            if nights<=0:flag('TRV-03','离店须晚于入住，须核对住宿晚数')
            else:
                limit=Decimal(p['hotel'].get(grade,p['hotel']['staff'])[tier or 'C'])*nights
                facts.update(nights=nights,city_tier=tier or '待确认',hotel_limit=format(limit,'.2f'))
                if amount>limit:flag('TRV-05','住宿超标准，须说明例外或退回修改金额')
        else:flag('TRV-03','请补充入住和离店日期')
        if not d.get('approval_no') or not d.get('proof_reference'):flag('TRV-03','住宿缺少出差审批或住宿清单')
    elif category=='业务招待费':
        flag('ENT-01','招待费用统一人工审批')
        count=d.get('attendees',0)
        if count<=0 or not d.get('business_partner') or not d.get('approval_no'):flag('ENT-01','请补充招待人数、对象和事前审批')
        if amount>Decimal(p['entertainment_event']) or (count>0 and amount>Decimal(p['entertainment_person'])*count):flag('ENT-02','招待超过人均或单次标准，须说明')
    elif category=='办公用品':
        if not d.get('proof_reference'):flag('OFF-01','请补充办公用品明细或收货说明')
        if amount>Decimal(p['auto_limit']) and not d.get('approval_no'):flag('OFF-01','大额办公采购缺少审批编号')
    elif category=='通讯费':
        flag('TEL-01','通讯费用按月份人工核对')
        if not d.get('service_period'):flag('TEL-01','请补充通讯月份')
        elif not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',d['service_period']):flag('TEL-01','通讯月份格式需要修正，应为YYYY-MM')
        if amount>Decimal(p['phone_limit']):flag('TEL-01','通讯申报超过单次标准')
    elif category in ['培训会议费','软件服务费']:
        code='EDU-01' if category=='培训会议费' else 'IT-01'
        flag(code,'此类费用须人工核对业务和审批材料')
        if not d.get('approval_no') or not d.get('proof_reference'):flag(code,'请补充审批/合同和业务材料说明')
        if category=='软件服务费' and not d.get('service_period'):flag(code,'请补充软件服务期间')
    elif category=='出差餐补':
        flag('MEAL-01','出差餐补按证明人工审批')
        if not d.get('approval_no'):flag('MEAL-01','请补充出差审批')
        if d.get('trip_start') and d.get('trip_end'):
            days=(date.fromisoformat(d['trip_end'])-date.fromisoformat(d['trip_start'])).days+1
            meals=[d.get('supplied_breakfasts',0),d.get('supplied_lunches',0),d.get('supplied_dinners',0)]
            if days<=0 or any(x>days for x in meals):flag('MEAL-02','出差日期或供餐次数需要修正')
            else:
                limit=max(Decimal(0),days*Decimal(p['meal_day'])-sum(n*Decimal(p[k]) for n,k in zip(meals,['breakfast','lunch','dinner'])))
                facts.update(trip_days=days,meal_limit=format(limit,'.2f'))
                if amount>limit:flag('MEAL-02','餐补超出扣除已供餐后的标准')
        else:flag('MEAL-01','请补充出差起止日期')
    else:flag('OTH-01','其他类别须人工确认适用依据')
    return {'hard':hard,'manual':manual,'facts':facts}
