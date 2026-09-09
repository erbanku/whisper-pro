from io import BytesIO
from pathlib import Path
import subprocess
import wave

import httpx
import imageio_ffmpeg
import pytest
import yaml
from dify_plugin.entities.tool import ToolProviderConfiguration
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from dify_plugin.file.file import File

import audio
from audio import DEFAULT_ENDPOINT, DEFAULT_MODEL, Media, TranscriptionError, download_media, number_setting, prepare_audio, structured_result
from provider.whisper_pro import WhisperProProvider
from tools.transcribe import TranscribeTool


@pytest.fixture
def wav_bytes():
    buffer = BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b"\0\0" * 4000)
    return buffer.getvalue()


@pytest.fixture
def native_file(wav_bytes):
    return File(url="https://files.example.test/audio.wav?signature=secret", type="audio", filename="audio.wav", mime_type="audio/wav", size=len(wav_bytes))


def mock_network(monkeypatch, content, status=200, payload=None, plain_text=None, error=None, download_status=200, download_headers=None):
    requests = []
    original_client = httpx.Client
    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(download_status, content=content, headers=download_headers or {"content-type": "audio/wav"})
        if error:
            raise error
        if plain_text is not None:
            return httpx.Response(status, text=plain_text)
        return httpx.Response(status, json=payload if payload is not None else {"text": "Meeting transcript"})
    monkeypatch.setattr(audio.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    return requests


def invoke(parameters, credentials=None):
    return list(TranscribeTool.from_credentials(credentials or {})._invoke(parameters))


def test_default_native_upload_matches_curl(monkeypatch, wav_bytes, native_file):
    requests = mock_network(monkeypatch, wav_bytes)
    messages = invoke({"file": native_file})
    assert len(requests) == 2
    post = requests[-1]
    assert str(post.url) == DEFAULT_ENDPOINT
    assert b'name="model"\r\n\r\nwhisper-large-v3-turbo' in post.content
    assert b'name="response_format"\r\n\r\njson' in post.content
    assert b'name="temperature"\r\n\r\n0.5' in post.content
    assert b'name="file"; filename="audio.wav"' in post.content
    assert wav_bytes in post.content
    assert "authorization" not in post.headers
    result = messages[0].message.json_object
    assert result["text"] == "Meeting transcript"
    assert result["audio"]["converted"] is False
    assert result["language"] is None and result["duration"] is None
    assert result["segments"] == [] and result["words"] == []
    assert messages[-1].message.text == "Meeting transcript"
    assert messages[1].message.variable_name == "transcript"
    assert "signature" not in str(messages)


def test_url_input_and_custom_parameters(monkeypatch, wav_bytes):
    requests = mock_network(monkeypatch, wav_bytes, payload={"text": "Ciao", "language": "it", "duration": 0.25, "segments": [{"start": 0, "end": 0.25, "text": "Ciao"}], "words": [{"word": "Ciao", "start": 0, "end": 0.25}]})
    messages = invoke({"media_url": "https://files.example.test/download?token=secret", "endpoint": "https://stt.example.test/custom", "model": "my-model", "temperature": 0, "language": "it", "prompt": "Rotech", "response_format": "verbose_json"}, {"api_key": "test_key"})
    post = requests[-1]
    assert str(post.url) == "https://stt.example.test/custom"
    assert post.headers["Authorization"] == "Bearer test_key"
    assert "authorization" not in requests[0].headers
    for name, value in [("model", "my-model"), ("temperature", "0.0"), ("language", "it"), ("prompt", "Rotech"), ("response_format", "verbose_json")]:
        assert f'name="{name}"\r\n\r\n{value}'.encode() in post.content
    result = messages[0].message.json_object
    assert result["model"] == "my-model"
    assert result["duration"] == 0.25
    assert result["language"] == "it"
    assert result["source"]["filename"] == "download.wav"
    assert result["segments"][0]["text"] == "Ciao"
    assert result["words"][0]["word"] == "Ciao"
    assert "test_key" not in str(messages)


def test_plain_text_response(monkeypatch, wav_bytes, native_file):
    mock_network(monkeypatch, wav_bytes, plain_text="Hello\nworld")
    messages = invoke({"file": native_file, "response_format": "text"})
    assert messages[0].message.json_object["text"] == "Hello\nworld"


@pytest.mark.parametrize("parameters", [{}, {"file": "path.wav"}, {"file": []}, {"media_url": "file:///etc/passwd"}, {"media_url": "https://user:secret@example.test/a.wav"}, {"media_url": "https://example.test/a.wav", "endpoint": "https://[bad"}])
def test_invalid_inputs_fail_before_network(monkeypatch, wav_bytes, parameters):
    requests = mock_network(monkeypatch, wav_bytes)
    with pytest.raises(TranscriptionError):
        invoke(parameters)
    assert requests == []


def test_both_inputs_rejected(monkeypatch, wav_bytes, native_file):
    requests = mock_network(monkeypatch, wav_bytes)
    with pytest.raises(TranscriptionError, match="exactly one"):
        invoke({"file": native_file, "media_url": "https://example.test/a.wav"})
    assert not requests


@pytest.mark.parametrize("name,value", [("temperature", -0.1), ("temperature", 1.1), ("temperature", "nan"), ("temperature", True), ("timeout", 1), ("max_input_mb", 1.5), ("max_audio_mb", 0), ("max_input_mb", "x")])
def test_invalid_number_settings(monkeypatch, wav_bytes, native_file, name, value):
    requests = mock_network(monkeypatch, wav_bytes)
    with pytest.raises(TranscriptionError):
        invoke({"file": native_file, name: value})
    assert not requests


def test_optional_credentials(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("Provider validation must not make requests"))
    WhisperProProvider()._validate_credentials({})
    WhisperProProvider()._validate_credentials({"api_key": "test-key"})
    for key in ("bad\nkey", " ", 42):
        with pytest.raises(ToolProviderCredentialValidationError):
            WhisperProProvider()._validate_credentials({"api_key": key})


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_streamed_size_boundary(monkeypatch, tmp_path, delta):
    content = b"x" * (1000 + delta)
    mock_network(monkeypatch, content, download_headers={"content-type": "audio/wav", "content-length": "1"})
    arguments = ("https://example.test/a.wav", tmp_path / "input", "a.wav", "audio/wav", None, 1000, 10)
    if delta > 0:
        with pytest.raises(TranscriptionError, match="Maximum Input"):
            download_media(*arguments)
    else:
        assert download_media(*arguments).size == len(content)


def test_metadata_limit_prevents_download(monkeypatch, wav_bytes, native_file):
    requests = mock_network(monkeypatch, wav_bytes)
    native_file.size = 1_000_001
    with pytest.raises(TranscriptionError, match="Maximum Input"):
        invoke({"file": native_file, "max_input_mb": 1})
    assert not requests


@pytest.mark.parametrize("status,content", [(403, b"secret URL"), (200, b"")])
def test_download_failure_never_calls_transcription(monkeypatch, native_file, status, content):
    requests = mock_network(monkeypatch, content, download_status=status)
    with pytest.raises(TranscriptionError) as error:
        invoke({"file": native_file})
    assert len(requests) == 1
    assert "secret" not in str(error.value)


@pytest.fixture
def wave_media(tmp_path, wav_bytes):
    path = tmp_path / "source.wav"
    path.write_bytes(wav_bytes)
    return Media(path, "source.wav", len(wav_bytes), "audio/wav")


@pytest.mark.parametrize("format", ["mp3", "wav"])
def test_bundled_ffmpeg_real_conversion(tmp_path, wave_media, format):
    result = prepare_audio(wave_media, tmp_path, "always", format, 1_000_000, 10)
    assert result.converted is True
    assert result.size > 0
    assert result.path.suffix == "." + format
    if format == "wav":
        with wave.open(str(result.path)) as recording:
            assert recording.getnchannels() == 1
            assert recording.getframerate() == 16000
    assert Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file()


def test_ogg_automatic_conversion_and_upload(monkeypatch, tmp_path, wave_media):
    ogg = tmp_path / "sample.ogg"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(wave_media.path), "-c:a", "libvorbis", str(ogg)], check=True)
    requests = mock_network(monkeypatch, ogg.read_bytes())
    messages = invoke({"media_url": "https://example.test/sample.ogg"})
    assert messages[0].message.json_object["audio"]["converted"] is True
    assert b'filename="sample.mp3"' in requests[-1].content


def test_video_audio_extraction_with_seekable_input(monkeypatch, tmp_path, wave_media):
    video = tmp_path / "video.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "color=black:s=16x16:d=0.25", "-i", str(wave_media.path), "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video)], check=True)
    requests = mock_network(monkeypatch, video.read_bytes(), download_headers={"content-type": "video/mp4"})
    messages = invoke({"media_url": "https://example.test/video.mp4"})
    assert messages[0].message.json_object["audio"]["converted"] is True
    assert b'filename="video.mp3"' in requests[-1].content


def test_conversion_never_and_final_size_limit(tmp_path, wave_media):
    assert prepare_audio(wave_media, tmp_path, "never", "mp3", 1_000_000, 10) is wave_media
    with pytest.raises(TranscriptionError, match="Original file exceeds"):
        prepare_audio(wave_media, tmp_path, "never", "mp3", 1, 10)


def test_converted_size_limit_rejects_truncation(tmp_path, wave_media):
    with pytest.raises(TranscriptionError, match="No partial transcription"):
        prepare_audio(wave_media, tmp_path, "always", "wav", 1000, 10)


def test_conversion_timeout(monkeypatch, tmp_path, wave_media):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 10)
    monkeypatch.setattr(audio.subprocess, "run", fail)
    with pytest.raises(TranscriptionError, match="timed out"):
        prepare_audio(wave_media, tmp_path, "always", "mp3", 1000000, 10)


def test_corrupt_media_is_rejected(tmp_path):
    path = tmp_path / "broken"
    path.write_bytes(b"not media")
    with pytest.raises(TranscriptionError, match="could not decode"):
        prepare_audio(Media(path, "broken.ogg", 9, "audio/ogg"), tmp_path, "auto", "mp3", 1000000, 10)


def test_external_playlist_references_are_blocked(tmp_path, wave_media):
    playlist = tmp_path / "playlist"
    playlist.write_text(f"ffconcat version 1.0\nfile '{wave_media.path}'\n")
    with pytest.raises(TranscriptionError, match="external references"):
        prepare_audio(Media(playlist, "playlist.ffconcat", playlist.stat().st_size, ""), tmp_path, "auto", "mp3", 1000000, 10)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 415, 422, 429, 500, 302])
def test_api_failures_are_sanitized(monkeypatch, wav_bytes, native_file, status):
    requests = mock_network(monkeypatch, wav_bytes, status=status, payload={"error": "private key"})
    with pytest.raises(TranscriptionError, match=f"HTTP {status}") as error:
        invoke({"file": native_file})
    assert "private" not in str(error.value)
    assert len(requests) == 2


def test_endpoint_timeout_is_not_retried(monkeypatch, wav_bytes, native_file):
    requests = mock_network(monkeypatch, wav_bytes, error=httpx.ReadTimeout("private host"))
    with pytest.raises(TranscriptionError, match="timed out") as error:
        invoke({"file": native_file})
    assert "private host" not in str(error.value)
    assert len(requests) == 2


@pytest.mark.parametrize("payload", [{}, [], {"text": 12}, {"error": "secret"}])
def test_invalid_api_json(monkeypatch, wav_bytes, native_file, payload):
    mock_network(monkeypatch, wav_bytes, payload=payload)
    with pytest.raises(TranscriptionError, match="valid text field"):
        invoke({"file": native_file})


@pytest.mark.parametrize("field,value", [("segments", {}), ("words", ["wrong"]), ("duration", float("nan")), ("duration", -1), ("language", 2)])
def test_malformed_structured_fields(wave_media, field, value):
    with pytest.raises(TranscriptionError):
        structured_result({"text": "hello", field: value}, wave_media, wave_media, "model", "json")


def test_sdk_contract_and_defaults():
    root = Path(__file__).resolve().parent.parent
    provider = ToolProviderConfiguration.model_validate(yaml.safe_load((root / "provider/whisper_pro.yaml").read_text()))
    defaults = {parameter.name: parameter.default for parameter in provider.tools[0].parameters}
    assert defaults["endpoint"] == DEFAULT_ENDPOINT
    assert defaults["model"] == DEFAULT_MODEL
    assert defaults["temperature"] == 0.5
    assert defaults["response_format"] == "json"
    assert set(provider.tools[0].output_schema["properties"]) == {"transcript", "language", "segments", "words"}
    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    assert manifest["author"] + "/" + manifest["name"] == "erbanku/whisper-pro"
