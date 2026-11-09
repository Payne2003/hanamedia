"""Durable production board shared by manual actions and browser-agent workers."""
import time
import uuid

from .characters import check_id, text

STAGES = (
    ("research", "Nghiên cứu", False),
    ("script", "Đạo diễn & biên kịch", True),
    ("prompt", "Prompt & continuity", True),
    ("video", "Tạo video · Muse", True),
    ("shot_qc", "QC cảnh quay", True),
    ("edit", "Dựng phim", False),
    ("film_qc", "QC phim", False),
    ("delivery", "Bàn giao & lưu trữ", False),
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS production_projects (
 project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
 status TEXT NOT NULL DEFAULT 'active', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS production_tasks (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 scene_id TEXT REFERENCES scenes(id) ON DELETE CASCADE, stage TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'waiting', attempts INTEGER NOT NULL DEFAULT 0,
 output TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', job_id TEXT,
 created REAL NOT NULL, updated REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS production_scope ON production_tasks(project_id, stage, coalesce(scene_id,''));
CREATE TABLE IF NOT EXISTS production_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 task_id TEXT, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
"""


class ProductionBoard:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript(SCHEMA)

    @staticmethod
    def event(db, project_id, task_id, action, detail=""):
        db.execute("INSERT INTO production_events(project_id,task_id,action,detail,created) VALUES(?,?,?,?,?)",
                   (project_id, task_id, action, detail, time.time()))

    def enable(self, project_id):
        check_id(project_id, "Dự án")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("Không tìm thấy dự án.")
            scenes = db.execute("SELECT id FROM scenes WHERE project_id=? ORDER BY ordinal", (project_id,)).fetchall()
            if not scenes:
                raise ValueError("Dự án cần ít nhất một cảnh.")
            now = time.time()
            added = db.execute("INSERT OR IGNORE INTO production_projects VALUES(?, 'active', ?)",
                               (project_id, now)).rowcount
            for stage, _, per_scene in STAGES:
                for scene_id in ([s['id'] for s in scenes] if per_scene else [None]):
                    db.execute("""INSERT OR IGNORE INTO production_tasks
                        (id,project_id,scene_id,stage,created,updated) VALUES(?,?,?,?,?,?)""",
                        (uuid.uuid4().hex, project_id, scene_id, stage, now, now))
            if added:
                self.event(db, project_id, None, "enabled", "Đã bật bảng sản xuất; có thể xử lý thủ công hoặc giao agent.")
        return self.snapshot(project_id)

    @staticmethod
    def dependencies(task, tasks):
        stages = [s[0] for s in STAGES]
        index = stages.index(task['stage'])
        if not index:
            return []
        previous = [t for t in tasks if t['stage'] == stages[index - 1]]
        return [t for t in previous if task['scene_id'] is None or t['scene_id'] in (None, task['scene_id'])]

    def snapshot(self, project_id):
        check_id(project_id, "Dự án")
        with self.store.connect() as db:
            project = db.execute("""SELECT p.*, pp.status AS production_status FROM projects p
                JOIN production_projects pp ON pp.project_id=p.id WHERE p.id=?""", (project_id,)).fetchone()
            if not project:
                raise ValueError("Dự án chưa bật bảng sản xuất.")
            scenes = [dict(r) for r in db.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY ordinal", (project_id,))]
            tasks = [dict(r) for r in db.execute("SELECT * FROM production_tasks WHERE project_id=? ORDER BY created,rowid", (project_id,))]
            invalidated = {r['task_id']:r['since'] for r in db.execute("SELECT task_id,max(created) AS since FROM production_events WHERE project_id=? AND action='invalidated' GROUP BY task_id", (project_id,))}
            jobs = [dict(r) for r in db.execute("""SELECT j.id,j.scene_id,j.status,j.cost,j.output,j.error,j.created,j.batch,j.submitted
                FROM jobs j JOIN scenes s ON s.id=j.scene_id WHERE s.project_id=? ORDER BY j.created""", (project_id,))]
            events = [dict(r) for r in db.execute("SELECT * FROM production_events WHERE project_id=? ORDER BY id DESC LIMIT 100", (project_id,))]
        for scene in scenes:
            scene['code'] = f"SCENE_{scene['ordinal'] + 1:03d}"
        for task in tasks:
            task['take_after'] = invalidated.get(task['id'], 0)
            deps = self.dependencies(task, tasks)
            task['blocked_by'] = [t['id'] for t in deps if t['status'] != 'done']
            task['ready'] = (project['production_status'] == 'active' and task['status'] == 'waiting'
                             and not task['blocked_by'])
        done = sum(t['status'] == 'done' for t in tasks)
        return dict(project=dict(project), scenes=scenes, tasks=tasks, jobs=jobs, events=events,
                    stages=[dict(id=s, name=n, per_scene=p) for s, n, p in STAGES],
                    summary=dict(done=done, total=len(tasks), errors=sum(t['status'] == 'error' for t in tasks),
                                 attempts=sum(t['attempts'] for t in tasks),
                                 progress=round(100 * done / len(tasks)) if tasks else 0),
                    execution_mode='manual', audio_policy='natural_only', schema_version=1)

    def list_projects(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("""SELECT p.id,p.name,pp.status,
                count(t.id) AS total, sum(t.status='done') AS done, sum(t.status='error') AS errors
                FROM production_projects pp JOIN projects p ON p.id=pp.project_id
                LEFT JOIN production_tasks t ON t.project_id=p.id GROUP BY p.id ORDER BY p.updated DESC""")]

    def control(self, project_id, action):
        if action not in ('pause', 'resume'):
            raise ValueError("Thao tác không hợp lệ.")
        check_id(project_id, 'Dự án')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("UPDATE production_projects SET status=? WHERE project_id=?",
                          ('paused' if action == 'pause' else 'active', project_id)).rowcount != 1:
                raise ValueError('Không tìm thấy dự án sản xuất.')
            self.event(db, project_id, None, action)
        return self.snapshot(project_id)

    def transition(self, task_id, action, output='', job_id=None):
        check_id(task_id, 'Tác vụ')
        if action not in ('start', 'complete', 'fail', 'retry'):
            raise ValueError('Thao tác không hợp lệ.')
        output = text(output, 20000)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("""SELECT t.*,p.status AS project_status FROM production_tasks t
                JOIN production_projects p ON p.project_id=t.project_id WHERE t.id=?""", (task_id,)).fetchone()
            if not row:
                raise ValueError('Không tìm thấy tác vụ.')
            task = dict(row)
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_runs'").fetchone():
                if db.execute("SELECT 1 FROM production_runs WHERE task_id=? AND status IN ('running','review')", (task_id,)).fetchone():
                    raise ValueError('Tác vụ có agent đang chạy hoặc chờ duyệt. Dùng Duyệt/Trả lại kết quả agent trước.')
            if task['project_status'] != 'active':
                raise ValueError('Dự án đang tạm dừng. Tiếp tục dự án trước khi cập nhật tác vụ.')
            tasks = [dict(r) for r in db.execute('SELECT * FROM production_tasks WHERE project_id=?', (task['project_id'],))]
            if action == 'start':
                if task['status'] != 'waiting' or any(t['status'] != 'done' for t in self.dependencies(task, tasks)):
                    raise ValueError('Tác vụ chưa sẵn sàng hoặc đã được nhận.')
                db.execute("UPDATE production_tasks SET status='working',attempts=attempts+1,updated=? WHERE id=?", (time.time(), task_id))
            elif action == 'retry':
                if task['status'] != 'error':
                    raise ValueError('Chỉ thử lại tác vụ lỗi; không tự gửi lại video đã gửi lên Muse.')
                db.execute("UPDATE production_tasks SET status='waiting',error='',updated=? WHERE id=?", (time.time(), task_id))
            else:
                if task['status'] != 'working':
                    raise ValueError('Cần nhận xử lý tác vụ trước khi ghi kết quả.')
                if not output:
                    raise ValueError('Cần ghi kết quả hoặc lý do lỗi để lưu lịch sử.')
                if action == 'complete' and task['stage'] == 'video':
                    check_id(job_id, 'Video job')
                    job = db.execute("SELECT * FROM jobs WHERE id=? AND scene_id=?", (job_id, task['scene_id'])).fetchone()
                    since = db.execute("SELECT coalesce(max(created),0) FROM production_events WHERE task_id=? AND action='invalidated'", (task_id,)).fetchone()[0]
                    if not job or job['status'] != 'done' or not job['output'] or job['created'] < since:
                        raise ValueError('Chọn video job đã hoàn thành của đúng Scene ID.')
                else:
                    job_id = None
                db.execute('UPDATE production_tasks SET status=?,output=?,error=?,job_id=?,updated=? WHERE id=?',
                           ('done' if action == 'complete' else 'error', output if action == 'complete' else '',
                            output if action == 'fail' else '', job_id, time.time(), task_id))
                if action == 'complete' and task['stage'] == 'prompt':
                    db.execute('UPDATE scenes SET prompt=?,updated=? WHERE id=?', (output, time.time(), task['scene_id']))
            self.event(db, task['project_id'], task_id, action, output)
        return self.snapshot(task['project_id'])
