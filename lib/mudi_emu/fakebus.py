"""`mudi-emu fakebus start|stop`: the image's ubusd on a private socket + a FAKE GL stack
(lib/fakebus/fake-gl.lua under the image's eco) answering from a scenario file.

Use it for fast, fully controlled tests of code that talks to GL over ubus (failures, hangs,
handover sequences on cue). For what GL itself does, use `up` (GL's real cellular_manager).

    mudi-emu fakebus start <dir> <scenario.json>   -> <dir>/ubus.sock, <dir>/bin/{ubus,jsonfilter}
                                                       (host wrappers for the image's CLIs on it),
                                                       <dir>/calls.jsonl, <dir>/notify.jsonl
    mudi-emu fakebus stop <dir>

The scenario keys are documented at the top of lib/fakebus/fake-gl.lua."""
import os, signal, subprocess, time

from . import images, userland

FAKE = os.path.join(images.REPO, "lib", "fakebus", "fake-gl.lua")


def start(img, d, scenario):
    userland.have_rootfs(img)
    q, root = userland.qemu(), img["rootfs"]
    d = os.path.abspath(d)
    os.makedirs(os.path.join(d, "bin"), exist_ok=True)
    for f in ("ubus.sock", "ready", "calls.jsonl", "notify.jsonl"):
        try:
            os.remove(os.path.join(d, f))
        except OSError:
            pass
    sock = os.path.join(d, "ubus.sock")
    u = subprocess.Popen([q, "-L", root, os.path.join(root, "sbin/ubusd"), "-s", sock],
                         stdout=open(os.path.join(d, "ubusd.log"), "w"), stderr=subprocess.STDOUT, start_new_session=True)
    open(os.path.join(d, "ubusd.pid"), "w").write(str(u.pid))
    for _ in range(100):
        if os.path.exists(sock):
            break
        time.sleep(0.1)
    else:
        raise SystemExit("ubusd did not start")
    g = subprocess.Popen([q, "-L", root, os.path.join(root, "usr/bin/eco"), FAKE, sock, os.path.abspath(scenario), d],
                         stdout=open(os.path.join(d, "fake-gl.log"), "w"), stderr=subprocess.STDOUT, start_new_session=True)
    open(os.path.join(d, "fake-gl.pid"), "w").write(str(g.pid))
    for _ in range(150):
        if os.path.exists(os.path.join(d, "ready")):
            break
        time.sleep(0.1)
    else:
        raise SystemExit("the fake GL stack did not start: " + open(os.path.join(d, "fake-gl.log")).read())
    for name, prog, extra in (("ubus", "bin/ubus", ' -s "%s"' % sock), ("jsonfilter", "usr/bin/jsonfilter", "")):
        # under qemu-user a PATH lookup (`command -v`) does not see a rootfs-only binary
        p = os.path.join(d, "bin", name)
        with open(p, "w") as f:
            f.write('#!/bin/sh\nexec "%s" -L "%s" "%s/%s"%s "$@"\n' % (q, root, root, prog, extra))
        os.chmod(p, 0o755)
    print(sock)


def stop(d):
    for p in ("fake-gl", "ubusd"):
        f = os.path.join(d, p + ".pid")
        try:
            os.killpg(int(open(f).read()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        try:
            os.remove(f)
        except OSError:
            pass
