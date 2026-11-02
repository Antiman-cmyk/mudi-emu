"""mudi-emu — develop and test for GL.iNet Mudi routers without the router.

usage: mudi-emu [--image NAME] <command> ...

firmware
  fetch                       download the pinned OTA (sha256 checked against the pin AND GL's API)
  extract                     rootfs + boot.img + hw-info.json (the device tree's gl-hw node)
  extract-baseband            unpack the NON-HLOS baseband FAT images (for re/ analysis)
  info                        what is on disk
  path rootfs|dir|zip|hwinfo|boot|baseband|reference
                              print one location (for scripts; honours MUDI_EMU_FIRMWARE / _ROOTFS)
  reference check|refresh     the GL file copies in reference/<image>/ against the extracted image
  check                       can this machine run the userland / GL's stack?
the router's userland (qemu-user)
  run lua|eco|sh|bin ...      the image's Lua 5.1, lua-eco, busybox ash (image applets only), any binary
GL's own cellular stack + the modem behaviour model
  up <dir> [--scenario F] [--keep] [--no-gl] [-- CMD ...]
                              run CMD (default: sh) with cellular_manager up against the model
  ubus ...                    (inside up) the image's ubus CLI on GL's bus
  glc OBJECT METHOD [JSON]    (inside up) call GL's glc CGI, as /rpc does
  modem JSON                  (inside up) merge JSON into the model's live state (see modem/README.md)
  wait-ready [SECONDS]        (inside up) until GL's sampler has a row
a fake GL bus (fast, scripted)
  fakebus start <dir> <scenario.json> | stop <dir>
the modem model
  evidence                    every behaviour the model implements and the evidence for it

images: images.json (default: the first listed under "default"). Docs: README.md, docs/."""
import json, os, subprocess, sys

from . import images


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    name = None
    if argv[:1] == ["--image"]:
        name, argv = argv[1], argv[2:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    img = images.pick(name)
    if cmd in ("fetch", "extract", "extract-baseband", "info"):
        from . import firmware
        {"fetch": firmware.fetch, "extract": firmware.extract,
         "extract-baseband": firmware.extract_baseband, "info": firmware.info}[cmd](img)
        return 0
    if cmd == "path":
        keys = {"rootfs": "rootfs", "dir": "dir", "zip": "zip", "hwinfo": "hwinfo", "boot": "boot",
                "baseband": "baseband_dir"}
        if rest == ["reference"]:
            from . import reference
            print(reference.directory(img))
            return 0
        if len(rest) != 1 or rest[0] not in keys:
            raise SystemExit("path " + "|".join(list(keys) + ["reference"]))
        print(img[keys[rest[0]]])
        return 0
    if cmd == "reference":
        from . import reference
        if rest == ["check"]:
            return 1 if reference.check(img) else 0
        if rest == ["refresh"]:
            reference.refresh(img)
            return 0
        raise SystemExit("reference check|refresh")
    if cmd == "check":
        from . import userland
        problems, stack = userland.check(img)
        return 1 if problems else 0
    if cmd == "run":
        from . import userland
        if not rest:
            raise SystemExit("run lua|eco|sh|bin ...")
        userland.run(img, rest[0], rest[1:])
    if cmd == "up":
        from . import stack
        if not rest:
            raise SystemExit("up <dir> [--scenario F] [--keep] [--no-gl] [-- CMD ...]")
        work, scen, keep, gl, i = rest[0], None, False, True, 1
        while i < len(rest) and rest[i] != "--":
            if rest[i] == "--scenario":
                scen = rest[i + 1]; i += 2
            elif rest[i] == "--keep":
                keep = True; i += 1
            elif rest[i] == "--no-gl":
                gl = False; i += 1
            else:
                raise SystemExit("up: unknown option " + rest[i])
        stack.up(img, work, scen, keep, gl, rest[i + 1:] if i < len(rest) else None)
    if cmd == "ubus":
        from . import stack
        stack.ubus(rest)
    if cmd == "glc":
        from . import stack
        if len(rest) < 2:
            raise SystemExit("glc OBJECT METHOD [JSON]")
        code, res = stack.glc(rest[0], rest[1], rest[2] if len(rest) > 2 else "{}")
        print(json.dumps({"code": code, "result": res}, indent=1))
        return 0 if code == 0 else 1
    if cmd == "modem":
        from . import stack
        stack.modem_live(json.loads(rest[0] if rest else "{}"))
        return 0
    if cmd == "wait-ready":
        from . import stack
        return 0 if stack.wait_ready(float(rest[0]) if rest else 90) else 1
    if cmd == "fakebus":
        from . import fakebus
        if rest[:1] == ["start"] and len(rest) == 3:
            fakebus.start(img, rest[1], rest[2])
        elif rest[:1] == ["stop"] and len(rest) == 2:
            fakebus.stop(rest[1])
        else:
            raise SystemExit("fakebus start <dir> <scenario.json> | stop <dir>")
        return 0
    if cmd == "evidence":
        model = os.path.join(images.REPO, "modem", "model.py")
        return subprocess.run([sys.executable, "-I", model, "--evidence"]).returncode
    raise SystemExit("unknown command %r (mudi-emu --help)" % cmd)
