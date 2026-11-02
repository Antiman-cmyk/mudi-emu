"""The router's own userland on the dev box, through qemu-user.

    run lua <script> [args]   the image's Lua 5.1 (OpenWrt LNUM: 32-bit ints, GL's cjson/ubus)
    run eco <script> [args]   the image's lua-eco (Lua 5.4 + event loop)
    run sh  <script> [args]   the image's busybox ash with ONLY the image's applets on PATH
    run bin <path> [args]     any binary from the image (path inside the image, e.g. usr/bin/jsonfilter)

qemu-user's -L prefix makes absolute paths resolve inside the rootfs first. busybox re-execs
itself for some applets, which needs the kernel's binfmt_misc aarch64 entry: `check` registers it
when it can (root) and says so when it cannot. Unix-socket paths are NOT translated by qemu-user
(bind/connect see host paths) — that is why `up` uses chroot instead of -L."""
import os, shutil, subprocess, sys

BINFMT = "/proc/sys/fs/binfmt_misc/qemu-aarch64"


def qemu():
    q = shutil.which(os.environ.get("MUDI_EMU_QEMU", "qemu-aarch64-static"))
    if not q:
        raise SystemExit("qemu-aarch64-static not installed (apt install qemu-user-static)")
    return q


def binfmt():
    if os.path.exists(BINFMT):
        return True
    reg = "/proc/sys/fs/binfmt_misc/register"
    if not os.access(reg, os.W_OK):
        subprocess.run(["mount", "-t", "binfmt_misc", "binfmt_misc", "/proc/sys/fs/binfmt_misc"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    conf = "/usr/lib/binfmt.d/qemu-aarch64.conf"
    if os.access(reg, os.W_OK) and os.path.exists(conf):
        try:
            with open(conf) as src, open(reg, "w") as dst:
                dst.write(src.read())
        except OSError:
            pass
    return os.path.exists(BINFMT)


def have_rootfs(img):
    if not os.access(os.path.join(img["rootfs"], "usr/bin/lua"), os.X_OK):
        raise SystemExit("no rootfs at %s (mudi-emu fetch && mudi-emu extract)" % img["rootfs"])


def check(img, verbose=True):
    """-> list of problems (empty = ready). Also registers binfmt when possible."""
    problems = []
    try:
        qemu()
    except SystemExit as e:
        problems.append(str(e))
    if not os.access(os.path.join(img["rootfs"], "usr/bin/lua"), os.X_OK):
        problems.append("no rootfs (mudi-emu fetch && mudi-emu extract)")
    stack = []
    if not binfmt():
        stack.append("binfmt_misc aarch64 entry (register qemu-user-static's, needs root)")
    if os.geteuid() != 0:
        stack.append("root (mount + pid namespaces, overlayfs, chroot)")
    if not shutil.which("unshare"):
        stack.append("unshare (util-linux)")
    if verbose:
        print("userland (run): %s" % ("ok" if not problems else "; ".join(problems)))
        print("GL stack (up):  %s" % ("ok" if not problems and not stack else "needs " + "; ".join(problems + stack)))
    return problems, stack


def applets(img):
    """One host wrapper per applet the image's busybox provides, rebuilt when busybox changes."""
    d = os.path.join(img["dir"], ".applets")
    q = qemu()
    root = img["rootfs"]
    stamp = os.path.join(d, ".stamp")
    st = os.stat(os.path.join(root, "bin/busybox"))
    want = "%s %s %s %s" % (st.st_size, int(st.st_mtime), q, root)
    try:
        if open(stamp).read() == want:
            return d
    except OSError:
        pass
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    out = subprocess.run([q, "-L", root, os.path.join(root, "bin/busybox"), "--list"], capture_output=True, text=True).stdout
    for a in out.split():
        _wrapper(os.path.join(d, a), '"%s" -L "%s" "%s/bin/busybox" %s "$@"' % (q, root, root, a))
    for p in ("bin/busybox", "usr/bin/jsonfilter", "usr/bin/lua", "bin/ubus", "usr/bin/eco"):
        if os.path.exists(os.path.join(root, p)):
            _wrapper(os.path.join(d, os.path.basename(p)), '"%s" -L "%s" "%s/%s" "$@"' % (q, root, root, p))
    with open(stamp, "w") as f:
        f.write(want)
    return d


def _wrapper(path, cmd):
    with open(path, "w") as f:
        f.write("#!/bin/sh\nexec %s\n" % cmd)
    os.chmod(path, 0o755)


def run(img, kind, args):
    have_rootfs(img)
    q, root = qemu(), img["rootfs"]
    if kind == "lua":
        argv = [q, "-L", root, os.path.join(root, "usr/bin/lua")] + args
    elif kind == "eco":
        argv = [q, "-L", root, os.path.join(root, "usr/bin/eco")] + args
    elif kind == "bin":
        if not args:
            raise SystemExit("run bin <path inside the image> [args]")
        argv = [q, "-L", root, os.path.join(root, args[0].lstrip("/"))] + args[1:]
    elif kind == "sh":
        binfmt()
        d = applets(img)
        os.environ["QEMU_LD_PREFIX"] = root
        os.environ["PATH"] = d
        argv = [q, "-L", root, os.path.join(root, "bin/busybox"), "ash"] + args
    else:
        raise SystemExit("run lua|eco|sh|bin ...")
    sys.stdout.flush()
    os.execv(argv[0], argv)
