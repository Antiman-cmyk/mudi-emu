# What mudi-emu cannot do, and why

## The baseband is not emulated
The RG650V's modem runs on the SoC's Hexagon DSP under Qualcomm's QuRT RTOS (`NON-HLOS*.bin` →
`image/modem.b24` and friends). Running it would need all of these:

- a Hexagon system emulator that boots QuRT. QEMU's Hexagon target is user-mode Linux only, and
  no public system model boots a modem image.
- the hardware around it: the shared-memory/SMD/IPC-router links to the application processor,
  the crypto and compression engines, the modem's own accelerators (the "Q6 + vector + HW
  accelerator" blocks), the clocks, the PMIC and the RF front end.
- something for it to talk to. A modem without a radio and a network stays in `SEARCH`. A
  meaningful run needs a simulated eNB/gNB and core network driving the RF interface, which
  doesn't exist for this chip.

Research systems that emulate basebands (FirmWire, for example) cover some Samsung Shannon and
MediaTek parts by modelling their peripherals by hand. Nothing comparable exists for Qualcomm
Hexagon modems.

**What we do instead:** the baseband is a *source of evidence*, not something we run. Its format
strings fix what each reply looks like, field for field (`re/fatx.py` unpacks the FAT images;
`strings` finds the printf formats; `tests/test_model.py` checks the model against them). Captures
from a real box fix values and sequences. GL's own parsers, run here, fix what GL makes of them.
The modem model (`modem/model.py`) puts those together and tags every reply with its evidence.

## What only a real box can answer
- Real RF: what the cell actually reports, handovers, CA combinations a carrier configures,
  timing.
- Which variant the modem emits when several exist. For example, which of the two NR SCC
  QCAINFO forms appears under EN-DC, and when the AMBR keys answer. The model picks one and marks
  it `assumed`.
- Anything on the QMI side: data calls, DDS, SIM hot-plug events, traffic counters (only sampled
  while a call is up). GL's QMI calls fail here (`QMI_FW` errors in `syslog.txt`), so dial-time
  logic is reached through `cellular.cm cm_start_dial` only.
- Values that persist in modem NV across reboots (locks, band prefs). The model keeps them for
  one run.

Mark such questions as needing a box, batch them, and design so that nothing has to wait on one.

## qemu-user quirks you will meet
- Unix-socket paths are not translated by `-L`. That is why `up` uses a chroot, so GL's ubusd and
  `modem_AT`'s sockets live inside the image root.
- busybox re-execs itself, so the binfmt_misc entry for aarch64 must be registered (`mudi-emu
  check` does it as root).
- `glc` overruns its read buffer, and qemu reports `EFAULT` where the kernel would not
  (`docs/gl-behaviour.md`). `mudi-emu glc` pads around it.
- `/proc/self/*` is answered by qemu. The real `/proc` is not mounted inside the root, and GL's
  `/proc/gl-hw-info` is a plain directory in the overlay.
