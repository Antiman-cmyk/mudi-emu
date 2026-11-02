#!/bin/sh
# Every self-test. The model tests need nothing. The stack test needs the firmware
# (bin/mudi-emu fetch && bin/mudi-emu extract), root and qemu binfmt, and skips without them.
set -e
cd "$(dirname "$0")/.."
python3 -I tests/test_model.py
python3 -I tests/test_stack.py
