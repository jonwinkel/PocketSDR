# FE 4CH diagnostic firmware 3.2 candidate

This instruments the 3.1 capture lifecycle without changing the GPIF waveform,
DMA mode/buffer count, USB descriptors, link power policy, or sample format.
Hardware acceptance is still required. The committed Release image remains 3.1.

## Discovery and request

Read the existing six-byte `VR_STAT` response first. Bytes 4/5 contain `D1 01`
only when diagnostic protocol v1 is supported. Old firmware returns `00 00`;
do not probe it with an unknown request. The existing fields are unchanged.

`bmRequestType=C0`, `bRequest=4C`, `wValue=0`, `wIndex=0`, `wLength=160` returns
the snapshot below. Other direction, recipient, value, index or length is
rejected. No reset/clear request is provided. All words are unsigned little
endian; fields marked byte are single bytes. Reserved bytes are zero.

| Offset | Value |
| --- | --- |
| 0–3 | ASCII `PDG1` |
| 4–7 | Bytes: protocol 1, length 160, firmware BCD `32`, flags |
| 8 | FX3 uptime in milliseconds, modulo 2^32 |
| 12 | Successful stream-start epoch, modulo 2^32; duplicate START does not increment |
| 16–19 | Bytes: SDK USB speed, GPIF state, SDK link power state, saturation flags |
| 20–22 | Bytes: SDK results for GPIF state, link state, error count reads |
| 24–26 | Bytes: SDK read results for PIB socket 0, PIB socket 1, UIB consumer 6 |
| 28–43 | PIB socket 0: status, transfer count, descriptor chain, interrupt status |
| 44–59 | PIB socket 1: same four words |
| 60–75 | UIB consumer socket 6 (USB IN 0x86): same four words |
| 76, 80 | Accumulated PHY and LINK errors |
| 84, 88 | USB reset and disconnect event counts |
| 92, 96 | USB suspend and resume event counts |
| 100 | Combined USB3 link failure, compliance entry and LMP exchange failure count |
| 104, 108 | Link recovery and endpoint underrun event counts |
| 112, 116 | PIB error callback count and most recent SDK error argument |
| 120 | Most recent USB event: event in low 16 bits, event argument in high 16 bits |
| 124 | Link power request callback count |
| 128 | Setup callbacks falling through without a response, despite returning handled |
| 132, 136 | Most recent such request: raw SDK setupdat0 / setupdat1 |
| 140 | Such requests matching endpoint 0x86 CLEAR_FEATURE(HALT) |
| 144 | Uptime when the last unhandled setup request was observed |
| 148–159 | Reserved, zero |

Flags at byte 7: bit 4 = bulk active, bit 5 = application/DMA configured,
bit 6 = endpoint configured. These describe software state, not sample progress.
Use `VR_STAT` for PLL lock bits. A nonzero SDK result makes its corresponding
value invalid. Failed or unconfigured socket reads have zero-filled words.

## Existing control-request issue being observed

The current callback returns `CyTrue` for requests that reach its final fallback,
without sending data, ACKing, or stalling EP0. The SDK contract instead requires
`CyFalse` for requests that the application cannot handle. The SDK bulk-source
example explicitly forwards endpoint CLEAR_FEATURE(HALT) to this callback even
in fast enumeration mode. PocketSDR's handler has no corresponding endpoint
recovery branch. The new counters observe this existing behavior; this candidate
does not change it. Whether the phone sends such a request before its bulk stall
is unproven. Adding recovery without evidence would also change the reproduction.

## Counter semantics and limitations

- Socket transfer counts update at completed descriptor boundaries. The count
  unit is the socket status bit 29 (0 = bytes, 1 = buffers); capture configures
  byte mode. Both producers feed the one USB consumer. Do not treat these counts
  as an RF sample counter or a count of data delivered to the host application.
- Socket counts wrap at 2^32 and are reset by STOP, RESET and reconfiguration.
  Compare deltas only within a continuously active stream with the same epoch.
  Hardware registers are read sequentially while DMA continues; a snapshot is
  not an atomic measurement of queue occupancy. Socket state STALL by itself is
  normal between buffers. Repeated snapshots and count progress are necessary.
- Event totals and the epoch persist through STOP, vendor reset and USB
  reconfiguration, but a device reboot clears them. They wrap at 2^32.
  Interrupt callbacks may update counters during a snapshot; count/error pairs
  are approximate observations, not a synchronized event log.
- `CyU3PUsbGetErrorCounts` clears the SDK hardware counters on each query. The
  firmware accumulates them in RAM so another reader cannot erase the reported
  totals. Each hardware counter saturates at 65535 between reads. Byte 19 bits
  0/1 mark a saturated PHY/LINK read; that interval is a lower bound. Hardware
  may also clear counters on USB reset. Counts only describe errors FX3 detects.
- A control request can wake an idle link. Link state sampled during that request
  is not proof of its earlier state. Record suspend/resume/recovery counters too.
- PIB errors use an error-only callback; DMA remains automatic without per-buffer
  callbacks. USB link recovery callbacks run in interrupt context and only update
  words. No logging or blocking SDK calls are added to these callbacks.
- The last USB event excludes SOF/ITP and EP0 status completion. The underrun
  counter covers all endpoints; the last event argument can identify an endpoint.
- Diagnostics cannot measure VBUS current/voltage, prove USB compliance, or assign
  blame to the phone driver. A power interruption can erase the very RAM evidence
  being collected. Hardware power measurements and host traces remain useful.

Register meanings and callback context are from FX3 SDK 1.3.5 `cyu3socket.h`,
`sock_regs.h`, `cyu3pib.h` and `cyu3usb.h`. The host should poll at a low rate
(currently 1 Hz) and save a final query before issuing STOP.

## Tests and hardware acceptance

Run `run_tests.py` with the SDK root as described in [README](README.md). It
executes the production firmware C translation unit against SDK stubs, including
malformed requests, failed reads, clear-on-read error accumulation, saturation,
counter retention and unchanged lifecycle sequencing.

Before EEPROM installation, load the candidate into RAM on native Linux and:

1. Confirm firmware 3.2 and capability `D1 01` with the normal status request.
2. Capture at 8 and 24 Msps with tracing; verify all three socket counts advance,
   host throughput remains correct, and no new sample-order or USB errors appear.
3. Repeat START/STOP and deliberate host backpressure checks. Inspect epoch and
   socket counts around STOP; compare trace-enabled and trace-disabled captures.
4. Run the phone capture-only reproduction for at least 15 minutes, collecting
   the fresh pre-STOP snapshot if it stalls. Compare counters with host USB state.

Host polling and the error callback are instrumentation and may affect timing;
passing a diagnostic run alone does not establish that the original bug is fixed.
