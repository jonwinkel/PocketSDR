#include <assert.h>
#include <string.h>

// Unused upstream code includes a 32-bit ThreadX callback cast on this 64-bit host.
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wcast-function-type"
#pragma GCC diagnostic ignored "-Wunused-but-set-variable"
#define main pocket_firmware_main
#include "../pocket_fw_v3/pocket_fw_v3.c"
#undef main
#include "../pocket_fw_v3/pocket_usb_dscr.c"
#pragma GCC diagnostic pop

enum operation {NONE, ENABLE, DISABLE, NAK_ON, NAK_OFF, CREATE, DESTROY,
    DMA_RESET, ARM, FLUSH, GPIF_START};
static enum operation failure;
static int endpoint, nak, settled, created, armed, running, loaded, queued;
static int gpif_socket, dma_socket, acknowledgements, starts;
static uint32_t counter_limit;
static uint16_t buffer_size;
static CyU3PUSBSpeed_t usb_speed;
static uint16_t sent_length, phy_errors, link_errors;
static uint8_t sent_bytes[160];
static CyU3PReturnStatus_t diagnostic_error;
static int socket_reads;


// Fail one SDK operation to exercise recovery paths.
static CyU3PReturnStatus_t result(enum operation op)
{
    if (failure != op) return CY_U3P_SUCCESS;
    failure = NONE;
    return CY_U3P_ERROR_FAILURE;
}

// Reset firmware globals and the simulated SDK state.
static void reset_fixture(void)
{
    failure = NONE;
    endpoint = nak = settled = created = armed = running = loaded = queued = 0;
    acknowledgements = starts = 0;
    gpif_socket = 1;
    dma_socket = -1;
    app_act = bulk_act = ep_act = usb_event = 0;
    bulk_size = buffer_size = 0;
    counter_limit = 0;
    diag_epoch = diag_phy_errors = diag_link_errors = 0;
    diag_unhandled_setup = diag_last_setup0 = diag_last_setup1 = 0;
    diag_clear_halt = diag_last_setup_ms = 0;
    diag_resets = diag_disconnects = diag_suspends = diag_resumes = 0;
    diag_link_failures = diag_recoveries = diag_underruns = 0;
    diag_pib_errors = diag_last_pib = diag_last_usb = diag_lpm = 0;
    sent_length = phy_errors = link_errors = 0;
    diagnostic_error = CY_U3P_SUCCESS;
    socket_reads = 0;
    usb_speed = CY_U3P_SUPER_SPEED;
    assert(app_init());
    assert(loaded && !running && !created);
}

// Configure the application without starting capture.
static void configure_fixture(void)
{
    reset_fixture();
    usb_event_cb(CY_U3P_USB_EVENT_SETCONF, 1);
    assert(app_act && ep_act && created && endpoint && nak);
    assert(!bulk_act && !running && !armed && starts == 0);
}

// Verify fresh socket order and absence of data from a previous capture.
static void assert_stream(void)
{
    assert(bulk_act && running && armed && endpoint && !nak);
    assert(gpif_socket == 0 && dma_socket == 0 && queued == 0);
    assert(counter_limit == (uint32_t)(buffer_size / 2 - 2));
}

// Model GPIF disable without resetting the remembered producer phase.
void CyU3PGpifDisable(CyBool_t forceReload)
{
    assert(!forceReload);
    running = 0;
}

// Start the existing waveform only after DMA and endpoint preparation.
CyU3PReturnStatus_t CyU3PGpifSMStart(uint8_t state, uint8_t alpha)
{
    assert(loaded && !running && armed && endpoint && !nak);
    assert(state == RESET && alpha == ALPHA_RESET && queued == 0);
    if (result(GPIF_START)) return CY_U3P_ERROR_FAILURE;
    gpif_socket = 0;
    running = 1;
    ++starts;
    return CY_U3P_SUCCESS;
}

// Configure the same buffer-length counter for each new capture.
void CyU3PGpifInitDataCounter(uint32_t initial, uint32_t limit,
    CyBool_t reload, CyBool_t up, uint8_t increment)
{
    assert(!running && initial == 0 && !reload && up && increment == 1);
    counter_limit = limit;
}

// Model endpoint creation and removal.
CyU3PReturnStatus_t CyU3PSetEpConfig(uint8_t ep, CyU3PEpConfig_t *cfg)
{
    assert(ep == EP_BULK_IN && !running);
    if (result(cfg->enable ? ENABLE : DISABLE)) return CY_U3P_ERROR_FAILURE;
    if (!cfg->enable) assert(!created);
    endpoint = cfg->enable;
    return CY_U3P_SUCCESS;
}

// Quiesce or release host transactions on the bulk endpoint.
CyU3PReturnStatus_t CyU3PUsbSetEpNak(uint8_t ep, CyBool_t value)
{
    assert(ep == EP_BULK_IN);
    if (!endpoint) return CY_U3P_ERROR_BAD_ARGUMENT;
    if (result(value ? NAK_ON : NAK_OFF)) return CY_U3P_ERROR_FAILURE;
    nak = value;
    settled = 0;
    return CY_U3P_SUCCESS;
}

// Track the USB quiesce delay; SPI delays are irrelevant to these tests.
void CyU3PBusyWait(uint16_t delay)
{
    if (delay == 125) {
        assert(nak && !running);
        settled = 1;
    }
}

// Preserve the two automatic DMA producers and speed-dependent buffer size.
CyU3PReturnStatus_t CyU3PDmaMultiChannelCreate(CyU3PDmaMultiChannel *handle,
    CyU3PDmaMultiType_t type, CyU3PDmaMultiChannelConfig_t *cfg)
{
    assert(endpoint && nak && !created && !running);
    assert(type == CY_U3P_DMA_TYPE_AUTO_MANY_TO_ONE && cfg->validSckCount == 2);
    assert(cfg->cb == NULL && cfg->notification == CY_U3P_DMA_CB_PROD_EVENT);
    assert(cfg->prodSckId[0] == CY_U3P_PIB_SOCKET_0);
    assert(cfg->prodSckId[1] == CY_U3P_PIB_SOCKET_1);
    assert(cfg->size == (usb_speed == CY_U3P_SUPER_SPEED ? 16384 : 512));
    if (result(CREATE)) return CY_U3P_ERROR_FAILURE;
    created = 1;
    buffer_size = cfg->size;
    return CY_U3P_SUCCESS;
}

// Reject DMA resets while GPIF or a USB transaction can still be active.
CyU3PReturnStatus_t CyU3PDmaMultiChannelReset(CyU3PDmaMultiChannel *handle)
{
    assert(created && !running && nak && settled);
    if (result(DMA_RESET)) return CY_U3P_ERROR_FAILURE;
    armed = queued = 0;
    dma_socket = -1;
    return CY_U3P_SUCCESS;
}

// Initialize the DMA consumer's producer ordering independently of GPIF.
CyU3PReturnStatus_t CyU3PDmaMultiChannelSetXfer(CyU3PDmaMultiChannel *handle,
    uint32_t count, uint16_t offset)
{
    assert(created && !armed && !running && queued == 0);
    assert(count == 0 && offset == 0);
    if (result(ARM)) return CY_U3P_ERROR_FAILURE;
    armed = 1;
    dma_socket = offset;
    return CY_U3P_SUCCESS;
}

// Destroy DMA only after the producer and endpoint have stopped.
CyU3PReturnStatus_t CyU3PDmaMultiChannelDestroy(CyU3PDmaMultiChannel *handle)
{
    assert(created && !running && (!endpoint || (nak && settled)));
    if (result(DESTROY)) return CY_U3P_ERROR_FAILURE;
    created = armed = queued = 0;
    return CY_U3P_SUCCESS;
}

// Flush data remaining on a quiesced endpoint.
CyU3PReturnStatus_t CyU3PUsbFlushEp(uint8_t ep)
{
    assert(ep == EP_BULK_IN && endpoint && nak && settled && !running);
    if (result(FLUSH)) return CY_U3P_ERROR_FAILURE;
    queued = 0;
    return CY_U3P_SUCCESS;
}

// Record successful vendor request acknowledgements.
void CyU3PUsbAckSetup(void) { ++acknowledgements; }

// Expose the configured USB speed.
CyU3PUSBSpeed_t CyU3PUsbGetSpeed(void) { return usb_speed; }

// Model loading the GPIF waveform without starting it.
CyU3PReturnStatus_t CyU3PGpifLoad(const CyU3PGpifConfig_t *cfg)
{ loaded = 1; return 0; }

// Unexpected firmware fatal-error loops fail the test immediately.
UINT CyU3PThreadSleep(ULONG ticks) { assert(0); return 0; }

// Supply the SDK initialization and unused control operations retained by the TU.
#define SDK_OK(type, name, args) type name args { return 0; }
#define SDK_VOID(name, args) void name args {}
SDK_OK(CyU3PReturnStatus_t, CyU3PPibInit, (CyBool_t init, CyU3PPibClock_t *clock))
SDK_OK(CyU3PReturnStatus_t, CyU3PGpioInit, (CyU3PGpioClock_t *clock, CyU3PGpioIntrCb_t cb))
SDK_OK(CyU3PReturnStatus_t, CyU3PGpioSetSimpleConfig, (uint8_t pin, CyU3PGpioSimpleConfig_t *cfg))
SDK_OK(CyU3PReturnStatus_t, CyU3PGpioSetValue, (uint8_t pin, CyBool_t value))
SDK_OK(CyU3PReturnStatus_t, CyU3PI2cInit, (void))
SDK_OK(CyU3PReturnStatus_t, CyU3PI2cSetConfig, (CyU3PI2cConfig_t *cfg, CyU3PI2cIntrCb_t cb))
SDK_OK(CyU3PReturnStatus_t, CyU3PI2cTransmitBytes,
    (CyU3PI2cPreamble_t *pre, uint8_t *data, uint32_t count, uint32_t retry))
SDK_OK(CyU3PReturnStatus_t, CyU3PI2cWaitForAck, (CyU3PI2cPreamble_t *pre, uint32_t retry))
SDK_OK(CyU3PReturnStatus_t, CyU3PUsbStart, (void))
SDK_OK(CyU3PReturnStatus_t, CyU3PUsbSetDesc,
    (CyU3PUSBSetDescType_t type, uint8_t index, uint8_t *data))
SDK_OK(CyU3PReturnStatus_t, CyU3PUsbLPMDisable, (void))
SDK_OK(CyU3PReturnStatus_t, CyU3PUsbGetEP0Data,
    (uint16_t count, uint8_t *buffer, uint16_t *received))

SDK_OK(CyU3PReturnStatus_t, CyU3PUsbStall, (uint8_t ep, CyBool_t stall, CyBool_t toggle))
SDK_VOID(CyU3PUsbRegisterSetupCallback, (CyU3PUSBSetupCb_t cb, CyBool_t fast))
SDK_VOID(CyU3PUsbRegisterEventCallback, (CyU3PUSBEventCb_t cb))
SDK_VOID(CyU3PUsbRegisterLPMRequestCallback, (CyU3PUsbLPMReqCb_t cb))

// Capture replies and enforce the EP0 buffer boundary.
CyU3PReturnStatus_t CyU3PUsbSendEP0Data(uint16_t count, uint8_t *buffer)
{
    assert(count <= sizeof(sent_bytes));
    sent_length = count;
    memcpy(sent_bytes, buffer, count);
    return 0;
}

// Provide the SDK memory helper used to clear reserved packet fields.
void CyU3PMemSet(uint8_t *data, uint8_t value, uint32_t count)
{ memset(data, value, count); }

// Return a recognizable firmware uptime for endian checks.
ULONG CyU3PGetTime(void) { return 0x12345678; }

// Model a GPIF state read failure without initializing the result.
CyU3PReturnStatus_t CyU3PGpifGetSMState(uint8_t *state)
{ if (!diagnostic_error) *state = TH1_WAIT; return diagnostic_error; }

// Model link-state reads independently of stream control.
CyU3PReturnStatus_t CyU3PUsbGetLinkPowerState(CyU3PUsbLinkPowerMode *mode)
{ if (!diagnostic_error) *mode = CyU3PUsbLPM_U0; return diagnostic_error; }

// Model the hardware's clear-on-read error counters.
CyU3PReturnStatus_t CyU3PUsbGetErrorCounts(uint16_t *phy, uint16_t *link)
{
    if (diagnostic_error) return diagnostic_error;
    *phy = phy_errors;
    *link = link_errors;
    phy_errors = link_errors = 0;
    return 0;
}

// Return distinct raw socket counters, descriptors and status without modifying DMA.
CyU3PReturnStatus_t CyU3PDmaSocketGetConfig(uint16_t id, CyU3PDmaSocketConfig_t *cfg)
{
    assert(created);
    socket_reads++;
    if (diagnostic_error) return diagnostic_error;
    cfg->status = 0x80110000;
    cfg->xferCount = 0x10203040 + id;
    cfg->dscrChain = 0x00050004;
    cfg->intr = 0x40;
    return 0;
}

// Ensure only the error callback is enabled, with no per-buffer instrumentation.
void CyU3PPibRegisterCallback(CyU3PPibIntrCb_t cb, uint32_t mask)
{ assert(cb == pib_error_cb && mask == CYU3P_PIB_INTR_ERROR); }

// Return a stable input level for status and unused SPI reads.
CyU3PReturnStatus_t CyU3PGpioGetValue(uint8_t pin, CyBool_t *value)
{ *value = 0; return 0; }

// Initialize the destination of unused EEPROM reads.
CyU3PReturnStatus_t CyU3PI2cReceiveBytes(CyU3PI2cPreamble_t *pre,
    uint8_t *data, uint32_t count, uint32_t retry)
{ memset(data, 0, count); return 0; }

// Exercise start, idempotence, stale producer phase, and both USB speeds.
static void test_capture(void)
{
    for (int high_speed = 0; high_speed < 2; ++high_speed) {
        reset_fixture();
        if (high_speed) usb_speed = CY_U3P_HIGH_SPEED;
        assert(app_start() && !running && !armed);
        queued = 4;
        gpif_socket = 1;
        assert(handle_req(VR_START, 0, 0));
        assert_stream();
        assert(acknowledgements == 1);
        assert(handle_req(VR_START, 0, 0) && starts == 1);
        for (int i = 0; i < 100; ++i) {
            gpif_socket = 1;
            queued = 4;
            assert(handle_req(VR_STOP, 0, 0));
            assert(!running && !armed && !bulk_act && queued == 0);
            assert(handle_req(VR_STOP, 0, 0));
            assert(handle_req(VR_START, 0, 0));
            assert_stream();
        }
        assert(app_stop(CyFalse));
        assert(!created && !endpoint && !app_act && !ep_act && !running);
    }
}

// Exercise vendor and USB reset paths with a producer left on thread 1.
static void test_resets(void)
{
    const CyU3PUsbEventType_t events[] = {CY_U3P_USB_EVENT_RESET,
        CY_U3P_USB_EVENT_DISCONNECT, CY_U3P_USB_EVENT_SETCONF};
    configure_fixture();
    assert(start_bulk());
    gpif_socket = 1;
    assert(handle_req(VR_RESET, 0, 0));
    assert(!running && !armed && !bulk_act && created);
    assert(start_bulk());
    assert_stream();
    for (unsigned i = 0; i < sizeof(events) / sizeof(events[0]); ++i) {
        for (int was_streaming = 0; was_streaming < 2; ++was_streaming) {
            configure_fixture();
            if (was_streaming) assert(start_bulk());
            gpif_socket = 1;
            queued = 4;
            endpoint = 0; // stack already deconfigured the endpoint
            usb_event_cb(events[i], 0);
            assert(!running && !bulk_act);
            if (events[i] != CY_U3P_USB_EVENT_SETCONF) {
                assert(!created && !ep_act);
                assert(!handle_req(VR_START, 0, 0));
                usb_event_cb(CY_U3P_USB_EVENT_SETCONF, 1);
            }
            assert(start_bulk());
            assert_stream();
        }
    }
}

// Ensure failed startup/stop requests do not acknowledge success or keep sampling.
static void test_failures(void)
{
    const enum operation setup[] = {ENABLE, NAK_ON, CREATE};
    for (unsigned i = 0; i < sizeof(setup) / sizeof(setup[0]); ++i) {
        reset_fixture();
        failure = setup[i];
        assert(!app_start());
        assert(!running && !created && !endpoint && !app_act && !ep_act);
        assert(app_start() && start_bulk());
        assert_stream();
    }
    const enum operation start[] = {NAK_ON, DMA_RESET, FLUSH, ARM, NAK_OFF, GPIF_START};
    for (unsigned i = 0; i < sizeof(start) / sizeof(start[0]); ++i) {
        configure_fixture();
        failure = start[i];
        assert(!handle_req(VR_START, 0, 0));
        assert(!running && !bulk_act && acknowledgements == 0);
        assert(handle_req(VR_START, 0, 0));
        assert_stream();
    }
    const enum operation stop[] = {NAK_ON, DMA_RESET, FLUSH};
    for (unsigned i = 0; i < sizeof(stop) / sizeof(stop[0]); ++i) {
        configure_fixture();
        assert(start_bulk());
        failure = stop[i];
        assert(!handle_req(VR_STOP, 0, 0));
        assert(!running && !bulk_act && acknowledgements == 0);
        assert(start_bulk());
        assert_stream();
    }
    const enum operation teardown[] = {NAK_ON, DESTROY, FLUSH, DISABLE};
    for (unsigned i = 0; i < sizeof(teardown) / sizeof(teardown[0]); ++i) {
        configure_fixture();
        assert(start_bulk());
        failure = teardown[i];
        assert(!app_stop(CyFalse));
        assert(!running && !bulk_act);
        assert(app_stop(CyFalse));
        assert(!created && !endpoint && !ep_act && !app_act);
        assert(app_start() && start_bulk());
        assert_stream();
    }
}

// Decode emitted words independently of the production serializer.
static uint32_t reply_word(unsigned offset)
{
    return (uint32_t)sent_bytes[offset] | ((uint32_t)sent_bytes[offset + 1] << 8) |
        ((uint32_t)sent_bytes[offset + 2] << 16) | ((uint32_t)sent_bytes[offset + 3] << 24);
}

// Verify capability discovery, request bounds, counter retention and non-invasive reads.
static void test_diagnostics(void)
{
    reset_fixture();
    assert(handle_req(VR_STAT, 0, 6));
    assert(sent_length == 6 && sent_bytes[0] == 0x32);
    assert(sent_bytes[4] == 0xD1 && sent_bytes[5] == 1);
    assert(handle_req(VR_DIAG, 0, 160));
    assert(sent_length == 160 && !socket_reads);
    assert(sent_bytes[24] == CY_U3P_ERROR_NOT_CONFIGURED && reply_word(28) == 0);
    assert(!usb_setup_cb(0x4C40, 160U << 16));
    assert(!usb_setup_cb(0x4CC1, 160U << 16));
    assert(!usb_setup_cb(0x4CC0, (160U << 16) | 1));
    assert(!usb_setup_cb(0x14CC0, 160U << 16));
    for (unsigned length = 0; length <= 0xFFFF; length++) {
        if (length != 160) assert(!usb_setup_cb(0x4CC0, length << 16));
    }
    assert(app_start() && start_bulk());
    phy_errors = 2;
    link_errors = 3;
    pib_error_cb(CYU3P_PIB_INTR_ERROR, 0x1005);
    pib_error_cb(CYU3P_PIB_INTR_DLL_UPDATE, 1);
    usb_event_cb(CY_U3P_USB_EVENT_LNK_RECOVERY, 0);
    usb_event_cb(CY_U3P_USB_EVENT_SUSPEND, 0);
    usb_event_cb(CY_U3P_USB_EVENT_RESUME, 0);
    lpm_req_cb(CyU3PUsbLPM_U1);
    assert(usb_setup_cb(0x4CC0, 160U << 16));
    assert_stream();
    assert(starts == 1 && socket_reads == 3 && reply_word(12) == 1);
    assert(!memcmp(sent_bytes, "PDG1\x01\xA0\x32\x70", 8));
    assert(reply_word(8) == 0x12345678);
    assert(sent_bytes[17] == TH1_WAIT && sent_bytes[19] == 0);
    assert(reply_word(32) == 0x10203040 + CY_U3P_PIB_SOCKET_0);
    assert(reply_word(48) == 0x10203040 + CY_U3P_PIB_SOCKET_1);
    assert(reply_word(64) == 0x10203040 + CY_U3P_UIB_SOCKET_CONS_6);
    assert(reply_word(76) == 2 && reply_word(80) == 3);
    assert(reply_word(92) == 1 && reply_word(96) == 1 && reply_word(104) == 1);
    assert(reply_word(112) == 1 && reply_word(116) == 0x1005 && reply_word(124) == 1);
    assert(reply_word(120) == CY_U3P_USB_EVENT_RESUME);
    int acks = acknowledgements;
    assert(usb_setup_cb(0x00000102, 0x00000086));
    assert(acknowledgements == acks); // observe the existing unanswered CLEAR_HALT
    assert(handle_req(VR_DIAG, 0, 160));
    assert(reply_word(128) == 1 && reply_word(132) == 0x0102);
    assert(reply_word(136) == 0x86 && reply_word(140) == 1);
    assert(reply_word(144) == 0x12345678);
    assert(usb_setup_cb(0x000000A1, 0x00080000));
    assert(handle_req(VR_DIAG, 0, 160));
    assert(reply_word(128) == 2 && reply_word(132) == 0xA1);
    assert(reply_word(136) == 0x00080000 && reply_word(140) == 1);
    assert(start_bulk() && handle_req(VR_DIAG, 0, 160));
    assert(reply_word(12) == 1 && reply_word(76) == 2 && reply_word(80) == 3);
    phy_errors = 0xFFFF;
    assert(handle_req(VR_DIAG, 0, 160));
    assert(sent_bytes[19] == 1 && reply_word(76) == 65537);
    diagnostic_error = CY_U3P_ERROR_FAILURE;
    assert(handle_req(VR_DIAG, 0, 160));
    assert(sent_bytes[17] == 0xFF && sent_bytes[20] == diagnostic_error);
    assert(sent_bytes[22] == diagnostic_error && sent_bytes[24] == diagnostic_error);
    assert(reply_word(28) == 0 && reply_word(76) == 65537);
    diagnostic_error = 0;
    assert(stop_bulk() && start_bulk() && handle_req(VR_DIAG, 0, 160));
    assert(reply_word(12) == 2 && reply_word(112) == 1);
    endpoint = 0; // stack already deconfigured the endpoint
    usb_event_cb(CY_U3P_USB_EVENT_RESET, 0);
    assert(handle_req(VR_DIAG, 0, 160));
    assert(reply_word(84) == 1 && reply_word(112) == 1);
    assert(sent_bytes[24] == CY_U3P_ERROR_NOT_CONFIGURED);
}

// Run lifecycle invariants against the actual firmware source.
int main(void)
{
    test_capture();
    test_resets();
    test_failures();
    test_diagnostics();
    puts("PocketSDR lifecycle: capture, reset, failure, cleanup and diagnostic checks passed");
    return 0;
}
