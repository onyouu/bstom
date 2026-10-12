"""Explicit opt-in, bounded live provider check; never prints credentials."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bstom.config import Settings
from bstom.engine import Session
from bstom.llm import CallGate, make_provider


async def main():
    settings = Settings(session_max_calls=24, session_max_messages=12, session_max_seconds=160,
                        data_dir='artifacts/live-smoke')
    if not settings.active_api_key:
        raise SystemExit('No local key configured for the selected provider')
    provider = make_provider(settings, CallGate(settings))
    session = Session('친구랑 같이 쓰는 재밌는 웹 서비스', provider, settings, 'compatible')
    await session.start()
    intervened = False
    try:
        async with asyncio.timeout(155):
            while session.status == 'running':
                if session.messages and session.active_agent and not intervened:
                    await session.user_message('갑자기 생각났는데, 그냥 쓸모없어도 웃긴 게 좋겠어')
                    intervened = True
                completed = [m for m in session.messages if m.finished and not m.interrupted and m.speaker != 'user' and m.text]
                if len(completed) >= 3 and len({m.speaker for m in completed}) >= 2:
                    break
                await asyncio.sleep(.01)
    except TimeoutError:
        pass
    finally:
        await session.end('live_smoke_complete')
        await provider.close()
    report = {'provider':settings.llm_provider,'model':settings.llm_model,'calls':session.calls,'user_intervened':intervened,
              'interrupted':any(e['type']=='agent_interrupted' and e['data']['reason']=='user' for e in session.events),
              'speakers':list({m.speaker for m in session.messages if m.speaker!='user' and m.text}),
              'completed_ai_messages':len([m for m in session.messages if m.speaker!='user' and m.text and m.finished and not m.interrupted]),
              'errors':[e['data'] for e in session.events if e['type']=='provider_error'],
              'messages':[m.public() for m in session.messages]}
    Path('artifacts').mkdir(exist_ok=True)
    Path('artifacts/live-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
    if report['completed_ai_messages'] < 3 or not intervened or not report['interrupted']:
        raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True, help='Make real requests to the configured provider')
    parser.parse_args()
    asyncio.run(main())
