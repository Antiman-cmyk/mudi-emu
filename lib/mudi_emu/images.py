"""The pinned firmware images (images.json) and where each one lives on disk.

    firmware/<image>/ota.zip          the OTA zip, sha256-checked
    firmware/<image>/rootfs/          its root filesystem (system.new.dat -> ext4 -> debugfs rdump)
    firmware/<image>/hw-info.json     the device tree's gl-hw node = what /proc/gl-hw-info serves
    firmware/<image>/boot.img         kept for re/dtb.py
    firmware/<image>/baseband/<bin>/  the NON-HLOS FAT images, unpacked (extract-baseband)

MUDI_EMU_FIRMWARE overrides firmware/ (e.g. to share one download between checkouts)."""
import json, os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def catalog():
    with open(os.path.join(REPO, "images.json")) as f:
        return json.load(f)


def pick(name=None):
    c = catalog()
    name = name or os.environ.get("MUDI_EMU_IMAGE") or c["default"]
    if name not in c["images"]:
        raise SystemExit("unknown image %r (images.json has: %s)" % (name, ", ".join(sorted(c["images"]))))
    img = dict(c["images"][name], name=name)
    base = os.path.abspath(os.environ.get("MUDI_EMU_FIRMWARE") or os.path.join(REPO, "firmware"))
    d = os.path.join(base, name)
    img["dir"] = d
    img["zip"] = os.path.join(d, "ota.zip")
    img["rootfs"] = os.environ.get("MUDI_EMU_ROOTFS") or os.path.join(d, "rootfs")
    img["hwinfo"] = os.path.join(d, "hw-info.json")
    img["boot"] = os.path.join(d, "boot.img")
    img["baseband_dir"] = os.path.join(d, "baseband")
    return img


def hwinfo(img):
    """The gl-hw properties, or {} before `mudi-emu extract`."""
    try:
        with open(img["hwinfo"]) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}
