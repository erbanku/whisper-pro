# Whisper Pro

`erbanku/whisper-pro` is a native Dify tool plugin for audio/video transcription. It accepts either a Dify file or a direct media URL, bundles FFmpeg for conversion, and returns transcript text with structured JSON. Version `0.0.1` targets **Linux amd64 / x86_64 with Python 3.12**.

## Defaults

The default request matches the supplied workflow and curl example:

| Setting | Default |
| --- | --- |
| Endpoint | `http://host.docker.internal:9997/v1/audio/transcriptions` |
| Model | `whisper-large-v3-turbo` |
| API response format | `json` |
| Temperature | `0.5` |
| Authentication | None; optional Bearer API key |
| Conversion | Auto, targeting MP3 when conversion is needed |

The endpoint receives multipart fields `file`, `model`, `response_format`, and `temperature`. Optional `language` and `prompt` fields are sent only when supplied. Whisper Pro does not run a Whisper model locally: inference remains on your configured server.

## Installation and use

1. Install `artifacts/whisper-pro-0.0.1.difypkg` through Dify's local plugin package installer.
2. Leave the optional provider API key empty for the supplied unauthenticated local server. Configure it only if your endpoint requires Bearer authentication.
3. Add **Whisper Pro → Transcribe Audio or Video** to a workflow.
4. Bind a single native Dify file to **Audio / Video File**, or supply a direct HTTP(S) download link in **Media URL**. Do not fill both.
5. Keep the defaults or set your endpoint, model, response format, temperature, language hint and prompt.
6. Connect `text` or the named `transcript` output to a text/Answer node. Use JSON for file metadata, conversion status, language, duration and timestamp arrays.

`whisper-current.yml` was used as a read-only reference and is not modified or included in the package. The separate workflow extraction and transcription steps are handled internally by this tool; the existing workflow itself is not migrated.

## Conversion behavior

- **Auto:** passes `.mp3`, `.wav`, `.m4a`, and `.flac` audio through unchanged. Video inputs and other formats are converted with bundled FFmpeg.
- **Always convert:** decodes and normalizes every input. Use it when a filename looks supported but the server rejects the actual codec.
- **Never convert:** submits the original bytes and filename, subject to the submitted-file size limit.
- **MP3 output:** mono, 16 kHz, 64 kbps, matching the reference workflow's preference for compact extracted audio.
- **WAV output:** mono, 16 kHz, signed 16-bit PCM.

Conversion selects the first audio track and drops video, subtitles, data tracks and media metadata. Common additional inputs include AAC, OGG/Opus, WMA, AMR, MP4, MOV, MKV and WebM when decodable by the bundled FFmpeg build. Unsupported/corrupt files, protected media, files without audio, and streaming playlists produce errors. A webpage URL is not a media download URL.

FFmpeg reads the downloaded local file through a seekable file descriptor, so videos with metadata at the end are supported without giving the decoder access to network URLs or other local files through media references. The decoder's input protocol allowlist is restricted to `fd`; no shell commands or user-provided FFmpeg arguments are evaluated.

## Structured outputs

The standard `text` output and the named `transcript` string contain the transcript. Named `language`, `segments`, and `words` outputs are also available. The JSON output has this shape:

```json
{
  "text": "Meeting transcript...",
  "model": "whisper-large-v3-turbo",
  "response_format": "json",
  "language": null,
  "duration": null,
  "segments": [],
  "words": [],
  "source": {"filename": "meeting.mov", "size": 12000000},
  "audio": {"filename": "meeting.mp3", "size": 800000, "converted": true}
}
```

Language, duration, segments and words are populated only when returned by the endpoint. Ordinary `json` often returns only `text`; missing metadata remains null/empty rather than being fabricated. The named language string is empty when unavailable. Choose `verbose_json` for richer metadata if supported by your deployed model/server. Selecting it does not guarantee word-level timestamps. Plain-text API responses are also wrapped into the same structured result.

The output reports filenames and byte sizes but excludes the input URL, endpoint URL, API key, local temporary paths and arbitrary raw response fields. The plugin does not summarize transcripts or invent speaker identities.

## Limits and networking

| Setting | Default | Allowed range |
| --- | --- | --- |
| Maximum Input Size | 1000 MB | 1–2048 MB |
| Maximum Submitted Audio Size | 200 MB | 1–1024 MB |
| Timeout | 600 seconds | 10–3600 seconds |

Sizes use decimal MB. Downloads stream to temporary disk and check declared size, response Content-Length and actual bytes. Both converted and unconverted uploads respect the submitted-file limit. If conversion reaches that limit, the operation fails rather than submitting a truncated recording. MP3 is preferable to WAV for long recordings because PCM output is much larger.

Timeout controls download wall-clock checks, the hard FFmpeg subprocess timeout, and the HTTP client's transcription I/O timeouts. Dify, its plugin daemon, proxies and the inference server may impose shorter independent limits. Temporary input and converted files are removed on success and failure; provide enough writable temporary disk space for both.

`host.docker.internal` must resolve **inside the plugin runtime** and port `9997` must be reachable there. Linux Docker deployments may need an operator-configured host-gateway mapping; alternatively set a reachable internal service URL or host IP. The plugin does not change container networking or your server configuration. The supplied default uses HTTP; use HTTPS for traffic that crosses an untrusted network. HTTPS certificate validation remains enabled.

Optional API keys go only to the configured transcription endpoint. Downloads use a separate client without inference credentials; endpoint redirects are not followed. Use trusted media URLs because the runtime may have internal-network access. Errors omit raw remote bodies and signed URLs. Requests are not automatically retried, unlike the reference workflow's retry setting; after a timeout, the server may still be processing the recording.

## Bundled runtime and validation

The package includes `imageio-ffmpeg==0.6.0` with **FFmpeg 7.0.2 for Linux x86_64**, plus vendored Python runtime wheels. No system FFmpeg installation or network dependency installation is required. The wheel expands its executable at dependency installation time; keep adequate install disk space. `IMAGEIO_FFMPEG_EXE`, if set by an operator, can override imageio-ffmpeg's executable selection.

From the repository root:

```bash
uv sync --directory ai-pkgs/whisper_pro --all-groups --python 3.12
python .agents/skills/dify-plugin-generator-by-erbanku/scripts/validate_plugin.py ai-pkgs/whisper_pro --with-pytest --with-offline-check
python .agents/skills/dify-plugin-generator-by-erbanku/scripts/package_plugin.py ai-pkgs/whisper_pro --cli-path /usr/local/bin/dify
```

Tests use actual bundled FFmpeg for synthetic WAV/MP3 conversion, OGG conversion, MP4 audio extraction with seekable input, output-limit rejection and blocked external playlist references. HTTP tests mock inference: no real recording is sent to the supplied endpoint during validation. They cover default/custom multipart requests, native file and URL input, API-key isolation, bounds, responses and sanitized failures.

See `PRIVACY.md` and `THIRD_PARTY_NOTICES.md`. This is an independent Whisper-compatible integration, not an official OpenAI or Xinference plugin.
