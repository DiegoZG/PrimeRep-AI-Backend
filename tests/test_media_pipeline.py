import json
import shutil
import subprocess

import pytest

from scripts import media_pipeline as media


def test_probe_rejects_landscape_and_handles_phone_rotation(tmp_path, monkeypatch):
    path = tmp_path / "clip.mov"
    path.write_bytes(b"source")
    monkeypatch.setattr(media, "executable", lambda name: name)
    payload = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080, "codec_name": "h264", "pix_fmt": "yuv420p"}], "format": {"duration": "8"}}
    monkeypatch.setattr(media, "run_command", lambda args: json.dumps(payload))
    with pytest.raises(ValueError, match="9:16"):
        media.probe_video(path)
    payload["streams"][0]["side_data_list"] = [{"rotation": 90}]
    assert media.probe_video(path)["width"] == 1080
    payload["format"]["duration"] = "300"
    with pytest.raises(ValueError, match="120 seconds"):
        media.probe_video(path)


def test_missing_binaries_are_actionable(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="install FFmpeg"):
        media.executable("ffprobe")


def test_processing_errors_do_not_echo_untrusted_decoder_output(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "ffmpeg", stderr="untrusted input secret")
    monkeypatch.setattr(media.subprocess, "run", fail)
    with pytest.raises(ValueError) as error:
        media.run_command(["ffmpeg"])
    assert "secret" not in str(error.value)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools unavailable")
def test_real_synthetic_portrait_transcode_and_poster(tmp_path):
    source = tmp_path / "synthetic.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=purple:s=360x640:r=30:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
    first = media.prepare_video(source, tmp_path / "first")
    second = media.prepare_video(source, tmp_path / "second")
    assert first["video_hash"] == second["video_hash"]
    assert first["poster_hash"] == second["poster_hash"]
    assert first["technical"]["derivative"]["width"] == 720
    assert first["technical"]["derivative"]["height"] == 1280
    assert first["poster"].stat().st_size > 0
