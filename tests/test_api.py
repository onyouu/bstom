from fastapi.testclient import TestClient

from bstom.app import app, settings


def test_rest_ws_controls_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', str(tmp_path))
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        config = client.get('/api/config').json()
        assert config['name'] == 'Bstom'
        assert 'llm_api_key' not in config
        assert client.post('/api/sessions', json={'topic':' ', 'mode':'mock'}).status_code == 422
        created = client.post('/api/sessions', json={'topic':'작은 실험', 'mode':'mock'})
        assert created.status_code == 201
        session_id = created.json()['id']
        with client.websocket_connect(f'/ws/{session_id}') as ws:
            assert ws.receive_json()['type'] == 'snapshot'
            result = client.post(f'/api/sessions/{session_id}/message', json={'text':'식물이 먼저 말을 걸면?'})
            assert result.status_code == 200
            for _ in range(30):
                event = ws.receive_json()
                assert {'type','session_id','agent_id','timestamp','seq','data'} <= event.keys()
                if event['type'] == 'user_spoke':
                    break
            else:
                raise AssertionError('user event missing')
        assert client.post(f'/api/sessions/{session_id}/pause').json()['status'] == 'paused'
        assert client.post(f'/api/sessions/{session_id}/resume').json()['status'] == 'running'
        assert client.post(f'/api/sessions/{session_id}/end').json()['status'] == 'ended'
        assert any(m['speaker']=='user' for m in client.get(f'/api/sessions/{session_id}').json()['messages'])
        assert client.post(f'/api/sessions/{session_id}/message', json={'text':'hi'}).status_code == 409
        assert client.post('/api/sessions', json={'topic':'x'}, headers={'origin':'https://evil.example'}).status_code == 403
        with client.websocket_connect(f'/ws/{session_id}', headers={'origin':'http://testserver'}) as ws:
            assert ws.receive_json()['data']['status'] == 'ended'


def test_unknown_and_malformed_session():
    with TestClient(app) as client:
        assert client.get('/api/sessions/not-a-uuid').status_code == 404
        assert client.post('/api/sessions/missing/end').status_code == 404
