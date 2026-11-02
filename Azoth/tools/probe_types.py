#!/usr/bin/env python3
"""Show OLED sensor types on the Azoth one screen at a time, to read labels/units.

Uses the connection logic from linux/azoth_oled.py (cable or Omni receiver).

  probe_types.py --check                 send one frame, report whether it was acked
  probe_types.py --hold 6 0x02:4501,0x05:1252 0x03:1803,0x04:1404 ...

Each positional argument is one screen: up to two TYPE:VALUE items separated
by a comma. Label index is 1 for every item.
"""
import argparse
import importlib.util
import os
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "azoth_oled", os.path.join(here, "..", "linux", "azoth_oled.py"))
azoth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(azoth)


def connect():
    """Return an open Link the keyboard acks on, trying for up to 10 s."""
    pkt = azoth.build_packet([(0x01, 1, 0)])
    links = azoth.find_links()
    if not links:
        sys.exit("No Azoth cable interface or Omni receiver found.")
    for link in links:
        link.open()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        for link in links:
            if link.send(pkt):
                for other in links:
                    if other is not link:
                        other.close()
                return link
        time.sleep(1)
    sys.exit("Found " + ", ".join(map(str, links)) + " but the keyboard did not "
             "acknowledge in 10 s. Press a key to wake it and try again.")


def parse_screen(s):
    items = []
    for part in s.split(","):
        t, v = part.split(":")
        items.append((int(t, 0), 1, int(v, 0)))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--hold", type=float, default=6.0, help="seconds per screen")
    ap.add_argument("screens", nargs="*", type=parse_screen)
    a = ap.parse_args()

    link = connect()
    print(f"Keyboard answering via {link}", flush=True)
    if a.check:
        cpu = azoth.cpu_temp_reader()()
        gpu = azoth.pick_gpu(azoth.list_gpus(), None).reader("temp")()
        link.send(azoth.build_packet([(0x01, 1, cpu), (0x11, 1, gpu)]))
        print(f"Sent live CPU {cpu:.0f} C, GPU {gpu:.0f} C", flush=True)
        return
    for n, items in enumerate(a.screens, 1):
        pkt = azoth.build_packet(items)
        acks = 0
        end = time.monotonic() + a.hold
        while time.monotonic() < end:
            acks += bool(link.send(pkt))
            time.sleep(1)
        shown = ", ".join(f"0x{t:02x}={v}" for t, _, v in items)
        print(f"screen {n}: {shown}  (acked {acks}x)", flush=True)


if __name__ == "__main__":
    main()
