"""`mudi-emu up`: GL's OWN cellular stack on the dev box, against the modem behaviour model.

    mudi-emu up <workdir> [--scenario FILE] [--keep] [--no-gl] [-- CMD ...]

Runs CMD (default: an interactive sh) with GL's cellular_manager up — it spawns modem_AT on the
AT port itself, exactly as on the router — and tears everything down when CMD ends.

How, and why each piece:
  * private mount + pid namespaces (unshare -m -p): every process started here dies with CMD,
    and nothing is mounted on the host.
  * root = overlayfs: the extracted image read-only below, <workdir>/upper above. GL's writes land
    in upper/ (reset every run unless --keep), so the unpacked image is never modified.
  * /proc/gl-hw-info: GL's kernel module serves the device tree's gl-hw node there; we write the
    same properties (firmware/<image>/hw-info.json, plus the scenario's "hw_info") into the
    overlay's /proc. No real /proc is mounted (qemu-user answers /proc/self itself).
  * chroot + binfmt_misc: GL's system() shells, its scripts and the modem_AT it spawns resolve
    inside the image. (qemu's -L alone is not enough: it does not translate unix-socket paths, so
    ubusd and modem_AT's /tmp/<bus>.sock would be the host's.)
  * /tmp and /dev are private tmpfs mounts; /dev gets null/zero/random/urandom/tty/ptmx/pts from
    the host and the AT port (/dev/smd9 on the E5800) -> the modem model's pty; /dev/log -> a
    syslog catcher, so GL's own log lines land in <workdir>/syslog.txt.
  * GL waits up to 60 s for Quectel's MCM ready flags before it registers anything; they are
    dropped first (images.json ready_flags).

Inside CMD: MUDI_EMU_ROOT (the merged root), MUDI_EMU_WORK, MUDI_EMU_UBUS_SOCK (host path of
GL's ubus socket — point a host-side eco/ubus client at it), MUDI_EMU_IMAGE; and the helpers
`mudi-emu ubus|glc|modem|wait-ready`. Files in <workdir>: at.jsonl (every AT command + the
evidence behind each reply), syslog.txt, cm.out (GL's stdout/stderr), modem.out."""
import json, os, shutil, subprocess, sys, time

from . import images, userland

MODEL = os.path.join(images.REPO, "modem", "model.py")
SYSLOG = os.path.join(images.REPO, "lib", "mudi_emu", "syslog_catch.py")


def _wait(pred, timeout, step=0.1):
    t = time.time() + timeout
    while time.time() < t:
        if pred():
            return True
        time.sleep(step)
    return False


def up(img, work, scenario=None, keep=False, gl=True, cmd=None):
    userland.have_rootfs(img)
    problems, stack = userland.check(img, verbose=False)
    if problems or stack:
        raise SystemExit("mudi-emu up needs: " + "; ".join(problems + stack))
    work = os.path.abspath(work)
    os.makedirs(work, exist_ok=True)
    if os.environ.get("MUDI_EMU_INNER") != "1":
        env = dict(os.environ, MUDI_EMU_INNER="1")
        argv = ["unshare", "-m", "-p", "-f", "--mount-proc", "--propagation", "private",
                sys.executable, "-I", os.path.join(images.REPO, "bin", "mudi-emu"), "--image", img["name"],
                "up", work] + (["--scenario", scenario] if scenario else []) + (["--keep"] if keep else []) \
               + ([] if gl else ["--no-gl"]) + ["--"] + (cmd or [])
        os.execvpe(argv[0], argv, env)

    upper, wdir, root = (os.path.join(work, d) for d in ("upper", "ovl-work", "root"))
    if not keep:
        shutil.rmtree(upper, ignore_errors=True)
        shutil.rmtree(wdir, ignore_errors=True)
    for d in (upper, wdir, root):
        os.makedirs(d, exist_ok=True)
    scen = {}
    if scenario:
        with open(scenario) as f:
            scen = json.load(f)
    hw = dict(images.hwinfo(img))
    hw.setdefault("country_code", "US")                     # factory data: not in the device tree
    hw.update(scen.get("hw_info", {}))
    hwdir = os.path.join(upper, "proc", "gl-hw-info")
    os.makedirs(hwdir, exist_ok=True)
    for k, v in hw.items():
        if v is None:
            continue
        with open(os.path.join(hwdir, k), "w") as f:
            f.write(str(v))

    def sh(*a):
        subprocess.run(list(a), check=True)
    sh("mount", "-t", "overlay", "overlay", "-o", "lowerdir=%s,upperdir=%s,workdir=%s" % (img["rootfs"], upper, wdir), root)
    sh("mount", "-t", "tmpfs", "none", os.path.join(root, "tmp"))
    sh("mount", "-t", "tmpfs", "none", os.path.join(root, "dev"))
    for n in ("null", "zero", "random", "urandom", "tty", "ptmx"):
        open(os.path.join(root, "dev", n), "w").close()
        sh("mount", "--bind", "/dev/" + n, os.path.join(root, "dev", n))
    os.makedirs(os.path.join(root, "dev", "pts"))
    sh("mount", "--bind", "/dev/pts", os.path.join(root, "dev", "pts"))
    for d in ("tmp/run/ubus", "tmp/lock"):
        os.makedirs(os.path.join(root, d), exist_ok=True)

    logs = {}
    def spawn(name, argv, chroot=False):
        out = open(os.path.join(work, name), "w")
        logs[name] = out
        if chroot:
            argv = ["chroot", root] + argv
        return subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT)

    for name in ("at.jsonl", "syslog.txt"):
        open(os.path.join(work, name), "w").close()
    try:
        os.remove(os.path.join(work, "modem-live.json"))     # live overrides belong to one run
    except OSError:
        pass
    spawn("syslog.out", [sys.executable, "-I", SYSLOG, os.path.join(root, "dev", "log"), os.path.join(work, "syslog.txt")])
    port = img["modem"]["at_port"]
    link = os.path.join(root, port.lstrip("/"))
    model_argv = [sys.executable, "-I", MODEL, "--link", link, "--log", os.path.join(work, "at.jsonl"),
                  "--live", os.path.join(work, "modem-live.json")]
    if scenario:
        model_argv += ["--scenario", os.path.abspath(scenario)]
    spawn("modem.out", model_argv)
    if not _wait(lambda: os.path.islink(link), 10):
        raise SystemExit("the modem model did not come up (%s)" % os.path.join(work, "modem.out"))
    spawn("ubusd.out", ["/sbin/ubusd"], chroot=True)
    sock = os.path.join(root, "tmp/run/ubus/ubus.sock")
    if not _wait(lambda: os.path.exists(sock), 15):
        raise SystemExit("GL's ubusd did not come up")
    for f in img.get("ready_flags", []):
        open(os.path.join(root, f.lstrip("/")), "w").close()
    if gl:
        spawn("cm.out", ["/usr/bin/cellular_manager"], chroot=True)

    env = dict(os.environ, MUDI_EMU_ROOT=root, MUDI_EMU_WORK=work, MUDI_EMU_UBUS_SOCK=sock,
               MUDI_EMU_IMAGE=img["name"], PATH=os.path.join(images.REPO, "bin") + os.pathsep + os.environ.get("PATH", ""))
    env.pop("MUDI_EMU_INNER", None)
    rc = subprocess.run(cmd or ["sh"], env=env).returncode
    sys.exit(rc)


# ---- helpers for commands running inside `up` ------------------------------------------------
def _root():
    r = os.environ.get("MUDI_EMU_ROOT")
    if not r:
        raise SystemExit("only inside `mudi-emu up` (MUDI_EMU_ROOT is not set)")
    return r


def ubus(args):
    os.execvp("chroot", ["chroot", _root(), "/bin/ubus"] + args)


def glc(obj, method, args_json):
    """GL's glc CGI, as nginx calls it for /rpc: {object, method, args} on stdin.
    -> (code, result). CONTENT_LENGTH is padded to >= 4096: glc read()s 4096 bytes at a time into
    a calloc(len+1) buffer, which the real kernel tolerates but qemu-user turns into EFAULT."""
    body = json.dumps({"object": obj, "method": method, "args": json.loads(args_json) if isinstance(args_json, str) else args_json})
    env = {"REQUEST_METHOD": "POST", "REQUEST_URI": "/rpc", "CONTENT_LENGTH": str(max(len(body), 8192)),
           "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
    out = subprocess.run(["chroot", _root(), "/www/cgi-bin/glc"], input=body, capture_output=True, text=True, env=env).stdout
    txt = out.split("\n\n", 1)[-1].strip()
    code, _, rest = txt.partition(" ")
    try:
        return int(code), (json.loads(rest) if rest else None)
    except ValueError:
        return None, out


def wait_ready(timeout=90):
    """GL's cellular objects registered and its sampler holding a row for the active slot."""
    def ready():
        r = subprocess.run(["chroot", _root(), "/bin/ubus", "-t", "5", "call", "cellular.collect", "get_signals",
                            '{"time":30,"slot":1}'], capture_output=True, text=True)
        try:
            return bool(json.loads(r.stdout).get("signals"))
        except ValueError:
            return False
    return _wait(ready, timeout, 1)


def modem_live(updates):
    """Merge `updates` (JSON object) into the running model's live overrides."""
    work = os.environ.get("MUDI_EMU_WORK")
    if not work:
        raise SystemExit("only inside `mudi-emu up` (MUDI_EMU_WORK is not set)")
    path = os.path.join(work, "modem-live.json")
    try:
        cur = json.load(open(path))
    except (OSError, ValueError):
        cur = {}
    _merge(cur, updates)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cur, f)
    os.replace(tmp, path)


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v
