#!/usr/bin/env python3
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIO_C = ROOT / "TMessagesProj/jni/audio.c"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    audio_c = read(AUDIO_C)

    bitrate_match = re.search(
        r"const\s+opus_int32\s+voice_record_bitrate\s*=\s*(\d+)\s*;",
        audio_c,
    )
    require(
        bitrate_match is not None,
        "audio.c must define an explicit voice_record_bitrate for voice notes",
        failures,
    )
    if bitrate_match is not None:
        bitrate = int(bitrate_match.group(1))
        require(
            12000 <= bitrate <= 24000,
            "voice_record_bitrate must stay in the compact speech range of 12-24 kbps",
            failures,
        )

    require(
        "OPUS_SET_BITRATE(OPUS_BITRATE_MAX)" not in audio_c
        and "bitrate = OPUS_BITRATE_MAX" not in audio_c,
        "voice recorder must not ask Opus for OPUS_BITRATE_MAX",
        failures,
    )
    require(
        "OPUS_SET_BITRATE(voice_record_bitrate)" in audio_c,
        "initRecorder() must apply voice_record_bitrate to the Opus encoder",
        failures,
    )
    require(
        "OPUS_SET_VBR(1)" in audio_c,
        "voice recorder must keep Opus VBR enabled for compact speech",
        failures,
    )
    require(
        "OPUS_SET_SIGNAL(OPUS_SIGNAL_VOICE)" in audio_c,
        "voice recorder must explicitly bias Opus toward voice",
        failures,
    )

    if failures:
        print("Voice recording bitrate guard failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1

    print("Voice recording bitrate guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
