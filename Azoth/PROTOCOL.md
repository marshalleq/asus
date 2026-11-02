# ROG Azoth (M701) OLED protocol notes

Source: static analysis of Armoury Crate Gear 1.4.11 for macOS (arm64), which is
a Swift app with an embedded Go service (`rog-service`) and a Go keyboard SDK
dylib (`asus_kb_macos_sdk`). Go function names were recovered from `__gopclntab`.

Status: 0x66 packet verified on hardware (USB cable, macOS hidapi): types 0x01 CPU temp and 0x11 GPU temp display correctly; the index byte is the number shown in the label ("CPU1", "GPU2").

## Device IDs (vendor 0x0B05, ASUSTek)

| Mode      | PID dec | PID hex |
|-----------|---------|---------|
| USB cable | 6787    | 0x1A83  |
| Dongle    | 6789    | 0x1A85  |
| Bluetooth / bootloader (config lists both) | 6791 | 0x1A87 |

ROG Azoth Extreme is a different model (6975/6978/6977); don't confuse them.

## Transport

### USB cable (0B05:1A83), verified on macOS and Linux

All commands are 64-byte buffers passed to `hid_write` unchanged
(`service.(*USBDevice).Write`). Interface 1, usage page 0xFF00, no report IDs,
64-byte input and output reports. On Linux: `write()` the 64 bytes to that
`/dev/hidrawN`. The keyboard echoes an ack on the same interface
(`66 00 <dual> 00 ...` for the HW info packet).

### ROG Omni Receiver dongle (0B05:1ACE), verified on Linux

`service.(*OmniDevice).Write` puts a per-device report ID in byte 0 and the
first 63 bytes of the command after it. The receiver's interface 2 has three
vendor collections, each 63-byte in/out:

| report ID | usage page | behaviour                                  |
|-----------|------------|--------------------------------------------|
| 1         | 0xFF02     | no reply to 0x66                           |
| 2         | 0xFF00     | **keyboard**: acks `02 66 00 <dual> ...`, OLED updates |
| 3         | 0xFF01     | replies `03 ff aa ...` (rejected)          |

So for the keyboard: `write(fd, b"\x02" + cmd64[:63])` on the receiver's
interface-2 hidraw node. Tested with the keyboard switched to 2.4 GHz mode; the
cable device (1A83) is not present in that mode with the cable unplugged.

## System info / HW info packet (opcode 0x66)

Built by `rog-service/keyboard.(*HWInfoActivityEvent).GetCmd` @ 0x1004bd470 and
sent by a ticker (`startHWInfoTimer`, period ~1 s) via
`keyboard.sendCommandWithBlockLock`.

```
byte 0     0x66
byte 1     0x00
byte 2     1 if exactly two items (dual HW info), else 0
byte 3     0x00
byte 4..   up to 15 entries of 4 bytes:
             +0  sensor type
             +1  display slot (show_index from the UI; 0xFF if <= 0)
             +2  value low byte   (int16 little-endian)
             +3  value high byte
rest       zero padding to 64 bytes
```

Sensor types. The full list comes from the Windows driver
(`ArmouryKbSDK.dll`, `CHalFunction_M701` HW-info thread @ 0x10056880, which
switches on the type byte and reads each value from a sensor struct). The
macOS build only sends 0x00, 0x01, 0x10 and 0x30.

High nibble = device (0 CPU, 1 GPU, 2 VRM, 3 DRAM, 4 chassis fan, 6 other),
low nibble = metric (0 usage, 1 temperature, 2 frequency, 3/4 fan, 5 voltage).

| type | meaning              | evidence                                   | on screen |
|------|----------------------|--------------------------------------------|-----------|
| 0x00 | CPU usage %          | macOS log string "CPU Usage"               | not yet   |
| 0x01 | CPU temperature C    | macOS log string "CPU Temp"                | yes: "CPU1 Temp" |
| 0x02 | CPU frequency        | Windows: struct field next to temperature  | not yet   |
| 0x03 | CPU fan speed        | Windows: motherboard fan block             | not yet   |
| 0x04 | CPU_OPT fan speed    | Windows: motherboard fan block             | not yet   |
| 0x05 | CPU voltage          | Windows: struct field next to frequency    | not yet   |
| 0x10 | GPU usage %          | macOS log string "GPU Usage"               | not yet   |
| 0x11 | GPU temperature C    | nibble pattern                             | yes: "GPU2 Temp" |
| 0x12 | GPU frequency        | Windows: per-GPU record                    | not yet   |
| 0x15 | GPU voltage          | Windows: per-GPU record                    | not yet   |
| 0x21 | VRM temperature      | Windows: `vrm_temperature`                 | not yet   |
| 0x30 | DRAM usage %         | macOS log string "RAM Usage"               | not yet   |
| 0x32 | DRAM frequency       | Windows: per-DIMM record                   | not yet   |
| 0x35 | DRAM voltage         | Windows: per-DIMM record                   | not yet   |
| 0x43 | chassis fan n speed  | Windows: per-fan array                     | not yet   |
| 0x64 | laptop battery       | Windows: `GetSystemPowerStatus`            | not yet   |
| 0x65 | time / date          | Windows: 12-hour format check              | not yet   |

Units for frequency, voltage and fan speed are not known yet. There is no
disk, network or free-memory type.

The keyboard keeps showing the last frame it received after the stream stops.

## Other OLED / keyboard opcodes (byte 0..1 of the 64-byte command)

From `asus_keyboard_sdk.dylib` `main.func*` (little-endian 32-bit store at
offset 0, so `0x0361` means bytes `61 03`):

| Function              | bytes       | notes                           |
|-----------------------|-------------|---------------------------------|
| SetOledAnimation      | 61 00       |                                 |
| SetOledCustomImage    | 61 03       |                                 |
| SetOledBrightness     | 68 00 00 00 [brightness] |                    |
| SetOledOpenStatus     | 69 00       | OLED on/off                     |
| SetAMOledSwitch       | 6a 00       |                                 |
| SetAMOledMode         | 6a 01       |                                 |
| SetKPMSwitch          | 6b 00       |                                 |
| SetLeverSwitch        | 71 00       | knob                            |
| SetLeverMode          | 71 02       | knob                            |
| PowerMode             | 51 51       |                                 |
| SetROGLogoLED         | 51 0a       |                                 |
| SetPollingRate        | 51 31       |                                 |
| SetBLEHostLED         | 51 41       |                                 |
| SwitchDemo            | 51 42       |                                 |
| Effect (lighting)     | 51 2c       |                                 |
| HW info (agent)       | 66 00 [dual] 00 ... | see above               |

The JS side (Node web framework) configures this with XML commands:
`SYSTEM_INFO` = 103 with an `hwinfo` list, `duration` 3000, `switch` 1. The
native agent then reads the sensors and streams 0x66 packets.

## Open questions

1. ~~Which HID interface accepts these writes.~~ Cable: interface 1 (0xFF00).
2. ~~Is GPU temperature 0x11?~~ Yes, shows "GPU<n> Temp". Does the firmware accept types the Mac build never sends?
3. What does the display slot byte mean exactly for single vs dual layout?
4. Does the OLED need a mode command first, or does it show HW info whenever
   the System Info page is selected with the button?
5. ~~Dongle prefix byte value.~~ Report ID 2 on the receiver's interface 2.
