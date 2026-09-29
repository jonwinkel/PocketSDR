# Firmware lifecycle tests

Run the actual firmware lifecycle code on the host with FX3 SDK 1.3.5 headers
and a native C compiler:

```sh
python3 FE_4CH/FW/v3.0/tests/run_tests.py --sdk-root /path/to/cyfx3sdk
```

The harness models GPIF and DMA producer phases independently, and asserts that
DMA reset/destruction happens only after GPIF and USB transactions stop. It covers
initial configuration, duplicate START/STOP, 100 restart cycles at each USB speed,
vendor reset, USB reset/disconnect/reconfiguration, partial setup and API failures.
It includes the production translation unit without conditional test hooks.

The harness also checks the [3.2 diagnostic protocol](diagnostics.md): capability
discovery, request validation, socket validity, error accumulation and preservation
of streaming state. SDK stubs cannot establish real DMA count or USB link behavior.

These tests verify software sequencing. Verify sample continuity on real hardware
before installing a candidate permanently; the stubs cannot establish GPIF timing
or USB controller behavior.

Validate an image against its unstripped ELF independently of the SDK converter:

```sh
python3 FE_4CH/FW/v3.0/tests/validate_image.py candidate.img candidate.elf
```

This checks load addresses, bytes, zero fill, overlap, checksum and entry point;
it does not program a device or replace hardware acceptance.
