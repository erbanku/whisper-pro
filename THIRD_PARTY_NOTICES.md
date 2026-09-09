# Third-party runtime

The original runtime wheels are retained under `vendor/wheels/linux-amd64/`, including their distributed license files and metadata. Whisper Pro invokes the bundled FFmpeg binary as a separate process rather than importing its code into the plugin.

## imageio-ffmpeg

Version `0.6.0` supplies the executable-discovery helper and Linux wheel. Its Python package metadata identifies the wrapper as BSD-2-Clause; the wheel's `imageio_ffmpeg-0.6.0.dist-info/LICENSE` is preserved.

- Project and source: https://github.com/imageio/imageio-ffmpeg/tree/v0.6.0
- Release metadata: https://pypi.org/project/imageio-ffmpeg/0.6.0/

## FFmpeg binary

The bundled Linux amd64 wheel contains `ffmpeg-linux-x86_64-v7.0.2`, whose version output identifies FFmpeg 7.0.2 and John Van Sickle's static build. The FFmpeg executable has its own licensing terms, separate from the Python wrapper; run the bundled binary with `-L` for its license information and `-buildconf` for build configuration.

- Binary distribution and build/source information: https://johnvansickle.com/ffmpeg/
- FFmpeg 7.0.2 source release: https://ffmpeg.org/releases/ffmpeg-7.0.2.tar.xz
- Project: https://ffmpeg.org/

Operators redistributing this deployment artifact should retain the supplied notices and review the executable's licensing and corresponding-source requirements. No claim is made that the Python wrapper's license covers the FFmpeg binary or every compiled codec.
