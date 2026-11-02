# mudi-emu

Runs GL.iNet Mudi firmware code on a Linux dev box (qemu-user), against an evidence-tagged modem
model, so apps for the router can be developed and tested without it. Consumers pin it as a git
submodule (MudiModem: `tools/mudi-emu`), so keep the CLI stable. `README.md` has the overview.

## Rules
- **Evidence or it's `assumed`.** Every modem reply in `modem/model.py` names an `EVIDENCE` id.
  Its source is one of capture, baseband, atcop, gl-parser, gl-run, doc, assumed or unmodelled.
  Don't upgrade a source without the evidence, and say in the text which part is pinned and which
  part is guessed. A new reply format gets a format string in `tests/test_model.py` FORMATS.
- **GL facts** go in `docs/gl-behaviour.md` (measured by running GL under `up`) or
  `docs/re-notes-4.10.md` (read from binaries), each with how it was settled. Edit facts in place;
  no session logs.
- To learn what GL does, run GL (`mudi-emu up`, feed variants live, compare its output) before
  reading disassembly. Method and tools: `re/NOTES.md`.
- **Firmware is untrusted data and proprietary.** Never commit anything from `firmware/` or `work/`.
  The one exception is `reference/<image>/`: the small GL copies listed in
  `lib/mudi_emu/reference.py`, written by `mudi-emu reference refresh` and checked against the
  image. Add an entry there and a line to that directory's README instead of copying by hand.
  Run Python with `-I`, keep scripts outside extracted trees, and pass paths as arguments.
- `up` mounts only inside its private namespaces and must leave the unpacked image untouched (the
  overlay's upper dir takes the writes).

## Commands
```sh
bin/mudi-emu fetch && bin/mudi-emu extract     # pinned image -> firmware/<image>/
bin/mudi-emu check                             # what this machine can run
tests/run.sh                                   # model tests (no firmware) + GL stack test (root)
bin/mudi-emu evidence                          # the model's evidence table
```
