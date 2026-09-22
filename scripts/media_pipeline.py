"""Local technical preparation; human approval is a separate catalog operation."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

PROFILE = "primerep-portrait-720p-h264-v1"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def executable(name: str) -> str:
    result = shutil.which(name)
    if not result:
        raise ValueError(f"{name} is required; install FFmpeg before preparing footage")
    return result


def run_command(arguments: list[str]) -> str:
    try:
        result = subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Media processing exceeded five minutes") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"Media processing failed ({Path(arguments[0]).name}); inspect the source locally") from exc
    return result.stdout


def probe_video(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Source video is missing or empty")
    raw = json.loads(run_command([executable("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))
    videos = [s for s in raw.get("streams", []) if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    if len(videos) != 1:
        raise ValueError("Exactly one video stream is required")
    stream = videos[0]
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    rotation = int(stream.get("tags", {}).get("rotate", 0))
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            rotation = int(side["rotation"])
    if abs(rotation) % 180 == 90:
        width, height = height, width
    duration = float(stream.get("duration") or raw.get("format", {}).get("duration") or 0)
    if not (1 <= duration <= 120):
        raise ValueError("Demonstrations must be between 1 and 120 seconds")
    if min(width, height) <= 0 or abs(width / height - 9 / 16) > 0.025:
        raise ValueError("A portrait 9:16 master is required; reframe without cropping the movement")
    return {"width": width, "height": height, "duration_seconds": round(duration, 3),
            "codec": stream.get("codec_name"), "pixel_format": stream.get("pix_fmt"), "rotation": rotation}


def prepare_video(source: Path, output_directory: Path) -> dict:
    source_info = probe_video(source)
    encoder = executable("ffmpeg")
    source_hash = file_sha256(source)
    folder = output_directory / source_hash
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "demo.mp4"
    poster = folder / "poster.jpg"
    run_command([encoder, "-nostdin", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0", "-an",
                 "-vf", "scale=720:1280:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
                 "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-preset", "medium",
                 "-movflags", "+faststart", str(video)])
    derivative = probe_video(video)
    if derivative["codec"] != "h264" or derivative["pixel_format"] != "yuv420p" or (derivative["width"], derivative["height"]) != (720, 1280):
        raise ValueError("Encoded derivative did not pass H.264/720p validation")
    run_command([encoder, "-nostdin", "-v", "error", "-y", "-ss", "0.5", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(poster)])
    if not poster.is_file() or poster.stat().st_size == 0:
        raise ValueError("Poster generation failed")
    return {"video": video, "poster": poster, "video_hash": file_sha256(video), "poster_hash": file_sha256(poster),
            "source_hash": source_hash, "technical": {"source": source_info, "derivative": derivative, "profile": PROFILE}}
