import concurrent.futures
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from app import AppServer
from runner.store import Store


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'studio.sqlite3')
        self.board = self.store.production
        self.project = self.store.characters.create_project('Wildlife')
        self.scenes = self.store.characters.add_scenes(self.project, [{'action': 'Dê trên núi'}, {'action': 'Báo quan sát'}])
        self.board.enable(self.project)

    def task(self, stage, scene=None):
        return next(t for t in self.board.snapshot(self.project)['tasks'] if t['stage'] == stage and t['scene_id'] == scene)

    def complete(self, stage, scene=None, job=None):
        task = self.task(stage, scene)
        self.board.transition(task['id'], 'start')
        return self.board.transition(task['id'], 'complete', 'Kết quả thực tế', job)

    def test_dependencies_are_per_scene_then_join_before_edit(self):
        with self.assertRaises(ValueError):
            self.board.transition(self.task('script')['id'], 'start')
        self.complete('research')
        self.complete('script')
        self.assertFalse(self.task('prompt', self.scenes[0])['ready'])
        self.complete('bible')
        self.assertTrue(self.task('prompt', self.scenes[0])['ready'])
        for index, scene in enumerate(self.scenes):
            self.complete('prompt', scene)
            self.assertEqual(self.store.characters.project(self.project)['scenes'][index]['prompt'], 'Kết quả thực tế')
        self.assertFalse(self.task('video', self.scenes[0])['ready'])  # chờ duyệt tạo thử
        self.complete('pilot')
        for index, scene in enumerate(self.scenes):
            self.store.enqueue(['Prompt'], 1, 'production-test-batch-'+str(index), scene_ids=[scene])
            job = next(j for j in self.store.snapshot()['jobs'] if j['scene_id'] == scene)
            # Simulate an engine result; the production board must inspect the persisted job.
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='done',output='generated.mp4' WHERE id=?", (job['id'],))
            self.complete('video', scene, job['id'])
            self.complete('shot_qc', scene)
            if not index:
                self.assertFalse(self.task('edit')['ready'])
        for stage in ['edit', 'film_qc', 'delivery']:
            self.complete(stage)
        result = self.board.snapshot(self.project)
        self.assertEqual(result['summary']['progress'], 100)
        self.assertEqual(result['execution_mode'], 'manual')
        self.assertEqual(result['audio_policy'], 'natural_only')

    def test_video_requires_completed_job_of_correct_scene(self):
        scene = self.scenes[0]
        for stage in ('research', 'script', 'bible'):
            self.complete(stage)
        for other in self.scenes:
            self.complete('prompt', other)
        self.complete('pilot')
        task = self.task('video', scene)
        self.board.transition(task['id'], 'start')
        self.store.enqueue(['Other scene'], 1, 'production-other-scene', scene_ids=[self.scenes[1]])
        job = self.store.snapshot()['jobs'][0]
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='done',output='other.mp4' WHERE id=?", (job['id'],))
        with self.assertRaises(ValueError):
            self.board.transition(task['id'], 'complete', 'Video', job['id'])
        self.assertEqual(self.task('video', scene)['status'], 'working')
        self.assertFalse(self.task('shot_qc', scene)['ready'])
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET scene_id=?,status='waiting' WHERE id=?", (scene,job['id']))
        with self.assertRaises(ValueError):
            self.board.transition(task['id'], 'complete', 'Video', job['id'])

    def test_pause_retry_and_restart_preserve_audit_without_replaying_muse(self):
        task = self.task('research')
        self.board.control(self.project, 'pause')
        with self.assertRaises(ValueError):
            self.board.transition(task['id'], 'start')
        self.board.control(self.project, 'resume')
        self.board.transition(task['id'], 'start')
        with self.assertRaises(ValueError):
            self.board.transition(task['id'], 'complete', '   ')
        self.board.transition(task['id'], 'fail', 'Thiếu dữ liệu tham chiếu')
        self.board.transition(task['id'], 'retry')
        self.board.transition(task['id'], 'start')
        restored = Store(self.store.path).production.snapshot(self.project)
        task = next(t for t in restored['tasks'] if t['stage'] == 'research')
        self.assertEqual((task['status'], task['attempts']), ('working', 2))
        self.assertIn('Thiếu dữ liệu tham chiếu', [e['detail'] for e in restored['events']])
        self.assertEqual(restored['jobs'], [])

    def test_concurrent_claims_and_enable_are_idempotent(self):
        original = self.board.snapshot(self.project)
        self.board.enable(self.project)
        self.assertEqual(len(original['tasks']), len(self.board.snapshot(self.project)['tasks']))
        task = self.task('research')
        def start(_):
            try:
                self.board.transition(task['id'], 'start')
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(start, range(4))), 1)
        self.assertEqual(self.task('research')['attempts'], 1)

    def test_existing_projects_survive_migration_and_delete_cascades(self):
        restored = Store(self.store.path)
        self.assertEqual(restored.characters.project(self.project)['scenes'][0]['id'], self.scenes[0])
        restored.characters.delete_project(self.project)
        with restored.connect() as db:
            for table in ['production_projects', 'production_tasks', 'production_events']:
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)


class ProductionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'studio.sqlite3')
        class IdleEngine:
            changed = threading.Event()
        self.server = AppServer(0, self.store, IdleEngine())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, body=None, key=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        headers = {'Content-Type': 'application/json'}
        if key:
            headers.update({'Origin': self.server.origin, 'X-Runner-Key': self.server.secret})
        conn.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, headers)
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def test_sound_library_upload_serve_and_delete(self):
        from runner import video_tools
        wav = Path(self.temp.name) / 'wind.wav'
        video_tools.run([video_tools.ffmpeg(), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anoisesrc=d=0.5:a=0.1', str(wav)], 30)
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        conn.request('POST', '/api/production/sounds/upload', wav.read_bytes(), {'Origin': self.server.origin,
                     'X-Runner-Key': self.server.secret, 'X-Sound-Name': 'gi%C3%B3%20n%C3%BAi.wav', 'Content-Type': 'application/octet-stream'})
        response = conn.getresponse(); body = json.loads(response.read()); conn.close()
        self.assertEqual(response.status, 200, body)
        sound = body['sound']; self.assertEqual(sound['name'], 'gió núi')
        self.assertEqual(self.request('/api/production/sound/' + sound['id'])[0], 200)
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        conn.request('POST', '/api/production/sounds/upload', b'not audio', {'Origin': self.server.origin,
                     'X-Runner-Key': self.server.secret, 'X-Sound-Name': 'x.mp3'})
        self.assertEqual(conn.getresponse().status, 400); conn.close()
        status, raw = self.request('/api/production/sounds/delete', {'id': sound['id']})
        self.assertEqual((status, json.loads(raw)['sounds']), (200, []))
        self.assertEqual(self.request('/api/production/sound/' + sound['id'])[0], 404)

    def test_agent_configuration_endpoints_keep_security_and_validate_inputs(self):
        _, raw=self.request('/api/production/create',dict(name='Agents',scenes=[dict(action='Goat')]))
        project=json.loads(raw)['project']['id']
        data=json.loads(self.request('/api/production/'+project)[1])
        self.assertEqual(set(data['agent_state']['providers']),{'gemini','chatgpt','claude'})
        self.assertEqual(self.request('/production-agents.js')[0],200)
        payload=dict(project_id=project,config=data['agent_state']['config'])
        self.assertEqual(self.request('/api/production/agent/configure',payload,key=False)[0],403)
        payload['config']['discussion_rounds']=3
        self.assertEqual(self.request('/api/production/agent/configure',payload)[0],400)
        self.assertEqual(self.request('/api/production/agent/login',dict(provider='unknown'))[0],400)
        self.assertEqual(self.request('/api/production/run/'+('0'*32)+'/film')[0],404)

    def test_api_and_gateway_credentials_require_key_and_are_never_echoed(self):
        _,raw=self.request('/api/production/create',dict(name='Keys',scenes=[dict(action='Goat')]))
        project=json.loads(raw)['project']['id']
        body=dict(provider='gemini',key='fake-test-only-private',model='gemini-2.5-flash',max_output_tokens=128)
        self.assertEqual(self.request('/api/production/agent/api-save',body,key=False)[0],403)
        code,response=self.request('/api/production/agent/api-save',body)
        self.assertEqual(code,200);self.assertNotIn(b'fake-test-only-private',response)
        self.assertNotIn(b'fake-test-only-private',self.request('/api/production/'+project)[1])
        payload=dict(url='http://127.0.0.1:8899',token='fake-gateway-test-token')
        code,response=self.request('/api/production/agent/gateway-save',payload)
        self.assertEqual(code,200);self.assertNotIn(b'fake-gateway-test-token',response)
        self.assertNotIn(b'fake-gateway-test-token',self.request('/api/production/'+project)[1])
        self.assertEqual(self.request('/api/production/agent/gateway-save',dict(url='http://remote.example',token='fake-token'))[0],400)

    def test_create_board_export_and_existing_security(self):
        payload = dict(name='Phim núi', scenes=[dict(action='Dê bước qua vách đá')])
        self.assertEqual(self.request('/api/production/create', payload, key=False)[0], 403)
        status, body = self.request('/api/production/create', payload)
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(len(data['tasks']), 10)  # 7 công đoạn toàn phim + 3 theo cảnh
        project = data['project']['id']
        self.assertEqual(self.request('/api/production/'+project)[0], 200)
        self.assertEqual(json.loads(self.request('/api/production')[1])['projects'][0]['name'], 'Phim núi')
        self.assertEqual(self.request('/production.js')[0], 200)
        self.assertEqual(self.request('/api/production/create', dict(scenes=[{}]))[0], 400)
        research = next(t for t in data['tasks'] if t['stage']=='research')
        self.assertEqual(self.request('/api/production/task', dict(task_id=research['id'],action='start'))[0],200)
        self.assertEqual(self.request('/api/production/task', dict(task_id=research['id'],action='complete',output='Đã phân tích'))[0],200)
        self.assertEqual(self.request('/api/production/'+('0'*32))[0],404)
