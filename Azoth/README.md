# ROG Azoth (M701)

The Azoth's OLED can show live system information (CPU and GPU temperature,
usage, and more) when the computer streams it to the keyboard. On Windows,
Armoury Crate does this. This folder has the protocol and a Linux daemon that
does the same.

- [linux/](linux/): the daemon, udev rule and systemd user service. Start here.
- [PROTOCOL.md](PROTOCOL.md): the HID protocol, recovered from Armoury Crate
  for macOS and Windows and verified on hardware.
- `azoth_hwinfo_test.py`: send hand-made packets (macOS or Linux, needs `hidapi`).
- `tools/`: helpers used for the reverse engineering (Go function-name
  recovery from `__gopclntab`, annotated disassembly).

Tested with the keyboard on the USB cable and on the ROG Omni Receiver
(2.4 GHz). Bluetooth is not supported.

## Install

See [linux/README.md](linux/README.md#install).

## Keyboard tips

- **Fn + Tab** switches the keyboard between PC and Mac mode (the status area
  next to the OLED shows PC or MAC).
- The status icons beside the OLED (caps lock, wireless, battery, PC/MAC) are
  drawn by the keyboard firmware; nothing in the host protocol sets them.
