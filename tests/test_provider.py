import httpx
import pytest

from bstom.config import Settings
from bstom.engine import Session
from bstom.llm import CallGate, CompatibleLLM, ProviderError
from bstom.models import Decision


def cfg(tmp_path):
    return Settings(_env_file=None, llm_api_key='test-secret-not-real', data_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_compatible_decision_and_stream(tmp_path):
    settings = cfg(tmp_path)
    provider = CompatibleLLM(settings, CallGate(settings))
    await provider.client.aclose()
    plan = Decision(desire=.8, relevance=.7, novelty=.6, thought='그런 생각도 해볼까', interest='일상')

    def handler(request):
        assert request.headers['authorization'] == 'Bearer test-secret-not-real'
        import json
        body = json.loads(request.content)
        if body['stream']:
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"한마디"}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={'choices':[{'message':{'content':plan.model_dump_json()}}]})

    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    session = Session('일상', provider, settings)
    parsed = await provider.decide(session.agents[0], session.view(), session)
    assert parsed.thought == plan.thought
    chunks = [chunk async for chunk in provider.stream(session.agents[0],session.view(), parsed, session)]
    assert chunks == ['한마디']
    assert session.calls == 2
    await provider.close()


@pytest.mark.asyncio
async def test_rate_limit_and_invalid_output_do_not_expose_response(tmp_path):
    settings = cfg(tmp_path)
    gate = CallGate(settings)
    provider = CompatibleLLM(settings, gate)
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(429,headers={'retry-after':'7'},text='test-secret-not-real')))
    session = Session('test', provider, settings)
    with pytest.raises(ProviderError) as caught:
        await provider.decide(session.agents[0],session.view(), session)
    assert str(caught.value) == 'rate_limited' and caught.value.retry_after == 7
    assert gate.blocked_until > 0
    await provider.close()
    gate.blocked_until = 0
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'choices':[{'message':{'content':'not valid JSON'}}]})))
    with pytest.raises(ProviderError, match='invalid_response'):
        await provider.decide(session.agents[0],session.view(), session)
    await provider.close()
