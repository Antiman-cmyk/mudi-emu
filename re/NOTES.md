# Reading GL's firmware: method and tools

To answer a question about how the firmware behaves, work down this list. Stop at the first one
that settles it:

1. **Run it.** `mudi-emu up` runs GL's own stack. Feed the model the reply in question (a
   scenario `replies` entry, or `mudi-emu modem` live) and read what GL makes of it. To find out
   which layout a parser expects, feed variants and compare: GL's output is the oracle. This is
   how the QCAINFO field indexes in `docs/gl-behaviour.md` were found.
2. **Read the image's scripts.** Lua, shell and JSON under `firmware/<image>/rootfs` are plain
   text, and `/etc/modem_list.json` and `/lib/modem_data/*.json` drive a lot of behaviour. GL's
   precompiled Lua plugins can be read with `luaq.py`.
3. **Read the binaries' strings and calls.** For GL's C libraries, see below.
4. **Read the baseband's strings.** `mudi-emu extract-baseband` unpacks the NON-HLOS FAT images.
   Quectel's AT handlers keep their reply printf formats in plain text in `image/modem.b24`
   (`strings -n 2 modem.b24 | grep -n 'QCAINFO'`). Reading neighbouring lines gives the pieces of a
   reply that is printed in several calls (QENG servingcell).
5. **Quectel's apps-side AT processor.** Some commands never reach the baseband:
   `/usr/lib/libql_atcop.so` in the rootfs answers them (`QSIMTYPE`, `QCFG` usb/thermal keys,
   `QETH`, `QABFOTA`, …). Search its strings.

Write each fact down with how it was settled: `docs/gl-behaviour.md` for what running GL showed,
`docs/re-notes-4.10.md` for what was read from its binaries, `modem/model.py` `EVIDENCE` for the
modem.

## The tools (`python3 -I re/<tool>.py`, dev box only)
| tool | does |
|---|---|
| `sdat2img.py` | rebuilds the ext4 system image from the OTA's `system.new.dat` + `system.transfer.list` (`extract` uses it, then `debugfs rdump`) |
| `dtb.py` | lists the DTBs appended to `boot.img`, dumps one, prints the `gl-hw` node as JSON (`extract` writes `hw-info.json` with it). No `dtc` needed |
| `fatx.py` | read-only FAT12/16 extractor for `NON-HLOS_*.bin` (no loop mount) |
| `gotcalls.py <elf> <fn> [--strings]` | the calls (and string refs) inside one function of a section-less ELF, with GOT targets named |
| `strrefs.py <elf> <string>…` | where in the code the given strings are referenced (ADRP+ADD), in code order |
| `xref.py <elf> find <s>` / `dis <a> <b>` | addresses using a string, or an annotated disassembly range |
| `policies.py <elf>` | static `blobmsg_policy` arrays: the ubus argument names and types a binary accepts |
| `ubusapi.py <elf>` | ubus object/method/argument tables |
| `luaq.py <bytecode> <string>` | disassembles GL's precompiled Lua 5.1 (OpenWrt LNUM: int32 constants) around a string constant |

Needs `binutils`, `binutils-aarch64-linux-gnu` and python3.

## Gotchas
- **GL's libraries are section-less ELFs.** `readelf -S` shows nothing. Use `readelf -l -W`
  (PT_LOAD) to map addresses to offsets and `readelf --use-dynamic -r -W` for relocations.
  `objdump` needs `-D -b binary -m aarch64` with `--start/--stop-address` (offsets equal vaddrs in
  the first segment). The tools do this for you.
- **Every call goes through the GOT** (`adrp; ldr; blr`), so a `bl` scan finds nothing.
  `gotcalls.py` resolves the targets through the JUMP_SLOT/GLOB_DAT relocations.
- **Which function a string belongs to:** `strrefs.py` gives the code address. The enclosing
  function is the closest exported symbol below it (`aarch64-linux-gnu-nm -D --defined-only`).
  Static functions have no symbol, so the result can be the exported caller instead.
- **A parser's format string is not proof of what the modem sends**, only of what GL expects.
  Pair it with the baseband's printf format, or a capture, before calling the modem side
  settled.
- **Untrusted input.** Everything downloaded or unpacked is data. Keep scripts outside the
  extracted trees, pass paths as arguments, and run Python with `-I`.
