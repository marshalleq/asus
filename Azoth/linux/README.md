# ROG Azoth OLED system info on Linux

Shows CPU and GPU temperature (or usage) on the Azoth's OLED, like Armoury
Crate's "System Information" mode. Works over the USB cable and the ROG Omni
Receiver (2.4 GHz dongle); it follows whichever one the keyboard answers on.

## Install

Run each line on the Linux machine, from this `linux/` folder:

    sudo cp 70-rog-azoth.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger
    mkdir -p ~/.local/bin ~/.config/systemd/user && cp azoth_oled.py ~/.local/bin/ && chmod +x ~/.local/bin/azoth_oled.py
    cp azoth-oled.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now azoth-oled

Try it in the foreground first:

    ~/.local/bin/azoth_oled.py --verbose

## Sensors

| name      | source                                  | confirmed on screen |
|-----------|-----------------------------------------|---------------------|
| cpu_temp  | k10temp `Tctl` (AMD)                    | yes                 |
| gpu_temp  | NVML (NVIDIA driver)                    | yes                 |
| cpu_usage | /proc/stat                              | yes                 |
| gpu_usage | NVML                                    | yes                 |
| ram_usage | /proc/meminfo                           | yes                 |

With no `--show`, it shows `cpu_temp` and `gpu_temp`. One `--show` gives the
single layout and two give the dual layout. The number
after the label on screen ("CPU1") comes from the optional `:N` suffix,
default 1, e.g. `--show gpu_temp:1`.

To change what is shown, edit `ExecStart` in
`~/.config/systemd/user/azoth-oled.service`, then
`systemctl --user daemon-reload && systemctl --user restart azoth-oled`.
