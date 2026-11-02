#!/usr/bin/env python3
"""Device trees from a GL boot.img (DTBs appended to the kernel), without dtc.

    python3 -I dtb.py <boot.img> list                    every DTB: index, offset, model
    python3 -I dtb.py <boot.img> dump <match> [<node>]   properties of the DTB whose model
                                                          contains <match>, under <node> (substring)
    python3 -I dtb.py <boot.img> hwinfo <match>          the gl-hw node as JSON (what GL's
                                                          gl-hw-info kernel module serves in
                                                          /proc/gl-hw-info/)

The image is data: parsed, never executed."""
import json, struct, sys

MAGIC = b"\xd0\x0d\xfe\xed"


def blobs(data):
    i = 0
    while True:
        i = data.find(MAGIC, i)
        if i < 0:
            return
        tot = struct.unpack(">I", data[i + 4:i + 8])[0]
        if 40 < tot < 16 << 20:
            yield i, data[i:i + tot]
        i += 4


def walk(b):
    """-> [(path, name, raw bytes)] for every property."""
    _, _, off_struct, off_strings, _, _, _, _, size_str, _ = struct.unpack(">10I", b[:40])
    strs = b[off_strings:off_strings + size_str]
    p, path, out = off_struct, [], []
    while p + 4 <= len(b):
        tok = struct.unpack(">I", b[p:p + 4])[0]; p += 4
        if tok == 1:
            e = b.index(b"\0", p); path.append(b[p:e].decode("latin-1")); p = (e + 4) & ~3
        elif tok == 2:
            path.pop()
        elif tok == 3:
            ln, no = struct.unpack(">II", b[p:p + 8]); p += 8
            v = b[p:p + ln]; p = (p + ln + 3) & ~3
            name = strs[no:strs.index(b"\0", no)].decode("latin-1")
            out.append(("/".join(path) or "/", name, v))
        elif tok == 9:
            break
    return out


def text(v):
    if v and v[-1:] == b"\0" and all(32 <= c < 127 or c == 0 for c in v):
        return v.rstrip(b"\0").decode().replace("\0", " | ")
    if len(v) % 4 == 0 and len(v) <= 32:
        return " ".join(hex(x) for x in struct.unpack(">%dI" % (len(v) // 4), v))
    return v.hex()[:64]


def model(b):
    for path, name, v in walk(b):
        if path == "/" and name == "model":
            return text(v)
    return ""


def pick(data, match):
    for off, b in blobs(data):
        if match.lower() in model(b).lower():
            return b
    for off, b in blobs(data):                     # GL's own node names the model too
        for path, name, v in walk(b):
            if name == "model" and "gl-hw" in path and text(v).lower() == match.lower():
                return b
    sys.exit("no DTB matching %r" % match)


def hwinfo(b):
    """The gl-hw node's plain string properties (factory_data entries point into a partition and
    are not reproducible, so they are left out)."""
    out = {}
    for path, name, v in walk(b):
        if path.endswith("gl-hw") and name not in ("compatible", "name", "phandle"):
            out[name] = text(v)
    return out


def main():
    data = open(sys.argv[1], "rb").read()
    cmd = sys.argv[2] if len(sys.argv) > 2 else "list"
    if cmd == "list":
        for n, (off, b) in enumerate(blobs(data)):
            print(n, off, model(b))
    elif cmd == "dump":
        b = pick(data, sys.argv[3])
        node = sys.argv[4] if len(sys.argv) > 4 else ""
        for path, name, v in walk(b):
            if node in path:
                print(path, name, "=", text(v))
    elif cmd == "hwinfo":
        print(json.dumps(hwinfo(pick(data, sys.argv[3])), indent=1, sort_keys=True))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
