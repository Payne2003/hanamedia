import concurrent.futures
import http.client
import json
import tempfile
import threading
import unittest
import uuid

from runner.ai_api import APIClients
from runner.ai_gateway import GatewayError, GatewayServer, GatewayService


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.calls=[]
        self.api=APIClients(self.temp.name,post=self.post)
        self.api.vault.save('chatgpt',key='owner-test-key',max_output_tokens=64)
        self.service=GatewayService(self.temp.name,api=self.api)
        self.grant=self.service.store.create_user('Người dùng thử',daily_tokens=10000,daily_requests=2)
        self.user=self.service.store.authenticate(self.grant['token'])
    def post(self,url,headers,payload):
        self.calls.append((url,headers,payload))
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"ok":true}'}]}],'usage':{'input_tokens':10,'output_tokens':5}}
    def payload(self):return dict(provider='chatgpt',prompt='Return JSON',request_id=uuid.uuid4().hex,images=[])
    def test_authenticated_use_hides_owner_key_and_counts_usage(self):
        result=self.service.complete(self.user,self.payload())
        self.assertNotIn('owner-test-key',json.dumps(result))
        report=self.service.store.report()[0]
        self.assertEqual(report['used_tokens'],15);self.assertEqual(report['used_requests'],1)
        self.assertNotIn('token_hash',report)
    def test_replay_does_not_call_or_charge_twice_and_payload_collision_fails(self):
        payload=self.payload();first=self.service.complete(self.user,payload)
        self.assertEqual(first,self.service.complete(self.user,payload));self.assertEqual(len(self.calls),1)
        with self.assertRaises(GatewayError) as error:self.service.complete(self.user,{**payload,'prompt':'Different'})
        self.assertEqual(error.exception.status,409)
    def test_daily_request_quota_and_revocation(self):
        self.service.complete(self.user,self.payload());self.service.complete(self.user,self.payload())
        with self.assertRaises(GatewayError) as error:self.service.complete(self.user,self.payload())
        self.assertEqual(error.exception.status,429)
        self.service.store.revoke(self.user['id'])
        with self.assertRaises(GatewayError):self.service.store.authenticate(self.grant['token'])
        with self.assertRaises(GatewayError):self.service.complete(self.user,self.payload())
    def test_token_reservation_blocks_expensive_request_before_provider_call(self):
        payload=self.payload();payload['prompt']='x'*20000
        with self.assertRaises(GatewayError) as error:self.service.complete(self.user,payload)
        self.assertEqual(error.exception.status,429);self.assertEqual(self.calls,[])
    def test_unknown_provider_model_and_owner_error_are_not_forwarded(self):
        with self.assertRaises(GatewayError):self.service.complete(self.user,{**self.payload(),'model':'unapproved-expensive-model'})
        self.api.post=lambda *args:(_ for _ in ()).throw(RuntimeError('owner-test-key sensitive exception'))
        payload=self.payload()
        with self.assertRaises(GatewayError) as error:self.service.complete(self.user,payload)
        self.assertNotIn('owner-test-key',str(error.exception))
        with self.assertRaises(GatewayError) as retry:self.service.complete(self.user,payload)
        self.assertEqual(retry.exception.status,409)
        self.assertGreater(self.service.store.report()[0]['used_tokens'],0)
    def test_concurrent_reservations_cannot_bypass_quota(self):
        grant=self.service.store.create_user('One request',10000,1);user=self.service.store.authenticate(grant['token'])
        def reserve(_):
            try:self.service.store.reserve(user['id'],uuid.uuid4().hex,'hash',100);return True
            except GatewayError:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:self.assertEqual(sum(pool.map(reserve,range(6))),1)
    def test_owner_can_adjust_limits_and_client_sees_only_own_quota(self):
        self.service.store.set_limits(self.user['id'],20000,5)
        quota=self.service.store.quota(self.user['id'])
        self.assertEqual(quota['daily_tokens'],20000);self.assertEqual(quota['daily_requests'],5)
        self.assertNotIn('token_hash',quota);self.assertNotIn('token',quota)

    def test_real_http_client_relay_auth_and_idempotency(self):
        server=GatewayServer(('127.0.0.1',0),self.service);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=APIClients(self.temp.name+'/client')
            client.vault.save_gateway('http://127.0.0.1:'+str(server.server_port),self.grant['token'])
            info=client.relay_info()
            self.assertEqual(info['quota']['id'],self.user['id'])
            self.assertNotIn('owner-test-key',json.dumps(info))
            request_id=uuid.uuid4().hex
            result=client.relay('chatgpt','Return JSON',request_id=request_id)
            self.assertTrue(json.loads(result['answer'])['ok'])
            self.assertEqual(result,client.relay('chatgpt','Return JSON',request_id=request_id));self.assertEqual(len(self.calls),1)
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
            conn.request('POST','/v1/agent',body='{}',headers={'Content-Type':'application/json'})
            response=conn.getresponse();self.assertEqual(response.status,401);response.read();conn.close()
        finally:server.shutdown();server.server_close();thread.join()
