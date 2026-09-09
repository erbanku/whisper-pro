from dataclasses import dataclass
import math
import mimetypes
from pathlib import Path, PurePosixPath
import subprocess
import time
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
import imageio_ffmpeg


DEFAULT_ENDPOINT = "http://host.docker.internal:9997/v1/audio/transcriptions"
DEFAULT_MODEL = "whisper-large-v3-turbo"
PASSTHROUGH_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac"}


class TranscriptionError(ValueError):
    pass


@dataclass
class Media:
    path: Path
    filename: str
    size: int
    mime_type: str
    converted: bool = False


def text_setting(parameters: dict[str, Any], name: str, default: str = "") -> str:
    value = parameters.get(name)
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        raise TranscriptionError(f"{name} must be text.")
    return value


def number_setting(parameters: dict[str, Any], name: str, default: float, minimum: float, maximum: float, integer: bool = False) -> float:
    value = parameters.get(name)
    if value is None or value == "":
        value = default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TranscriptionError(f"{name} must be a number.") from None
    if isinstance(value, bool) or not math.isfinite(number) or not minimum <= number <= maximum or (integer and not number.is_integer()):
        raise TranscriptionError(f"{name} must be {'an integer' if integer else 'a number'} between {minimum} and {maximum}.")
    return number


def validate_url(value: str, label: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or any(ord(character) < 33 for character in value):
            raise ValueError
        parsed.port
    except ValueError:
        raise TranscriptionError(f"{label} must be a valid HTTP(S) URL without embedded credentials, fragments or whitespace.") from None
    return value


def safe_filename(value: str) -> str:
    filename = PurePosixPath(value.replace("\\", "/")).name
    filename = "".join(character if character.isprintable() else "_" for character in filename).strip()
    if not filename or filename in {".", ".."}:
        return "recording"
    return filename[:180]


def download_media(url: str, destination: Path, filename: str, mime_type: str, declared_size: int | None, max_bytes: int, timeout: int) -> Media:
    if declared_size is not None and (declared_size < 0 or declared_size > max_bytes):
        raise TranscriptionError("Input exceeds Maximum Input Size, or its declared size is invalid.")
    started = time.monotonic()
    size = 0
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(15, timeout)), follow_redirects=True, max_redirects=3) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                header_size = response.headers.get("content-length", "")
                if header_size.isdigit() and int(header_size) > max_bytes:
                    raise TranscriptionError("Input exceeds Maximum Input Size.")
                mime_type = mime_type or response.headers.get("content-type", "").split(";", 1)[0].strip()
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=64 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            raise TranscriptionError("Downloaded media exceeds Maximum Input Size.")
                        if time.monotonic() - started > timeout:
                            raise TranscriptionError("Media download exceeded its wall-clock timeout.")
                        output.write(chunk)
    except (httpx.HTTPError, httpx.InvalidURL):
        raise TranscriptionError("Cannot download media. Check the file URL, its expiry and runtime network access.") from None
    if time.monotonic() - started > timeout:
        raise TranscriptionError("Media download exceeded its wall-clock timeout.")
    if size == 0:
        raise TranscriptionError("The media file is empty.")
    filename = safe_filename(filename or unquote(urlsplit(url).path))
    if not PurePosixPath(filename).suffix:
        filename += {"audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/flac": ".flac", "audio/mp4": ".m4a"}.get(mime_type, mimetypes.guess_extension(mime_type) or ".media")
    return Media(destination, filename, size, mime_type)


def prepare_audio(media: Media, directory: Path, mode: str, output_format: str, max_bytes: int, timeout: int) -> Media:
    extension = PurePosixPath(media.filename).suffix.lower()
    convert = mode == "always" or (mode == "auto" and (extension not in PASSTHROUGH_EXTENSIONS or media.mime_type.startswith("video/")))
    if not convert:
        if media.size > max_bytes:
            raise TranscriptionError("Original file exceeds Maximum Submitted Audio Size. Enable conversion or raise the limit.")
        return media
    destination = directory / f"converted.{output_format}"
    try:
        executable = imageio_ffmpeg.get_ffmpeg_exe()
    except (RuntimeError, OSError):
        raise TranscriptionError("Bundled FFmpeg is unavailable. Install the Linux amd64 package on a compatible runtime.") from None
    command = [
        executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
        "-protocol_whitelist", "fd", "-threads", "2", "-i", "fd:",
        "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1",
        "-ac", "1", "-ar", "16000", "-threads", "2",
    ]
    command += ["-c:a", "libmp3lame", "-b:a", "64k"] if output_format == "mp3" else ["-c:a", "pcm_s16le"]
    command += ["-fs", str(max_bytes + 1), "-f", output_format, "-y", str(destination)]
    try:
        with media.path.open("rb") as source:
            result = subprocess.run(command, stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise TranscriptionError("FFmpeg conversion timed out. Use a shorter recording or increase Timeout.") from None
    except OSError:
        raise TranscriptionError("FFmpeg could not run. Check the package architecture and temporary disk space.") from None
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        raise TranscriptionError("FFmpeg could not decode an audio track. The file may be corrupt, unsupported, protected, or contain no audio. Streaming playlists and external references are not supported.")
    size = destination.stat().st_size
    if size > max_bytes:
        raise TranscriptionError("Converted audio exceeds Maximum Submitted Audio Size. No partial transcription was submitted; choose MP3 or raise the limit.")
    return Media(destination, safe_filename(PurePosixPath(media.filename).stem) + "." + output_format, size, "audio/mpeg" if output_format == "mp3" else "audio/wav", True)


def submit_transcription(media: Media, endpoint: str, fields: dict[str, str], api_key: str, timeout: int) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with media.path.open("rb") as source:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(15, timeout)), follow_redirects=False) as client:
                response = client.post(endpoint, headers=headers, data=fields, files={"file": (media.filename, source, media.mime_type or mimetypes.guess_type(media.filename)[0] or "application/octet-stream")})
    except (httpx.HTTPError, httpx.InvalidURL):
        raise TranscriptionError("Transcription request failed or timed out. Check endpoint connectivity and model availability before retrying; the server may still have processed the request.") from None
    if not 200 <= response.status_code < 300:
        reason = {
            400: "Check model, format and parameters. Try Always convert if the codec is unsupported.",
            401: "Check the optional API key.",
            403: "Check endpoint authorization and network policy.",
            404: "Check the full transcription endpoint path and deployed model name.",
            413: "The server rejected the upload size. Use MP3 conversion or a smaller recording.",
            415: "The server rejected the media format. Try Always convert.",
            422: "Check model, response format and optional parameter support.",
            429: "The server is rate-limiting requests. Retry later.",
        }.get(response.status_code, "Check the transcription server and model health.")
        raise TranscriptionError(f"Transcription HTTP {response.status_code}: {reason}")
    if fields["response_format"] == "text" and "json" not in response.headers.get("content-type", "").lower():
        return {"text": response.text}
    try:
        payload = response.json()
    except ValueError:
        raise TranscriptionError("The transcription endpoint returned invalid JSON. Choose Plain text only if the endpoint returns plain text.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str) or payload.get("error"):
        raise TranscriptionError("The transcription endpoint returned no valid text field.")
    return payload


def structured_result(payload: dict[str, Any], source: Media, submitted: Media, model: str, response_format: str) -> dict[str, Any]:
    result: dict[str, Any] = {"text": payload["text"], "model": model, "response_format": response_format, "language": None, "duration": None}
    if payload.get("language") is not None:
        if not isinstance(payload["language"], str):
            raise TranscriptionError("The endpoint returned an invalid language field.")
        result["language"] = payload["language"]
    if payload.get("duration") is not None:
        duration = payload["duration"]
        if isinstance(duration, bool) or not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration < 0:
            raise TranscriptionError("The endpoint returned an invalid duration field.")
        result["duration"] = duration
    for field in ("segments", "words"):
        values = payload.get(field)
        if values is None:
            values = []
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise TranscriptionError(f"The endpoint returned invalid {field} data.")
        result[field] = values
    result["source"] = {"filename": source.filename, "size": source.size}
    result["audio"] = {"filename": submitted.filename, "size": submitted.size, "converted": submitted.converted}
    return result
