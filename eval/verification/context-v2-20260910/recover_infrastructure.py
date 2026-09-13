"""主实验不覆盖；按预先登记的基础设施规则成组复测，单独生成恢复分析。"""
from pathlib import Path
import copy,datetime,hashlib,json,os,shutil,subprocess,sys,tarfile,time

ROOT=Path.cwd();OUT=ROOT/'eval/verification/context-v2-20260910'
FROZEN=Path('/tmp/globex-context-v3-accepted-frozen');PRIMARY=Path('/tmp/context-v3-accepted/holdout')
# 复测调用、评分和归档只使用主实验已经冻结的实现。
sys.path.insert(0,str(FROZEN))
from app.infrastructure.settings import load_settings, _load_environment
_load_environment(ROOT/".env")
from context_reason_review import review_score as score
import scripts.eval.report_context as report_module
from scripts.eval.package_context import package


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def main():
    protocol_path=OUT/'infrastructure-recovery-protocol.json'
    protocol=json.loads(protocol_path.read_text())
    while not (OUT/'holdout/checksums.json').exists():time.sleep(5)
    manifest=json.loads((PRIMARY/'manifest.json').read_text())
    assert manifest['completed']==252 and manifest['code_stable'] and manifest['status']=='completed'
    original=json.loads((PRIMARY/'results.json').read_text())
    cases={c['id']:c for c in json.loads((PRIMARY/'cases.json').read_text())}
    graded=score(copy.deepcopy(original),cases)
    def infrastructure(row):
        return bool(row.get('error')) and row['error'].split(':',1)[0] in protocol['eligible_error_types']
    affected=sorted({(r['case_id'],r['repetition']) for r in original if infrastructure(r)})
    plan={'created_at':datetime.datetime.now().astimezone().isoformat(),'protocol_sha256':hashlib.sha256(protocol_path.read_bytes()).hexdigest(),'primary_results_sha256':hashlib.sha256((PRIMARY/'results.json').read_bytes()).hexdigest(),'affected_blocks':[{'case_id':c,'repetition':r} for c,r in affected],'minimum_selected_supplementary_runs':3*len(affected),'maximum_supplementary_runs':3*len(affected)*protocol['maximum_recovery_attempts_per_block'],'selection':'全部符合基础设施错误规则的整组；不基于答案选择'}
    (OUT/'infrastructure-recovery-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    if not affected:
        print('NO_INFRASTRUCTURE_RECOVERY_NEEDED',flush=True);return
    for case_id,repetition in affected:
        peers=[r for r in graded if r['case_id']==case_id and r['repetition']==repetition]
        assert len(peers)==3
        assert not [r for r in peers if not r['passed'] and not infrastructure(r)],'整组存在业务失败，不允许基础设施补测掩盖'
    settings=load_settings();env=os.environ.copy();env.update(LLM_API_KEY=settings.llm_api_key,LLM_BASE_URL=settings.llm_base_url,LLM_MODEL=settings.llm_model,LANGFUSE_BASE_URL='',LANGFUSE_PUBLIC_KEY='',LANGFUSE_SECRET_KEY='')
    assert settings.llm_model==manifest['model']
    replacements={};recovery_folders={};supplementary=[];all_attempts=[];attempt_records=[]
    for case_id,repetition in affected:
        for attempt in range(1,protocol['maximum_recovery_attempts_per_block']+1):
            run_folder=Path('/tmp/context-v3-infrastructure-recovery')/f'{case_id}-original-{repetition}-attempt-{attempt}'
            assert not run_folder.exists(),'不可覆盖或重复使用已有尝试'
            run_folder.parent.mkdir(exist_ok=True)
            log_path=run_folder.with_suffix('.log')
            with log_path.open('w') as log:
                result=subprocess.run([sys.executable,'-m','scripts.eval.run_context','--split','holdout','--cases-file','eval/context/cases-v3.json','--case',case_id,'--timing','pressure','--concurrency','2','--min-interval-seconds','5','--model-retries','0','--repetitions','1','--output',str(run_folder)],cwd=FROZEN,env=env,stdout=log,stderr=subprocess.STDOUT)
            assert result.returncode==0,'基础设施补测执行失败'
            rows=json.loads((run_folder/'results.json').read_text());run_manifest=json.loads((run_folder/'manifest.json').read_text())
            assert run_manifest['code_stable'] and run_manifest['fingerprint']==manifest['fingerprint']
            assert run_manifest['transport']==manifest['transport'] and run_manifest['cases_sha256']==manifest['cases_sha256']
            assert len(rows)==3 and {r['strategy'] for r in rows}==set(manifest['strategies'])
            dest=OUT/'infrastructure-recovery'/run_folder.name;dest.mkdir(parents=True)
            for name in ['results.json','manifest.json','cases.json']:shutil.copy(run_folder/name,dest/name)
            shutil.copy(log_path,dest/'run.log')
            with tarfile.open(dest/'database-evidence.tar.gz','w:gz') as tar:
                for p in run_folder.rglob('*.db'):tar.add(p,arcname=p.relative_to(run_folder))
            scored=score(copy.deepcopy(rows),cases)
            complete=all(r['passed'] and r['input_tokens'] is not None and r['output_tokens'] is not None for r in scored)
            all_attempts.extend(rows)
            attempt_records.append({'case_id':case_id,'original_repetition':repetition,'attempt':attempt,'complete_observable':complete,'result_sha256':hashlib.sha256((run_folder/'results.json').read_bytes()).hexdigest(),'errors':[{'strategy':r['strategy'],'error':r['error'],'passed':r['passed']} for r in scored if not r['passed']],'folder':str(dest.relative_to(OUT))})
            (OUT/'infrastructure-recovery-attempts.json').write_text(json.dumps(attempt_records,ensure_ascii=False,indent=2)+'\n')
            assert not [r for r in scored if not r['passed'] and not infrastructure(r)],'出现真实执行或断言失败，禁止再试'
            if complete:break
            assert any(infrastructure(r) for r in scored),'只有usage缺失但无基础设施异常，不可补跑取优'
            assert attempt<protocol['maximum_recovery_attempts_per_block'],'基础设施恢复次数用尽，停止验收'
            time.sleep(protocol['recovery_retry_cooldown_seconds'])
        for row in rows:
            key=(case_id,row['strategy'],repetition);replacements[key]=row;recovery_folders[key]=run_folder/f"{case_id}-{row['strategy']}-0"
        supplementary.extend(rows)
        print(json.dumps({'block':case_id,'original_repetition':repetition,'recovery_passed':3},ensure_ascii=False),flush=True)
    analysis=Path('/tmp/context-v3-recovered-analysis');assert not analysis.exists();analysis.mkdir()
    combined=[];lineage=[]
    for row in original:
        key=(row['case_id'],row['strategy'],row['repetition']);replacement=replacements.get(key)
        result=copy.deepcopy(replacement if replacement is not None else row)
        result['repetition']=row['repetition']
        source='supplementary_full_block' if replacement is not None else 'primary'
        result['evidence_source']=source
        combined.append(result)
        lineage.append({'case_id':key[0],'strategy':key[1],'logical_repetition':key[2],'source':source,'original_row_sha256':digest(row),'selected_physical_repetition':replacement['repetition'] if replacement is not None else key[2],'selected_row_sha256':digest(replacement if replacement is not None else row)})
        physical=recovery_folders.get(key,PRIMARY/f'{key[0]}-{key[1]}-{key[2]}');logical=analysis/f'{key[0]}-{key[1]}-{key[2]}';logical.mkdir()
        (logical/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        for name in ['evidence.db','sessions.db']:
            if (physical/name).exists() and not score([copy.deepcopy(result)],cases)[0]['passed']:shutil.copy(physical/name,logical/name)
    recovery_manifest={**manifest,'passed':sum(r['passed'] for r in combined),'analysis_kind':'matched_infrastructure_recovery_with_explicit_reason_review','primary_report':'../holdout/report.json','protocol':'../infrastructure-recovery-protocol.json','supplementary_runs':len(all_attempts),'selected_supplementary_runs':len(supplementary),'observations_in_analysis':252,'primary_rows_replaced_as_whole_blocks':len(replacements),'primary_failures_preserved':True}
    (analysis/'manifest.json').write_text(json.dumps(recovery_manifest,ensure_ascii=False,indent=2)+'\n')
    (analysis/'results.json').write_text(json.dumps(combined,ensure_ascii=False,indent=2)+'\n')
    shutil.copy(PRIMARY/'cases.json',analysis/'cases.json')
    (OUT/'infrastructure-recovery-lineage.json').write_text(json.dumps(lineage,ensure_ascii=False,indent=2)+'\n')
    report_module.score=score
    package(analysis,OUT/'holdout-recovery')
    report_path=OUT/'holdout-recovery/report.json'
    reviewed_report=json.loads(report_path.read_text())
    reviewed_report['frozen_grading_version']=reviewed_report['grading_version']
    reviewed_report['grading_version']='context-facts-v5-plus-explicit-reason-review'
    reviewed_report['reason_review_source']='../context_reason_review.py'
    reviewed_report['reason_review_changes']=[{k:r[k] for k in ['case_id','strategy','repetition','reason_review']} for r in score(copy.deepcopy(combined),cases) if r.get('reason_review',{}).get('changed_verdict')]
    report_path.write_text(json.dumps(reviewed_report,ensure_ascii=False,indent=2)+'\n')
    checks_path=OUT/'holdout-recovery/checksums.json';checks=json.loads(checks_path.read_text());checks['report.json']=hashlib.sha256(report_path.read_bytes()).hexdigest();checks['grading_review_source_sha256']=hashlib.sha256((OUT/'context_reason_review.py').read_bytes()).hexdigest();checks_path.write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
    summary={'primary_runs':252,'supplementary_runs':len(all_attempts),'selected_supplementary_runs':len(supplementary),'valid_matched_analysis_runs':252,'primary_gate':json.loads((OUT/'holdout/report.json').read_text())['gate'],'recovery_gate':json.loads((OUT/'holdout-recovery/report.json').read_text())['gate'],'primary_total_usage':None,'primary_known_input_tokens_lower_bound':sum(u['input_tokens'] for r in original for u in r['usage'] if u['input_tokens'] is not None),'supplementary_input_tokens':sum(r['input_tokens'] for r in all_attempts) if all(r['input_tokens'] is not None for r in all_attempts) else None,'supplementary_known_input_tokens_lower_bound':sum(u['input_tokens'] for r in all_attempts for u in r['usage'] if u['input_tokens'] is not None),'selected_supplementary_input_tokens':sum(r['input_tokens'] for r in supplementary),'unknown_usage_preserved':True,'meaning':'恢复分析用于可观测的完整成对任务比较；主实验中的网络失败和总成本未知仍单独报告。'}
    (OUT/'infrastructure-recovery-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
