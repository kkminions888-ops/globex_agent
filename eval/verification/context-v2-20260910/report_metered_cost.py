"""独立完整长对话成本复核；缺摘要或未知usage即阻断，不推算原调用消耗。"""
from pathlib import Path
import copy,hashlib,json,shutil,tarfile,sqlite3
from scripts.eval.report_context import score,summarize,paired_interval,paired_cost_interval,percentile

def manual_operation_timings(folder,row):
    if row['strategy']=='layered':
        return [r['elapsed_ms'] for r in row['compactions'] if r.get('summary_changed') and isinstance(r.get('elapsed_ms'),(int,float))]
    key=f"{row['case_id']}-{row['strategy']}-{row['repetition']}"
    path=folder/key/'evidence.db'
    if not path.exists():return []
    with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
        records=db.execute("SELECT payload,sha256 FROM context_evidence WHERE buyer=? AND session=? AND kind='compression' ORDER BY created",('context-eval-'+key,'s-'+key)).fetchall()
    payloads=[]
    for raw,digest in records:
        if hashlib.sha256(raw.encode()).hexdigest()!=digest:raise ValueError('整理计时证据校验失败: '+key)
        data=json.loads(raw)
        if data.get('summary_changed'):payloads.append(data)
    # 若有额外自动摘要，不能把它们冒充预定的两次手动整理。
    if len(payloads)!=2:return []
    return [r['elapsed_ms'] for r in payloads if isinstance(r.get('elapsed_ms'),(int,float))]


def report(folder,output):
    manifest=json.loads((folder/'manifest.json').read_text())
    cases={c['id']:c for c in json.loads((folder/'cases.json').read_text())}
    rows=score(copy.deepcopy(json.loads((folder/'results.json').read_text())),cases)
    protocol=json.loads((output.parent/'metered-cost-protocol.json').read_text())
    expected={(case,strategy,rep) for case in protocol['cases'] for strategy in protocol['strategies'] for rep in range(3)}
    actual={(r['case_id'],r['strategy'],r['repetition']) for r in rows}
    errors=[]
    if actual!=expected or len(rows)!=36:errors.append('成对运行不完整')
    if not manifest['code_stable']:errors.append('源码运行期间发生改变')
    if manifest.get('cases_sha256')!=protocol['cases_sha256']:errors.append('案例冻结指纹不一致')
    if manifest.get('transport')!=protocol['transport']:errors.append('请求参数不一致')
    audits=[];operation_samples={s:[] for s in protocol['strategies']};operation_complete={s:True for s in protocol['strategies']}
    for row in rows:
        summary=[u for u in row['usage'] if u['kind']=='summary']
        known=bool(row['usage']) and all(type(u.get(k)) is int and u[k]>=0 for u in row['usage'] for k in ['input_tokens','output_tokens'])
        known=known and all(u['input_tokens']>0 for u in row['usage'])
        complete=len(summary)>=2 and known
        if known:
            complete=complete and all(row[k]==sum(u[k] for u in row['usage']) for k in ['input_tokens','output_tokens'])
        if not complete:
            errors.append(f"计量不完整: {row['case_id']}/{row['strategy']}/{row['repetition']}")
            row['captured_input_tokens']=row['input_tokens']
            row['input_tokens']=None;row['output_tokens']=None
        try:timings=manual_operation_timings(folder,row)
        except (ValueError,sqlite3.Error) as error:
            timings=[];errors.append(str(error))
        operation_samples[row['strategy']].extend(timings)
        operation_complete[row['strategy']] &= len(timings)==2
        audits.append({'manual_compaction_ms':timings if len(timings)==2 else None,'case_id':row['case_id'],'strategy':row['strategy'],'repetition':row['repetition'],
            'summary_calls':len(summary),'business_calls':sum(u['kind']=='business' for u in row['usage']),
            'lookup_calls':row['lookup_calls'],'lookup_model_calls':None,
            'lookup_accounting_note':'回查后的模型调用计入business和累计输入；单独实际token归因未测，不能记零',
            'summary_input_tokens':sum(u['input_tokens'] for u in summary) if summary and all(u['input_tokens'] is not None for u in summary) else None,
            'complete':complete})
    stats=summarize(rows);a=stats.get('legacy',{});c=stats.get('layered',{})
    for strategy,values in stats.items():
        values['manual_compaction_timings_complete']=operation_complete[strategy]
        values['manual_compaction_p95_ms']=percentile(operation_samples[strategy],.95) if operation_complete[strategy] else None
        values['summary_model_p95_ms']=percentile([u['elapsed_ms'] for r in rows if r['strategy']==strategy for u in r['usage'] if u['kind']=='summary'],.95)
    cost=paired_cost_interval(rows,'legacy','layered');quality=paired_interval(rows,'legacy','layered')
    latency=c.get('ordinary_round_p95_ms')/a['ordinary_round_p95_ms'] if a.get('ordinary_round_p95_ms') and c.get('ordinary_round_p95_ms') else None
    if c.get('passed')!=18:errors.append('候选存在长对话失败')
    if c.get('success_rate',0)<a.get('success_rate',0):errors.append('候选任务成功率低于基线')
    if cost is None or cost['reduction']<.25:errors.append('完整实际输入降幅未达25%')
    if latency is None or latency>1.15:errors.append('普通轮P95劣化超过15%或未知')
    result={'gate':'PASS_METERED_LONG_DIALOGUE' if not errors else 'BLOCK','errors':errors,'runs':len(rows),
        'manifest':manifest,'protocol_sha256':hashlib.sha256((output.parent/'metered-cost-protocol.json').read_bytes()).hexdigest(),
        'summary':stats,'paired_quality_interval':quality,'paired_cost_interval':cost,'ordinary_round_latency_ratio':latency,
        'usage_audit':audits,'failures':[r for r in rows if not r['passed']],
        'limitations':'独立6场景、每组3次的完整长对话复核；按场景聚合重复。原主实验与网络恢复的成本缺摘要，不能混用。共享网关延迟不是生产流量P95。'}
    output.mkdir(parents=True,exist_ok=False)
    for name in ['results.json','manifest.json','cases.json']:shutil.copy(folder/name,output/name)
    (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    with tarfile.open(output/'database-evidence.tar.gz','w:gz') as tar:
        for p in folder.rglob('*.db'):tar.add(p,arcname=p.relative_to(folder))
    rows_text='\n'.join(f"|{s}|{v['passed']}/{v['runs']}|{v['long_input_tokens']}|{v['ordinary_round_p95_ms']}|" for s,v in stats.items())
    timing_text='\n'.join(f"|{s}|{v['manual_compaction_p95_ms']}|{v['summary_model_p95_ms']}|{v['scenario_latency_p95_ms']}|" for s,v in stats.items())
    (output/'report.md').write_text(f"# 完整摘要计量后的独立长对话复核\n\n门禁：**{result['gate']}**。36次预登记运行；所有业务、摘要、回查后的模型调用均纳入usage核对。\n\n|策略|通过/运行|累计实际输入token|普通轮P95毫秒|\n|---|---:|---:|---:|\n{rows_text}\n\n按场景配对的成本结果：`{json.dumps(cost,ensure_ascii=False)}`。普通轮P95比值：`{latency}`。\n\n|策略|手动整理操作P95毫秒|摘要模型调用P95毫秒|整段对话P95毫秒|\n|---|---:|---:|---:|\n{timing_text}\n\n手动整理包括治理、排队和生成；摘要模型调用不含闸门等待。整段对话包含24轮选购加1轮验收续答、两次整理与一次数据库恢复。缺少观测时保留null，不补零。\n\n阻断项：{json.dumps(errors,ensure_ascii=False)}。\n\n{result['limitations']}\n")
    hashes={str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file()}
    (output/'checksums.json').write_text(json.dumps(hashes,indent=2)+'\n')
    return result

if __name__=='__main__':
    result=report(Path('/tmp/context-metered-cost'),Path.cwd()/'eval/verification/context-v2-20260910/metered-cost')
    print(json.dumps({k:result[k] for k in ['gate','errors','summary','paired_cost_interval','ordinary_round_latency_ratio']},ensure_ascii=False))
