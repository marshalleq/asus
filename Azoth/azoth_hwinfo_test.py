#!/usr/bin/env python3
"""Test sender for the ROG Azoth OLED system-info (0x66) packet.

Works on macOS and Linux with the `hidapi` Python package (pip install hidapi).

  python3 azoth_hwinfo_test.py --list
  python3 azoth_hwinfo_test.py --path <path from --list> --item 0x01:0:42
  python3 azoth_hwinfo_test.py --path <path> --item 0x01:0:42 --item 0x11:1:55 --repeat 10

--item is TYPE:SLOT:VALUE. Types seen so far: 0x00 CPU usage %, 0x01 CPU temp C,
0x10 GPU usage %, 0x30 RAM usage %. 0x11 (GPU temp) is a guess to be tested.
"""
import argparse
import sys
import time

import hid

VID = 0x0B05
PID_USB = 0x1A83


def build_packet(items):
    if not 1 <= len(items) <= 15:
        sys.exit(f"need 1..15 items, got {len(items)}")
    buf = bytearray(64)
    buf[0] = 0x66
    buf[2] = 1 if len(items) == 2 else 0
    off = 4
    for typ, slot, value in items:
        buf[off] = typ
        buf[off + 1] = slot if slot > 0 else 0xFF
        buf[off + 2:off + 4] = int(value).to_bytes(2, "little", signed=True)
        off += 4
    return bytes(buf)


def parse_item(s):
    try:
        t, slot, v = s.split(":")
        return int(t, 0), int(slot, 0), int(v, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad --item {s!r}, want TYPE:SLOT:VALUE")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list Azoth HID interfaces")
    ap.add_argument("--path", help="HID path of the interface to write to")
    ap.add_argument("--item", action="append", type=parse_item, default=[])
    ap.add_argument("--repeat", type=int, default=1, help="send N times, 1 s apart")
    a = ap.parse_args()

    devs = hid.enumerate(VID, PID_USB)
    if a.list:
        if not devs:
            sys.exit("No ROG Azoth (0B05:1A83) found. Plug it in with the USB cable.")
        for d in devs:
            print(f"path={d['path'].decode()}  iface={d['interface_number']}  "
                  f"usage_page=0x{d['usage_page']:04x} usage=0x{d['usage']:04x}")
        return

    if not a.path or not a.item:
        sys.exit("need --path and at least one --item (run --list first)")

    pkt = build_packet(a.item)
    dev = hid.device()
    dev.open_path(a.path.encode())
    try:
        for i in range(a.repeat):
            n = dev.write(pkt)
            print(f"wrote {n} bytes: {pkt[:4 + 4 * len(a.item)].hex(' ')}")
            reply = dev.read(64, 300)
            if reply:
                print(f"  reply: {bytes(reply).hex(' ')}")
            if i + 1 < a.repeat:
                time.sleep(1)
    finally:
        dev.close()


if __name__ == "__main__":
    main()
