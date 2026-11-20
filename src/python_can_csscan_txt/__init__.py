"""Read TXT log files from the CSS Electronics CLX000 (CL1000/CL2000/CL3000) through python-can."""

import calendar
import io
import logging
import os
from collections.abc import Generator
from typing import Any, TextIO

from can import Message
from can.io.generic import TextIOMessageReader
from can.typechecking import StringPathLike

__all__ = ["LogFile"]

logger = logging.getLogger(__name__)

_COLUMNS = {"Timestamp", "Lost", "Type", "ID", "Length", "Data"}

# Seconds after which the timestamp wraps around, for time formats 0 (kkk), 1 (sskkk), 2 (mmsskkk) and 3 (hhmmsskkk).
# Format 4 (DDhhmmsskkk) wraps every month and format 5 (MMDDhhmmsskkk) every year. Format 6 holds the full date.
_WRAP_SECONDS = {0: 1, 1: 60, 2: 60 * 60, 3: 24 * 60 * 60}


class LogFile(TextIOMessageReader):
    """Iterate over the CAN messages of a CLX000 TXT log file.

    :param file: Path to a log file, or an open file.
    :param kwargs: Ignored. Accepted for compatibility with ``can.LogReader``.
    """

    def __init__(self, file: StringPathLike | TextIO | io.BufferedIOBase, **kwargs: Any) -> None:
        if isinstance(file, (io.RawIOBase, io.BufferedIOBase)):
            file = io.TextIOWrapper(file, encoding="latin-1")
        super().__init__(file, mode="r", encoding="latin-1")

        try:
            self._read_header()
        except Exception:
            if isinstance(file, (str, os.PathLike)):  # Only close the file if opened here
                self.file.close()
            raise

    def _read_header(self) -> None:
        # Header: lines starting with "#", formatted as "# Key: value"
        self._header: dict[str, str] = {}
        line = self.file.readline()
        while line.startswith("#"):
            key, _, value = line.lstrip("#").strip().partition(": ")
            self._header[key] = value
            line = self.file.readline()

        # Column header follows the header, e.g. "Timestamp;Type;ID;Data"
        self._separator = self._quoted("Value separator")
        self._columns = self._split(line.rstrip("\r\n"))
        if "Logger type" not in self._header or not self._separator or not set(self._columns) <= _COLUMNS:
            raise ValueError("Not a CLX000 TXT log file")

        self._time_format = int(_digits(self._header.get("Time format", "")) or 0)

        # Start time from the header ("YYYYMMDDThhmmss"). Timestamps in the log only hold the least significant part of
        # the time (depending on the time format), the remaining digits are taken from the start time.
        start = _digits(self._header.get("Time", ""))  # "YYYYMMDDhhmmss"
        self._time_template = start + "19700101000000000"[len(start) :]  # "YYYYMMDDhhmmsskkk"

    def get_metadata(self) -> dict[str, Any]:
        """Return the logger information stored in the file header."""
        return {
            "id": self._header.get("Logger ID", ""),
            "hardware revision": self._header.get("HW rev", ""),
            "firmware revision": self._header.get("FW rev", ""),
            "session": int(self._header.get("Session No.", "0") or 0),
            "split": int(self._header.get("Split No.", "0") or 0),
            "bitrate": int(self._header.get("Bit-rate", "0") or 0),
        }

    def __iter__(self) -> Generator[Message, None, None]:
        wraps = 0
        parsed_previous = self._parse_timestamp("", 0)  # The header start time
        emitted_previous = 0.0

        for line in self.file:
            line = line.rstrip("\r\n")
            fields = self._split(line)
            if len(fields) != len(self._columns):
                logger.warning("Skipping invalid line: %r", line)  # E.g. cut short by a power loss
                continue
            row = dict(zip(self._columns, fields))

            try:
                timestamp = 0.0
                if "Timestamp" in row:
                    timestamp = self._parse_timestamp(row["Timestamp"], wraps)
                    # A step back in time means the timestamp wrapped around. The CLX000 occasionally logs a timestamp
                    # up to one second too late, so a step back of up to one second is not a wrap. Format 0 does not
                    # show seconds, so there any step back is a wrap.
                    tolerance = 0 if self._time_format == 0 else 1
                    if timestamp < parsed_previous - tolerance and self._time_format in range(6):
                        wraps += 1
                        timestamp = self._parse_timestamp(row["Timestamp"], wraps)
                    parsed_previous = timestamp

                msg_type = int(row.get("Type") or 0)
                message = Message(
                    timestamp=timestamp,
                    arbitration_id=int(row.get("ID") or "0", 16),
                    is_extended_id=bool(msg_type & 0x1),
                    is_rx=not msg_type & 0x8,
                    data=bytes.fromhex(row.get("Data", "")),
                )
            except ValueError:
                logger.warning("Skipping invalid line: %r", line)
                continue

            # After a timestamp that was logged too late, skip the following messages until time has caught up
            if timestamp < emitted_previous:
                logger.warning("Skipping non-increasing timestamp: %f", timestamp)
                continue
            emitted_previous = timestamp

            yield message

    def _quoted(self, key: str) -> str:
        """Header separator values are quoted, e.g. '";"'. Empty when not set."""
        value = self._header.get(key, "")
        return value[1] if len(value) == 3 else ""

    def _split(self, line: str) -> list[str]:
        return line.split(self._separator)

    def _parse_timestamp(self, text: str, wraps: int) -> float:
        # The log only holds the least significant digits of the time, the rest are taken from the header start time
        digits = _digits(text)
        full = self._time_template[: len(self._time_template) - len(digits)] + digits
        year = int(full[0:4])
        month, day, hour, minute, second = (int(full[i : i + 2]) for i in (4, 6, 8, 10, 12))

        if self._time_format == 4:
            year, month = year + (month - 1 + wraps) // 12, (month - 1 + wraps) % 12 + 1
        elif self._time_format == 5:
            year += wraps

        # timegm accepts days beyond the end of the month (e.g. 31 February), rolling over into the next month
        seconds = calendar.timegm((year, month, day, hour, minute, second))
        return seconds + _WRAP_SECONDS.get(self._time_format, 0) * wraps + int(full[14:17]) / 1000


def _digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())
