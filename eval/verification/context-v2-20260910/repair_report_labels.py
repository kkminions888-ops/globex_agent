"""只修报告展示标签，不修改冻结评分、JSON指标或原始答案。"""
from pathlib import Path
import hashlib,json

def repair(root):
    root=Path(root);changes=[]
    for name in ['dev','holdout','holdout-recovery']:
        folder=root/name
        if not (folder/'report.md').exists():continue
        cases=json.loads((folder/'cases.json').read_text());report=json.loads((folder/'report.json').read_text())
        rounds=sorted({c.get('fixture',{}).get('rounds',20) for c in cases if c['mode']=='long' and c['split']==report['manifest']['split']})
        before=(folder/'report.md').read_text();after=before
        if len(rounds)==1:
            after=after.replace('长对话实际运行20轮、两次整理、一次数据库重建。',f'长对话按冻结案例实际运行{rounds[0]}轮选购历史加1轮验收续答、两次整理、一次数据库重建。')
        if report.get('manifest',{}).get('split')=='holdout':
            old=f"长对话输入下降：{report['long_input_reduction']}；按场景配对区间：{report['paired_interval']}。"
            new=f"长对话输入下降：{report['long_input_reduction']}；成本按场景配对区间：{report['long_input_paired_interval']}。"
            after=after.replace(old,new)
        if after==before:continue
        (folder/'report.md').write_text(after)
        sha=lambda text:hashlib.sha256(text.encode()).hexdigest()
        changes.append({'path':str((folder/'report.md').relative_to(root)),'before_sha256':sha(before),'after_sha256':sha(after),'reason':'轮数从冻结案例读取；成本区间引用成本指标，避免误引任务成功率区间','raw_results_and_json_metrics_changed':False})
        p=folder/'checksums.json';checksums=json.loads(p.read_text());checksums['report.md']=sha(after);p.write_text(json.dumps(checksums,ensure_ascii=False,indent=2)+'\n')
    (root/'report-documentation-errata.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2)+'\n')
    return changes

if __name__=='__main__':print(json.dumps(repair(Path(__file__).parent),ensure_ascii=False))
