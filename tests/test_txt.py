import io
import random
from datetime import datetime, timezone

import can
import pytest
from clx000 import ALL_COLUMNS, write_log

from python_can_csscan_txt import LogFile

START = datetime(2023, 12, 30, 23, 59, 50, tzinfo=timezone.utc).timestamp()

# Step between messages per time format. Steps must be less than the wrap period minus one second, to tell a wrap apart
# from a timestamp logged too late (format 0 does not show seconds, so there steps must be less than one second).
MAX_STEP = {0: 0.9, 1: 50, 2: 3000, 3: 80000, 4: 20 * 86400, 5: 200 * 86400, 6: 1000 * 86400}


def random_messages(count: int, max_step: float, seed: int = 0) -> list[can.Message]:
    rng = random.Random(seed)
    t_ms = int(START * 1000)
    messages = []
    for _ in range(count):
        t_ms += rng.randint(0, int(max_step * 1000))  # The first message also comes some time after the header time
        extended = rng.random() < 0.5
        messages.append(
            can.Message(
                timestamp=t_ms / 1000,
                arbitration_id=rng.randint(0, 0x1FFFFFFF if extended else 0x7FF),
                is_extended_id=extended,
                is_rx=rng.random() < 0.5,
                data=rng.randbytes(rng.randint(0, 8)),
            )
        )
    return messages


def read(text: str) -> list[can.Message]:
    return list(LogFile(io.StringIO(text)))


def assert_equal(actual: list[can.Message], expected: list[can.Message]) -> None:
    assert len(actual) == len(expected)
    for a, e in zip(actual, expected):
        assert a.equals(e, timestamp_delta=1e-6), f"{a} != {e}"


@pytest.mark.parametrize("time_format", [0, 1, 2, 3, 4, 5, 6])
def test_time_formats(time_format):
    # Timestamps only hold the least significant part of the time and wrap around (e.g. every minute for format 1)
    messages = random_messages(500, MAX_STEP[time_format], seed=time_format)
    assert_equal(read(write_log(messages, start=START, time_format=time_format)), messages)


@pytest.mark.parametrize("time_format", [1, 2, 3])
def test_wrap_before_first_message(time_format):
    # The header holds the time the file was opened. The timestamp can wrap before the first message. Not for format 0:
    # the header time has no milliseconds, so a wrap within the first second after it cannot be detected.
    start = datetime(2023, 12, 31, 23, 59, 59, 500000, tzinfo=timezone.utc).timestamp()
    messages = [can.Message(timestamp=start + 0.7, arbitration_id=1), can.Message(timestamp=start + 0.8, arbitration_id=2)]
    assert_equal(read(write_log(messages, start=start, time_format=time_format)), messages)


@pytest.mark.parametrize("time_format", [4, 5])
def test_wrap_leap_year(time_format):
    # Wraps of a month (format 4) or a year (format 5) do not have a fixed length
    start = datetime(2023, 12, 1, tzinfo=timezone.utc).timestamp()
    if time_format == 4:  # Steps of less than a month
        times = [(2023, 12, 1), (2023, 12, 20), (2024, 1, 10), (2024, 2, 1), (2024, 2, 29), (2024, 3, 1), (2024, 3, 31)]
    else:  # Steps of less than a year
        times = [(2023, 12, 1), (2024, 2, 1), (2024, 2, 29), (2024, 3, 1), (2024, 12, 31), (2025, 3, 1)]
    messages = [
        can.Message(timestamp=datetime(*t, 12, tzinfo=timezone.utc).timestamp(), arbitration_id=i)
        for i, t in enumerate(times)
    ]
    assert_equal(read(write_log(messages, start=start, time_format=time_format)), messages)


@pytest.mark.parametrize(
    "separators",
    [
        dict(),  # Firmware default
        dict(value_sep=",", time_sep=":", ms_sep=".", date_sep="-", date_time_sep=" "),
        dict(value_sep=";", time_sep=".", ms_sep=",", date_sep="/", date_time_sep=" "),
        dict(value_sep=" ", time_sep=":", ms_sep=".", date_sep="/", date_time_sep="T"),
        dict(value_sep="|", date_time_sep=""),
    ],
)
def test_separators(separators):
    messages = random_messages(100, 10)
    assert_equal(read(write_log(messages, start=START, time_format=6, **separators)), messages)


def test_all_columns():
    messages = random_messages(100, 10)
    assert_equal(read(write_log(messages, start=START, columns=ALL_COLUMNS)), messages)


def test_without_timestamp():
    messages = random_messages(10, 10)
    for msg in messages:
        msg.timestamp = 0.0
    assert_equal(read(write_log(messages, start=START, columns=("Type", "ID", "Data"))), messages)


def test_timestamp_glitch():
    # The CLX000 occasionally logs a timestamp up to one second too late. It is kept, following earlier messages are
    # skipped and not mistaken for a wrap.
    messages = random_messages(5, 0.1)
    messages[1].timestamp += 1
    expected = [messages[0], messages[1]]
    assert_equal(read(write_log(messages, start=START, time_format=1)), expected)


def test_truncated_last_line():
    # Power loss while writing can cut the last line anywhere. Complete messages are read, the cut one is either
    # skipped or read with partial data (undetectable when cut between two data bytes).
    messages = [can.Message(timestamp=START + i, arbitration_id=i, data=bytes(range(1, 9))) for i in range(5)]
    text = write_log(messages, start=START, columns=ALL_COLUMNS)
    last_line = text.splitlines()[-1]
    for cut in range(1, len(last_line)):
        actual = read(text[: len(text) - len(last_line) - 1 + cut])
        assert_equal(actual[:4], messages[:4])
        assert len(actual) == 4 or actual[4].arbitration_id == messages[4].arbitration_id


def test_metadata():
    reader = LogFile(io.StringIO(write_log([], start=START, logger_id="ABCD1234", session=12, split=3, bitrate=250000)))
    assert reader.get_metadata() == {
        "id": "ABCD1234",
        "hardware revision": "00.04",
        "firmware revision": "05.41",
        "session": 12,
        "split": 3,
        "bitrate": 250000,
    }


def test_log_reader(tmp_path):
    messages = random_messages(10, 10)
    path = tmp_path / "00000001.TXT"
    path.write_text(write_log(messages, start=START), newline="\r\n")  # The firmware writes "\n", also accept "\r\n"
    with can.LogReader(path) as reader:
        assert isinstance(reader, LogFile)
        assert_equal(list(reader), messages)


def test_binary_file():
    messages = random_messages(10, 10)
    with LogFile(io.BytesIO(write_log(messages, start=START).encode())) as reader:
        assert_equal(list(reader), messages)


@pytest.mark.parametrize("text", ["not a log file\n", "# Some: comment\nData\n0102\n"])
def test_invalid_file(tmp_path, text):
    path = tmp_path / "notes.txt"
    path.write_text(text)
    with pytest.raises(ValueError):
        LogFile(path)  # Must not leak the open file (warnings are errors, see pyproject.toml)
