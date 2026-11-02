"""fetch / extract / extract-baseband / info for a pinned GL image.

The OTA's sha256 is checked against BOTH the pin in images.json and GL's own firmware API, so
neither a changed API answer nor a tampered download goes unnoticed. Unpacking never mounts or
runs anything from the image: sdat2img + debugfs for the rootfs, a FAT reader for the baseband,
a DTB parser for the hardware description. Needs curl, debugfs (e2fsprogs), python3."""
import hashlib, json, os, shutil, subprocess, sys, tempfile, zipfile

from . import images

RE = os.path.join(images.REPO, "re")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _api_sha(img):
    try:
        out = subprocess.run(["curl", "-fsS", "-m", "30", img["api"]], capture_output=True, text=True).stdout
        info = json.loads(out).get("info") or []
    except (OSError, ValueError):
        return None
    want = os.path.basename(img["url"])
    for rel in info:
        for f in rel.get("download") or []:
            if f.get("name") == want:
                return f.get("sha256")
    return None


def fetch(img):
    os.makedirs(img["dir"], exist_ok=True)
    api = _api_sha(img)
    if api and api != img["sha256"]:
        raise SystemExit("GL's API now reports sha256 %s for %s, images.json pins %s - investigate"
                         % (api, os.path.basename(img["url"]), img["sha256"]))
    if api:
        print("GL's API agrees with the pinned sha256")
    if os.path.exists(img["zip"]) and _sha(img["zip"]) == img["sha256"]:
        print("already have", img["zip"])
        return
    part = img["zip"] + ".part"
    print("downloading", img["url"])
    subprocess.run(["curl", "-fL", "-m", "3600", "-o", part, img["url"]], check=True)
    got = _sha(part)
    if got != img["sha256"]:
        os.remove(part)
        raise SystemExit("sha256 mismatch: got %s, want %s" % (got, img["sha256"]))
    os.replace(part, img["zip"])
    print("ok:", img["zip"])


def _need_zip(img):
    if not os.path.exists(img["zip"]):
        raise SystemExit("no %s - run: mudi-emu fetch" % img["zip"])


def extract(img):
    """rootfs/ + boot.img + hw-info.json."""
    _need_zip(img)
    if not shutil.which("debugfs"):
        raise SystemExit("debugfs missing (apt install e2fsprogs)")
    rootfs = img["rootfs"]
    with tempfile.TemporaryDirectory(dir=img["dir"], prefix=".extract.") as work:
        z = zipfile.ZipFile(img["zip"])
        for n in ("system.new.dat", "system.transfer.list", "boot.img"):
            z.extract(n, work)
        shutil.move(os.path.join(work, "boot.img"), img["boot"])
        subprocess.run([sys.executable, "-I", os.path.join(RE, "sdat2img.py"), os.path.join(work, "system.transfer.list"),
                        os.path.join(work, "system.new.dat"), os.path.join(work, "system.img")],
                       check=True, stdout=subprocess.DEVNULL)
        os.remove(os.path.join(work, "system.new.dat"))
        new = rootfs + ".new"
        shutil.rmtree(new, ignore_errors=True)
        os.makedirs(new)
        subprocess.run(["debugfs", "-R", "rdump / " + new, os.path.join(work, "system.img")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not os.access(os.path.join(new, "usr/bin/lua"), os.X_OK):
            raise SystemExit("extraction produced no usr/bin/lua - image layout changed?")
        # The image ships an empty /proc mount point. qemu's -L redirects every path that exists
        # under the prefix, so with it present a /proc listing (pgrep) would see nothing.
        try:
            os.rmdir(os.path.join(new, "proc"))
        except OSError:
            pass
        shutil.rmtree(rootfs, ignore_errors=True)
        os.replace(new, rootfs)
    hw = subprocess.run([sys.executable, "-I", os.path.join(RE, "dtb.py"), img["boot"], "hwinfo", img["model"]],
                        capture_output=True, text=True)
    if hw.returncode == 0:
        with open(img["hwinfo"], "w") as f:
            f.write(hw.stdout)
    else:
        print("note: no gl-hw node found in boot.img (%s)" % hw.stderr.strip())
    with open(os.path.join(rootfs, "etc/glversion")) as f:
        print("ok: %s (glversion %s)" % (rootfs, f.read().strip()))


def extract_baseband(img):
    _need_zip(img)
    z = zipfile.ZipFile(img["zip"])
    os.makedirs(img["baseband_dir"], exist_ok=True)
    for name in img.get("baseband", []):
        dest = os.path.join(img["baseband_dir"], name.replace(".bin", ""))
        if os.path.isdir(dest):
            print("already unpacked", dest)
            continue
        with tempfile.TemporaryDirectory(dir=img["dir"], prefix=".bb.") as work:
            z.extract(name, work)
            subprocess.run([sys.executable, "-I", os.path.join(RE, "fatx.py"), os.path.join(work, name), dest], check=True)
        print("ok:", dest)


def info(img):
    print("image:   %s (%s %s %s)" % (img["name"], img["model"], img["version"], img.get("build", "")))
    print("zip:     %s" % (img["zip"] if os.path.exists(img["zip"]) else "(none: mudi-emu fetch)"))
    gv = os.path.join(img["rootfs"], "etc/glversion")
    print("rootfs:  %s" % ("%s (glversion %s)" % (img["rootfs"], open(gv).read().strip()) if os.path.exists(gv)
                           else "(none: mudi-emu extract)"))
    hw = images.hwinfo(img)
    print("hw-info: %s" % (", ".join("%s=%s" % kv for kv in sorted(hw.items()) if kv[0] in
                                     ("model", "build-in-modem", "modem-cpu", "hwnat")) or "(none)"))
    bb = img["baseband_dir"]
    print("baseband: %s" % (", ".join(sorted(os.listdir(bb))) if os.path.isdir(bb) else "(none: mudi-emu extract-baseband)"))
