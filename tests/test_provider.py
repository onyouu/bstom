import httpx
import pytest

from bstom.config import Settings
from bstom.engine import Session
from bstom.llm import CallGate, CompatibleLLM, GeminiLLM, make_provider, ProviderError
from bstom.models import Decision


def cfg(tmp_path):
    return Settings(_env_file=None, llm_provider='compatible', llm_api_key='test-secret-not-real', data_dir=str(tmp_path))


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
async def test_gemini_native_decision_stream_usage_and_key_isolation(tmp_path):
    import json
    settings = Settings(_env_file=None, gemini_api_key='gemini-test-secret', llm_api_key='other-provider-secret',
                        data_dir=str(tmp_path))
    gate = CallGate(settings)
    provider = make_provider(settings, gate)
    assert isinstance(provider, GeminiLLM)
    assert settings.active_api_key == 'gemini-test-secret'
    await provider.client.aclose()
    plan = Decision(desire=.8, relevance=.7, novelty=.6, thought='그 얘기에서 뭐가 떠오르네', interest='일상')

    def handler(request):
        assert request.headers['x-goog-api-key'] == 'gemini-test-secret'
        assert 'authorization' not in request.headers
        assert 'key=' not in str(request.url)
        body = json.loads(request.content)
        assert body['systemInstruction']['parts'] and body['contents'][0]['role'] == 'user'
        config = body['generationConfig']
        assert config['thinkingConfig']['thinkingLevel'] == 'LOW'
        if request.url.path.endswith(':generateContent'):
            assert config['responseMimeType'] == 'application/json'
            assert 'desire' in config['responseJsonSchema']['properties']
            return httpx.Response(200, json={'candidates':[{'content':{'parts':[{'text':plan.model_dump_json()}]},'finishReason':'STOP'}],
                                             'usageMetadata':{'totalTokenCount':123}})
        assert request.url.path.endswith(':streamGenerateContent') and request.url.params['alt'] == 'sse'
        assert 'responseMimeType' not in config
        items = [ {'candidates':[{'content':{'parts':[{'text':'private reasoning','thought':True},{'text':'혹시'}]}}]},
                  {'candidates':[{'content':{'parts':[{'text':' 이런 건?'}]},'finishReason':'STOP'}],
                   'usageMetadata':{'totalTokenCount':77}} ]
        return httpx.Response(200, text=''.join('data: '+json.dumps(item)+'\n\n' for item in items))

    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    session = Session('일상', provider, settings)
    parsed = await provider.decide(session.agents[0], session.view(), session)
    assert parsed == plan and gate.tokens[-1][1] == 123
    assert [chunk async for chunk in provider.stream(session.agents[0], session.view(), parsed, session)] == ['혹시',' 이런 건?']
    assert gate.tokens[-1][1] == 77 and session.calls == 2
    await provider.close()


@pytest.mark.asyncio
async def test_gemini_blocked_empty_and_malformed_responses_are_sanitized(tmp_path):
    settings = Settings(_env_file=None, data_dir=str(tmp_path))
    provider = GeminiLLM(settings, CallGate(settings))
    await provider.client.aclose()
    session = Session('test', provider, settings)
    for result, code in [({'promptFeedback':{'blockReason':'SAFETY'}}, 'content_blocked'),
                         ({'candidates':[]}, 'invalid_response'),
                         ({'error':{'message':'private-key-and-prompt'}}, 'provider_stream_error')]:
        provider.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=result)))
        with pytest.raises(ProviderError, match=code):
            await provider.decide(session.agents[0], session.view(), session)
        await provider.close()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,text='data: {}\n\n')))
    with pytest.raises(ProviderError, match='empty_response'):
        async for _ in provider.stream(session.agents[0], session.view(), Decision(desire=.8, relevance=.7, novelty=.6, thought='test', interest='test'), session):
            pass
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
