#!/usr/bin/env python3
"""reference/<image>/: every file is in the source list, and (with the image extracted) every copy
still matches the image it came from."""
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))
from mudi_emu import images, reference  # noqa: E402


class Reference(unittest.TestCase):
    def test_listed(self):
        for name, srcs in reference.SOURCES.items():
            img = images.pick(name)
            files = set(os.listdir(reference.directory(img))) - {"README.md"}
            self.assertEqual(files, set(srcs), name)
            readme = open(os.path.join(reference.directory(img), "README.md")).read()
            for f in srcs:
                self.assertIn(f.split(".")[0], readme, "%s: say what %s is" % (name, f))

    def test_matches_the_image(self):
        for name in reference.SOURCES:
            img = images.pick(name)
            if not os.access(os.path.join(img["rootfs"], "usr/bin/lua"), os.X_OK):
                self.skipTest("%s not extracted (mudi-emu --image %s extract)" % (name, name))
            self.assertEqual(reference.check(img, verbose=False), [])


if __name__ == "__main__":
    unittest.main()
