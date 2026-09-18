import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

out=Path(__file__).parent
phase=sys.argv[1] if len(sys.argv)>1 else 'after'
report={'phase':phase,'steps':[],'posts':[],'page_errors':[]}
buyer='clarification-browser-test'
base='http://127.0.0.1:5174'
ids={'long':'history-fixture-55-rounds-20260918','form':'31b76494-ed11-45bc-8420-45dd9a395fd9','product':'6a356027-2306-4c80-baeb-6fcc697c2619'}
titles={'long':'隔离长会话恢复测试','form':'我想买耳机','product':'请查询 Wanderlite'}

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/Users/haojunpan/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing')
    context=browser.new_context(viewport={'width':1440,'height':1100})
    page=context.new_page()
    page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
    page.on('request',lambda req:report['posts'].append(req.url) if req.method=='POST' else None)
    def saved(kind):
        response=context.request.get(f'{base}/commerce/ag-ui/sessions/{ids[kind]}?buyer_id={buyer}')
        assert response.ok
        return response.json()
    def verify(kind):
        data=saved(kind)
        expect(page.locator('.query-bubble,.assistant-text')).to_have_count(len(data['messages']))
        expect(page.locator('.diagnostic-count')).to_have_text(str(len(data['events'])))
        expect(page.locator('.search-results .product-card')).to_have_count(len(data['run']['state'].get('products',[])))
        if kind=='form':
            expect(page.locator('.shopping-form-saved')).to_have_count(1,timeout=15000)
            expect(page.locator('.shopping-form-saved')).to_contain_text('头戴')
            expect(page.locator('.query-bubble').last).to_contain_text('已补充：')
        else:
            expect(page.locator('.shopping-form-saved')).to_have_count(0)
        report['steps'].append({'session':kind,'messages':len(data['messages']),'products':len(data['run']['state'].get('products',[])),
                                'historical_product_batches':len(data['productHistory']),'events':len(data['events'])})
    def switch(kind):
        page.get_by_role('complementary',name='主导航').get_by_role('button').filter(has_text=titles[kind]).click()
        expect(page.locator('.query-bubble').first).to_contain_text(titles[kind])
        verify(kind)
    try:
        page.goto(base);page.wait_for_load_state('networkidle')
        verify('long')
        assert page.locator('.query-bubble,.assistant-text').count()==110
        page.locator('.query-bubble').first.scroll_into_view_if_needed()
        page.screenshot(path=str(out/f'{phase}-02-long-history-first.png'))
        page.locator('.historical-products summary').click()
        expect(page.locator('.historical-products .product-card')).to_have_count(1)
        assert page.locator('.historical-products button,.historical-products input').count()==0
        page.set_viewport_size({'width':1440,'height':1500})
        page.locator('.historical-products').scroll_into_view_if_needed()
        page.screenshot(path=str(out/f'{phase}-03-historical-products.png'))
        page.set_viewport_size({'width':1440,'height':1100})
        for _ in range(3):
            switch('form');switch('product');switch('form')
        page.locator('.diagnostics summary').click()
        page.screenshot(path=str(out/f'{phase}-04-form-and-execution-records.png'),full_page=True)
        page.reload();page.wait_for_load_state('networkidle');verify('form')
        switch('product')
        page.screenshot(path=str(out/f'{phase}-05-product-restored.png'),full_page=True)
        # 清除缓存后仍从数据库恢复最近会话，不能依赖上次运行的页面状态。
        page.evaluate('localStorage.clear();sessionStorage.clear()')
        page.reload();page.wait_for_load_state('networkidle');verify('long')
        switch('form')
        assert report['posts']==[],report['posts']
        assert report['page_errors']==[],report['page_errors']
        report['status']='passed'
    except Exception as error:
        report['status']='failed';report['error']=str(error)
        page.screenshot(path=str(out/f'{phase}-failure.png'),full_page=True)
        (out/f'{phase}-failure.txt').write_text(page.locator('body').inner_text())
        raise
    finally:
        (out/f'{phase}-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False),flush=True)
        browser.close()
