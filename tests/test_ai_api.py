import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runner.ai_api import APIClients, APIError, KeyVault, NoRedirect


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.environment=patch.dict(os.environ,{},clear=True);self.environment.start();self.addCleanup(self.environment.stop)
        self.calls=[]
        self.api=APIClients(self.temp.name,post=self.post)
        for provider in ('chatgpt','gemini','claude'):self.api.vault.save(provider,key='test-only-'+provider)
    def post(self,url,headers,payload):
        self.calls.append((url,headers,payload))
        if 'openai' in url:return {'status':'completed','model':'gpt-test','output':[{'type':'message','content':[{'type':'output_text','text':'{"ok":true}'}]}],'usage':{'input_tokens':10,'output_tokens':5}}
        if 'anthropic' in url:return {'stop_reason':'end_turn','content':[{'type':'text','text':'{"ok":true}'}],'usage':{'input_tokens':10,'output_tokens':5,'cache_read_input_tokens':3}}
        return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"ok":true}'}]}}],'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':5,'thoughtsTokenCount':2}}
    def test_three_provider_protocols_and_usage(self):
        for provider in ('chatgpt','gemini','claude'):
            result=self.api.complete(provider,'Return JSON')
            self.assertTrue(json.loads(result['answer'])['ok']);self.assertGreater(result['usage']['output_tokens'],0)
        self.assertEqual(self.calls[0][1]['Authorization'],'Bearer test-only-chatgpt')
        self.assertFalse(self.calls[0][2]['store'])
        self.assertIn('generateContent',self.calls[1][0]);self.assertNotIn('test-only',self.calls[1][0])
        self.assertEqual(self.calls[2][1]['anthropic-version'],'2023-06-01')
    def test_keys_encrypted_and_never_returned_or_stored_in_gateway_public_state(self):
        public=json.dumps(self.api.vault.public())
        self.assertNotIn('test-only',public)
        self.assertNotIn(b'test-only',self.api.vault.path.read_bytes())
        self.assertEqual(KeyVault(self.temp.name).credential('chatgpt')[0],'test-only-chatgpt')
        if os.name!='nt':self.assertEqual(self.api.vault.path.stat().st_mode & 0o777,0o600)
        self.api.vault.save_gateway('https://ai.example.com','test-gateway-token')
        self.assertNotIn('test-gateway-token',json.dumps(self.api.vault.gateway(public=True)))
    def test_blank_preserves_key_remove_and_environment_override(self):
        self.api.vault.save('gemini',key='')
        self.assertEqual(self.api.vault.credential('gemini')[0],'test-only-gemini')
        self.api.vault.save('gemini',remove=True)
        self.assertFalse(self.api.vault.public()['gemini']['configured'])
        with patch.dict(os.environ,{'HANAMEDIA_GEMINI_API_KEY':'test-injected'}):
            self.assertEqual(self.api.vault.credential('gemini'),('test-injected','environment'))
    def test_gateway_url_tls_and_redirects(self):
        for url in ['http://remote.example','https://user:password@example.com','https://example.com?key=abc','https://example.com/#fragment']:
            with self.assertRaises(APIError):self.api.vault.save_gateway(url,'test-token')
        self.api.vault.save_gateway('http://127.0.0.1:8899','test-token')
        self.assertTrue(self.api.vault.gateway(public=True)['configured'])
        with self.assertRaises(APIError):NoRedirect().redirect_request(None,None,302,'',{},'https://other.example')
    def test_truncation_and_refusal_never_count_as_success(self):
        cases={'chatgpt':{'status':'incomplete'},'claude':{'stop_reason':'max_tokens'},'gemini':{'candidates':[{'finishReason':'MAX_TOKENS'}]}}
        for provider,response in cases.items():
            self.api.post=lambda *args, response=response:response
            with self.assertRaises(APIError):self.api.complete(provider,'Return JSON')
    def test_images_are_encoded_for_each_provider(self):
        image=Path(self.temp.name)/'frame.png';image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lGQAAAAASUVORK5CYII='))
        for provider in ('chatgpt','gemini','claude'):self.api.complete(provider,'Inspect this frame',[str(image)])
        self.assertIn('data:image/png;base64,',self.calls[0][2]['input'][0]['content'][1]['image_url'])
        self.assertIn('inlineData',self.calls[1][2]['contents'][0]['parts'][1])
        self.assertEqual(self.calls[2][2]['messages'][0]['content'][0]['source']['media_type'],'image/png')
