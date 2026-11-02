# ROG Azoth OLED system info on Linux

Shows CPU and GPU temperature (or usage) on the Azoth's OLED, like Armoury
Crate's "System Information" mode. Works over the USB cable and the ROG Omni
Receiver (2.4 GHz dongle). It follows whichever one the keyboard answers on, so
you can switch between cable and wireless without restarting anything.

Tested on Fedora 44 (KDE) with an AMD Ryzen 9 9950X3D and an NVIDIA RTX 4090.

## Requirements

- Python 3 (no extra packages).
- An AMD CPU for `cpu_temp` (the `k10temp` driver, loaded by default).
- The NVIDIA driver for `gpu_temp` / `gpu_usage` (it provides
  `libnvidia-ml.so.1`; check with `nvidia-smi`).
- systemd and a desktop login. The keyboard is opened as your user through
  the udev `uaccess` tag, which grants access to whoever is logged in at the
  machine's own screen (not over SSH).

## Install

Run each line on the Linux machine.

1. Get the files:

       git clone https://github.com/marshalleq/asus.git && cd asus/Azoth/linux

2. Let your user write to the keyboard (needs your sudo password once):

       sudo cp 70-rog-azoth.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger

3. Try it in the foreground. You should see `Connected via ...` and a line of
   readings every second, and the OLED should show CPU1 Temp and GPU1 Temp.
   Press Ctrl+C to stop.

       python3 azoth_oled.py --verbose

4. Install it as a user service so it starts every time you log in:

       mkdir -p ~/.local/bin ~/.config/systemd/user && cp azoth_oled.py ~/.local/bin/ && chmod +x ~/.local/bin/azoth_oled.py

       cp azoth-oled.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now azoth-oled

5. Check it is running:

       systemctl --user status azoth-oled

## Managing the service

| What | Command |
|------|---------|
| Status | `systemctl --user status azoth-oled` |
| Live log | `journalctl --user -u azoth-oled -f` |
| Restart (e.g. after editing) | `systemctl --user daemon-reload && systemctl --user restart azoth-oled` |
| Stop until next login | `systemctl --user stop azoth-oled` |
| Stop and disable | `systemctl --user disable --now azoth-oled` |

## How it behaves

- **Login and reboot:** the service starts when you log in to the desktop and
  stops when you log out. It does not run at the login screen. If it starts
  before your login has been given access to the keyboard, it exits with a
  clear message and systemd retries every 5 seconds.
- **Screen lock:** keeps running; a locked session is still your session.
- **Cable or wireless:** it uses whichever connection the keyboard
  acknowledges on. If the keyboard stops answering (mode switch, sleep,
  unplugged) it looks again and logs what it finds.
- **When it stops:** the keyboard keeps showing the last values it received.

## Changing what is shown

With no `--show`, it shows `cpu_temp` and `gpu_temp`. One `--show` gives the
single layout (icon and larger text); two give the left/right layout. The
number after the label on screen ("CPU1") comes from the optional `:N`
suffix, default 1, e.g. `--show gpu_temp:1`.

To change it, edit `ExecStart` in `~/.config/systemd/user/azoth-oled.service`,
for example:

    ExecStart=%h/.local/bin/azoth_oled.py --show cpu_usage --show gpu_usage

then restart the service (see the table above).

## Sensors

| name      | source                                  | confirmed on screen |
|-----------|-----------------------------------------|---------------------|
| cpu_temp  | k10temp `Tctl` (AMD)                    | yes                 |
| gpu_temp  | NVML (NVIDIA driver)                    | yes                 |
| cpu_usage | /proc/stat                              | yes                 |
| gpu_usage | NVML                                    | yes                 |
| ram_usage | /proc/meminfo                           | yes                 |

The keyboard also supports CPU/GPU frequency and voltage, VRM temperature,
DRAM frequency and voltage, and CPU and chassis fan speeds (see
`azoth_oled.py --list-sensors` and `../PROTOCOL.md`). The daemon has no Linux
source for those yet and says so if you ask for one.

## Troubleshooting

| Message | What to do |
|---------|------------|
| `No permission to open /dev/hidrawN` | Install the udev rule (step 2) and make sure you are logged in at the machine's desktop, not only over SSH. `getfacl /dev/hidrawN` should list your user. |
| `Waiting for the ROG Azoth ...` | The keyboard isn't answering on either connection. Press a key to wake it, and check the cable or that the keyboard is in 2.4 GHz mode with the receiver plugged in. |
| `cpu_temp: no k10temp 'Tctl' sensor` | Not an AMD CPU, or `k10temp` isn't loaded (`sudo modprobe k10temp`). |
| `libnvidia-ml.so.1 not found` | The NVIDIA driver isn't installed, or use sensors that don't need it. |

## Uninstall

    systemctl --user disable --now azoth-oled && rm ~/.config/systemd/user/azoth-oled.service ~/.local/bin/azoth_oled.py

    sudo rm /etc/udev/rules.d/70-rog-azoth.rules && sudo udevadm control --reload
