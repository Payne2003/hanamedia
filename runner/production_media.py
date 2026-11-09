"""Local evidence extraction and validated, non-destructive timeline rendering."""
import math
import hashlib
from pathlib import Path

from . import video_tools as video


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} phải là số hữu hạn.')
    return float(value)


def inspect_clip(path, folder):
    path, folder = Path(path), Path(folder)
    if not path.is_file():
        raise ValueError('Không tìm thấy file video của take đã chọn.')
    info = video.probe(path)
    if info['duration'] <= 0 or info['width'] < 16 or info['height'] < 16:
        raise ValueError('Video thiếu thời lượng hoặc kích thước hợp lệ.')
    # Decode the entire stream. Metadata alone cannot establish that a clip is playable.
    result = video.run([video.ffmpeg(), '-v', 'error', '-xerror', '-i', str(path),
                        '-map', '0:v:0', '-f', 'null', '-'], 600)
    if result.returncode:
        raise ValueError('Video lỗi giải mã; cần kiểm tra hoặc tải lại take.')
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, fraction in enumerate((0.1, 0.35, 0.65, 0.9)):
        timestamp = round(info['duration'] * fraction, 3)
        target = folder / f'frame-{index + 1}.jpg'
        result = video.run([video.ffmpeg(), '-v', 'error', '-y', '-ss', str(timestamp), '-i', str(path),
                            '-frames:v', '1', '-vf', 'scale=640:640:force_original_aspect_ratio=decrease', str(target)], 60)
        if result.returncode or not target.is_file() or target.stat().st_size == 0:
            raise ValueError('Không trích được khung hình để QC.')
        frames.append({'time': timestamp, 'file': str(target)})
    with path.open('rb') as f:
        digest=hashlib.file_digest(f,'sha256').hexdigest()
    return dict(info=info, frames=frames, decode_ok=True, audio_review_required=info['audio'], source_sha256=digest)


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


def render_timeline(plan, takes, destination):
    clips = validate_timeline(plan, takes)
    for take in takes:
        if take.get('sha256'):
            with Path(take['path']).open('rb') as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=take['sha256']:
                    raise ValueError('Take đã thay đổi sau khi lập timeline. Cần phân tích lại.')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = (takes[0]['width'] // 2 * 2, takes[0]['height'] // 2 * 2)
    filters, outputs = [], []
    silence_index = len(clips)
    for index, clip in enumerate(clips):
        filters.append(f"[{index}:v]trim=start={clip['start']}:end={clip['end']},"
                       f"setpts=(PTS-STARTPTS)/{clip['speed']},{video.fit(size)},fps=30,format=yuv420p[v{index}]")
        duration = (clip['end'] - clip['start']) / clip['speed']
        if takes[index]['audio']:
            filters.append(f"[{index}:a]atrim=start={clip['start']}:end={clip['end']},asetpts=PTS-STARTPTS,"
                           f"atempo={clip['speed']},aresample=48000,aformat=channel_layouts=stereo,"
                           f"apad,atrim=duration={duration}[a{index}]")
        else:
            # Append silence inputs after all original inputs (added below).
            filters.append(f"[{silence_index}:a]anull[a{index}]")
            silence_index += 1
        outputs.append(f'[v{index}][a{index}]')
    # Keep input numbering stable: originals first, generated silence second.
    original_args = [video.ffmpeg(), '-hide_banner', '-y']
    for clip in clips:
        original_args.extend(['-i', clip['path']])
    for clip, take in zip(clips, takes):
        if not take['audio']:
            duration = (clip['end'] - clip['start']) / clip['speed']
            original_args.extend(['-f','lavfi','-t',str(duration),'-i','anullsrc=channel_layout=stereo:sample_rate=48000'])
    args = original_args
    filters.append(''.join(outputs) + f'concat=n={len(clips)}:v=1:a=1[v][a]')
    temporary = destination.with_name(destination.stem + '.part.mp4')
    try:
        result = video.run(args + ['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters),
                                  '-map', '[v]', '-map', '[a]', '-c:a', 'aac', '-c:v', 'libx264', '-threads', '2', '-preset', 'medium',
                                  '-crf', '18', '-movflags', '+faststart', str(temporary)], 7200)
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
                audio_policy='source_audio_unverified', requires_audio_review=True)
