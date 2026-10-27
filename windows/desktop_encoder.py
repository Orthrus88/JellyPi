#!/usr/bin/env python3
import argparse
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'OrthrusMediaEncoder'
SHARE = Path(os.environ.get('JELLYPI_MEDIA_SHARE', r'\\jellypi\Media'))
ROOTS = [SHARE / 'Movies', SHARE / 'TV']
STATE_DIR = SHARE / '.media-encoder'
STATUS = STATE_DIR / 'desktop-status.json'
HEARTBEAT = STATE_DIR / 'desktop-heartbeat.json'
RECOVERY_STATUS = Path(
    os.environ.get('JELLYPI_RECOVERY_STATUS', str(SHARE / 'recovery' / 'status.json'))
)
# Recovered files are published with an atomic rename and still have to pass
# MIN_AGE before this worker considers them.  It is therefore safe for the
# desktop worker to process completed files while the one-time drive recovery
# continues in the background.
PAUSE_DURING_RECOVERY = os.environ.get('JELLYPI_PAUSE_DURING_RECOVERY', 'false').lower() in {
    '1', 'true', 'yes'
}
LOCAL_LOCK = APP_DIR / 'desktop-encoder.lock'
PRIORITY_FILE = APP_DIR / 'priority.txt'
ERROR_LOG = APP_DIR / 'encoder-errors.jsonl'
FAILURE_CACHE = APP_DIR / 'failed-files.json'
FFMPEG = Path(os.environ.get('JELLYPI_FFMPEG') or shutil.which('ffmpeg') or 'ffmpeg.exe')
FFPROBE = FFMPEG.with_name('ffprobe.exe')
EXTENSIONS = {'.mkv', '.mp4', '.m4v', '.avi', '.mov', '.ts', '.m2ts', '.webm'}
MIN_AGE = int(os.environ.get('JELLYPI_MIN_AGE_SECONDS', '900'))
MAX_VIDEO_BITRATE = int(os.environ.get('JELLYPI_MAX_VIDEO_BITRATE', '6000000'))
ENCODER_PROFILE = os.environ.get('JELLYPI_ENCODER_PROFILE', 'jellypi-remote-h264-v1')
WORKER_LABEL = os.environ.get('JELLYPI_WORKER_LABEL', f'{socket.gethostname()} NVIDIA GPU')
stop = False


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, payload):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
        temp.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        os.replace(temp, path)
    except OSError:
        pass


def load_failures():
    try:
        data = json.loads(FAILURE_CACHE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def file_signature(path):
    stat = path.stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def failure_is_current(path, failures):
    entry = failures.get(str(path).casefold())
    if not isinstance(entry, dict):
        return False
    try:
        return entry.get('signature') == file_signature(path)
    except OSError:
        return False


def record_failure(path, exc, failures):
    message = str(exc)
    try:
        signature = file_signature(path)
    except OSError:
        signature = None
    entry = {
        'file': str(path),
        'failedAt': now(),
        'signature': signature,
        'error': message,
    }
    failures[str(path).casefold()] = entry
    atomic_json(FAILURE_CACHE, failures)
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError:
        pass


def write_status(**changes):
    try:
        current = json.loads(STATUS.read_text(encoding='utf-8'))
    except Exception:
        current = {}
    current.update({'worker': WORKER_LABEL, 'updated': now()}, **changes)
    atomic_json(STATUS, current)


def recovery_active():
    try:
        return json.loads(RECOVERY_STATUS.read_text(encoding='utf-8')).get('state') == 'recovering'
    except Exception:
        return False


def heartbeat_loop():
    while not stop:
        atomic_json(HEARTBEAT, {'active': True, 'host': socket.gethostname(), 'updated': now()})
        for _ in range(5):
            if stop:
                return
            time.sleep(1)


def probe(path):
    p = subprocess.run([
        str(FFPROBE), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)
    ], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=240,
       creationflags=subprocess.CREATE_NO_WINDOW)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or 'ffprobe failed')
    return json.loads(p.stdout)


def duration(info):
    try:
        return float(info.get('format', {}).get('duration') or 0)
    except (TypeError, ValueError):
        return 0.0


def plan(info):
    videos = [s for s in info.get('streams', []) if s.get('codec_type') == 'video']
    audios = [s for s in info.get('streams', []) if s.get('codec_type') == 'audio']
    if not videos:
        return None
    v = videos[0]
    tags = info.get('format', {}).get('tags') or {}
    profile = str(tags.get('MEDIA_ENCODER_PROFILE') or tags.get('media_encoder_profile') or '')
    try:
        bitrate = int(v.get('bit_rate') or info.get('format', {}).get('bit_rate') or 0)
    except (TypeError, ValueError):
        bitrate = 0
    tone_map = (v.get('color_transfer') or '').lower() in {'smpte2084', 'arib-std-b67'}
    video_ok = (
        v.get('codec_name') == 'h264'
        and v.get('pix_fmt') in {'yuv420p', 'yuvj420p'}
        and int(v.get('width') or 0) <= 1920
        and int(v.get('height') or 0) <= 1080
        and not tone_map
        and (profile == ENCODER_PROFILE or not bitrate or bitrate <= MAX_VIDEO_BITRATE)
    )
    stereo_aac = any(a.get('codec_name') == 'aac' and int(a.get('channels') or 0) <= 2 for a in audios)
    if video_ok and stereo_aac:
        return None
    return {
        'video_encode': not video_ok,
        'tone_map': tone_map,
        'add_aac': not stereo_aac and bool(audios),
        'audio_count': len(audios),
    }


def parse_clock(value):
    try:
        h, m, s = value.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return 0.0


def validate_samples(path, total):
    length = min(120.0, total) if total else 120.0
    starts = [0.0]
    if total > length:
        starts += [max(0.0, total / 2 - length / 2), max(0.0, total - length)]
    starts = list(dict.fromkeys(round(x, 3) for x in starts))
    labels = ['beginning', 'middle', 'end'] if len(starts) == 3 else [f'sample {i + 1}' for i in range(len(starts))]
    for index, (label, start_at) in enumerate(zip(labels, starts), 1):
        write_status(state='validating', progress=100, validationSample=label,
                     validationStep=f'{index}/{len(starts)}')
        check = subprocess.run([
            str(FFMPEG), '-v', 'error', '-xerror', '-ss', str(start_at), '-i', str(path),
            '-t', str(length), '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
           encoding='utf-8', errors='replace',
           creationflags=subprocess.CREATE_NO_WINDOW)
        if check.returncode:
            raise RuntimeError(f'{label} sample validation failed: {check.stderr[-500:].strip()}')


def encode(src, info, job):
    total = duration(info)
    temp = src.with_name('.' + src.stem + '.encoder-part' + src.suffix.lower())
    temp.unlink(missing_ok=True)
    cmd = [str(FFMPEG), '-hide_banner', '-y', '-i', str(src), '-map', '0:v:0', '-map', '0:a?']
    if job['add_aac']:
        cmd += ['-map', '0:a:0']
    cmd += ['-map', '0:s?', '-map_metadata', '0', '-map_chapters', '0']
    if job['video_encode']:
        cmd += [
            '-c:v', 'h264_nvenc', '-preset', 'p5', '-tune', 'hq', '-rc', 'vbr',
            '-cq', '21', '-b:v', '0', '-maxrate', str(MAX_VIDEO_BITRATE),
            '-bufsize', str(MAX_VIDEO_BITRATE * 2),
            '-spatial-aq', '1', '-temporal-aq', '1', '-pix_fmt', 'yuv420p',
        ]
        scale = "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
        filters = [scale]
        if job.get('tone_map'):
            filters = [
                'zscale=transfer=linear:npl=100',
                'format=gbrpf32le',
                'tonemap=tonemap=hable:desat=0',
                'zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=tv',
                'format=yuv420p',
                scale,
            ]
        cmd += [
            '-vf', ','.join(filters),
            '-color_primaries', 'bt709', '-color_trc', 'bt709',
            '-colorspace', 'bt709', '-color_range', 'tv',
            '-metadata', f'MEDIA_ENCODER_PROFILE={ENCODER_PROFILE}'
        ]
    else:
        cmd += ['-c:v', 'copy']
    cmd += ['-c:a', 'copy']
    if job['add_aac']:
        n = job['audio_count']
        cmd += [
            f'-c:a:{n}', 'aac', f'-ac:a:{n}', '2', f'-b:a:{n}', '192k',
            f'-metadata:s:a:{n}', 'title=AAC Stereo Compatibility',
            f'-disposition:a:{n}', 'default'
        ]
        for i in range(n):
            cmd += [f'-disposition:a:{i}', '0']
    if src.suffix.lower() in {'.mp4', '.m4v', '.mov'}:
        cmd += ['-movflags', '+faststart+use_metadata_tags']
    cmd += ['-c:s', 'copy', '-max_muxing_queue_size', '4096', '-progress', 'pipe:1', '-nostats', str(temp)]

    started = now()
    write_status(state='encoding', file=str(src), progress=0, started=started, error=None,
                 decision=('GPU encode video' if job['video_encode'] else 'copy video') +
                          (' + AAC stereo' if job['add_aac'] else ''))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding='utf-8', errors='replace',
                            creationflags=subprocess.CREATE_NO_WINDOW)
    last = 0
    stderr_lines = []

    def collect_stderr():
        for line in proc.stderr:
            stderr_lines.append(line)
            if len(stderr_lines) > 100:
                del stderr_lines[:50]

    threading.Thread(target=collect_stderr, daemon=True).start()
    for line in proc.stdout:
        if stop:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            temp.unlink(missing_ok=True)
            raise RuntimeError('stopped')
        key, _, value = line.strip().partition('=')
        if key == 'out_time' and total and time.time() - last > 2:
            write_status(state='encoding', file=str(src),
                         progress=min(99, round(parse_clock(value) * 100 / total, 1)), started=started)
            last = time.time()
    if proc.wait() != 0:
        temp.unlink(missing_ok=True)
        raise RuntimeError('ffmpeg encoding failed: ' + ''.join(stderr_lines)[-1000:].strip())

    write_status(state='validating', file=str(src), progress=100, started=started)
    try:
        out_info = probe(temp)
        out_plan = plan(out_info)
        out_duration = duration(out_info)
        if out_plan and out_plan.get('video_encode'):
            video = next(
                (stream for stream in out_info.get('streams', [])
                 if stream.get('codec_type') == 'video'),
                {},
            )
            raise RuntimeError(
                'output video is not compatible: '
                f"codec={video.get('codec_name')} pix_fmt={video.get('pix_fmt')} "
                f"transfer={video.get('color_transfer')} primaries={video.get('color_primaries')}"
            )
        if job['add_aac'] and out_plan and out_plan.get('add_aac'):
            raise RuntimeError('AAC stereo fallback is missing')
        if total and abs(out_duration - total) > max(2.0, total * 0.01):
            raise RuntimeError(f'duration mismatch ({total:.1f}s vs {out_duration:.1f}s)')
        validate_samples(temp, out_duration)
        os.replace(temp, src)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    write_status(state='completed', file=None, progress=100, started=None,
                 lastCompleted=str(src), lastCompletedAt=now(), error=None,
                 validationSample=None, validationStep=None)


def candidates(failures):
    files = []
    cutoff = time.time() - MIN_AGE
    try:
        priority = PRIORITY_FILE.read_text(encoding='utf-8').strip().casefold()
    except OSError:
        priority = ''
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob('*'):
            try:
                stat = path.stat()
                is_priority = bool(priority and str(path).casefold() == priority)
                if (path.is_file() and path.suffix.lower() in EXTENSIONS
                        and not path.name.startswith('.') and (stat.st_mtime < cutoff or is_priority)
                        and not path.with_name('.' + path.stem + '.encoder-part' + path.suffix.lower()).exists()
                        and not failure_is_current(path, failures)):
                    files.append((0 if is_priority else 1, stat.st_mtime, path))
            except OSError:
                pass
    # Keep the timestamp captured while the file existed. Another worker may
    # replace a file between discovery and sorting; re-statting it here used
    # to crash the entire desktop worker.
    return [path for _, _, path in sorted(files, key=lambda item: item[:2])]


def run_once():
    failures = load_failures()
    files = candidates(failures)
    write_status(state='scanning', file=None, progress=0, queue=len(files), error=None)
    for index, src in enumerate(files):
        if stop:
            return False
        try:
            info = probe(src)
            job = plan(info)
            if job is None:
                continue
            write_status(queue=len(files) - index)
            encode(src, info, job)
            try:
                if (PRIORITY_FILE.read_text(encoding='utf-8').strip().casefold()
                        == str(src).casefold()):
                    PRIORITY_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            return True
        except Exception as exc:
            record_failure(src, exc, failures)
            write_status(state='error', file=str(src), progress=0, error=str(exc), failedAt=now())
            time.sleep(5)
    write_status(state='idle', file=None, progress=0, queue=0, error=None)
    return False


def acquire_lock():
    import msvcrt
    APP_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(LOCAL_LOCK, 'a+b')
    if handle.tell() == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def main():
    global stop
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--inspect')
    args = parser.parse_args()
    if args.inspect:
        info = probe(Path(args.inspect))
        print(json.dumps({'duration': duration(info), 'plan': plan(info)}, indent=2))
        return
    if not FFMPEG.exists() or not FFPROBE.exists():
        raise SystemExit('FFmpeg installation is missing')
    lock = acquire_lock()
    if lock is None:
        return

    def halt(*_):
        global stop
        stop = True

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    write_status(state='starting', file=None, progress=0, error=None)
    try:
        while not stop:
            if PAUSE_DURING_RECOVERY and recovery_active():
                write_status(state='paused', file=None, progress=0, queue=0, error=None,
                             decision='5 TB recovery is active; library scan paused')
                for _ in range(30):
                    if stop:
                        break
                    time.sleep(1)
                continue
            try:
                run_once()
            except Exception as exc:
                # The worker is supervised, but a transient share race should
                # not make it disappear until the next watchdog run.
                write_status(state='error', file=None, progress=0, error=str(exc), failedAt=now())
                time.sleep(10)
            if args.once:
                break
            for _ in range(30):
                if stop:
                    break
                time.sleep(1)
    finally:
        try:
            HEARTBEAT.unlink(missing_ok=True)
        except OSError:
            pass
        write_status(state='stopped', file=None, progress=0)
        lock.close()


if __name__ == '__main__':
    main()
