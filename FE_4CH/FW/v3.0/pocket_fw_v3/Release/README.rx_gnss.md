# rx_gnss firmware 3.1 review candidate

`pocket_fw_v3.img` and `pocket_fw_v3.elf` were built from firmware source commit
`917e3952690eded83941827e2efe30e652507e88` using ARM GCC 15.3.0 and FX3 SDK 1.3.5.
The source change coordinates GPIF and DMA restart at producer socket 0.

IMG SHA256: `7a1f04e4c49a2e4d0c25a05b17743908f7c1eb615722f62b1a9ef55dec1eb307`.

The native lifecycle harness and independent IMG/ELF load-data, checksum and
entry-point checks pass. **This candidate has not yet been loaded on hardware.**
Windows/WSL bootloader forwarding failed before any firmware data was written.
Use temporary RAM loading on native Linux, then verify raw sample continuity.

`baseline-7066e8027d/pocket_fw_v3.img` is the unchanged firmware 3.0 control built
with the same compiler and SDK. Its SHA256 is
`0b153efd0bd859ceb88684a970a5031526ff9fd2ca70a34cf4b558dd0b79de83`.

Each directory includes build provenance, input/artifact hashes and a frozen copy
of the receiver's build recipe. These snapshots document the build; invoke the
maintained `scripts/hardware.py` from the rx_gnss repository for future builds.
The candidate's recorded source revision deliberately precedes this artifact-only
commit. Consult rx_gnss `docs/repo-notes/pocketsdr_native_linux_handoff.md` for the
current experiment and loading commands.
