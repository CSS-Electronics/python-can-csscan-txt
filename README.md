# python-can-csscan-txt

Read TXT log files from the [CSS Electronics](https://www.csselectronics.com/) CLX000 CAN loggers through [python-can](https://python-can.readthedocs.io/).

```
pip install python-can-csscan-txt
```

```python
import can

with can.LogReader("00000001.TXT") as reader:
    for msg in reader:
        print(msg)
```

The logger information from the file header is available through `LogFile`:

```python
from python_can_csscan_txt import LogFile

with LogFile("00000001.TXT") as reader:
    print(reader.get_metadata())  # id, hardware/firmware revision, session, split, bitrate
    for msg in reader:
        print(msg)
```
