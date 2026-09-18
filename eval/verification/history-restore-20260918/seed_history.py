import asyncio
import json
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from app.infrastructure.ag_ui_journal import AGUIJournal
from app.infrastructure.persistence.sql.session_store import SqlFencedSessionStore

async def main():
    root=Path.cwd()
    data=root/'.pytest_cache/clarification-browser-20260918/data'
    journal=AGUIJournal(data/'ag_ui_runs.db')
    buyer='clarification-browser-test'
    session='history-fixture-55-rounds-20260918'
    engine=create_async_engine('sqlite+aiosqlite:///'+str(data/'globex.db'))
    await SqlFencedSessionStore(engine).assert_owner(session,buyer,create=True)
    product=(await journal.session('6a356027-2306-4c80-baeb-6fcc697c2619',buyer))['state']['products'][0]
    for index in range(55):
        run_id=f'history-fixture-round-{index}'
        request={'threadId':session,'runId':run_id,'messages':[{'id':f'fixture-u-{index}','role':'user','content':f'隔离长会话恢复测试：第{index+1:02}轮需求'}],
                 'state':{},'tools':[],'context':[],'forwardedProps':{'buyerId':buyer}}
        run,created=await journal.reserve(request,buyer,'history-fixture')
        if not created:continue
        await journal.append(run_id,'history-fixture',[
            {'type':'RUN_STARTED','threadId':session,'runId':run_id},
            {'type':'MESSAGES_SNAPSHOT','messages':[*run['messages'],{'id':f'fixture-a-{index}','role':'assistant','content':f'第{index+1:02}轮已保存的结果（受控验收数据，非模型生成）'}]},
            {'type':'STATE_SNAPSHOT','snapshot':{'products':[product] if index==0 else [],'searchCompleted':index==54,'toolApprovals':[]}},
            {'type':'RUN_FINISHED','threadId':session,'runId':run_id}])
    saved=await journal.session(session,buyer)
    assert len(saved['messages'])==110 and len(saved['productHistory'])==1
    (Path(__file__).parent/'fixture.json').write_text(json.dumps({'session_id':session,'buyer_id':buyer,'messages':len(saved['messages']),'product_batches':len(saved['productHistory']),'data_source':'真实SQLite；受控事件，非模型生成'},ensure_ascii=False,indent=2))
    await engine.dispose()
asyncio.run(main())
