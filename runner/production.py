"""Bảng sản xuất phim Wildlife: một video gốc -> phim AI 8–9 phút, công đoạn bền, dùng chung cho thao tác tay và agent.

Luồng (theo sơ đồ HANA MEDIA):
  A. 01 Phân tích video gốc -> 02 Đạo diễn & kịch bản toàn phim (tạo danh sách cảnh) -> 03 Nhân vật & môi trường
     -> 03 Prompt từng cảnh
  B. Tạo thử các cảnh khó (duyệt) -> 04 Sản xuất video Muse -> 05 QC cảnh & nối cảnh
  C. 06 Dựng & âm thanh (Editor + Sound Agent -> kế hoạch dựng) -> 07 QC toàn phim -> 08 Kiểm tra & bàn giao
Mỗi công đoạn chỉ mở khi đầu vào cần thiết đã được duyệt. Mã cảnh (SCENE_001…) xuyên suốt.
"""
import time
import uuid

from .characters import check_id, text

# (mã, tên, theo từng cảnh)
STAGES = (
    ("research", "01 · Phân tích video gốc", False),
    ("script", "02 · Đạo diễn & kịch bản", False),
    ("bible", "03 · Nhân vật & môi trường", False),
    ("prompt", "03 · Prompt từng cảnh", True),
    ("pilot", "Tạo thử các cảnh khó", False),
    ("video", "04 · Sản xuất video · Muse", True),
    ("shot_qc", "05 · QC cảnh & nối cảnh", True),
    ("edit", "06 · Dựng & âm thanh", False),
    ("film_qc", "07 · QC toàn phim", False),
    ("delivery", "08 · Kiểm tra & bàn giao", False),
)
STAGE_IDS = tuple(s[0] for s in STAGES)
SCENE_STAGES = tuple(s[0] for s in STAGES if s[2])
DEFAULT_TARGET_SECONDS = 510  # 8 phút 30 giây
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
COLUMNS = {
    "production_projects": {"source_video": "TEXT NOT NULL DEFAULT ''",
                            "target_seconds": f"INTEGER NOT NULL DEFAULT {DEFAULT_TARGET_SECONDS}"},
    "production_tasks": {"difficult": "INTEGER NOT NULL DEFAULT 0"},
    "scenes": {"purpose": "TEXT NOT NULL DEFAULT ''", "duration": "REAL NOT NULL DEFAULT 10",
               "difficult": "INTEGER NOT NULL DEFAULT 0"},
}
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")


def scene_code(ordinal):
    return f"SCENE_{ordinal + 1:03d}"


class ProductionBoard:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript(SCHEMA)
            for table, columns in COLUMNS.items():
                have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                for column, declaration in columns.items():
                    if have and column not in have:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            # Dự án của bản trước: thêm công đoạn mới; kịch bản/QC chuyển thành công đoạn toàn phim.
            for row in db.execute("SELECT project_id FROM production_projects").fetchall():
                self.ensure_tasks(db, row["project_id"])

    @staticmethod
    def event(db, project_id, task_id, action, detail=""):
        db.execute("INSERT INTO production_events(project_id,task_id,action,detail,created) VALUES(?,?,?,?,?)",
                   (project_id, task_id, action, detail, time.time()))

    @staticmethod
    def ensure_tasks(db, project_id):
        """Tạo tác vụ còn thiếu: công đoạn toàn phim, và công đoạn từng cảnh cho mọi cảnh hiện có."""
        now = time.time()
        db.execute("DELETE FROM production_tasks WHERE project_id=? AND scene_id IS NOT NULL AND stage NOT IN (%s)"
                   % ",".join("?" * len(SCENE_STAGES)), (project_id, *SCENE_STAGES))
        scenes = db.execute("SELECT id,difficult FROM scenes WHERE project_id=? ORDER BY ordinal", (project_id,)).fetchall()
        for stage, _, per_scene in STAGES:
            for scene in (scenes if per_scene else [None]):
                db.execute("""INSERT OR IGNORE INTO production_tasks
                    (id,project_id,scene_id,stage,difficult,created,updated) VALUES(?,?,?,?,?,?,?)""",
                           (uuid.uuid4().hex, project_id, scene['id'] if scene else None, stage,
                            int(scene['difficult']) if scene else 0, now, now))

    def create(self, name, brief="", source_video="", target_seconds=DEFAULT_TARGET_SECONDS, source_url=""):
        """Dự án mới từ một video gốc. Danh sách cảnh do công đoạn kịch bản tạo ra."""
        from pathlib import Path
        source_video = str(source_video or "").strip()
        if source_video:
            path = Path(source_video)
            if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
                raise ValueError("Video gốc không tồn tại hoặc không phải file video (mp4, mov, mkv, webm…).")
            source_video = str(path.resolve())
        if type(target_seconds) is not int or not 30 <= target_seconds <= 1200:
            raise ValueError("Thời lượng phim từ 30 giây đến 20 phút.")
        if not source_video and not text(brief):
            raise ValueError("Chọn video gốc hoặc nhập yêu cầu sản xuất.")
        project_id = self.store.characters.create_project(name, brief, source_url)
        with self.store.connect() as db:
            db.execute("INSERT INTO production_projects(project_id,status,created,source_video,target_seconds) VALUES(?,?,?,?,?)",
                       (project_id, 'active', time.time(), source_video, target_seconds))
            self.ensure_tasks(db, project_id)
            self.event(db, project_id, None, "enabled", "Đã tạo dự án sản xuất từ " + ("video gốc." if source_video else "yêu cầu."))
        return self.snapshot(project_id)

    def enable(self, project_id):
        """Đưa dự án Content Studio có sẵn vào bảng sản xuất (cảnh hiện có được giữ)."""
        check_id(project_id, "Dự án")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("Không tìm thấy dự án.")
            added = db.execute("INSERT OR IGNORE INTO production_projects(project_id,status,created) VALUES(?, 'active', ?)",
                               (project_id, time.time())).rowcount
            self.ensure_tasks(db, project_id)
            if added:
                self.event(db, project_id, None, "enabled", "Đã bật bảng sản xuất; có thể xử lý thủ công hoặc giao agent.")
        return self.snapshot(project_id)

    def replace_scenes(self, db, project_id, scenes):
        """Kịch bản được duyệt: thay danh sách cảnh (chỉ khi chưa cảnh nào có video job). Gọi trong giao dịch."""
        if db.execute("SELECT 1 FROM jobs j JOIN scenes s ON s.id=j.scene_id WHERE s.project_id=?", (project_id,)).fetchone():
            raise ValueError("Dự án đã có video job theo cảnh cũ; không thay danh sách cảnh. Tạo dự án mới cho kịch bản mới.")
        db.execute("DELETE FROM production_tasks WHERE project_id=? AND scene_id IS NOT NULL", (project_id,))
        db.execute("DELETE FROM scenes WHERE project_id=?", (project_id,))
        now, ids = time.time(), []
        for ordinal, scene in enumerate(scenes):
            scene_id = uuid.uuid4().hex
            db.execute("""INSERT INTO scenes(id,project_id,ordinal,setting,camera,behavior,action,source,prompt,purpose,
                          duration,difficult,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (scene_id, project_id, ordinal, text(scene['setting'], 2000), text(scene['camera'], 2000),
                        text(scene['behavior'], 2000), text(scene['action'], 2000), text(scene['action'], 2000), '',
                        text(scene['purpose'], 2000), float(scene['duration']), int(bool(scene['difficult'])), now, now))
            ids.append(scene_id)
        self.ensure_tasks(db, project_id)
        return ids

    @staticmethod
    def dependencies(task, tasks):
        """Các tác vụ phải xong trước `task` (theo sơ đồ: kịch bản -> nhân vật -> prompt -> thử cảnh khó -> video…)."""
        stage, scene = task['stage'], task['scene_id']
        of = lambda name, scene_id=Ellipsis: [t for t in tasks if t['stage'] == name and
                                             (scene_id is Ellipsis or t['scene_id'] == scene_id)]
        if stage == 'research':
            return []
        if stage == 'script':
            return of('research')
        if stage == 'bible':
            return of('script')
        if stage == 'prompt':
            return of('bible')
        if stage == 'pilot':
            hard = of('shot_qc')
            hard = [t for t in hard if t.get('difficult')]
            return of('script') + (hard or of('prompt'))
        if stage == 'video':
            return of('prompt', scene) + ([] if task.get('difficult') else of('pilot'))
        if stage == 'shot_qc':
            return of('video', scene)
        if stage == 'edit':
            return of('script') + of('shot_qc')
        if stage == 'film_qc':
            return of('edit')
        if stage == 'delivery':
            return of('film_qc')
        return []

    def project_settings(self, db, project_id):
        row = db.execute("SELECT source_video,target_seconds FROM production_projects WHERE project_id=?", (project_id,)).fetchone()
        return dict(row) if row else dict(source_video='', target_seconds=DEFAULT_TARGET_SECONDS)

    def snapshot(self, project_id):
        check_id(project_id, "Dự án")
        with self.store.connect() as db:
            project = db.execute("""SELECT p.*, pp.status AS production_status, pp.source_video, pp.target_seconds
                FROM projects p JOIN production_projects pp ON pp.project_id=p.id WHERE p.id=?""", (project_id,)).fetchone()
            if not project:
                raise ValueError("Dự án chưa bật bảng sản xuất.")
            scenes = [dict(r) for r in db.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY ordinal", (project_id,))]
            tasks = [dict(r) for r in db.execute("SELECT * FROM production_tasks WHERE project_id=? ORDER BY created,rowid", (project_id,))]
            invalidated = {r['task_id']: r['since'] for r in db.execute("SELECT task_id,max(created) AS since FROM production_events WHERE project_id=? AND action='invalidated' GROUP BY task_id", (project_id,))}
            jobs = [dict(r) for r in db.execute("""SELECT j.id,j.scene_id,j.status,j.cost,j.output,j.error,j.created,j.batch,j.submitted
                FROM jobs j JOIN scenes s ON s.id=j.scene_id WHERE s.project_id=? ORDER BY j.created""", (project_id,))]
            events = [dict(r) for r in db.execute("SELECT * FROM production_events WHERE project_id=? ORDER BY id DESC LIMIT 100", (project_id,))]
            characters = [dict(r) for r in db.execute("SELECT id,code,name,description FROM characters WHERE project_id=? ORDER BY created", (project_id,))]
            links = db.execute("SELECT sc.scene_id,sc.character_id FROM scene_characters sc JOIN scenes s ON s.id=sc.scene_id WHERE s.project_id=?", (project_id,)).fetchall()
        order = {s: i for i, s in enumerate(STAGE_IDS)}
        ordinal = {s['id']: s['ordinal'] for s in scenes}
        tasks.sort(key=lambda t: (order.get(t['stage'], 99), ordinal.get(t['scene_id'], -1)))
        for scene in scenes:
            scene['code'] = scene_code(scene['ordinal'])
            scene['characters'] = [l['character_id'] for l in links if l['scene_id'] == scene['id']]
        for task in tasks:
            task['take_after'] = invalidated.get(task['id'], 0)
            deps = self.dependencies(task, tasks)
            task['blocked_by'] = [t['id'] for t in deps if t['status'] != 'done']
            # Công đoạn gộp (dựng, thử cảnh khó) cần danh sách cảnh: chưa có cảnh thì chưa mở.
            needs_scenes = task['stage'] in ('pilot', 'edit') and not scenes
            task['ready'] = (project['production_status'] == 'active' and task['status'] == 'waiting'
                             and not task['blocked_by'] and not needs_scenes)
        done = sum(t['status'] == 'done' for t in tasks)
        planned = sum(float(s.get('duration') or 0) for s in scenes)
        return dict(project=dict(project), scenes=scenes, tasks=tasks, jobs=jobs, events=events, characters=characters,
                    stages=[dict(id=s, name=n, per_scene=p) for s, n, p in STAGES],
                    summary=dict(done=done, total=len(tasks), errors=sum(t['status'] == 'error' for t in tasks),
                                 attempts=sum(t['attempts'] for t in tasks),
                                 progress=round(100 * done / len(tasks)) if tasks else 0,
                                 planned_seconds=round(planned, 1), target_seconds=project['target_seconds']),
                    execution_mode='manual', audio_policy='natural_only', schema_version=2)

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
                if action == 'complete' and task['stage'] == 'script' and not db.execute(
                        "SELECT 1 FROM scenes WHERE project_id=?", (task['project_id'],)).fetchone():
                    raise ValueError('Kịch bản tạo danh sách cảnh: giao agent kịch bản rồi duyệt kết quả.')
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
