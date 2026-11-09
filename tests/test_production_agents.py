import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runner.production_agents import AgentReview, ProductionAgents, parse_answer, validate_result
from runner.production_media import inspect_clip, render_timeline, validate_timeline
from runner.store import Store
from runner import video_tools


class Browser:
    name='AI test'
    def __init__(self, answers):
        self.answers=list(answers);self.calls=[];self.job={'id':'','busy':False}
    def start(self, kind, prompt, **kwargs):
        if not self.answers:
            raise ValueError('Không có câu trả lời mô phỏng.')
        self.calls.append((kind,prompt,kwargs))
        answer=self.answers.pop(0)
        self.job={'id':str(len(self.calls)),'busy':False,'result':{'answer':json.dumps(answer)},'error':''}
        return self.job['id']
    def snapshot(self): return dict(self.job)
    def account(self): return {'logged_in':True}
    def cancel(self): self.job['busy']=False


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'studio.sqlite3')
        self.project=self.store.characters.create_project('Wildlife')
        self.scene=self.store.characters.add_scenes(self.project,[{'action':'Dê vượt vách núi'}])[0]
        self.store.production.enable(self.project)
        self.server=SimpleNamespace(store=self.store,license=SimpleNamespace(allows=lambda permission:True),
                                    gemini_web=Browser([]),chatgpt_web=Browser([]))
        self.agents=ProductionAgents(self.server);self.addCleanup(self.agents.close)
        config=self.agents.defaults()
        for agent in config['agents'].values():agent['transport']='browser'
        self.agents.configure(self.project,config)

    def task(self,stage):return next(t for t in self.store.production.snapshot(self.project)['tasks'] if t['stage']==stage)
    def complete_manual(self,stage):
        task=self.task(stage);self.store.production.transition(task['id'],'start')
        self.store.production.transition(task['id'],'complete','Kết quả đã duyệt')
    def run_agent(self,stage):
        run=self.agents.start(self.task(stage)['id']);self.agents.thread.join(5)
        self.assertFalse(self.agents.thread.is_alive())
        return next(r for r in self.agents.public(self.project)['runs'] if r['id']==run['run_id'])

    def test_research_discussion_is_logged_and_requires_review(self):
        self.server.gemini_web.answers=[{'summary':'Phác thảo từ yêu cầu','needs_review':False}]
        self.server.chatgpt_web.answers=[{'approved':True,'issues':[],'needs_review':False}]
        run=self.run_agent('research')
        self.assertEqual(run['status'],'review')
        self.assertEqual(len(run['calls']),2)
        self.assertFalse(self.task('script')['ready'])
        self.agents.apply(run['id'],human=True)
        self.assertTrue(self.task('script')['ready'])
        self.assertEqual(run['result']['reference_video_verified'],False)

    def test_script_and_prompt_are_written_to_scene(self):
        self.complete_manual('research')
        script=dict(setting='Núi tuyết',camera='Toàn cảnh',behavior='Quan sát',action='Dê bước qua khe đá',needs_review=False)
        self.server.gemini_web.answers=[script]
        self.server.chatgpt_web.answers=[{'approved':True,'issues':[],'needs_review':False}]
        run=self.run_agent('script');self.assertEqual(run['status'],'done')
        self.assertEqual(self.store.characters.project(self.project)['scenes'][0]['action'],script['action'])
        self.server.chatgpt_web.answers=[{'prompt':'Mountain goat steps over a rock crevice. Natural ambience.','needs_review':False}]
        self.server.gemini_web.answers=[{'approved':True,'issues':[],'needs_review':False}]
        self.assertEqual(self.run_agent('prompt')['status'],'done')
        self.assertTrue(self.store.characters.project(self.project)['scenes'][0]['prompt'].startswith('Mountain goat'))

    def test_unresolved_disagreement_cannot_self_approve(self):
        self.server.gemini_web.answers=[{'summary':'Draft','needs_review':False},{'summary':'Revised','needs_review':False}]
        self.server.chatgpt_web.answers=[{'approved':False,'issues':['Không đủ bằng chứng'],'needs_review':True}]
        run=self.run_agent('research')
        self.assertEqual(len(run['calls']),3)
        self.assertTrue(run['result']['needs_review'])
        self.assertEqual(run['status'],'review')

    def test_call_budget_and_bad_json_do_not_unlock_downstream(self):
        config=self.agents.settings(self.project);config['max_calls']=1;self.agents.configure(self.project,config)
        self.server.gemini_web.answers=[{'summary':'Draft','needs_review':False}]
        run=self.run_agent('research')
        self.assertIn('giới hạn',run['error']);self.assertEqual(len(run['calls']),1)
        with self.assertRaises(ValueError):self.agents.apply(run['id'],human=True)
        self.agents.reject(run['id']);self.assertEqual(self.task('research')['status'],'error')
        with self.assertRaises(AgentReview):parse_answer('Tôi đã làm xong')
        with self.assertRaises(AgentReview):validate_result('shot_qc',{'passed':'yes','needs_review':False,'observations':[]})

    def test_stale_input_is_not_applied(self):
        self.server.gemini_web.answers=[{'summary':'Draft','needs_review':False}]
        self.server.chatgpt_web.answers=[{'approved':True,'issues':[],'needs_review':False}]
        run=self.run_agent('research')
        with self.store.connect() as db:db.execute('UPDATE scenes SET action=? WHERE id=?',('Đã đổi hành động',self.scene))
        with self.assertRaisesRegex(ValueError,'Đầu vào đã đổi'):self.agents.apply(run['id'],human=True)
        self.assertFalse(self.task('script')['ready'])

    def test_restart_does_not_replay_browser_request(self):
        task=self.task('research');self.store.production.transition(task['id'],'start')
        with self.store.connect() as db:
            db.execute("INSERT INTO production_runs(id,project_id,task_id,status,fingerprint,created,updated) VALUES(?,?,?,'running','old',1,1)",('a'*32,self.project,task['id']))
        restarted=ProductionAgents(self.server);self.addCleanup(restarted.close)
        run=restarted.public(self.project)['runs'][0]
        self.assertEqual(run['status'],'review');self.assertIn('giữa tác vụ',run['error'])
        self.assertEqual(self.server.gemini_web.calls,[])

    def test_pause_and_manual_override_cannot_race_agent_review(self):
        self.server.gemini_web.answers=[{'summary':'Draft','needs_review':False}]
        self.server.chatgpt_web.answers=[{'approved':True,'issues':[],'needs_review':False}]
        run=self.run_agent('research')
        self.store.production.control(self.project,'pause')
        with self.assertRaises(ValueError):self.agents.apply(run['id'],human=True)
        self.store.production.control(self.project,'resume')
        with self.assertRaisesRegex(ValueError,'agent'):self.store.production.transition(self.task('research')['id'],'complete','Override')

    def test_three_providers_can_review_without_unbounded_conversation(self):
        self.server.claude_web=Browser([{'approved':True,'issues':[],'needs_review':False}])
        config=self.agents.settings(self.project);config['agents']['research']['consultant']='claude'
        self.agents.configure(self.project,config)
        self.server.gemini_web.answers=[{'summary':'Draft','needs_review':False}]
        self.server.chatgpt_web.answers=[{'approved':True,'issues':[],'needs_review':False}]
        run=self.run_agent('research')
        self.assertEqual([c['provider'] for c in run['calls']],['gemini','chatgpt','claude'])
        self.assertEqual(run['status'],'review')

    def test_qc_rework_invalidates_downstream_and_disallows_old_take(self):
        for stage in ('research','script','prompt'):self.complete_manual(stage)
        self.store.enqueue(['Goat'],1,'old-take-000000001',scene_ids=[self.scene])
        with self.store.connect() as db:db.execute("UPDATE jobs SET status='done',output='old.mp4'")
        oldjob=self.store.snapshot()['jobs'][0]['id']
        self.agents.sync_muse(self.task('video')['id'])
        self.server.gemini_web.answers=[{'passed':False,'needs_review':True,'observations':[{'time':.2,'description':'Sai nhân vật'}]}]
        self.server.chatgpt_web.answers=[{'approved':True,'issues':[],'needs_review':False}]
        evidence=dict(info={'duration':1,'audio':False},frames=[],decode_ok=True)
        with patch('runner.production_agents.inspect_clip',return_value=evidence):run=self.run_agent('shot_qc')
        self.agents.rework(run['id'],'prompt')
        self.assertTrue(self.task('prompt')['ready'])
        self.assertEqual(self.task('video')['job_id'],None)
        self.assertFalse(self.task('edit')['ready'])
        self.assertEqual(self.agents.public(self.project)['runs'][0]['status'],'superseded')
        self.complete_manual('prompt')
        with self.assertRaises(ValueError):self.agents.sync_muse(self.task('video')['id'],oldjob)
        self.assertTrue(self.store.snapshot()['jobs'])

    def test_real_media_qc_timeline_render_film_approval_and_delivery(self):
        source=Path(self.temp.name)/'source.mp4'
        result=video_tools.run([video_tools.ffmpeg(),'-v','error','-y','-f','lavfi','-i','color=c=green:s=160x90:r=30:d=1.2',
                                '-c:v','libx264','-threads','1',str(source)],30)
        self.assertEqual(result.returncode,0,result.stderr)
        config=self.agents.settings(self.project);config['discussion_rounds']=0;self.agents.configure(self.project,config)
        for stage in ('research','script','prompt'):self.complete_manual(stage)
        self.store.enqueue(['Goat'],1,'real-media-take-00001',scene_ids=[self.scene])
        with self.store.connect() as db:db.execute("UPDATE jobs SET status='done',output=?",(str(source),))
        self.agents.sync_muse(self.task('video')['id'])
        report={'passed':True,'needs_review':False,'observations':[{'time':.3,'description':'Khung hình mẫu đọc được'}]}
        self.server.gemini_web.answers=[report]
        qc=self.run_agent('shot_qc');self.assertEqual(qc['status'],'review')
        self.assertTrue(self.server.gemini_web.calls[-1][2]['lines']['files'])
        self.agents.apply(qc['id'],human=True)
        job=self.task('video')['job_id']
        self.server.gemini_web.answers=[dict(clips=[dict(scene_id=self.scene,job_id=job,**{'in':.1,'out':.9,'speed':1})],needs_review=False)]
        edit=self.run_agent('edit');self.assertTrue(edit['result']['render_pending'])
        self.agents.apply(edit['id'],human=True);self.agents.thread.join(10)
        self.assertFalse(self.agents.thread.is_alive())
        rendered=next(r for r in self.agents.public(self.project)['runs'] if r['id']==edit['id'])
        self.assertFalse(rendered['error']);self.assertTrue(Path(rendered['result']['render']['path']).is_file())
        self.agents.apply(edit['id'],human=True)
        self.server.gemini_web.answers=[report]
        film=self.run_agent('film_qc')
        with self.assertRaisesRegex(ValueError,'âm thanh'):self.agents.apply(film['id'],human=True)
        self.agents.apply(film['id'],human=True,audio_confirmed=True)
        delivery=self.run_agent('delivery');self.assertEqual(delivery['status'],'done')
        manifest=json.loads(Path(delivery['result']['manifest']).read_text(encoding='utf-8'))
        self.assertEqual(manifest['scenes'][0]['scene_id'],self.scene)
        self.assertEqual(len(manifest['sha256']),64)
        self.assertEqual(self.store.production.snapshot(self.project)['summary']['progress'],100)

    def test_closed_service_cannot_launch_another_browser_job(self):
        self.agents.close()
        with self.assertRaisesRegex(ValueError,'đang đóng'):
            self.agents.start(self.task('research')['id'])
        self.assertEqual(self.server.gemini_web.calls,[])

    def test_api_transport_records_usage_and_does_not_open_browser(self):
        self.complete_manual('research')
        self.agents.api.vault.save('gemini',key='test-owner-only',max_output_tokens=128)
        script=dict(setting='Núi',camera='Toàn cảnh',behavior='Quan sát',action='Dê bước qua đá',needs_review=False)
        self.agents.api.post=lambda *args:{'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(script)}]}}],
                                          'usageMetadata':{'promptTokenCount':20,'candidatesTokenCount':10}}
        config=self.agents.settings(self.project);config['agents']['script'].update(transport='api',reviewer='',consultant='')
        self.agents.configure(self.project,config)
        run=self.run_agent('script')
        self.assertEqual(run['status'],'done');self.assertEqual(run['calls'][0]['transport'],'api')
        self.assertEqual(json.loads(run['calls'][0]['usage'])['input_tokens'],20)
        self.assertEqual(self.server.gemini_web.calls,[])
        self.assertNotIn('test-owner-only',json.dumps(self.agents.public(self.project)))

    def test_gateway_transport_routes_through_real_http_and_keeps_owner_key_off_client(self):
        from runner.ai_api import APIClients
        from runner.ai_gateway import GatewayService,GatewayServer
        self.agents.gateway_enabled=True  # máy chủ trung gian đang tạm ẩn với người dùng; vẫn giữ đường chạy
        owner=APIClients(Path(self.temp.name)/'owner')
        owner.vault.save('gemini',key='test-gateway-owner-only',max_output_tokens=128)
        script=dict(setting='Núi',camera='Toàn cảnh',behavior='Quan sát',action='Dê bước qua đá',needs_review=False)
        owner.post=lambda *args:{'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(script)}]}}],
                                  'usageMetadata':{'promptTokenCount':20,'candidatesTokenCount':10}}
        service=GatewayService(Path(self.temp.name)/'owner',api=owner)
        grant=service.store.create_user('Client test')
        server=GatewayServer(('127.0.0.1',0),service);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            self.agents.api.vault.save_gateway('http://127.0.0.1:'+str(server.server_port),grant['token'])
            self.complete_manual('research')
            config=self.agents.settings(self.project);config['agents']['script'].update(transport='gateway',reviewer='',consultant='')
            self.agents.configure(self.project,config)
            run=self.run_agent('script');self.assertEqual(run['status'],'done')
            self.assertEqual(run['calls'][0]['transport'],'gateway')
            self.assertEqual(service.store.report()[0]['used_tokens'],30)
            self.assertFalse(self.agents.api.vault.public()['gemini']['configured'])
            self.assertNotIn(grant['token'],json.dumps(self.agents.public(self.project)))
        finally:server.shutdown();server.server_close();thread.join()

    def test_defaults_run_on_this_machine_and_old_gateway_config_falls_back_to_browser(self):
        self.assertTrue(all(a['transport']=='browser' for a in self.agents.defaults()['agents'].values()))
        config=self.agents.defaults();config['agents']['script']['transport']='gateway'
        with self.store.connect() as db:
            db.execute('UPDATE production_agent_settings SET config=? WHERE project_id=?',(json.dumps(config),self.project))
        self.assertEqual(self.agents.settings(self.project)['agents']['script']['transport'],'browser')
        with self.assertRaisesRegex(ValueError,'Trình duyệt'):self.agents.configure(self.project,config)
        self.assertNotIn('gateway',{a['transport'] for a in self.agents.public(self.project)['config']['agents'].values()})

    def test_preflight_blocks_before_claiming_task(self):
        self.server.gemini_web.account=lambda:{'logged_in':False}
        with self.assertRaisesRegex(ValueError,'chưa đăng nhập'):self.agents.start(self.task('research')['id'])
        self.assertEqual(self.task('research')['status'],'waiting')
        self.assertEqual(self.server.gemini_web.calls,[])
        readiness=self.agents.public(self.project)['readiness']
        self.assertFalse(readiness['ready']);self.assertTrue(readiness['stages']['research'])
        self.server.gemini_web.account=lambda:{'logged_in':True}
        self.server.chatgpt_web.job['busy']=True
        with self.assertRaisesRegex(ValueError,'đang bận'):self.agents.start(self.task('research')['id'])
        self.server.chatgpt_web.job['busy']=False
        config=self.agents.settings(self.project);config['agents']['research'].update(provider='claude',reviewer='',consultant='')
        self.agents.configure(self.project,config)
        with self.assertRaisesRegex(ValueError,'Claude'):self.agents.start(self.task('research')['id'])
        config['agents']['research'].update(transport='api');self.agents.configure(self.project,config)
        with self.assertRaisesRegex(ValueError,'khóa API'):self.agents.start(self.task('research')['id'])

    def test_edit_call_budget_is_per_scene_so_long_films_can_be_cut(self):
        self.agents.close();self.agents.closing=False;self.agents.stop.clear()
        with self.store.connect() as db:
            run_id='a'*32
            task=self.task('edit')
            db.execute("INSERT INTO production_runs(id,project_id,task_id,status,fingerprint,created,updated) VALUES(?,?,?,'running','x',0,0)",(run_id,self.project,task['id']))
        config=self.agents.settings(self.project);config['max_calls']=1
        self.server.gemini_web.answers=[{'a':1},{'a':2},{'a':3}]
        for scene in ('s1','s2','s3'):
            self.agents.ask(run_id,self.project,'gemini','Đề xuất','p',[],config,scope=scene)
        with self.assertRaisesRegex(AgentReview,'cảnh này'):
            self.agents.ask(run_id,self.project,'gemini','Đề xuất','p',[],config,scope='s1')

    def test_stop_kills_running_ffmpeg(self):
        from runner import production_media
        stop=threading.Event();timer=threading.Timer(.5,stop.set);timer.start()
        import time
        started=time.monotonic()
        with self.assertRaises(production_media.Stopped):
            production_media.run([video_tools.ffmpeg(),'-v','error','-re','-f','lavfi','-i','color=c=black:s=64x64:r=5:d=60','-f','null','-'],60,stop)
        self.assertLess(time.monotonic()-started,10)

    def test_muse_sync_requires_unambiguous_scene_take(self):
        for stage in ('research','script','prompt'):self.complete_manual(stage)
        self.store.enqueue(['Goat'],1,'muse-sync-000000001',scene_ids=[self.scene])
        with self.assertRaises(ValueError):self.agents.sync_muse(self.task('video')['id'])
        with self.store.connect() as db:db.execute("UPDATE jobs SET status='done',output='take.mp4'")
        self.agents.sync_muse(self.task('video')['id'])
        self.assertTrue(self.task('shot_qc')['ready'])


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def clip(self,name,color,audio=False):
        path=self.root/name
        args=[video_tools.ffmpeg(),'-v','error','-y','-f','lavfi','-i',f'color=c={color}:s=160x90:r=30:d=1.2']
        if audio:args+=['-f','lavfi','-i','sine=frequency=400:duration=1.2']
        args+=['-c:v','libx264','-threads','1','-pix_fmt','yuv420p']
        if audio:args+=['-c:a','aac','-shortest']
        result=video_tools.run(args+[str(path)],30)
        self.assertEqual(result.returncode,0,result.stderr)
        return path
    def test_real_ffmpeg_decode_frames_and_trimmed_mixed_audio_render(self):
        files=[self.clip('red.mp4','red',True),self.clip('blue.mp4','blue')]
        evidence=inspect_clip(files[0],self.root/'evidence')
        self.assertEqual(len(evidence['frames']),4);self.assertTrue(evidence['decode_ok'])
        takes=[dict(scene_id=str(i),job_id='job'+str(i),path=str(f),**video_tools.probe(f)) for i,f in enumerate(files)]
        plan={'clips':[dict(scene_id=t['scene_id'],job_id=t['job_id'],**{'in':.2,'out':1.,'speed':1.}) for t in takes]}
        result=render_timeline(plan,takes,self.root/'film.mp4')
        self.assertAlmostEqual(result['info']['duration'],1.6,delta=.15)
        self.assertTrue(result['info']['audio']);self.assertTrue(result['requires_audio_review'])
        self.assertTrue(all(f.is_file() for f in files))
    def test_invalid_cuts_paths_and_missing_scenes_are_rejected(self):
        takes=[dict(scene_id='s',job_id='j',path='trusted.mp4',duration=10,width=160,height=90,audio=False)]
        for clip in [dict(scene_id='s',job_id='j',**{'in':0,'out':11}),
                     dict(scene_id='s',job_id='j',**{'in':float('nan'),'out':2}),
                     dict(scene_id='other',job_id='j',**{'in':0,'out':2}),
                     dict(scene_id='s',job_id='wrong',**{'in':0,'out':2})]:
            with self.assertRaises(ValueError):validate_timeline({'clips':[clip]},takes)
        clip=dict(scene_id='s',job_id='j',path='/etc/passwd',**{'in':0,'out':2})
        self.assertEqual(validate_timeline({'clips':[clip]},takes)[0]['path'],'trusted.mp4')

class BrowserWorkerTests(unittest.TestCase):
    def test_generic_production_job_preserves_raw_json_and_attaches_evidence(self):
        from runner.gemini_web import GeminiWebRunner
        import time
        class Session:
            def __init__(self,profile):self.calls=[]
            def alive(self):return True
            def new_chat(self):self.calls.append('new')
            def wait_ready(self,*args):return True
            def choose_model(self,name):return name
            def attach_images(self,files,*args):self.calls.append(('files',files))
            def ask(self,prompt,*args,**kwargs):self.calls.append(('ask',prompt));return '{"needs_review":true}'
            def close(self):pass
        with tempfile.TemporaryDirectory() as folder:
            runner=GeminiWebRunner(Path(folder)/'profile',session_factory=Session)
            try:
                job=runner.start('production','Structured request',model='Pro',lines={'files':['frame.jpg']})
                deadline=time.monotonic()+3
                while runner.snapshot()['busy'] and time.monotonic()<deadline:time.sleep(.01)
                result=runner.snapshot()
                self.assertEqual(result['id'],job);self.assertFalse(result['error'])
                self.assertEqual(result['result']['answer'],'{"needs_review":true}')
                self.assertIn(('files',['frame.jpg']),runner.session.calls)
                self.assertIn(('ask','Structured request'),runner.session.calls)
            finally:runner.close();runner.thread.join(2)

    def test_explicit_unavailable_model_is_not_silently_replaced(self):
        from runner.gemini_web import GeminiWebRunner
        import time
        class Session:
            sent=False
            def __init__(self,profile):pass
            def alive(self):return True
            def new_chat(self):pass
            def wait_ready(self,*args):return True
            def choose_model(self,name):return ''
            def ask(self,*args,**kwargs):self.sent=True;raise AssertionError('Must not send')
            def close(self):pass
        with tempfile.TemporaryDirectory() as folder:
            runner=GeminiWebRunner(Path(folder)/'profile',session_factory=Session)
            try:
                runner.start('production','Request',model='Unavailable')
                deadline=time.monotonic()+3
                while runner.snapshot()['busy'] and time.monotonic()<deadline:time.sleep(.01)
                self.assertIn('Không chọn được model',runner.snapshot()['error'])
                self.assertFalse(runner.session.sent)
            finally:runner.close();runner.thread.join(2)
