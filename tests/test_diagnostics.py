import asyncio
import copy
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from netcoop import generate, normalize, analyze, correlate

def decision(raw):
    n=normalize(raw)
    return correlate([analyze(a,n) for a in ('performance','infrastructure','connectivity')],n)

@pytest.mark.parametrize('scenario,cause',[
    ('normal','normal'),('congestion','link_congestion'),('weak_wifi','weak_wifi'),
    ('interface_fault','interface_fault'),('dns_failure','dns_dhcp_failure'),
    ('interop_legacy','link_congestion'),('device_overload','device_overload'),
    ('missing_data','insufficient_evidence'),('stale_data','insufficient_evidence'),
])
def test_expected_diagnoses(scenario,cause):
    assert decision(generate(scenario))['cause']==cause

@pytest.mark.parametrize('value',['94',True,float('nan'),float('inf'),-1,101])
def test_invalid_percentages(value):
    raw=generate('normal');raw['devices'][0]['cpu_pct']=value
    with pytest.raises(ValueError):normalize(raw)

def test_legacy_converts_units():
    n=normalize(generate('interop_legacy'))
    assert n['metrics']['latency_ms']==280
    assert n['devices'][1]['link_util_pct']==94
    assert any('legacy' in s for s in n['quality']['warnings'])

def test_no_input_mutation():
    raw=generate('congestion');before=copy.deepcopy(raw);normalize(raw);assert raw==before

@pytest.mark.parametrize('raw',[{},[],{'schema_version':'9'},generate('malformed')])
def test_malformed_rejected(raw):
    with pytest.raises(ValueError):normalize(raw)

def test_duplicate_devices():
    raw=generate('normal');raw['devices'][1]['id']=raw['devices'][0]['id']
    with pytest.raises(ValueError):normalize(raw)

def test_invalid_topology():
    raw=generate('normal');raw['links'].append(['fake','CORE_RTR_01'])
    with pytest.raises(ValueError):normalize(raw)

def test_unknown_service_not_failure():
    raw=generate('normal');raw['services']['dns']='unknown'
    assert decision(raw)['cause']=='normal'

def test_agent_failure_does_not_imply_healthy():
    assert correlate([],normalize(generate('normal')))['cause']=='insufficient_evidence'

def test_conflicting_hypotheses_abstain():
    n=normalize(generate('normal'))
    d=correlate([{'support':{'interface_fault':1,'weak_wifi':.9}}]*3,n)
    assert d['cause']=='insufficient_evidence'
    assert d['confidence']<.5

def test_mixed_fault_alternatives():
    d=decision(generate('mixed_fault'))
    assert 'interface_fault' in d['alternatives'] or d['cause']=='interface_fault'

def test_real_mcp_end_to_end():
    from engine import diagnose
    r=asyncio.run(diagnose('congestion'))
    assert r['report']['cause']=='link_congestion',r['report']
    assert len(r['report']['evidence_sources'])==3
    assert any(m['type']=='connected' and m['payload']['transport']=='stdio' for m in r['messages'])
    assert [m['sequence'] for m in r['messages']]==list(range(1,len(r['messages'])+1))

def test_mcp_invalid_input_report():
    from engine import diagnose
    r=asyncio.run(diagnose('malformed'))
    assert r['report']['status']=='error'
    assert r['report']['confidence']==0

def test_http_stream_and_history(tmp_path,monkeypatch):
    import server
    from fastapi.testclient import TestClient
    monkeypatch.setattr(server,'DB',tmp_path/'incidents.sqlite3')
    with TestClient(server.app) as client:
        assert client.get('/').status_code==200
        assert client.post('/api/diagnose',json={'scenario':'not_real'}).status_code==422
        response=client.post('/api/diagnose/stream',json={'scenario':'weak_wifi','mode':'demo'})
        events=[json.loads(x) for x in response.text.splitlines()]
        assert events[-1]['event']=='complete'
        r=events[-1]['data']['report']
        assert r['cause']=='weak_wifi'
        assert client.get('/api/incidents/'+r['incident_id']).json()['report']==r
        assert len(client.get('/api/incidents').json())==1
        assert client.get('/api/incidents/not-found').status_code==404

def test_gemini_tool_loop_preserves_signature(monkeypatch):
    import engine
    monkeypatch.setenv('GEMINI_API_KEY','test-key-not-real')
    requests=[];calls=[]
    class Response:
        def __init__(self,data):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            requests.append(copy.deepcopy(kwargs['json']))
            part={'functionCall':{'name':'inspect_device','args':{'device_id':'CORE_RTR_01'},'id':'call-1'},'thoughtSignature':'opaque-signature'} if len(requests)==1 else {'text':'Evidence suggests congestion. Verify queue behavior.'}
            return Response({'candidates':[{'content':{'role':'model','parts':[part]}}]})
    monkeypatch.setattr(engine.httpx,'AsyncClient',Client)
    async def call(name,args,sender):calls.append((name,args,sender));return {'device':{'id':'CORE_RTR_01'}}
    async def emit(*args):pass
    r=asyncio.run(engine.investigate(normalize(generate('congestion')),{'cause':'link_congestion'},call,emit))
    assert r['status']=='connected' and r['tool_calls']==1
    assert calls[0][0]=='inspect_device'
    assert requests[1]['contents'][1]['parts'][0]['thoughtSignature']=='opaque-signature'
    assert requests[1]['contents'][2]['parts'][0]['functionResponse']['id']=='call-1'

def test_gemini_failure_falls_back(monkeypatch):
    import engine
    monkeypatch.setenv('GEMINI_API_KEY','test-key-not-real')
    class BadClient:
        def __init__(self,**kwargs):raise RuntimeError('secret error detail')
    monkeypatch.setattr(engine.httpx,'AsyncClient',BadClient)
    async def noop(*args):pass
    r=asyncio.run(engine.investigate({}, {}, noop, noop))
    assert r['status']=='unavailable'
    assert 'secret' not in r['summary']
