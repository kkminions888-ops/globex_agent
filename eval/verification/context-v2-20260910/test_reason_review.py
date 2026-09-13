import pytest
from context_reason_review import review_score

@pytest.mark.parametrize('strategy',['legacy','deterministic','layered'])
@pytest.mark.parametrize('answer,contains,expected',[
    ('P18412被淘汰的原因是：**价格超出您当前335元的预算**。',['太贵'],True),
    ('P18412不是因为价格超出您当前335元的预算被淘汰的。',['太贵'],False),
    ('P18412被淘汰的原因是价格并未超出335元预算。',['太贵'],False),
    ('P18412被淘汰的原因是价格超出335元预算，但这并非真正原因。',['太贵'],False),
    ('P999被淘汰的原因是价格超出335元预算。',['太贵'],False),
    ('P18412被淘汰的原因是价格超出335元预算。',['太贵','DE'],False),
    ('P18412-S1的单价139元。',['129','P18412-S1'],False),
])
def test_reason_review_preserves_other_checks_and_rejects_negation(strategy,answer,contains,expected):
    raw={'case_id':'c','strategy':strategy,'answer':answer,'current_calls':0,'mode':'snapshot','checks':{'facts':False},'error':None}
    result=review_score([raw],{'c':{'question':'P18412先前为什么被我淘汰？','contains':contains,'requires_current':False}})[0]
    assert result['passed'] is expected
    assert result['answer']==answer and raw['answer']==answer
