# Contributing

New device profiles, corrections from real units, bug reports and pull requests are welcome.

## Corrections from a real device

The most useful report is "the real one answers this differently": the exact bytes the device sent (hex for a binary
protocol), what was sent to it, its model and its firmware. That is evidence a manual or a control module can't give.

## A new profile

- Work from the manufacturer's protocol document. Emulate only what the real device does: its reply shapes, its
  refusals, whether it ever sends anything unprompted, and its timing.
- Write in the module docstring what's confirmed, against which source, and what's a best guess. The page shows it as
  the device's Fidelity notes.
- Add `tests/test_<profile>.py`: the manual's example frames over a real socket, and, where you have one, a real
  control module or an open-source client library against the profile (see `tests/harness.py`). Don't commit a
  vendor's control module: load it with `load_module()` from `tests/drivers/`.

## Before you open a pull request

- Run `py -3.12 tests/run_tests.py` and make sure nothing fails.
- Keep real site data out of the repository: no real MAC addresses, serial numbers, IP addresses, hostnames,
  passwords or client names in code, benches or tests. Made-up values are fine.
- Keep a change focused on one thing, and say in the pull request what it fixes or adds.

## Your contribution and the license

This project is under an MIT-based license with no-resale and no-bundling conditions (see
[LICENSE](LICENSE)). The copyright holder also grants separate licenses for commercial resale
and bundling. So that this stays
possible for code that includes yours, by opening a pull request you agree to the following:

1. Your contribution is your own original work, or you have the right to submit it.
2. Your contribution is licensed under this project's LICENSE.
3. You also grant Jaime Maertz a perpetual, worldwide, non-exclusive, royalty-free,
   irrevocable license to use, modify, sublicense and distribute your contribution under any
   terms, including commercial licenses.
4. You keep the copyright in your contribution and may use it in any other way you like.

If you can't agree to this, please open an issue describing the change instead.

Questions: JaimeMaertz@gmail.com
