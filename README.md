# mudi-emu

Develop and test for GL.iNet Mudi routers without the router. It runs **GL's own firmware
code** on a Linux dev box under qemu-user, against a modem behaviour model that tags every reply
with the evidence behind it.

It currently covers the GL-E5800 (Mudi 7) on GL firmware 4.10.0 (`images.json`), and grew out of
[MudiModem](https://github.com/Antiman-cmyk/MudiModem)'s test harness.

| layer | what runs | use it for |
|---|---|---|
| `mudi-emu run` | the image's own Lua 5.1, lua-eco, busybox ash (only the image's applets), any binary | scripts and daemons exactly as the router's userland runs them: 32-bit Lua ints, GL's cjson, busybox quirks |
| `mudi-emu fakebus` | the image's `ubusd` + a scripted fake GL stack (`lib/fakebus/fake-gl.lua`) | fast, fully controlled tests of code that calls GL over ubus: failures, hangs, sequences on cue |
| `mudi-emu up` | GL's real `cellular_manager`, the `modem_AT` it spawns, `ubusd` and `glc` (in a chroot, private namespaces, overlay root) against `modem/model.py` on the AT port | what GL itself does: the shapes it returns, the AT commands it sends, its band engine, its sampler |

The baseband itself is not emulated, and can't be (`docs/limits.md`). It's read for evidence
instead.

## Quick start
```sh
bin/mudi-emu fetch            # pinned OTA; sha256 checked against images.json AND GL's API
bin/mudi-emu extract          # rootfs + boot.img + hw-info.json (the device tree's gl-hw node)
bin/mudi-emu check            # what this machine can run
bin/mudi-emu run lua -e 'print(_VERSION)'      # the router's own Lua 5.1

sudo bin/mudi-emu up work/sa --scenario modem/scenarios/sa.json -- sh -c '
  mudi-emu wait-ready &&
  mudi-emu ubus call cellular.collect get_signals "{\"time\":30,\"slot\":1}" &&
  mudi-emu glc modem get_band_config "{\"bus\":\"cpu\",\"slot\":1}"'
jq -c '{cmd, reply, source}' work/sa/at.jsonl | head        # every AT command GL sent, and the evidence

bin/mudi-emu evidence         # every modelled behaviour and what it rests on
bin/mudi-emu path rootfs      # where things are, for scripts (MUDI_EMU_FIRMWARE moves firmware/)
tests/run.sh                  # self-tests (the stack test needs root + the firmware)
```

Inside `up`, the command gets `MUDI_EMU_ROOT` (the merged root), `MUDI_EMU_WORK` and
`MUDI_EMU_UBUS_SOCK` (the host path of GL's ubus socket, for a host-side eco or ubus client),
plus `mudi-emu ubus|glc|modem|wait-ready`. The working directory collects `at.jsonl`,
`syslog.txt` (GL's own log lines), `cm.out` and `modem.out`. GL's writes land in `<dir>/upper`,
which is reset every run unless `--keep`; the unpacked image is never modified.

To test your own code against GL, run it inside `up` and point it at `MUDI_EMU_UBUS_SOCK`, or
install it into the root (`$MUDI_EMU_ROOT`, an overlay) and run it there with `chroot`.

## Requirements
- Linux with `qemu-user-static` (qemu-aarch64) and its binfmt_misc entry (`check` registers it
  when run as root), Python ≥ 3.8, `curl` and `debugfs` (e2fsprogs).
- `up` also needs root: mount and pid namespaces (`unshare`), overlayfs, chroot.
- The `re/` tools need `binutils` and `binutils-aarch64-linux-gnu`.

## Layout
```
bin/mudi-emu            the CLI (lib/mudi_emu/cli.py)
images.json             pinned images: URL, sha256, GL API, baseband files, AT port, ready flags
lib/mudi_emu/           firmware (fetch/extract), userland (run), stack (up), fakebus
modem/model.py          the RG650V behaviour model; modem/README.md (evidence, scenarios, live changes)
modem/scenarios/        sa, nsa, lte, no-service, dual-sim
re/                     reverse-engineering tools + NOTES.md (method, gotchas)
docs/gl-behaviour.md    what GL's cellular stack does, measured by running it
docs/re-notes-4.10.md   what was read from the 4.10 image's binaries, and how
docs/limits.md          what can't be emulated, and why
tests/                  test_model.py (no firmware), test_stack.py (GL against the model)
firmware/ work/         downloads and run directories (gitignored)
```

## Adding an image
Add an entry to `images.json` with the OTA URL and sha256, GL's firmware API URL, the baseband
file names, and the modem's AT port (from the image's `/lib/modem_data/modem_list.json`) with the
evidence for it. Then `bin/mudi-emu --image <name> fetch && … extract`. The modem model is
written for the RG650V. Another module needs its own evidence first.

## Safety
Downloads and anything unpacked from them are treated as data. Nothing from the image runs
outside qemu, Python always runs with `-I`, and the tools keep their scripts outside the extracted
trees. `up` only mounts inside its own private mount namespace.
