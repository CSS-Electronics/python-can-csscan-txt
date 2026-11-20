"""Write CLX000 TXT log files the way the device firmware does (port of cCsvLog.c / cTime.c), for use in tests."""

from collections.abc import Iterable
from datetime import datetime, timezone

import can

ALL_COLUMNS = ("Timestamp", "Lost", "Type", "ID", "Length", "Data")
DEFAULT_COLUMNS = ("Timestamp", "Type", "ID", "Data")  # Default of LOG_dataFields_config (LOG_logger.c)


def write_log(
    messages: Iterable[can.Message],
    *,
    start: float,
    time_format: int = 4,
    value_sep: str = ";",
    time_sep: str = "",
    ms_sep: str = "",
    date_sep: str = "",
    date_time_sep: str = "T",
    columns: Iterable[str] = DEFAULT_COLUMNS,
    logger_id: str = "00AABBCC",
    session: int = 1,
    split: int = 1,
    bitrate: int = 500000,
) -> str:
    """Return the content of a log file with the given messages. Defaults match the firmware defaults."""
    columns = [c for c in ALL_COLUMNS if c in columns]  # Fixed order, as in the firmware

    lines = [
        "# Logger type: CANLogger2000",
        "# HW rev: 00.04",
        "# FW rev: 05.41",
        f"# Logger ID: {logger_id}",
        f"# Session No.: {session}",
        f"# Split No.: {split}",
        f"# Time: {datetime.fromtimestamp(int(start), timezone.utc):%Y%m%dT%H%M%S}",
        f'# Value separator: "{value_sep}"',
        f"# Time format: {time_format}",
        f'# Time separator: "{time_sep}"',
        f'# Time separator ms: "{ms_sep}"',
        f'# Date separator: "{date_sep}"',
        f'# Time and date separator: "{date_time_sep}"',
        f"# Bit-rate: {bitrate}",
        "# Silent mode: false",
        "# Cyclic mode: false",
        value_sep.join(columns),
    ]

    for msg in messages:
        seconds, ms = divmod(round(msg.timestamp * 1000), 1000)
        t = datetime.fromtimestamp(seconds, timezone.utc)

        # Time fields with the separator following each field, most significant first. Time format N includes the
        # N + 1 least significant fields.
        fields = [
            f"{t.year:04}{date_sep}",
            f"{t.month:02}{date_sep}",
            f"{t.day:02}{date_time_sep}",
            f"{t.hour:02}{time_sep}",
            f"{t.minute:02}{time_sep}",
            f"{t.second:02}{ms_sep}",
            f"{ms:03}",
        ]
        msg_type = (1 if msg.is_extended_id else 0) | (0 if msg.is_rx else 8)

        values = {
            "Timestamp": "".join(fields[6 - time_format :]),
            "Lost": "0",
            "Type": str(msg_type),
            "ID": f"{msg.arbitration_id:x}",
            "Length": f"{len(msg.data):x}",
            "Data": msg.data.hex(),
        }
        lines.append(value_sep.join(values[c] for c in columns))

    return "\n".join(lines) + "\n"
