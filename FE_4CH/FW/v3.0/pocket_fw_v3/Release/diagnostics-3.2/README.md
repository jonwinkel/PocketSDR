# Firmware 3.2 diagnostic candidate

This is an unvalidated hardware candidate for temporary RAM testing, built from
PocketSDR source commit `fdb77af4f7a7dbc3671ef286c0f80cd3e232bff4` using FX3 SDK 1.3.5 and
ARM GCC 15.3.0. The normal pinned `scripts/hardware.py firmware` recipe reproduced
the original working-tree image byte for byte. Build provenance, exact recipe,
license, unstripped ELF and independent IMG validation accompany the image.

Image SHA256:
`ea8ae06ec91041cedcbd21729879187253613d671f9e0cfcc348efb08440934b`

No device has run this candidate yet. The main Release image (`../pocket_fw_v3.img`)
and installed EEPROM remain firmware 3.1. Its SHA256 is
`7a1f04e4c49a2e4d0c25a05b17743908f7c1eb615722f62b1a9ef55dec1eb307`.

Read [the protocol and acceptance procedure](../../../tests/diagnostics.md).
RAM loading requires the ROM bootloader (J2 fitted while disconnected from power).
Use the RAM target, verify firmware 3.2 plus VR_STAT capability `D1 01`, and perform
PC acceptance before deciding on a separate EEPROM installation for phone tests.
USB power loss discards a RAM-loaded image. Removing J2 while powered off and
reconnecting restores the installed EEPROM image. The Android app has no firmware
loader. WSL bootloader forwarding failed previously; use native Linux.
