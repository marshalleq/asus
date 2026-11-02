#!/usr/bin/env python3
"""Stream system info to the ROG Azoth (M701) OLED on Linux.

The keyboard shows its "System Information" screen while it is receiving
0x66 packets on the vendor HID interface (usage page 0xFF00), and goes back to
its animation when they stop. See ../PROTOCOL.md.

Usage:
  azoth_oled.py                       (CPU temp + GPU temp)
  azoth_oled.py --show cpu_temp --show gpu_usage --verbose
  azoth_oled.py --list-sensors

Works over the USB cable (0B05:1A83) and the ROG Omni Receiver dongle
(0B05:1ACE). It uses whichever connection the keyboard acknowledges on, and
re-checks when that connection stops answering (mode switch, sleep, unplug).
No third-party Python packages are needed. GPU sensors use NVIDIA's NVML
library (libnvidia-ml.so.1, installed with the NVIDIA driver).
"""
import argparse
import ctypes
import errno
import glob
import os
import select
import signal
import sys
import time

VID = 0x0B05
PID_USB = 0x1A83
PID_RECEIVER = 0x1ACE
# Cable: the vendor interface descriptor starts with Usage Page (Vendor 0xFF00).
USB_DESC_PREFIX = bytes([0x06, 0x00, 0xFF])
# Receiver: the keyboard channel is the 0xFF00 collection with Report ID 2.
RECEIVER_KB_COLLECTION = bytes([0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x02])
RECEIVER_REPORT_ID = 0x02
ACK_WAIT = 0.5          # seconds to wait for the keyboard's acknowledgement
MISSES_BEFORE_RESCAN = 3

DEFAULT_SHOW = [("cpu_temp", 1), ("gpu_temp", 1)]

# Sensor type byte -> (name, description). Confirmed on hardware: 0x01, 0x11.
# 0x00/0x10/0x30 come from the macOS driver and are not yet seen on screen.
SENSOR_TYPES = {
    "cpu_usage": 0x00,
    "cpu_temp": 0x01,
    "gpu_usage": 0x10,
    "gpu_temp": 0x11,
    "ram_usage": 0x30,
}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- packet

def build_packet(items):
    """items: list of (type_byte, label_index, value). Returns 64 bytes."""
    if not 1 <= len(items) <= 15:
        raise ValueError(f"need 1..15 items, got {len(items)}")
    buf = bytearray(64)
    buf[0] = 0x66
    buf[2] = 1 if len(items) == 2 else 0
    off = 4
    for typ, index, value in items:
        v = max(-32768, min(32767, int(round(value))))
        buf[off] = typ
        buf[off + 1] = index if index > 0 else 0xFF
        buf[off + 2:off + 4] = v.to_bytes(2, "little", signed=True)
        off += 4
    return bytes(buf)


# ---------------------------------------------------------------- device

def _hidraw_nodes(pid, match):
    """hidraw device paths for VID:pid whose report descriptor satisfies match()."""
    want = f"HID_ID=0003:{VID:08X}:{pid:08X}"
    found = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(node, "device", "uevent")) as f:
                if want not in f.read().upper():
                    continue
            with open(os.path.join(node, "device", "report_descriptor"), "rb") as f:
                desc = f.read()
        except OSError:
            continue
        if match(desc):
            found.append("/dev/" + os.path.basename(node))
    return found


def find_links():
    """All present connections to the keyboard, cable first."""
    links = []
    for kind, pid, match, prefix in (
            ("USB cable", PID_USB, lambda d: d.startswith(USB_DESC_PREFIX), b""),
            ("Omni receiver", PID_RECEIVER, lambda d: RECEIVER_KB_COLLECTION in d,
             bytes([RECEIVER_REPORT_ID]))):
        paths = _hidraw_nodes(pid, match)
        if len(paths) > 1:
            sys.exit(f"More than one {kind} found ({', '.join(paths)}). "
                     "This script supports one; unplug the extra device.")
        if paths:
            links.append(Link(kind, paths[0], prefix))
    return links


class Link:
    """One path to the keyboard: the cable interface or the receiver channel."""

    def __init__(self, kind, path, prefix):
        self.kind, self.path, self.prefix = kind, path, prefix
        # Cable sends the 64-byte command as-is; the receiver puts the report
        # ID first and carries 63 bytes of command.
        self.length = 64 - len(prefix)
        self.ack = prefix + b"\x66"
        self.fd = None

    def __str__(self):
        return f"{self.kind} ({self.path})"

    def open(self):
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            sys.exit(f"No permission to open {self.path}. Install linux/70-rog-azoth.rules "
                     "(see linux/README.md) and log in at the machine's desktop.")

    def send(self, pkt):
        """Write the packet; return True if the keyboard has acknowledged.

        Through the receiver a waking keyboard can ack well after ACK_WAIT
        (the receiver answers ff aa until it reaches the keyboard), so an ack
        that arrived late for the previous packet also counts.
        """
        late_ack = self._drain(0)
        os.write(self.fd, self.prefix + pkt[:self.length])
        return self._drain(ACK_WAIT, stop_on_ack=True) or late_ack

    def _drain(self, wait, stop_on_ack=False):
        acked = False
        end = time.monotonic() + wait
        while select.select([self.fd], [], [], max(0.0, end - time.monotonic()))[0]:
            try:
                reply = os.read(self.fd, 64)
            except BlockingIOError:
                break
            if reply.startswith(self.ack):
                acked = True
                if stop_on_ack:
                    break
        return acked

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


GONE = (errno.ENODEV, errno.EIO, errno.ENOENT, errno.EPIPE)


# ---------------------------------------------------------------- sensors

class CpuTemp:
    """AMD k10temp Tctl, in degrees C."""

    def __init__(self):
        self.path = None
        for hw in glob.glob("/sys/class/hwmon/hwmon*"):
            try:
                with open(os.path.join(hw, "name")) as f:
                    if f.read().strip() != "k10temp":
                        continue
            except OSError:
                continue
            for lab in glob.glob(os.path.join(hw, "temp*_label")):
                with open(lab) as f:
                    if f.read().strip() == "Tctl":
                        self.path = lab.replace("_label", "_input")
        if not self.path:
            sys.exit("cpu_temp: no k10temp 'Tctl' sensor in /sys/class/hwmon. "
                     "Is the k10temp module loaded (modprobe k10temp)?")

    def read(self):
        with open(self.path) as f:
            return int(f.read()) / 1000.0


class CpuUsage:
    """Whole-CPU busy percent from /proc/stat, averaged since the last read."""

    def __init__(self):
        self.prev = self._sample()

    @staticmethod
    def _sample():
        with open("/proc/stat") as f:
            vals = [int(x) for x in f.readline().split()[1:]]
        idle = vals[3] + vals[4]  # idle + iowait
        return sum(vals), idle

    def read(self):
        total, idle = self._sample()
        dt, di = total - self.prev[0], idle - self.prev[1]
        self.prev = (total, idle)
        return 0.0 if dt <= 0 else 100.0 * (dt - di) / dt


class RamUsage:
    def read(self):
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0])
        return 100.0 * (info["MemTotal"] - info["MemAvailable"]) / info["MemTotal"]


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class Nvml:
    """Minimal NVML binding for one GPU."""

    def __init__(self, gpu_index):
        try:
            self.lib = ctypes.CDLL("libnvidia-ml.so.1")
        except OSError:
            sys.exit("gpu sensors: libnvidia-ml.so.1 not found. It ships with the "
                     "NVIDIA driver; check the driver is installed (nvidia-smi).")
        self._check(self.lib.nvmlInit_v2(), "nvmlInit")
        self.handle = ctypes.c_void_p()
        self._check(self.lib.nvmlDeviceGetHandleByIndex_v2(
            ctypes.c_uint(gpu_index), ctypes.byref(self.handle)),
            f"open GPU {gpu_index}")

    def _check(self, rc, what):
        if rc != 0:
            self.lib.nvmlErrorString.restype = ctypes.c_char_p
            sys.exit(f"NVML {what} failed: {self.lib.nvmlErrorString(rc).decode()}")

    def temp(self):
        t = ctypes.c_uint()
        self._check(self.lib.nvmlDeviceGetTemperature(
            self.handle, ctypes.c_int(0), ctypes.byref(t)), "temperature")
        return float(t.value)

    def usage(self):
        u = _NvmlUtilization()
        self._check(self.lib.nvmlDeviceGetUtilizationRates(
            self.handle, ctypes.byref(u)), "utilization")
        return float(u.gpu)


def make_readers(names, gpu_index):
    nvml = None
    readers = {}
    for name in names:
        if name in readers:
            continue
        if name == "cpu_temp":
            readers[name] = CpuTemp().read
        elif name == "cpu_usage":
            readers[name] = CpuUsage().read
        elif name == "ram_usage":
            readers[name] = RamUsage().read
        elif name in ("gpu_temp", "gpu_usage"):
            nvml = nvml or Nvml(gpu_index)
            readers[name] = nvml.temp if name == "gpu_temp" else nvml.usage
    return readers


# ---------------------------------------------------------------- main

def parse_show(s):
    name, _, idx = s.partition(":")
    if name not in SENSOR_TYPES:
        raise argparse.ArgumentTypeError(
            f"unknown sensor {name!r}; choose from {', '.join(SENSOR_TYPES)}")
    return name, int(idx) if idx else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", action="append", type=parse_show, default=[],
                    metavar="SENSOR[:LABEL]",
                    help="sensor to display (1 = single layout, 2 = dual). "
                         "LABEL is the number shown after the name, default 1. "
                         "Without --show: cpu_temp and gpu_temp.")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between updates")
    ap.add_argument("--gpu-index", type=int, default=0, help="NVML GPU index")
    ap.add_argument("--list-sensors", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    if a.list_sensors:
        for n, t in SENSOR_TYPES.items():
            print(f"{n:10s} type 0x{t:02x}")
        return
    a.show = a.show or DEFAULT_SHOW
    if len(a.show) > 2:
        ap.error("give one or two --show options")

    readers = make_readers([n for n, _ in a.show], a.gpu_index)

    stop = False

    def on_signal(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    def send_on(link, pkt):
        """Send on an open link; True if acked, False if not, None if it vanished."""
        try:
            return link.send(pkt)
        except OSError as e:
            if e.errno in GONE:
                return None
            raise

    current = None      # the link the keyboard is answering on
    candidates = {}     # path -> open Link, while looking for the keyboard
    misses = 0
    waiting_logged = False
    while not stop:
        started = time.monotonic()
        items = [(SENSOR_TYPES[n], idx, readers[n]()) for n, idx in a.show]
        pkt = build_packet(items)

        if current is not None:
            ok = send_on(current, pkt)
            if ok:
                misses = 0
            else:
                misses += 1
                if ok is None or misses >= MISSES_BEFORE_RESCAN:
                    log(f"Keyboard stopped answering on {current}.")
                    current.close()
                    current = None

        if current is None:
            # Keep candidates open between rescans so a late ack isn't lost.
            present = {link.path: link for link in find_links()}
            for path in list(candidates):
                if path not in present:
                    candidates.pop(path).close()
            for path, link in present.items():
                if path not in candidates:
                    link.open()
                    candidates[path] = link
            for path, link in list(candidates.items()):
                ok = send_on(link, pkt)
                if ok is None:
                    candidates.pop(path).close()
                elif ok:
                    current, misses, waiting_logged = candidates.pop(path), 0, False
                    log(f"Connected via {current}")
                    break
            if current is not None:
                for link in candidates.values():
                    link.close()
                candidates = {}
            elif not waiting_logged:
                log("Waiting for the ROG Azoth (USB cable 0B05:1A83 or "
                    "Omni receiver 0B05:1ACE with the keyboard on 2.4 GHz)...")
                waiting_logged = True

        if a.verbose and current is not None:
            log("  ".join(f"{n}={v:.0f}" for (n, _), (_, _, v) in zip(a.show, items)))
        time.sleep(max(0.0, a.interval - (time.monotonic() - started)))

    if current is not None:
        current.close()


if __name__ == "__main__":
    main()
