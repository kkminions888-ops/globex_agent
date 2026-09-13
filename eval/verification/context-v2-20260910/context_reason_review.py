"""明确肯定的“价格超出…预算”原因同义复核；原始回答、原评分与其它断言不变。"""
import copy,re
from scripts.eval.report_context import score as frozen_score

RULE='explicit_affirmative_over_budget_reason_v1'

def affirmative_reason(case, answer):
    if '太贵' not in case['contains']:return False
    text=re.sub(r'[*_`]','',answer)
    match=re.search(r'(?:原因(?:是|为)|因为|由于)[：:\s]*价格超出[^。！？\n;；]{0,24}预算',text)
    if not match:return False
    before=text[max(0,match.start()-8):match.start()]
    after=text[match.end():match.end()+24]
    if re.search(r'不是|并非|不因|没有|未',before):return False
    if re.search(r'(?:不是|并非).{0,6}原因',after):return False
    identifiers=re.findall(r'(?<![A-Za-z0-9])P\d+(?:-S\d+)?(?![A-Za-z0-9])',case.get('question',''))
    if identifiers and not all(identifier in text for identifier in identifiers):return False
    return True


def review_score(rows,cases):
    originals=copy.deepcopy(rows);prepared=copy.deepcopy(rows)
    eligible=[]
    for row in prepared:
        yes=affirmative_reason(cases[row['case_id']],row['answer']);eligible.append(yes)
        if yes:row['answer']+=' 太贵'
    result=frozen_score(prepared,cases)
    for raw,reviewed,yes in zip(originals,result,eligible):
        reviewed['answer']=raw['answer']
        if yes:
            original=frozen_score([copy.deepcopy(raw)],cases)[0]
            reviewed['reason_review']={'rule':RULE,'frozen_passed':original['passed'],'frozen_checks':original['checks'],'changed_verdict':original['passed']!=reviewed['passed'],'original_answer_unchanged':True}
    return result
