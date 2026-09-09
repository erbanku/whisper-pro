# Privacy

Whisper Pro downloads the selected media file and sends its original bytes or extracted audio to the endpoint configured by the user. It does not use a public third-party transcription service unless that is the endpoint the user selects. Configure only trusted inference endpoints and media URLs.

The optional API key is sent as a Bearer header only to the configured transcription endpoint. It is not attached to input downloads. HTTPS certificate verification is enabled; the user-supplied default endpoint uses HTTP for a local network service.

Original and converted media are stored in an invocation-scoped temporary directory that is cleaned on success or failure. Conversion strips container metadata and uses the first audio stream; passthrough mode retains original file metadata. FFmpeg's input protocol allowlist permits only the supplied file descriptor, not external media references.

Outputs contain transcript text, any endpoint-returned timestamp/language metadata, filenames, sizes and conversion status. Input URLs, API keys, local temporary paths and arbitrary backend fields are not emitted. Raw remote error bodies and FFmpeg diagnostics are not returned. The plugin adds no analytics and does not create a permanent media or transcript store.

Dify and the selected inference server may retain files, transcripts, prompts and execution logs according to their own policies. Users are responsible for permissions to process recordings and for configuring retention, network access and endpoint security.
