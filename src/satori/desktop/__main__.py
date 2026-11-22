"""Launch the native client without starting any model or microphone implicitly."""

import argparse
import sys
from pathlib import Path


def main() -> int:
    if sys.argv[1:2] == ["--public-source-worker"]:
        if len(sys.argv) != 2:
            return 2
        import os
        from contextlib import ExitStack

        original = (sys.stdin, sys.stdout, sys.stderr)
        try:
            with ExitStack() as owned:
                for name, descriptor, mode in (
                    ("stdin", 0, "r"),
                    ("stdout", 1, "w"),
                    ("stderr", 2, "w"),
                ):
                    if getattr(sys, name) is None:
                        duplicate = os.dup(descriptor)
                        owned.callback(os.close, duplicate)
                        stream = owned.enter_context(
                            os.fdopen(duplicate, mode, encoding="utf-8", closefd=False)
                        )
                        setattr(sys, name, stream)
                from satori.infrastructure.public_sources.worker import main as public_source_main

                return public_source_main()
        except Exception:
            return 1
        finally:
            sys.stdin, sys.stdout, sys.stderr = original
    if sys.argv[1:2] == ["--telegram-worker"]:
        from satori.desktop.telegram_worker import main as telegram_main

        return telegram_main(sys.argv[2:])
    if sys.argv[1:2] == ["--telegram-reply-worker"]:
        from satori.desktop.telegram_reply_worker import main as telegram_reply_main

        return telegram_reply_main(sys.argv[2:])
    if sys.argv[1:2] == ["--telegram-runtime-preflight"]:
        from satori.desktop.telegram_worker import _owned_stdio, preflight

        with _owned_stdio():
            return preflight()
    if sys.argv[1:2] == ["--telegram-native-no-auth-check"]:
        from satori.desktop.telegram_worker import _owned_stdio, native_no_auth_check

        with _owned_stdio():
            return native_no_auth_check()
    if sys.argv[1:2] == ["--cloud-speech-worker"]:
        from satori.desktop.voice.cloud_worker import main as cloud_speech_main

        return cloud_speech_main(sys.argv[2:])
    if sys.argv[1:2] == ["--speech-worker"]:
        from satori.desktop.voice.speech_worker import main as speech_main

        return speech_main(sys.argv[2:])
    if sys.argv[1:2] == ["--speech-model-setup"]:
        from satori.desktop.voice.neural_models import main as speech_model_main

        return speech_model_main(sys.argv[2:])
    if sys.argv[1:2] == ["--voice-worker"]:
        from satori.desktop.voice.worker import main as voice_main

        return voice_main(sys.argv[2:])
    if sys.argv[1:2] == ["--voice-model-setup"]:
        from satori.desktop.voice.models import main as model_main

        return model_main(sys.argv[2:])
    if sys.argv[1:2] == ["--worker"]:
        from satori.desktop.worker import main as worker_main

        return worker_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description="Satori desktop")
    parser.add_argument("--repository-directory", type=Path)
    parser.add_argument("--hidden", action="store_true")
    arguments = parser.parse_args()
    try:
        from satori.desktop.ui import run_desktop
    except ImportError:
        print(
            "Desktop requires the desktop dependency group: uv sync --all-groups", file=sys.stderr
        )
        return 2
    return run_desktop(arguments.repository_directory, hidden=arguments.hidden)


if __name__ == "__main__":
    raise SystemExit(main())
