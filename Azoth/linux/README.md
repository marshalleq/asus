# ROG Azoth OLED system info on Linux

Shows CPU and GPU temperature (or usage) on the Azoth's OLED, like Armoury
Crate's "System Information" mode. Works over the USB cable and the ROG Omni
Receiver (2.4 GHz dongle). It follows whichever one the keyboard answers on, so
you can switch between cable and wireless without restarting anything.

Tested on Fedora 44 (KDE) with an AMD Ryzen 9 9950X3D, an NVIDIA RTX 4090 and
the Ryzen's integrated AMD graphics.

## Requirements

- Python 3 (no extra packages) and systemd.
- A desktop login. The keyboard is opened as your user through the udev
  `uaccess` tag, which grants access to whoever is logged in at the machine's
  own screen (not over SSH).
- Nothing hardware-specific to configure for the default display. Sensors
  are found automatically (see [Sensors](#sensors)); GPU sensors need the
  NVIDIA driver (`libnvidia-ml.so.1`, check with `nvidia-smi`) or `amdgpu`.

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

Run `python3 azoth_oled.py --list-sensors` for the full list.

**Found automatically:**

| name      | source |
|-----------|--------|
| cpu_usage | `/proc/stat` |
| cpu_temp  | the CPU's hwmon driver: AMD `k10temp` (Tdie if shown, else Tctl), AMD `zenpower` (Tdie), Intel `coretemp` (hottest package) |
| cpu_freq  | cpufreq: the highest current core clock |
| ram_usage | `/proc/meminfo` (MemTotal - MemAvailable) |
| gpu_usage, gpu_temp, gpu_freq | NVIDIA: NVML. AMD: `gpu_busy_percent` and the card's hwmon (`edge`, `sclk`) |
| gpu_volt  | AMD only (`vddgfx`); NVIDIA doesn't report GPU voltage on Linux |

About AMD `Tctl`: on some older Ryzen and Threadripper models `Tctl` is
reported higher than the real temperature (an offset used for fan control),
and `k10temp` then also shows `Tdie`, the real die temperature, which is what
the daemon uses. Current Ryzen models have no offset and only show `Tctl`.

**Which GPU:** the one with the most dedicated VRAM, so a discrete card is
used rather than integrated graphics. It is logged at start
(`GPU: NVIDIA GeForce RTX 4090`). `--list-gpus` lists them and `--gpu N`
picks another. If two GPUs tie it stops and asks you to choose.

**Board-specific sensors** (cpu_fan, cpu_opt_fan, cpu_volt, vrm_temp,
ram_freq, ram_volt, chassis_fan) come from motherboard chips whose input
names differ on every board, so you map them yourself. `--list-hwmon` shows
every chip and input with its current raw value; then:

    python3 azoth_oled.py --show cpu_fan --show chassis_fan --hwmon cpu_fan=nct6799/fan2 --hwmon chassis_fan=nct6799/fan3

CHIP is the hwmon name (or `hwmonN` if several chips share a name); INPUT is
an input (`fan2`, `temp1`, `in0`) or its label. `--hwmon` also overrides an
automatic source, e.g. `--hwmon cpu_temp=k10temp/Tccd1`. Temperatures,
voltages (mV), fans (RPM) and frequencies are converted to what the keyboard
expects. Motherboard fan and voltage chips often need a driver, e.g.
`nct6775` (many ASUS/Gigabyte/ASRock boards) or the out-of-tree `nct6687d`
(MSI boards).

## Troubleshooting

| Message | What to do |
|---------|------------|
| `No permission to open /dev/hidrawN` | Install the udev rule (step 2) and make sure you are logged in at the machine's desktop, not only over SSH. `getfacl /dev/hidrawN` should list your user. |
| `Waiting for the ROG Azoth ...` | The keyboard isn't answering on either connection. Press a key to wake it, and check the cable or that the keyboard is in 2.4 GHz mode with the receiver plugged in. |
| `cpu_temp: no CPU temperature driver found` | Load the CPU's driver (`sudo modprobe k10temp` or `coretemp`), or map one with `--hwmon cpu_temp=CHIP/INPUT`. |
| `An NVIDIA GPU is installed but libnvidia-ml.so.1 can't be loaded` | Install the NVIDIA driver (`nvidia-smi` should work). |
| `board-specific sensor; map it ...` | See [Sensors](#sensors): use `--list-hwmon` and `--hwmon`. |

## Uninstall

    systemctl --user disable --now azoth-oled && rm ~/.config/systemd/user/azoth-oled.service ~/.local/bin/azoth_oled.py

    sudo rm /etc/udev/rules.d/70-rog-azoth.rules && sudo udevadm control --reload
