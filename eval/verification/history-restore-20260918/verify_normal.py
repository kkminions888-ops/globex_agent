import json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
out=Path(__file__).parent
report={'buyer_id':'pao-coder','steps':[],'posts':[],'page_errors':[],'captures':'原账号仅做只读断言，不保存买家原文截图'}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/Users/haojunpan/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing')
    context=browser.new_context(viewport={'width':1440,'height':1100})
    page=context.new_page()
    page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
    page.on('request',lambda req:report['posts'].append(req.url) if req.method=='POST' else None)
    try:
        page.goto('http://127.0.0.1:5174');page.wait_for_load_state('networkidle')
        expect(page.locator('.sidebar')).to_contain_text('pao-coder')
        response=context.request.get('http://127.0.0.1:5174/commerce/ag-ui/sessions?buyer_id=pao-coder')
        assert response.ok
        sessions=response.json()['sessions']
        assert len(sessions)==7
        for index,item in enumerate(sessions):
            page.get_by_role('complementary',name='主导航').get_by_role('button',name='对话历史',exact=True).click()
            expect(page.locator('.history-entry')).to_have_count(len(sessions))
            assert page.locator('.history-entry strong').all_text_contents()==[s['title'] for s in sessions]
            with page.expect_response(lambda r: '/commerce/ag-ui/sessions/'+item['id']+'?' in r.url) as restoring:
                page.locator('.history-entry').nth(index).click()
            result=restoring.value
            assert result.ok
            data=result.json()
            assert data['id']==item['id']
            expect(page.locator('.query-bubble,.assistant-text')).to_have_count(len(data['messages']))
            first=next(m for m in data['messages'] if m['role']=='user')
            expect(page.locator('.query-bubble').first).to_have_text(first['content'])
            count=len(data['run']['state'].get('products',[]))
            expect(page.locator('.search-results .product-card')).to_have_count(count)
            expect(page.locator('.diagnostic-count')).not_to_have_text('0')
            report['steps'].append({'session_id':item['id'],'messages':len(data['messages']),'products':count,'events':len(data['events']),'result':'passed'})
        page.reload();page.wait_for_load_state('networkidle')
        expect(page.locator('.query-bubble,.assistant-text')).to_have_count(report['steps'][-1]['messages'])
        assert report['posts']==[] and report['page_errors']==[]
        report['status']='passed'
    except Exception as error:
        report['status']='failed';report['error_type']=type(error).__name__
        raise
    finally:
        (out/'normal-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))
        browser.close()
