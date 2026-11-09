"""Local evidence extraction and validated, non-destructive timeline rendering."""
import math
import hashlib
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from . import video_tools as video


class Stopped(ValueError):
    pass


def run(args, timeout, stop=None):
    """FFmpeg có thể dừng giữa chừng: bấm Dừng agent / đóng app thì tắt hẳn tiến trình, không để chạy ngầm."""
    if stop is None:
        return video.run(args, timeout)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors,
                                   creationflags=flags)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if stop.is_set():
                    raise Stopped('Đã dừng xử lý video theo yêu cầu.')
                if time.monotonic() > deadline:
                    raise video.VideoError('Xử lý video quá lâu, đã dừng.')
                stop.wait(.2)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        errors.seek(0)
        return SimpleNamespace(returncode=process.returncode,
                               stderr=errors.read()[-20000:].decode('utf-8', 'replace'), stdout='')


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} phải là số hữu hạn.')
    return float(value)


def inspect_clip(path, folder, stop=None):
    path, folder = Path(path), Path(folder)
    if not path.is_file():
        raise ValueError('Không tìm thấy file video của take đã chọn.')
    info = video.probe(path)
    if info['duration'] <= 0 or info['width'] < 16 or info['height'] < 16:
        raise ValueError('Video thiếu thời lượng hoặc kích thước hợp lệ.')
    # Decode the entire stream. Metadata alone cannot establish that a clip is playable.
    result = run([video.ffmpeg(), '-v', 'error', '-xerror', '-i', str(path),
                        '-map', '0:v:0', '-f', 'null', '-'], 600, stop)
    if result.returncode:
        raise ValueError('Video lỗi giải mã; cần kiểm tra hoặc tải lại take.')
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, fraction in enumerate((0.1, 0.35, 0.65, 0.9)):
        timestamp = round(info['duration'] * fraction, 3)
        target = folder / f'frame-{index + 1}.jpg'
        result = run([video.ffmpeg(), '-v', 'error', '-y', '-ss', str(timestamp), '-i', str(path),
                            '-frames:v', '1', '-vf', 'scale=640:640:force_original_aspect_ratio=decrease', str(target)], 60, stop)
        if result.returncode or not target.is_file() or target.stat().st_size == 0:
            raise ValueError('Không trích được khung hình để QC.')
        frames.append({'time': timestamp, 'file': str(target)})
    with path.open('rb') as f:
        digest=hashlib.file_digest(f,'sha256').hexdigest()
    return dict(info=info, frames=frames, decode_ok=True, audio_review_required=info['audio'], source_sha256=digest)


def sample_source(path, folder, stop=None, limit=40):
    """Khung hình rải đều trên video gốc (kèm mốc giây) để AI phân tích diễn biến, loài, địa hình, góc máy."""
    path, folder = Path(path), Path(folder)
    info = video.probe(path)
    if info['duration'] <= 0:
        raise ValueError('Video gốc thiếu thời lượng hợp lệ.')
    count = max(8, min(limit, int(info['duration'] / 3)))
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    for index in range(count):
        timestamp = round(info['duration'] * (index + .5) / count, 2)
        target = folder / f'src-{index + 1:03d}.jpg'
        result = run([video.ffmpeg(), '-v', 'error', '-y', '-ss', str(timestamp), '-i', str(path), '-frames:v', '1',
                      '-vf', 'scale=512:512:force_original_aspect_ratio=decrease', '-q:v', '4', str(target)], 60, stop)
        if result.returncode or not target.is_file() or target.stat().st_size == 0:
            continue
        frames.append({'time': timestamp, 'file': str(target)})
    if len(frames) < 4:
        raise ValueError('Không trích được đủ khung hình từ video gốc.')
    return dict(info=info, frames=frames)


def validate_timeline(plan, takes):
    """Resolve model-proposed IDs through trusted takes; never execute model paths or filters."""
    if not isinstance(plan, dict) or not isinstance(plan.get('clips'), list) or not plan['clips']:
        raise ValueError('Timeline cần danh sách clips.')
    if len(plan['clips']) != len(takes):
        raise ValueError('Timeline phải có đúng một đoạn cho mỗi cảnh đã duyệt.')
    clips, seen = [], set()
    for proposed, take in zip(plan['clips'], takes):
        if not isinstance(proposed, dict) or proposed.get('scene_id') != take['scene_id']:
            raise ValueError('Timeline sai thứ tự hoặc Scene ID.')
        if proposed.get('job_id') != take['job_id'] or take['scene_id'] in seen:
            raise ValueError('Timeline sai take hoặc trùng cảnh.')
        start = finite(proposed.get('in'), 'Điểm vào')
        end = finite(proposed.get('out'), 'Điểm ra')
        speed = finite(proposed.get('speed', 1), 'Tốc độ')
        if start < 0 or end <= start or end > take['duration'] + .001 or not .5 <= speed <= 2:
            raise ValueError('Điểm cắt vượt video hoặc tốc độ ngoài 0.5–2.0.')
        clips.append(dict(scene_id=take['scene_id'], job_id=take['job_id'], path=take['path'],
                          start=start, end=end, speed=speed))
        seen.add(take['scene_id'])
    return clips


def validate_sound(spec, duration, library):
    """Kế hoạch âm thanh của Sound Agent cho một cảnh: chỉ nhận âm thanh có trong thư viện, mốc trong cảnh."""
    if not isinstance(spec, dict):
        raise ValueError('Kế hoạch âm thanh không hợp lệ.')
    source = spec.get('source_audio', 'keep')
    if source not in ('keep', 'mute'):
        raise ValueError('source_audio phải là keep hoặc mute.')
    volume = finite(spec.get('source_volume', 1), 'Âm lượng nguồn')
    layers = spec.get('layers', [])
    if not isinstance(layers, list) or len(layers) > 6:
        raise ValueError('Tối đa 6 lớp âm thanh mỗi cảnh.')
    if not 0 <= volume <= 1.5:
        raise ValueError('Âm lượng từ 0 đến 1.5.')
    clean = []
    for layer in layers:
        if not isinstance(layer, dict) or layer.get('sound_id') not in library:
            raise ValueError('Âm thanh không có trong thư viện tự nhiên.')
        at = finite(layer.get('at', 0), 'Thời điểm')
        length = finite(layer.get('length', duration - at), 'Độ dài')
        level = finite(layer.get('volume', .6), 'Âm lượng')
        if not 0 <= at < duration or length <= 0 or not 0 <= level <= 1.5:
            raise ValueError('Lớp âm thanh nằm ngoài cảnh hoặc âm lượng ngoài 0–1.5.')
        clean.append(dict(sound_id=layer['sound_id'], path=library[layer['sound_id']], at=at,
                          length=min(length, duration - at), volume=level, purpose=str(layer.get('purpose', ''))[:200]))
    return dict(source_audio=source, source_volume=volume, layers=clean)


def render_timeline(plan, takes, destination, stop=None, sounds=None):
    """sounds: {scene_id: kế hoạch âm thanh đã validate_sound}. Không có thì giữ âm thanh gốc của take."""
    clips = validate_timeline(plan, takes)
    sounds = sounds or {}
    for take in takes:
        if take.get('sha256'):
            with Path(take['path']).open('rb') as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=take['sha256']:
                    raise ValueError('Take đã thay đổi sau khi lập timeline. Cần phân tích lại.')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = (takes[0]['width'] // 2 * 2, takes[0]['height'] // 2 * 2)
    inputs = [['-i', clip['path']] for clip in clips]  # chỉ số input i = đoạn thứ i

    def add_input(arguments):
        inputs.append(arguments)
        return len(inputs) - 1
    filters, outputs = [], []
    for index, clip in enumerate(clips):
        filters.append(f"[{index}:v]trim=start={clip['start']}:end={clip['end']},"
                       f"setpts=(PTS-STARTPTS)/{clip['speed']},{video.fit(size)},fps=30,format=yuv420p[v{index}]")
        duration = (clip['end'] - clip['start']) / clip['speed']
        spec = sounds.get(clip['scene_id']) or {}
        parts = [f"[{add_input(['-f', 'lavfi', '-t', str(duration), '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000'])}:a]"]
        if takes[index]['audio'] and spec.get('source_audio', 'keep') == 'keep':
            filters.append(f"[{index}:a]atrim=start={clip['start']}:end={clip['end']},asetpts=PTS-STARTPTS,"
                           f"atempo={clip['speed']},aresample=48000,aformat=channel_layouts=stereo,"
                           f"volume={spec.get('source_volume', 1)},apad,atrim=duration={duration}[s{index}]")
            parts.append(f'[s{index}]')
        for number, layer in enumerate(spec.get('layers', [])):
            length = min(layer['length'], duration - layer['at'])
            fade = min(.5, length / 4)
            source = add_input(['-stream_loop', '-1', '-i', str(layer['path'])])
            filters.append(f"[{source}:a]atrim=duration={length},asetpts=PTS-STARTPTS,aresample=48000,"
                           f"aformat=channel_layouts=stereo,volume={layer['volume']},afade=t=in:d={fade},"
                           f"afade=t=out:st={max(0, length - fade)}:d={fade},adelay={int(layer['at'] * 1000)}:all=1[l{index}x{number}]")
            parts.append(f'[l{index}x{number}]')
        filters.append(''.join(parts) + f"amix=inputs={len(parts)}:duration=longest:normalize=0,"
                       f"atrim=duration={duration},asetpts=PTS-STARTPTS[a{index}]")
        outputs.append(f'[v{index}][a{index}]')
    args = [video.ffmpeg(), '-hide_banner', '-y'] + [part for item in inputs for part in item]
    filters.append(''.join(outputs) + f'concat=n={len(clips)}:v=1:a=1[v][a]')
    temporary = destination.with_name(destination.stem + '.part.mp4')
    try:
        result = run(args + ['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters),
                                  '-map', '[v]', '-map', '[a]', '-c:a', 'aac', '-c:v', 'libx264', '-threads', '2', '-preset', 'medium',
                                  '-crf', '18', '-movflags', '+faststart', str(temporary)], 7200, stop)
        if result.returncode or not temporary.is_file():
            raise ValueError('FFmpeg không render được timeline.')
        info = video.probe(temporary)
        expected = sum((c['end'] - c['start']) / c['speed'] for c in clips)
        if abs(info['duration'] - expected) > max(.25, len(clips) / 30 + .1):
            raise ValueError('Thời lượng bản dựng không khớp timeline.')
        video.finish(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    with destination.open('rb') as f:
        digest=hashlib.file_digest(f,'sha256').hexdigest()
    return dict(path=str(destination), sha256=digest, info=info, timeline={'clips':[dict(c) for c in plan['clips']]}, expected_duration=expected,
                audio_policy='natural_layers_unverified' if sounds else 'source_audio_unverified', requires_audio_review=True)
