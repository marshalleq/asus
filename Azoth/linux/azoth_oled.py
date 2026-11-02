#!/usr/bin/env python3
"""Stream system info to the ROG Azoth (M701) OLED on Linux.

The keyboard shows its "System Information" screen while it is receiving
0x66 packets on the vendor HID interface (usage page 0xFF00) and keeps the
last frame when they stop. See ../PROTOCOL.md.

Usage:
  azoth_oled.py                       (CPU temp + GPU temp)
  azoth_oled.py --show cpu_temp --show gpu_usage --verbose
  azoth_oled.py --show cpu_fan --hwmon cpu_fan=nct6799/fan2
  azoth_oled.py --list-sensors | --list-gpus | --list-hwmon

Works over the USB cable (0B05:1A83) and the ROG Omni Receiver dongle
(0B05:1ACE). It uses whichever connection the keyboard acknowledges on, and
re-checks when that connection stops answering (mode switch, sleep, unplug).

Sensors come from standard Linux interfaces: /proc, cpufreq, the CPU's hwmon
driver (k10temp, zenpower, coretemp), amdgpu sysfs, and NVIDIA's NVML library
(libnvidia-ml.so.1, part of the NVIDIA driver). Board-specific sensors (fans,
voltages, VRM) are mapped with --hwmon. No third-party Python packages.
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

# Sensor name -> type byte. All checked on the keyboard; see ../PROTOCOL.md.
# High nibble = device (0 CPU, 1 GPU, 2 VRM, 3 DRAM, 4 chassis fan),
# low nibble = metric (0 usage %, 1 temp C, 2 freq MHz, 3/4 fan RPM, 5 volt mV).
SENSOR_TYPES = {
    "cpu_usage": 0x00,
    "cpu_temp": 0x01,
    "cpu_freq": 0x02,
    "cpu_fan": 0x03,
    "cpu_opt_fan": 0x04,
    "cpu_volt": 0x05,
    "gpu_usage": 0x10,
    "gpu_temp": 0x11,
    "gpu_freq": 0x12,
    "gpu_volt": 0x15,
    "vrm_temp": 0x21,
    "ram_usage": 0x30,
    "ram_freq": 0x32,
    "ram_volt": 0x35,
    "chassis_fan": 0x43,
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
            sys.exit(f"No permission to open {self.path}. Install Azoth/linux/70-rog-azoth.rules "
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
#
# Every sensor is found from standard Linux interfaces (/proc, cpufreq,
# hwmon, amdgpu sysfs, NVIDIA NVML). Motherboard sensors (fans, voltages,
# VRM) have board-specific input names, so they are mapped explicitly with
# --hwmon. Anything that can't be found stops the daemon with a message.

HWMON_ROOT = "/sys/class/hwmon"

# Sensors found automatically. The rest need --hwmon SENSOR=CHIP/INPUT.
AUTO_SENSORS = ("cpu_usage", "cpu_temp", "cpu_freq", "ram_usage",
                "gpu_usage", "gpu_temp", "gpu_freq", "gpu_volt")


class SensorError(Exception):
    pass


def _read(path):
    with open(path) as f:
        return f.read().strip()


# ---- hwmon

def hwmon_chips():
    """[(chip name, hwmonN dir)] for every hwmon device, in hwmonN order."""
    chips = []
    for d in sorted(glob.glob(os.path.join(HWMON_ROOT, "hwmon*")),
                    key=lambda p: int(p.rsplit("hwmon", 1)[1])):
        try:
            chips.append((_read(os.path.join(d, "name")), d))
        except OSError:
            continue
    return chips


def hwmon_inputs(d):
    """{input stem: label or None} for one hwmon dir, e.g. {'temp1': 'Tctl'}."""
    out = {}
    for p in sorted(glob.glob(os.path.join(d, "*_input"))):
        stem = os.path.basename(p)[:-len("_input")]
        try:
            out[stem] = _read(os.path.join(d, stem + "_label"))
        except OSError:
            out[stem] = None
    return out


# hwmon sysfs units -> what the keyboard expects
_HWMON_SCALE = {"temp": 0.001,   # millidegree C -> C
                "in": 1.0,       # mV -> mV
                "fan": 1.0,      # RPM -> RPM
                "freq": 1e-6}    # Hz -> MHz


def hwmon_reader(d, stem):
    kind = stem.rstrip("0123456789")
    if kind not in _HWMON_SCALE:
        raise SensorError(f"{d}/{stem}: unsupported hwmon input type {kind!r} "
                          f"(supported: {', '.join(_HWMON_SCALE)})")
    path = os.path.join(d, stem + "_input")
    scale = _HWMON_SCALE[kind]
    return lambda: int(_read(path)) * scale


def resolve_hwmon(spec):
    """'CHIP/INPUT' -> reader. CHIP is a hwmon name (nct6799) or hwmonN;
    INPUT is an input (fan2, temp1, in0) or its label ('CPU Fan')."""
    chip, sep, ident = spec.partition("/")
    if not sep or not chip or not ident:
        raise SensorError(f"--hwmon source {spec!r}: expected CHIP/INPUT, "
                          "e.g. nct6799/fan2 (see --list-hwmon)")
    chips = [(n, d) for n, d in hwmon_chips() if chip in (n, os.path.basename(d))]
    if not chips:
        raise SensorError(f"--hwmon: no hwmon chip called {chip!r}. Run --list-hwmon "
                          "to see the chips on this machine.")
    if len(chips) > 1:
        raise SensorError(f"--hwmon: {len(chips)} chips are called {chip!r} "
                          f"({', '.join(os.path.basename(d) for _, d in chips)}); "
                          "use the hwmonN name instead.")
    _, d = chips[0]
    inputs = hwmon_inputs(d)
    if ident in inputs:
        return hwmon_reader(d, ident)
    by_label = [s for s, lab in inputs.items() if lab == ident]
    if len(by_label) == 1:
        return hwmon_reader(d, by_label[0])
    raise SensorError(f"--hwmon: {chip} has no input or label {ident!r}. Run "
                      "--list-hwmon to see its inputs.")


# ---- CPU and memory

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


def ram_usage():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
    return 100.0 * (info["MemTotal"] - info["MemAvailable"]) / info["MemTotal"]


def cpu_freq_reader():
    """Highest current core clock in MHz, from cpufreq."""
    paths = glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")
    if not paths:
        raise SensorError("cpu_freq: no cpufreq data in /sys/devices/system/cpu "
                          "(no cpufreq driver loaded).")
    return lambda: max(int(_read(p)) for p in paths) / 1000.0  # kHz -> MHz


def cpu_temp_reader():
    """CPU temperature from the CPU's own hwmon driver.

    AMD k10temp: Tdie if the driver shows it, otherwise Tctl. On older Ryzen
    and Threadripper parts Tctl carries a fixed offset for fan control and the
    driver adds Tdie (the real die temperature); on parts without an offset
    only Tctl exists and it is the die temperature.
    AMD zenpower (out-of-tree replacement for k10temp): Tdie.
    Intel coretemp: the hottest "Package id N".
    """
    chips = hwmon_chips()
    names = [n for n, _ in chips]
    for name, d in chips:
        inputs = hwmon_inputs(d)
        labels = {lab: s for s, lab in inputs.items() if lab}
        if name == "k10temp":
            stem = labels.get("Tdie") or labels.get("Tctl")
            if stem:
                return hwmon_reader(d, stem)
        elif name == "zenpower" and "Tdie" in labels:
            return hwmon_reader(d, labels["Tdie"])
        elif name == "coretemp":
            pkgs = [hwmon_reader(d, s) for lab, s in labels.items()
                    if lab.startswith("Package id")]
            if pkgs:
                return lambda: max(r() for r in pkgs)
    raise SensorError("cpu_temp: no CPU temperature driver found (looked for "
                      "k10temp, zenpower, coretemp; this machine has: "
                      f"{', '.join(names) or 'no hwmon chips'}). Load the driver, "
                      "or map it with --hwmon cpu_temp=CHIP/INPUT.")


# ---- GPUs

class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _NvmlMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong)]


class Nvml:
    """Minimal NVML binding (libnvidia-ml.so.1 ships with the NVIDIA driver)."""

    def __init__(self):
        try:
            self.lib = ctypes.CDLL("libnvidia-ml.so.1")
        except OSError:
            raise SensorError("An NVIDIA GPU is installed but libnvidia-ml.so.1 can't "
                              "be loaded. Install the NVIDIA driver (check nvidia-smi).")
        self.lib.nvmlErrorString.restype = ctypes.c_char_p
        self.check(self.lib.nvmlInit_v2(), "nvmlInit")

    def check(self, rc, what):
        if rc != 0:
            raise SensorError(f"NVML {what} failed: {self.lib.nvmlErrorString(rc).decode()}")

    def handles(self):
        n = ctypes.c_uint()
        self.check(self.lib.nvmlDeviceGetCount_v2(ctypes.byref(n)), "device count")
        out = []
        for i in range(n.value):
            h = ctypes.c_void_p()
            self.check(self.lib.nvmlDeviceGetHandleByIndex_v2(ctypes.c_uint(i),
                                                              ctypes.byref(h)), f"GPU {i}")
            out.append(h)
        return out

    def uint(self, fn, h, *args):
        v = ctypes.c_uint()
        self.check(getattr(self.lib, fn)(h, *args, ctypes.byref(v)), fn)
        return v.value


class NvidiaGpu:
    def __init__(self, nvml, handle):
        self.nvml, self.h = nvml, handle
        buf = ctypes.create_string_buffer(96)
        nvml.check(nvml.lib.nvmlDeviceGetName(handle, buf, ctypes.c_uint(96)), "name")
        mem = _NvmlMemory()
        nvml.check(nvml.lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)), "memory")
        self.name, self.vram = buf.value.decode(), mem.total

    def reader(self, metric):
        n, h = self.nvml, self.h
        if metric == "temp":
            return lambda: float(n.uint("nvmlDeviceGetTemperature", h, ctypes.c_int(0)))
        if metric == "freq":
            return lambda: float(n.uint("nvmlDeviceGetClockInfo", h, ctypes.c_int(0)))
        if metric == "usage":
            def usage():
                u = _NvmlUtilization()
                n.check(n.lib.nvmlDeviceGetUtilizationRates(h, ctypes.byref(u)), "utilization")
                return float(u.gpu)
            return usage
        raise SensorError(f"gpu_{metric}: NVIDIA's driver doesn't report GPU {metric} "
                          "on Linux.")


class AmdGpu:
    """amdgpu sysfs: gpu_busy_percent and the card's hwmon (edge, sclk, vddgfx)."""

    def __init__(self, card):
        self.dev = os.path.join(card, "device")
        slot = os.path.basename(os.path.realpath(self.dev))
        self.name = f"AMD GPU {_read(self.dev + '/vendor')[2:]}:" \
                    f"{_read(self.dev + '/device')[2:]} at {slot}"
        try:
            self.vram = int(_read(self.dev + "/mem_info_vram_total"))
        except OSError:
            self.vram = 0
        hw = glob.glob(self.dev + "/hwmon/hwmon*")
        self.hwmon = hw[0] if hw else None

    def _hwmon_label(self, label, metric):
        if self.hwmon:
            for stem, lab in hwmon_inputs(self.hwmon).items():
                if lab == label:
                    return hwmon_reader(self.hwmon, stem)
        raise SensorError(f"gpu_{metric}: {self.name} has no '{label}' hwmon input.")

    def reader(self, metric):
        if metric == "usage":
            p = self.dev + "/gpu_busy_percent"
            if not os.path.exists(p):
                raise SensorError(f"gpu_usage: {self.name} has no gpu_busy_percent.")
            return lambda: float(_read(p))
        label = {"temp": "edge", "freq": "sclk", "volt": "vddgfx"}[metric]
        return self._hwmon_label(label, metric)


def _pci_has_nvidia_gpu():
    for d in glob.glob("/sys/bus/pci/devices/*"):
        try:
            if _read(d + "/vendor") == "0x10de" and _read(d + "/class").startswith("0x03"):
                return True
        except OSError:
            continue
    return False


def list_gpus():
    """GPUs the daemon can read, in a stable order: NVIDIA (NVML order), then AMD."""
    gpus = []
    if _pci_has_nvidia_gpu():
        nvml = Nvml()
        gpus += [NvidiaGpu(nvml, h) for h in nvml.handles()]
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        if "-" in os.path.basename(card):
            continue  # connector, not a card
        drv = os.path.realpath(os.path.join(card, "device", "driver"))
        if os.path.basename(drv) == "amdgpu":
            gpus.append(AmdGpu(card))
    return gpus


def pick_gpu(gpus, index):
    """--gpu N, or the GPU with the most dedicated VRAM (so a discrete card is
    picked over integrated graphics)."""
    if not gpus:
        raise SensorError("No supported GPU found (NVIDIA with the NVIDIA driver, or "
                          "AMD with amdgpu).")
    if index is not None:
        if not 0 <= index < len(gpus):
            raise SensorError(f"--gpu {index}: there are {len(gpus)} GPUs "
                              "(see --list-gpus).")
        return gpus[index]
    best = max(g.vram for g in gpus)
    top = [g for g in gpus if g.vram == best]
    if len(top) > 1:
        raise SensorError("Several GPUs have the same amount of VRAM; choose one with "
                          "--gpu N (see --list-gpus).")
    return top[0]


# ---- wiring

def make_readers(show, gpu_index, hwmon_map):
    """{(name, label_index): reader} for the --show items."""
    readers = {}
    gpu = None
    for name, idx in show:
        key = (name, idx)
        if key in hwmon_map:
            readers[key] = resolve_hwmon(hwmon_map[key])
        elif name == "cpu_usage":
            readers[key] = CpuUsage().read
        elif name == "cpu_temp":
            readers[key] = cpu_temp_reader()
        elif name == "cpu_freq":
            readers[key] = cpu_freq_reader()
        elif name == "ram_usage":
            readers[key] = ram_usage
        elif name.startswith("gpu_"):
            if gpu is None:
                gpu = pick_gpu(list_gpus(), gpu_index)
                log(f"GPU: {gpu.name}")
            readers[key] = gpu.reader(name[4:])
        else:
            raise SensorError(
                f"{name}: board-specific sensor; map it to a hwmon input with "
                f"--hwmon {name}{'' if idx == 1 else ':' + str(idx)}=CHIP/INPUT "
                "(see --list-hwmon).")
    return readers


def print_hwmon():
    for name, d in hwmon_chips():
        print(f"{os.path.basename(d)}  {name}")
        for stem, lab in hwmon_inputs(d).items():
            try:
                raw = _read(os.path.join(d, stem + "_input"))
            except OSError:
                raw = "?"
            print(f"    {stem:8s} {lab or '':20s} raw={raw}")


def print_gpus():
    gpus = list_gpus()
    if not gpus:
        print("No supported GPU found.")
    for i, g in enumerate(gpus):
        print(f"{i}: {g.name}  VRAM {g.vram // (1024 * 1024)} MiB")


# ---------------------------------------------------------------- main

def parse_show(s):
    name, _, idx = s.partition(":")
    if name not in SENSOR_TYPES:
        raise argparse.ArgumentTypeError(
            f"unknown sensor {name!r}; choose from {', '.join(SENSOR_TYPES)}")
    return name, int(idx) if idx else 1


def parse_hwmon(s):
    left, sep, spec = s.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"--hwmon {s!r}: expected SENSOR=CHIP/INPUT")
    return parse_show(left), spec


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", action="append", type=parse_show, default=[],
                    metavar="SENSOR[:LABEL]",
                    help="sensor to display (1 = single layout, 2 = dual). "
                         "LABEL is the number shown after the name, default 1. "
                         "Without --show: cpu_temp and gpu_temp.")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between updates")
    ap.add_argument("--gpu", type=int, metavar="N",
                    help="GPU to read (see --list-gpus). Default: the one with the "
                         "most dedicated VRAM.")
    ap.add_argument("--hwmon", action="append", type=parse_hwmon, default=[],
                    metavar="SENSOR[:LABEL]=CHIP/INPUT",
                    help="read SENSOR from a hwmon input, e.g. cpu_fan=nct6799/fan2 or "
                         "chassis_fan:2=nct6799/fan3 (see --list-hwmon). Needed for "
                         "fans, voltages and VRM; also overrides automatic sources.")
    ap.add_argument("--list-sensors", action="store_true", help="sensors the keyboard shows")
    ap.add_argument("--list-gpus", action="store_true", help="GPUs this machine has")
    ap.add_argument("--list-hwmon", action="store_true", help="hwmon chips and inputs")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    try:
        if a.list_sensors:
            for n, t in SENSOR_TYPES.items():
                src = "automatic" if n in AUTO_SENSORS else "needs --hwmon"
                print(f"{n:12s} type 0x{t:02x}  {src}")
            return
        if a.list_gpus:
            print_gpus()
            return
        if a.list_hwmon:
            print_hwmon()
            return
        a.show = a.show or DEFAULT_SHOW
        if len(a.show) > 2:
            ap.error("give one or two --show options")
        hwmon_map = dict(a.hwmon)
        unused = set(hwmon_map) - set(a.show)
        if unused:
            ap.error("--hwmon given for sensors that aren't in --show: "
                     + ", ".join(n if i == 1 else f"{n}:{i}" for n, i in sorted(unused)))
        readers = make_readers(a.show, a.gpu, hwmon_map)
        for key, read in readers.items():
            read()  # fail now, with a clear message, rather than in the loop
    except SensorError as e:
        sys.exit(str(e))

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
        items = [(SENSOR_TYPES[n], idx, readers[(n, idx)]()) for n, idx in a.show]
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
