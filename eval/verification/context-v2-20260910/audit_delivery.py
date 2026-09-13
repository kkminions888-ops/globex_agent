"""交付前核对原始记录、整组恢复来源与冻结代码，不能用新结果覆盖原记录。"""
from pathlib import Path
import copy,hashlib,json,tarfile
from context_reason_review import review_score

ROOT=Path.cwd();OUT=ROOT/'eval/verification/context-v2-20260910'
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(row):return hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def key(row):return row['case_id'],row['strategy'],row['repetition']

def main(quality_only=False):
    primary=json.loads((OUT/'holdout/results.json').read_text());combined=json.loads((OUT/'holdout-recovery/results.json').read_text());lineage=json.loads((OUT/'infrastructure-recovery-lineage.json').read_text());plan=json.loads((OUT/'infrastructure-recovery-plan.json').read_text());protocol=json.loads((OUT/'infrastructure-recovery-protocol.json').read_text());attempts=json.loads((OUT/'infrastructure-recovery-attempts.json').read_text());cases={c['id']:c for c in json.loads((OUT/'holdout/cases.json').read_text())}
    assert sha(OUT/'holdout/results.json')==plan['primary_results_sha256']
    assert sha(OUT/'infrastructure-recovery-protocol.json')==plan['protocol_sha256']
    before={key(r):r for r in primary};after={key(r):r for r in combined}
    assert len(before)==len(after)==len(lineage)==252 and set(before)==set(after)
    affected={(p['case_id'],p['repetition']) for p in plan['affected_blocks']}
    selected={};all_count=0
    for block in affected:
        trials=[a for a in attempts if (a['case_id'],a['original_repetition'])==block]
        assert trials and len(trials)<=protocol['maximum_recovery_attempts_per_block']
        assert [a['attempt'] for a in trials]==list(range(1,len(trials)+1))
        assert [a['complete_observable'] for a in trials]==[False]*(len(trials)-1)+[True]
        for a in trials:
            folder=OUT/a['folder'];assert sha(folder/'results.json')==a['result_sha256']
            rows=json.loads((folder/'results.json').read_text());all_count+=len(rows)
            assert len(rows)==3 and {r['strategy'] for r in rows}=={'legacy','deterministic','layered'}
            reviewed=review_score(copy.deepcopy(rows),cases)
            assert all(r['passed'] or (r.get('error') or '').split(':',1)[0] in protocol['eligible_error_types'] for r in reviewed)
            if a['complete_observable']:
                assert all(r['passed'] and r['input_tokens'] is not None and r['output_tokens'] is not None for r in reviewed)
                for r in rows:selected[(block[0],r['strategy'],block[1])]=r
            with tarfile.open(folder/'database-evidence.tar.gz') as tar:assert not any(m.issym() or m.islnk() for m in tar)
    assert len(selected)==3*len(affected)
    for item in lineage:
        k=(item['case_id'],item['strategy'],item['logical_repetition']);assert digest(before[k])==item['original_row_sha256']
        source=selected.get(k,before[k]);assert digest(source)==item['selected_row_sha256']
        restored=copy.deepcopy(after[k]);kind=restored.pop('evidence_source');restored['repetition']=item['selected_physical_repetition']
        assert digest(restored)==digest(source)
        assert kind==('supplementary_full_block' if k in selected else 'primary')
    summary=json.loads((OUT/'infrastructure-recovery-summary.json').read_text());report=json.loads((OUT/'holdout-recovery/report.json').read_text())
    assert summary['supplementary_runs']==all_count and summary['selected_supplementary_runs']==len(selected)
    assert report['summary']['layered']['passed']==84 and not [r for r in report['failures'] if r['strategy']=='layered']
    assert report.get('cost_accounting_valid') is False
    if quality_only:
        result={'passed':True,'primary_records_unchanged':252,'analysis_records_verified':252,'selected_whole_blocks':len(affected),'supplementary_attempt_runs':all_count,'first_complete_block_rule_verified':True,'cost_acceptance':False}
        (OUT/'quality-lineage-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False));return
    metered=json.loads((OUT/'metered-cost/report.json').read_text())
    assert metered['gate']=='PASS_METERED_LONG_DIALOGUE' and metered['runs']==36
    assert all(r['complete'] and r['summary_calls']>=2 for r in metered['usage_audit'])
    assert sha(OUT/'context_reason_review.py')==json.loads((OUT/'holdout-recovery/checksums.json').read_text())['grading_review_source_sha256']
    from scripts.eval.run_context import fingerprint
    assert fingerprint()==metered['manifest']['fingerprint']
    assert (OUT/'frozen-release-source.tar.gz').read_bytes()==(OUT/'frozen-metered-source.tar.gz').read_bytes()
    assert json.loads((OUT/'selection-api.json').read_text())['restart_restored_selection']
    assert json.loads((OUT/'final-source-api.json').read_text())['passed']
    assert json.loads((OUT/'requirement-question-api.json').read_text())['passed']
    assert json.loads((OUT/'metered-sdk-api.json').read_text())['passed']
    activation=json.loads((OUT/'activation.json').read_text())
    assert activation['activated'] and activation['strategy']=='layered' and activation['timing']=='pressure'
    assert activation['existing_session_ids_preserved'] and activation['existing_chat_history_preserved']
    assert activation['chat_histories_verified']==activation['prior_session_count']
    assert json.loads((OUT/'blind-review/audit.json').read_text())['passed']
    for name,value in json.loads((OUT/'blind-review/prepared-inputs-checksums.json').read_text()).items():assert sha(OUT/name)==value
    for folder in [OUT/'dev',OUT/'holdout',OUT/'holdout-recovery',OUT/'metered-cost',OUT/'blind-review']:
        checks=json.loads((folder/'checksums.json').read_text())
        for name,value in checks.items():
            if (folder/name).is_file():assert sha(folder/name)==value,(folder,name)
    with tarfile.open(OUT/'holdout-recovery/failure-evidence.tar.gz') as tar:assert not any(m.issym() or m.islnk() for m in tar)
    result={'passed':True,'primary_records_unchanged':252,'analysis_records_verified':252,'selected_whole_blocks':len(affected),'supplementary_attempt_runs':all_count,'selected_supplementary_runs':len(selected),'source_frozen':True,'independent_full_usage_runs':36,'old_cost_metrics_rejected':True,'original_unknown_usage_preserved':True,'original_grading_preserved':True,'first_complete_block_rule_verified':True,'portable_failure_archives':True}
    (OUT/'delivery-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':
    import sys
    main('--quality-only' in sys.argv)
