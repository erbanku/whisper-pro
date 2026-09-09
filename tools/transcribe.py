from collections.abc import Generator
from pathlib import Path
import tempfile
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.file.file import File

from audio import DEFAULT_ENDPOINT, DEFAULT_MODEL, TranscriptionError, download_media, number_setting, prepare_audio, structured_result, submit_transcription, text_setting, validate_url
from provider.whisper_pro import WhisperProProvider


class TranscribeTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        file = tool_parameters.get("file")
        media_url = text_setting(tool_parameters, "media_url").strip()
        if (file is not None) == bool(media_url):
            raise TranscriptionError("Provide exactly one native Dify file or Media URL.")
        if file is not None and not isinstance(file, File):
            raise TranscriptionError("File must be one native Dify file, not a path, URL or list.")
        source_url = validate_url(file.url if file is not None else media_url, "Media URL")
        endpoint = validate_url(text_setting(tool_parameters, "endpoint", DEFAULT_ENDPOINT), "Transcription Endpoint")
        model = text_setting(tool_parameters, "model", DEFAULT_MODEL).strip()
        if not model:
            raise TranscriptionError("Model must not be blank.")
        response_format = text_setting(tool_parameters, "response_format", "json")
        mode = text_setting(tool_parameters, "conversion_mode", "auto")
        output_format = text_setting(tool_parameters, "conversion_format", "mp3")
        if response_format not in {"json", "verbose_json", "text"} or mode not in {"auto", "always", "never"} or output_format not in {"mp3", "wav"}:
            raise TranscriptionError("Choose valid response format and audio conversion options.")
        temperature = number_setting(tool_parameters, "temperature", 0.5, 0, 1)
        timeout = int(number_setting(tool_parameters, "timeout", 600, 10, 3600, True))
        max_input = int(number_setting(tool_parameters, "max_input_mb", 1000, 1, 2048, True)) * 1_000_000
        max_audio = int(number_setting(tool_parameters, "max_audio_mb", 200, 1, 1024, True)) * 1_000_000
        credentials = dict(self.runtime.credentials)
        WhisperProProvider()._validate_credentials(credentials)
        api_key = (credentials.get("api_key") or "").strip()
        fields = {"model": model, "response_format": response_format, "temperature": str(temperature)}
        for field in ("language", "prompt"):
            value = text_setting(tool_parameters, field)
            if value.strip():
                fields[field] = value
        try:
            with tempfile.TemporaryDirectory(prefix="whisper-pro-") as directory:
                workdir = Path(directory)
                source = download_media(
                    url=source_url,
                    destination=workdir / "input.media",
                    filename=(file.filename or "") if file is not None else "",
                    mime_type=(file.mime_type or "") if file is not None else "",
                    declared_size=file.size if file is not None else None,
                    max_bytes=max_input,
                    timeout=timeout,
                )
                submitted = prepare_audio(source, workdir, mode, output_format, max_audio, timeout)
                payload = submit_transcription(submitted, endpoint, fields, api_key, timeout)
                result = structured_result(payload, source, submitted, model, response_format)
        except OSError:
            raise TranscriptionError("Unable to store temporary media. Check available disk space and runtime permissions.") from None
        yield self.create_json_message(result)
        yield self.create_variable_message("transcript", result["text"])
        yield self.create_variable_message("language", result["language"] or "")
        yield self.create_variable_message("segments", result["segments"])
        yield self.create_variable_message("words", result["words"])
        yield self.create_text_message(result["text"])
