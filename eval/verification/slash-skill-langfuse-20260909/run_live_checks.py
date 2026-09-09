"""本轮可复现的真实 AG-UI 验证；只提交测试需求，不批准交易。"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent


async def main():
    cases = json.loads((ROOT / 'docs/capabilities/shopping-needs-clarification.cases.json').read_text())['cases']
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8001', timeout=240) as client:
        response = await client.get('/commerce/skills', params={'buyer_id': 'slash-live-verification'})
        response.raise_for_status()
        skill = next(row for row in response.json()['skills'] if row['id'] == 'shopping-needs-clarification')
        checks = [*cases, {'id': 'real_product_trace', 'query': '只查询 P1001-S2 的名称和商品价，预算300元人民币以内，配送中国。不下单。'}]
        reports = []
        for case in checks:
            run_id, session_id = uuid.uuid4().hex, 'slash-check-' + uuid.uuid4().hex
            props = {'buyerId': 'slash-live-verification', 'locale': 'zh-CN', 'currency': 'CNY'}
            if case['id'] != 'real_product_trace':
                props['selectedSkill'] = {'id': skill['id'], 'version': skill['version'], 'contentHash': skill['content_hash']}
            body = {'threadId': session_id, 'runId': run_id, 'state': {}, 'tools': [], 'context': [],
                    'messages': [{'id': uuid.uuid4().hex, 'role': 'user', 'content': case['query']}], 'forwardedProps': props}
            events = []
            async with client.stream('POST', '/commerce/ag-ui/run', json=body) as stream:
                stream.raise_for_status()
                trace_id = stream.headers.get('x-trace-id')
                async for line in stream.aiter_lines():
                    if line.startswith('data: '):
                        events.append(json.loads(line[6:]))
            states = [event['snapshot'] for event in events if event['type'] == 'STATE_SNAPSHOT']
            snapshots = [event['messages'] for event in events if event['type'] == 'MESSAGES_SNAPSHOT']
            messages = snapshots[-1] if snapshots else []
            final_text = '\n'.join(message.get('content', '') for message in messages if message.get('role') == 'assistant')
            state = states[-1] if states else {}
            counts = dict(Counter(event['type'] for event in events))
            tools = [event.get('toolCallName') for event in events if event['type'] == 'TOOL_CALL_START']
            mechanism_pass = counts.get('RUN_FINISHED') == 1 and not counts.get('RUN_ERROR')
            if 'selectedSkill' in props:
                mechanism_pass = mechanism_pass and any(item.get('id') == skill['id'] and item.get('status') == 'used'
                    and item.get('source') == 'server_preload' for item in state.get('skillUsages', []))
            report = {'case_id': case['id'], 'run_id': run_id, 'session_id': session_id, 'trace_id': trace_id,
                      'query': case['query'], 'event_counts': counts, 'tool_names': tools,
                      'skill_usages': state.get('skillUsages', []), 'products': state.get('products', []),
                      'final_text': final_text, 'mechanism_pass': bool(mechanism_pass),
                      'semantic_review': 'pending', 'checked_at': datetime.now(timezone.utc).isoformat()}
            (OUTPUT / (case['id'] + '.events.json')).write_text(json.dumps(events, ensure_ascii=False, indent=2) + '\n')
            (OUTPUT / (case['id'] + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            reports.append(report)
            print(json.dumps({key: report[key] for key in ('case_id', 'trace_id', 'tool_names', 'final_text', 'mechanism_pass')}, ensure_ascii=False), flush=True)
        (OUTPUT / 'live-checks.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    asyncio.run(main())
