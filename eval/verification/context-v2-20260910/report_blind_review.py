"""匿名辅助评分汇总：以场景为统计单位，未知不补零。"""
from pathlib import Path
import hashlib,json,random,statistics

ROOT=Path.cwd(); OUT=ROOT/'eval/verification/context-v2-20260910'; FOLDER=OUT/'blind-review'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def mean(values):return statistics.mean(values) if values else None
def quantile(values,p):
    values=sorted(values);index=(len(values)-1)*p;lo=int(index);hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(index-lo)
def interval(values):
    if not values:return None
    rng=random.Random(2026091054)
    samples=[mean(rng.choices(values,k=len(values))) for _ in range(10000)]
    return [quantile(samples,.025),quantile(samples,.975)]

def main():
    from run_blind_review import RUBRIC,Review
    protocol=json.loads((FOLDER/'protocol.json').read_text());packets=json.loads((FOLDER/'packets.json').read_text());mapping=json.loads((FOLDER/'mapping.json').read_text());results=json.loads((FOLDER/'results.json').read_text())
    source=json.loads((OUT/'holdout-recovery/results.json').read_text())
    assert sha(OUT/'holdout-recovery/results.json')==protocol['source_sha256']
    assert hashlib.sha256(RUBRIC.encode()).hexdigest()==protocol['rubric_sha256']
    assert len(results)==len(packets)==protocol['cases']==28
    originals={(r['case_id'],r['strategy'],r['repetition']):r for r in source}
    aliases={(m['case_id'],m['response_id']):m for m in mapping}
    assert len(aliases)==len(mapping)==protocol['eligible_answers']
    for packet in packets:
        assert set(packet['payload'])=={'question','answers'}
        for answer in packet['payload']['answers']:
            assert set(answer)=={'response_id','answer'}
            item=aliases[(packet['case_id'],answer['response_id'])]
            original=originals[(item['case_id'],item['strategy'],item['repetition'])]
            assert hashlib.sha256(original['answer'].encode()).hexdigest()==item['original_answer_sha256']
            assert hashlib.sha256(answer['answer'].encode()).hexdigest()==item['anonymous_answer_sha256']
    dimensions=protocol['dimensions'];decoded=[];errors=[];usage=[]
    assert len({r['case_id'] for r in results})==28
    for packet,result in zip(packets,results):
        assert packet['case_id']==result['case_id']
        usage.extend(result['usage'])
        if result['error']:
            errors.append({'case_id':result['case_id'],'error':result['error']});continue
        ratings=Review.model_validate({'ratings':result['ratings']}).ratings
        actual=[r.response_id for r in ratings];expected=[a['response_id'] for a in packet['payload']['answers']]
        assert len(actual)==len(set(actual))==len(expected) and set(actual)==set(expected)
        for rating in ratings:
            item=aliases[(result['case_id'],rating.response_id)];values=rating.model_dump()
            decoded.append({**item,**values,'overall':mean([values[d] for d in dimensions])})
    summaries={};case_scores={}
    for strategy in ['legacy','deterministic','layered']:
        group=[r for r in decoded if r['strategy']==strategy];case_scores[strategy]={}
        for case_id in sorted({r['case_id'] for r in group}):
            peers=[r for r in group if r['case_id']==case_id]
            case_scores[strategy][case_id]={d:mean([r[d] for r in peers]) for d in [*dimensions,'overall']}
        summaries[strategy]={'rated_answers':len(group),'unknown_answers':84-len(group),'rated_scenarios':len(case_scores[strategy]),'scenario_mean':{d:mean([r[d] for r in case_scores[strategy].values()]) for d in [*dimensions,'overall']}}
    paired={}
    for reference in ['legacy','deterministic']:
        cases=sorted(set(case_scores[reference])&set(case_scores['layered']))
        paired[reference]={}
        for d in [*dimensions,'overall']:
            differences=[case_scores['layered'][c][d]-case_scores[reference][c][d] for c in cases]
            paired[reference][d]={'scenarios':len(cases),'mean_difference_C_minus_reference':mean(differences),'ci95':interval(differences)}
    complete=bool(usage) and all(type(u.get('input_tokens')) is int and u['input_tokens']>0 and type(u.get('output_tokens')) is int and u['output_tokens']>=0 for u in usage)
    report={'scope':'辅助单模型表达质量盲评，无预设硬分数门槛，不替代事实安全验收','source':'holdout-recovery/results.json','judge_model':protocol['judge_model'],'summary':summaries,'paired_scenario_difference':paired,'errors':errors,'excluded':protocol['excluded'],'usage':{'calls':len(usage),'complete':complete,'input_tokens':sum(u['input_tokens'] for u in usage) if complete else None,'output_tokens':sum(u['output_tokens'] for u in usage) if complete else None,'cost_scope':protocol['cost_scope']},'limitations':protocol['limitations'],'coverage':{'logical_answers':252,'eligible_answers':len(mapping),'rated_answers':len(decoded),'unknown_answers':252-len(decoded)}}
    for name,value in [('decoded-ratings.json',decoded),('scenario-scores.json',case_scores),('report.json',report)]:
        (FOLDER/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    table='\n'.join('|'+s+'|'+str(v['rated_answers'])+'/84|'+str(v['rated_scenarios'])+'|'+('|'.join('未知' if v['scenario_mean'][d] is None else f"{v['scenario_mean'][d]:.3f}" for d in [*dimensions,'overall']))+'|' for s,v in summaries.items())
    delta=paired['legacy']['overall'];ci=delta['ci95']
    comparison='配对证据不足。' if ci is None else f"C相对A综合分的场景配对平均差为 {delta['mean_difference_C_minus_reference']:+.3f}，95% bootstrap区间 [{ci[0]:+.3f}, {ci[1]:+.3f}]（{delta['scenarios']}个场景，场景内重复先取均值）。区间跨零时不宣称显著更优。"
    text=f'''# 辅助解释质量盲评

28个场景、252个逻辑答案中，{len(decoded)}份完成评分；{252-len(decoded)}份未知。B组一次真实工具JSON错误没有有效答案，保留失败，表达分数不补零。评委错误共{len(errors)}场。

答案随机排序，仅向评委发送买家问题、匿名编号和答案；策略、重复编号、正确性与成本均隐藏。原回答hash、匿名映射、固定rubric和逐次评委输出一并保留。每个场景只评一次，没有按得分择优重评。

|策略|有效答案|场景|清晰度|贴合度|时效表达|综合分|
|---|---:|---:|---:|---:|---:|---:|
{table}

分数范围0～4，三个维度等权，表中均按场景等权。{comparison}

评委为qwen3-max，与被评模型同型号。这是单模型辅助盲评，人工评审、跨评委一致性仍未知；不验证数据库事实，不替代SKU、金额、归属与审批等确定性安全断言。未预设表达分数硬门槛，不在事后添加有利阈值。

评委调用{len(usage)}次，计量完整={complete}，实际输入{report['usage']['input_tokens']}、输出{report['usage']['output_tokens']} token；这些消耗独立于买家长对话成本实验。无完整usage时总量标未知。

[冻结规则](protocol.json) · [匿名输入](packets.json) · [匿名映射](mapping.json) · [逐次输出](results.json) · [分组与配对统计](report.json)
'''
    (FOLDER/'report.md').write_text(text)
    audit={'passed':True,'source_hash_verified':True,'rubric_hash_verified':True,'anonymous_payload_fields_verified':True,'answer_mapping_hashes_verified':len(mapping),'rating_ids_and_schema_verified':True,'rated_answers':len(decoded),'errors_preserved':len(errors),'unknown_not_zero':True,'statistical_unit':'scenario'}
    (FOLDER/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    checks={p.name:sha(p) for p in sorted(FOLDER.iterdir()) if p.is_file() and p.name!='checksums.json'}
    (FOLDER/'checksums.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
