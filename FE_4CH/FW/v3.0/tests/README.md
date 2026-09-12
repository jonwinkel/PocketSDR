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

These tests verify software sequencing. Verify sample continuity on real hardware
before installing a candidate permanently; the stubs cannot establish GPIF timing
or USB controller behavior.
