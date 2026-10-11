"""Official magic-key scenarios through the real API and WireMock request journal."""
from copy import deepcopy

import pytest
import requests

from tests.utils.wiremock import WireMock



def post(live,path,body):
    base,headers,_,_,_=live
    return requests.post(base+path,json=body,headers=headers,timeout=10)


def state(live,key):
    base,headers,_,_,_=live
    response=requests.get(base+'/contracts/'+key,headers=headers,timeout=10)
    assert response.status_code==200
    return response.json()


def create(live,payload=None):
    response=post(live,'/contracts',payload or live[2])
    assert response.status_code==201,response.text
    assert response.json()['status']=='draft'
    return response.json()['contract_key']


def activate(live,key):
    response=post(live,f'/contracts/{key}/activation',{'actor':'docker-test','reason':'activation-check'})
    assert response.status_code==200,response.text
    assert response.json()['ledger_accounts']
    return response.json()


def assert_error(response,status,code):
    assert response.status_code==status,response.text
    assert response.json()['code']==code


def test_live_lifecycle_headers_bodies_and_idempotent_activation(live):
    base,headers,payload,config,mocks=live
    assert requests.get(base+'/health_check',timeout=10).status_code==204
    assert requests.post(base+'/contracts',json=payload,timeout=10).status_code==403
    key=create(live)
    assert mocks['ledger'].journal()==[]
    registry=mocks['party'].journal()
    assert len(registry)==1 and registry[0]['url']=='/clients/'+payload['client_key']
    activate(live,key)
    sent=mocks['ledger'].journal()
    assert len(sent)==1
    request=sent[0]
    assert request['method']=='POST' and request['url']=='/contract_ledgers'
    assert WireMock.header(request,'X-Request-ID')==headers['X-Request-ID']
    assert WireMock.header(request,'INTERNAL-TOKEN')==config['services']['api']['environment']['LEDGER_API_INTERNAL_TOKEN']
    assert WireMock.header(request,'Content-Type').startswith('application/json')
    assert WireMock.header(request,'Idempotency-Key') is None
    body=WireMock.body(request)
    assert set(body)=={'contract_key','client_key','client_party_key','split_version','rent_amount_in_cents','platform_fee_percentage','items','actor','reason'}
    assert body['contract_key']==key and body['split_version']==1
    assert body['client_party_key']=='11111111-1111-4111-8111-111111111111'
    assert body['platform_fee_percentage']=='1.0000'
    assert body['actor']=='contract-api' and body['reason']=='activation-check'
    expected=[{'purpose_code':i['purpose_code'],'beneficiary_party_key':i['party_key'],'beneficiary_role':i['role'],'type':i['type'],**({'value':i['value']} if 'value' in i else {})} for i in payload['split']]
    assert sorted(body['items'],key=lambda i:i['purpose_code'])==sorted(expected,key=lambda i:i['purpose_code'])
    for call in mocks['party'].journal():
        assert call['method']=='GET' and not call.get('body')
        assert WireMock.header(call,'X-Request-ID')==headers['X-Request-ID']
        assert WireMock.header(call,'INTERNAL-TOKEN')==config['services']['api']['environment']['PARTY_API_INTERNAL_TOKEN']
    count=(len(mocks['party'].journal()),len(mocks['ledger'].journal()))
    replay=post(live,f'/contracts/{key}/activation',{'actor':'replay'})
    assert replay.status_code==200 and 'ledger_accounts' not in replay.json()
    assert count==(len(mocks['party'].journal()),len(mocks['ledger'].journal()))
    # Official mappings keep expected_amount=640000 even when the rent changes.
    version=post(live,f'/contracts/{key}/split_versions',{'actor':'docker-test','rent_amount_in_cents':530000,'split':payload['split']})
    assert version.status_code==201 and version.json()['split_version']==2,version.text
    assert state(live,key)['rent_amount_in_cents']==530000
    closed=post(live,f'/contracts/{key}/termination',{'actor':'docker-test','reason':'ordinary closure'})
    assert closed.status_code==200
    closure=next(call for call in mocks['ledger'].journal() if call['url'].endswith('/closure'))
    assert closure['url']==f'/contract_ledgers/{key}/closure'
    assert WireMock.body(closure)=={'actor':'contract-api','reason':'ordinary closure'}
    final=state(live,key)
    assert final['status']=='terminated' and len(final['status_events'])==3
    assert all(e['trace_id']==headers['X-Request-ID'] for e in final['status_events'])
    assert_error(post(live,'/contracts',payload),409,'QIT003002')
    natural=requests.get(base+'/contracts',params={'client_key':payload['client_key'],'external_reference':payload['external_reference']},headers=headers,timeout=10)
    assert natural.status_code==200 and natural.json()['contracts'][0]['contract_key']==key


@pytest.mark.parametrize('client,status,code',[
    ('c404c404-c404-4c40-8c40-c404c404c404',422,'QIT003008'),
    ('c503c503-c503-4c50-8c50-c503c503c503',503,'QIT003010'),
])
def test_live_client_errors(live,client,status,code):
    payload=deepcopy(live[2]);payload['client_key']=client
    response=post(live,'/contracts',payload)
    assert_error(response,status,code)
    if status==422:
        assert 'QIT002005' in response.json()['description']
    assert live[4]['ledger'].journal()==[]


@pytest.mark.parametrize('party,status,code',[
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',409,'QIT003007'),
    ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',422,'QIT003008'),
    ('cccccccc-cccc-4ccc-8ccc-cccccccccccc',503,'QIT003010'),
])
def test_live_party_errors_allow_draft_and_prevent_ledger_call(live,party,status,code):
    payload=deepcopy(live[2])
    old=payload['parties'][3]['party_key'];payload['parties'][3]['party_key']=party
    for item in payload['split']:
        if item['party_key']==old:item['party_key']=party
    key=create(live,payload)
    assert not any(call['url'].startswith('/parties/') for call in live[4]['party'].journal())
    response=post(live,f'/contracts/{key}/activation',{'actor':'test'})
    assert_error(response,status,code)
    if status==422:
        assert 'QIT002001' in response.json()['description']
    assert live[4]['ledger'].journal()==[]
    draft=state(live,key)
    assert draft['status']=='draft' and draft['provisioned_at'] is None and len(draft['status_events'])==1


@pytest.mark.parametrize('rent,status,code',[(422008,422,'QIT003009'),(409032,409,'QIT003013'),(503503,503,'QIT003010')])
def test_live_ledger_magic_errors(live,rent,status,code):
    payload=deepcopy(live[2]);payload['rent_amount_in_cents']=rent
    key=create(live,payload)
    response=post(live,f'/contracts/{key}/activation',{'actor':'test'})
    assert_error(response,status,code)
    source={422008:'QIT001008',409032:'QIT001032'}.get(rent)
    if source:
        assert source in response.json()['description']
    draft=state(live,key)
    assert draft['status']=='draft' and draft['provisioned_at'] is None and len(draft['status_events'])==1


def test_live_activation_timeout_then_official_replay(live):
    assert live[3]['services']['api']['environment']['LEDGER_API_TIMEOUT']=='1','Use scripts/test-contracts.ps1'
    payload=deepcopy(live[2]);payload['rent_amount_in_cents']=408200
    key=create(live,payload)
    assert_error(post(live,f'/contracts/{key}/activation',{'actor':'first'}),503,'QIT003010')
    assert state(live,key)['status']=='draft'
    activate(live,key)
    active=state(live,key)
    assert active['status']=='active' and len(active['status_events'])==2
    assert len(live[4]['ledger'].journal())==2


@pytest.mark.parametrize('reason,status,code',[('mock:saldo-retido',409,'QIT003013'),('mock:fora-do-ar',503,'QIT003010')])
def test_live_closure_failure_preserves_state(live,reason,status,code):
    key=create(live);activate(live,key)
    response=post(live,f'/contracts/{key}/termination',{'actor':'test','reason':reason})
    assert_error(response,status,code)
    if status==409:
        assert 'QIT001037' in response.json()['description']
    active=state(live,key)
    assert active['status']=='active' and len(active['status_events'])==2
    assert post(live,f'/contracts/{key}/termination',{'actor':'retry'}).status_code==200


def test_live_pending_split_replay(live):
    key=create(live);activate(live,key)
    data={'actor':'first','rent_amount_in_cents':408200,'split':live[2]['split']}
    assert_error(post(live,f'/contracts/{key}/split_versions',data),503,'QIT003010')
    assert state(live,key)['split_version']==1
    changed=deepcopy(data);changed['rent_amount_in_cents']+=1
    count=len(live[4]['ledger'].journal())
    assert_error(post(live,f'/contracts/{key}/split_versions',changed),409,'QIT003013')
    assert len(live[4]['ledger'].journal())==count
    data['actor']='retry';data['split']=list(reversed(data['split']))
    replay=post(live,f'/contracts/{key}/split_versions',data)
    assert replay.status_code==200 and replay.json()['split_version']==2,replay.text
    assert state(live,key)['split_version']==2
