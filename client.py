"""BLE client for Typhur Sync Gold devices.

This module implements the protocol details present in API.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Any

from bleak import BleakClient, BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakScanner,
    establish_connection,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback

from .const import (
    DEFAULT_BLE_MTU_PAYLOAD,
    DEFAULT_COMMAND_TIMEOUT,
    DUMMY_USER_ID,
    NOTIFY_CHARACTERISTIC_UUID,
    WRITE_CHARACTERISTIC_UUID,
    ZSTD_MAGIC,
)
from .models import BaseStationStatus
from .parser import (
    TyphurParseError,
    build_auth_command,
    build_cooking_data_command,
    build_status_command,
    extract_device_type,
    extract_user_id,
    parse_status_response,
)

_LOGGER = logging.getLogger(__name__)

FRAME_CONTROL_ENCRYPTED = 0x08
FRAME_CONTROL_FRAGMENTED = 0x10
FRAME_TYPE_DH_PUBLIC_KEY = 0x01
FRAME_TYPE_DEVICE_READY = 0x48
FRAME_TYPE_DEVICE_READY_LEGACY = 0x49
FRAME_TYPE_ERROR = 0x31
FRAME_TYPE_APP_PAYLOAD = 0x4D
DH_PUBLIC_KEY_LENGTH = 128
APPLICATION_ENVELOPE_MAGIC = b"\xaa\xaa"
APPLICATION_ENVELOPE_HEADER_LENGTH = 6
APPLICATION_ENVELOPE_CRC_LENGTH = 4


class TyphurError(Exception):
    """Base Typhur error."""


class TyphurConnectionError(TyphurError):
    """Raised when BLE communication fails."""


class TyphurProtocolError(TyphurError):
    """Raised when protocol handling fails."""


@dataclass(slots=True, frozen=True)
class TyphurAuthResult:
    """Authentication result."""

    user_id: str | None = None
    device_type: str | None = None


@dataclass(slots=True, frozen=True)
class BluFiFrame:
    """A decoded BluFi frame."""

    frame_type: int
    control: int
    sequence: int
    payload: bytes

    @property
    def encrypted(self) -> bool:
        """Return true if the frame payload is encrypted."""
        return bool(self.control & FRAME_CONTROL_ENCRYPTED)

    @property
    def fragmented(self) -> bool:
        """Return true if the frame is fragmented."""
        return bool(self.control & FRAME_CONTROL_FRAGMENTED)


class FragmentAssembler:
    """Assemble fragmented BluFi payloads."""

    def __init__(self) -> None:
        self._expected_length: int | None = None
        self._buffer = bytearray()

    def add(self, frame: BluFiFrame) -> bytes | None:
        """Add a frame and return a complete payload when available."""
        payload = frame.payload
        if self._expected_length is None:
            if len(payload) < 2:
                raise TyphurProtocolError("First fragmented packet is missing length")
            self._expected_length = int.from_bytes(payload[:2], "little")
            self._buffer.extend(payload[2:])
        else:
            if frame.fragmented and frame.frame_type == FRAME_TYPE_APP_PAYLOAD:
                if len(payload) < 2:
                    raise TyphurProtocolError("Fragmented app packet is missing length")
                payload = payload[2:]
            self._buffer.extend(payload)

        if frame.fragmented:
            return None

        if self._expected_length is None:
            raise TyphurProtocolError("Fragment state was reset unexpectedly")
        
        if len(self._buffer) != self._expected_length:
            raise TyphurProtocolError(
                "Fragment length mismatch: "
                f"expected {self._expected_length}, got {len(self._buffer)}"
            )

        complete = bytes(self._buffer)
        self.reset()
        return complete

    def reset(self) -> None:
        """Reset the assembler."""
        self._expected_length = None
        self._buffer.clear()

    @property
    def active(self) -> bool:
        """Return true if a fragmented payload is being assembled."""
        return self._expected_length is not None


def crc16_ccitt(data: bytes) -> int:
    """Calculate CRC-16/CCITT with polynomial 0x1021 and init 0x0000."""
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def _sequence_iv(sequence: int) -> bytes:
    """Build the AES-CFB IV for a sequence number."""
    return bytes([sequence & 0xFF]) + b"\x00" * 15


def aes_cfb_encrypt(key: bytes, sequence: int, payload: bytes) -> bytes:
    """Encrypt a payload using AES-128-CFB."""
    encryptor = Cipher(algorithms.AES(key), modes.CFB(_sequence_iv(sequence))).encryptor()
    return encryptor.update(payload) + encryptor.finalize()


def aes_cfb_decrypt(key: bytes, sequence: int, payload: bytes) -> bytes:
    """Decrypt a payload using AES-128-CFB."""
    decryptor = Cipher(algorithms.AES(key), modes.CFB(_sequence_iv(sequence))).decryptor()
    return decryptor.update(payload) + decryptor.finalize()


def derive_aes_key(shared_secret: int) -> bytes:
    """Derive the documented AES key from a DH shared secret."""
    return hashlib.md5(shared_secret.to_bytes(DH_PUBLIC_KEY_LENGTH, "big")).digest()


def _zstd_compress(payload: bytes) -> bytes:
    """Compress bytes using zstandard."""
    try:
        import zstandard as zstd
    except ImportError as err:
        raise TyphurProtocolError("zstandard is required for Typhur payloads") from err
    return zstd.ZstdCompressor().compress(payload)


def _zstd_decompress(payload: bytes, max_output_size: int | None = None) -> bytes:
    """Decompress bytes using zstandard."""
    try:
        import zstandard as zstd
    except ImportError as err:
        raise TyphurProtocolError("zstandard is required for Typhur payloads") from err
    decompressor = zstd.ZstdDecompressor()
    if max_output_size is None:
        return decompressor.decompress(payload)
    return decompressor.decompress(payload, max_output_size=max_output_size)


def _zstd_decompress_with_trailing_byte_fallback(
    payload: bytes, max_output_size: int | None = None
) -> bytes:
    """Decompress zstd bytes, tolerating documented trailing transport noise."""
    last_error: Exception | None = None
    for drop_count in range(25):
        candidate = payload if drop_count == 0 else payload[:-drop_count]
        if not candidate:
            break
        try:
            decompressed = _zstd_decompress(candidate, max_output_size=max_output_size)
        except Exception as err:  # zstandard raises its own extension exception type.
            last_error = err
            continue
        if b"{" in decompressed:
            return decompressed[decompressed.index(b"{") :]
    raise TyphurProtocolError("Unable to decompress Typhur zstd payload") from last_error


def encode_application_payload(json_payload: bytes) -> bytes:
    """Encode JSON bytes into the Typhur application envelope."""
    compressed = _zstd_compress(json_payload)
    if len(compressed) > 0xFFFF or len(json_payload) > 0xFFFF:
        raise TyphurProtocolError("Typhur application payload is too large")
    envelope_without_crc = (
        APPLICATION_ENVELOPE_MAGIC
        + len(compressed).to_bytes(2, "little")
        + len(json_payload).to_bytes(2, "little")
        + compressed
    )
    package_crc = crc16_ccitt(envelope_without_crc[2:])
    real_crc = crc16_ccitt(json_payload)
    return (
        envelope_without_crc
        + package_crc.to_bytes(2, "little")
        + real_crc.to_bytes(2, "little")
    )


def decode_application_payload(payload: bytes) -> bytes:
    """Decode a Typhur application payload into JSON bytes."""
    if payload.startswith(APPLICATION_ENVELOPE_MAGIC):
        if len(payload) < APPLICATION_ENVELOPE_HEADER_LENGTH + APPLICATION_ENVELOPE_CRC_LENGTH:
            raise TyphurProtocolError("Typhur application envelope is too short")
        compressed_length = int.from_bytes(payload[2:4], "little")
        original_length = int.from_bytes(payload[4:6], "little")
        expected_length = (
            APPLICATION_ENVELOPE_HEADER_LENGTH
            + compressed_length
            + APPLICATION_ENVELOPE_CRC_LENGTH
        )
        if len(payload) != expected_length:
            raise TyphurProtocolError(
                "Typhur application envelope length mismatch: "
                f"expected {expected_length}, got {len(payload)}"
            )
        compressed = payload[6 : 6 + compressed_length]
        package_crc = int.from_bytes(payload[-4:-2], "little")
        real_crc = int.from_bytes(payload[-2:], "little")
        calculated_package_crc = crc16_ccitt(payload[2:-4])
        if package_crc != calculated_package_crc:
            raise TyphurProtocolError(
                "Typhur application package CRC mismatch: "
                f"expected {package_crc}, got {calculated_package_crc}"
            )
        _LOGGER.debug(
            "Typhur: decompressing payload - envelope_len=%s, compressed_len=%s, original_len=%s, first_bytes=%s",
            len(payload),
            compressed_length,
            original_length,
            payload[:20].hex(),
        )
        json_payload = _zstd_decompress_with_trailing_byte_fallback(
            compressed, max_output_size=original_length
        )
        if len(json_payload) != original_length:
            raise TyphurProtocolError(
                "Typhur application JSON length mismatch: "
                f"expected {original_length}, got {len(json_payload)}"
            )
        calculated_real_crc = crc16_ccitt(json_payload)
        if real_crc != calculated_real_crc:
            raise TyphurProtocolError(
                "Typhur application real CRC mismatch: "
                f"expected {real_crc}, got {calculated_real_crc}"
            )
        return json_payload

    if payload.startswith(ZSTD_MAGIC):
        return _zstd_decompress_with_trailing_byte_fallback(payload)

    magic_index = payload.find(ZSTD_MAGIC)
    if magic_index >= 0:
        return _zstd_decompress_with_trailing_byte_fallback(payload[magic_index:])

    return payload


def decode_frame(data: bytes, aes_key: bytes | None = None) -> BluFiFrame:
    """Decode a raw BluFi frame."""
    if len(data) < 4:
        raise TyphurProtocolError("BluFi frame is shorter than its header")
    frame_type = data[0]
    control = data[1]
    sequence = data[2]
    length = data[3]
    payload = data[4 : 4 + length]
    if len(payload) != length:
        raise TyphurProtocolError("BluFi frame payload length mismatch")
    if control & FRAME_CONTROL_ENCRYPTED:
        if aes_key is None:
            raise TyphurProtocolError("Encrypted payload received before key exchange")
        payload = aes_cfb_decrypt(aes_key, sequence, payload)
    return BluFiFrame(frame_type, control, sequence, payload)


def encode_frames(
    payload: bytes,
    sequence_factory: Callable[[], int],
    aes_key: bytes | None = None,
    frame_type: int = FRAME_TYPE_APP_PAYLOAD,
    max_payload: int = DEFAULT_BLE_MTU_PAYLOAD,
) -> list[bytes]:
    """Encode a payload into one or more BluFi frames."""
    encrypted = aes_key is not None
    if len(payload) <= max_payload:
        sequence = sequence_factory()
        frame_payload = aes_cfb_encrypt(aes_key, sequence, payload) if encrypted else payload
        control = FRAME_CONTROL_ENCRYPTED if encrypted else 0
        return [bytes([frame_type, control, sequence, len(frame_payload)]) + frame_payload]

    frames: list[bytes] = []
    remaining = len(payload).to_bytes(2, "little") + payload
    while remaining:
        chunk = remaining[:max_payload]
        remaining = remaining[max_payload:]
        sequence = sequence_factory()
        frame_payload = aes_cfb_encrypt(aes_key, sequence, chunk) if encrypted else chunk
        control = FRAME_CONTROL_ENCRYPTED if encrypted else 0
        if remaining:
            control |= FRAME_CONTROL_FRAGMENTED
        frames.append(bytes([frame_type, control, sequence, len(frame_payload)]) + frame_payload)
    return frames


class TyphurBleClient:
    """Client for one Typhur Sync Gold BLE base station."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        status_callback: Callable[[BaseStationStatus], None] | None = None,
        unavailable_callback: Callable[[], None] | None = None,
    ) -> None:
        self.hass = hass
        self.address = address
        self.name = name
        self._status_callback = status_callback
        self._unavailable_callback = unavailable_callback
        self._client: BleakClient | None = None
        self._aes_key: bytes | None = None
        self._handshake_ready = False
        self._frame_sequence = 0
        self._command_sequence = 0
        self._notification_buffer = bytearray()
        self._fragment_assembler = FragmentAssembler()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._dh_public_key_future: asyncio.Future[bytes | None] | None = None
        self._last_status: BaseStationStatus | None = None
        self._last_user_id: str | None = None
        self._status_event = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        """Return true if the BLE client is connected."""
        return bool(self._client and self._client.is_connected)

    async def connect(self) -> None:
        """Connect and subscribe to notifications."""
        if self.is_connected:
            return
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            ble_device = await BleakScanner.find_device_by_address(
                self.address, timeout=10.0
            )
        if ble_device is None:
            raise TyphurConnectionError(f"No connectable BLE device for {self.address}")
        client = None
        try:
            self._reset_protocol_state()
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                ble_device.address,
                disconnected_callback=self._disconnected,
                max_attempts=3,
                use_services_cache=True,
            )
            self._client = client
            if self._client is not client or not client.is_connected:
                raise TyphurConnectionError(f"Disconnected while connecting to {self.address}")
            with suppress(AttributeError):
                await client.get_services()
            with suppress(AttributeError):
                await client.acquire_mtu()
            await client.start_notify(
                NOTIFY_CHARACTERISTIC_UUID, self._notification_handler
            )
            await asyncio.sleep(2.0)
        except (BleakError, TimeoutError) as err:
            self._client = None
            with suppress(BleakError, TimeoutError):
                if client is not None and client.is_connected:
                    await client.disconnect()
            raise TyphurConnectionError(f"Failed to connect to {self.address}: {err}") from err

    async def disconnect(self) -> None:
        """Disconnect from the BLE device."""
        client = self._client
        self._client = None
        self._reset_protocol_state()
        self._cancel_pending(TyphurConnectionError("Disconnected"))
        if client is None:
            return
        try:
            if client.is_connected:
                await client.stop_notify(NOTIFY_CHARACTERISTIC_UUID)
                await client.disconnect()
        except (BleakError, TimeoutError) as err:
            _LOGGER.debug("Error while disconnecting from %s: %s", self.address, err)

    async def authenticate(
        self, user_id: str | None, device_type: str | None
    ) -> TyphurAuthResult:
        """Authenticate and return discovered authentication details.

        Sends the trust command and waits for the receipt. If the receipt
        contains a different userId, we extract it for the caller to use.
        """
        command = build_auth_command(
            self.address, device_type, user_id or DUMMY_USER_ID,
            self._next_command_sequence(),
        )
        cmd_id = command.get("cmdId")
        _LOGGER.debug(
            "Typhur %s: sending auth command with cmdId=%s, userId=%s",
            self.address,
            cmd_id,
            command.get("cmdData", {}).get("userId"),
        )
        
        # Create a future to wait for the receipt
        # We can't rely on exact cmdId match because the device truncates it
        receipt_future: asyncio.Future[dict[str, Any]] = self.hass.loop.create_future()
        self._pending[cmd_id] = receipt_future
        
        # Send the command
        await self._ensure_handshake_ready()
        client = self._require_client()
        payload = encode_application_payload(
            json.dumps(command, separators=(",", ":")).encode()
        )
        async with asyncio.timeout(DEFAULT_COMMAND_TIMEOUT):
            await self._write_frames(
                client,
                encode_frames(payload, self._next_frame_sequence, self._aes_key),
            )
        
        # Wait for the receipt
        try:
            async with asyncio.timeout(DEFAULT_COMMAND_TIMEOUT):
                receipt = await receipt_future
        except TimeoutError:
            _LOGGER.debug(
                "Typhur %s: timed out waiting for auth receipt",
                self.address,
            )
            return TyphurAuthResult()
        finally:
            self._pending.pop(cmd_id, None)
        
        _LOGGER.debug(
            "Typhur %s: auth receipt received: %s",
            self.address,
            receipt,
        )
        
        # Extract userId from receipt
        discovered_user_id = extract_user_id(receipt)
        if discovered_user_id and discovered_user_id != DUMMY_USER_ID:
            self._last_user_id = discovered_user_id
            _LOGGER.debug(
                "Typhur %s: discovered user ID from auth receipt: %s",
                self.address,
                discovered_user_id,
            )
        
        return TyphurAuthResult(
            user_id=discovered_user_id,
            device_type=extract_device_type(receipt),
        )

    async def request_status(self, device_type: str) -> BaseStationStatus:
        """Request status and wait for the response.

        The device sends a receipt with the cmdId, then pushes status data.
        This method sends the request, waits for the receipt, then waits
        for the status push.
        """
        self._status_event.clear()
        _LOGGER.debug("Typhur %s: requesting status", self.address)
        command = build_status_command(
            self.address, device_type, self._next_command_sequence()
        )
        await self._send_fire_and_forget(command)
        _LOGGER.debug("Typhur %s: status request sent, waiting for status push", self.address)
        try:
            async with asyncio.timeout(DEFAULT_COMMAND_TIMEOUT):
                await self._status_event.wait()
        except TimeoutError as err:
            _LOGGER.debug(
                "Typhur %s: timed out waiting for status push", self.address
            )
            raise TyphurConnectionError(
                f"Timed out waiting for status from {self.address}"
            ) from err
        _LOGGER.debug(
            "Typhur %s: status push received, returning status", self.address
        )
        if self._last_status is None:
            raise TyphurConnectionError(
                f"Status event fired but no status data from {self.address}"
            )
        return self._last_status

    async def request_cooking_data(
        self, device_type: str | None
    ) -> dict[str, Any]:
        """Request cooking data."""
        return await self._send_command(
            build_cooking_data_command(
                self.address, device_type, self._next_command_sequence()
            )
        )

    async def _send_fire_and_forget(self, command: dict[str, Any]) -> None:
        """Send a command without waiting for a response."""
        await self._ensure_handshake_ready()
        client = self._require_client()
        payload = encode_application_payload(
            json.dumps(command, separators=(",", ":")).encode()
        )
        async with asyncio.timeout(DEFAULT_COMMAND_TIMEOUT):
            await self._write_frames(
                client,
                encode_frames(payload, self._next_frame_sequence, self._aes_key),
            )

    async def _send_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send a command and wait for its response."""
        await self._ensure_handshake_ready()
        client = self._require_client()
        cmd_id = command["cmdId"]
        future: asyncio.Future[dict[str, Any]] = self.hass.loop.create_future()
        self._pending[cmd_id] = future
        payload = encode_application_payload(
            json.dumps(command, separators=(",", ":")).encode()
        )
        try:
            async with asyncio.timeout(DEFAULT_COMMAND_TIMEOUT):
                await self._write_frames(
                    client,
                    encode_frames(payload, self._next_frame_sequence, self._aes_key),
                )
                return await future
        except TimeoutError as err:
            raise TyphurConnectionError(
                f"Timed out waiting for response to {command['cmdType']}"
            ) from err
        finally:
            self._pending.pop(cmd_id, None)

    async def _ensure_handshake_ready(self) -> None:
        """Ensure the device is ready for application payloads.

        The Typhur Sync Gold device does not support DH key exchange.
        Communication is always plaintext.
        """
        if self._aes_key is not None or self._handshake_ready:
            return
        self._handshake_ready = True
        _LOGGER.debug(
            "Typhur %s: handshake ready (plaintext, no DH)", self.address
        )

    async def _write_frame(
        self, client: BleakClient, frame: bytes, *, response: bool = True
    ) -> None:
        """Write one BluFi frame."""
        await asyncio.sleep(0.1)
        try:
            await client.write_gatt_char(
                WRITE_CHARACTERISTIC_UUID, frame, response=response
            )
        except BleakError as err:
            if response:
                if not client.is_connected:
                    raise TyphurConnectionError(
                        "Typhur write with response disconnected "
                        f"{self.address}: {err}"
                    ) from err
                _LOGGER.debug(
                    "Typhur write with response failed for %s; "
                    "retrying without response: %s",
                    self.address,
                    err,
                )
                try:
                    await client.write_gatt_char(
                        WRITE_CHARACTERISTIC_UUID, frame, response=False
                    )
                except BleakError as fallback_err:
                    raise TyphurConnectionError(
                        "Typhur write fallback failed for "
                        f"{self.address}: {fallback_err}"
                    ) from fallback_err
            else:
                raise TyphurConnectionError(
                    f"Typhur write failed for {self.address}: {err}"
                ) from err

    async def _write_frames(self, client: BleakClient, frames: list[bytes]) -> None:
        """Write one or more BluFi frames with documented pacing."""
        for index, frame in enumerate(frames):
            await self._write_frame(client, frame)
            if index < len(frames) - 1:
                await asyncio.sleep(0.3)

    def _require_client(self) -> BleakClient:
        """Return the active client or raise."""
        if self._client is None or not self._client.is_connected:
            raise TyphurConnectionError("BLE client is not connected")
        return self._client

    def _next_frame_sequence(self) -> int:
        """Return the next 8-bit frame sequence."""
        sequence = self._frame_sequence
        self._frame_sequence = (self._frame_sequence + 1) & 0xFF
        return sequence

    def _next_command_sequence(self) -> int:
        """Return the next command sequence."""
        self._command_sequence += 1
        return self._command_sequence

    def _cancel_pending(self, err: Exception) -> None:
        """Cancel all pending requests with an exception."""
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._dh_public_key_future is not None and not self._dh_public_key_future.done():
            self._dh_public_key_future.cancel()
        self._dh_public_key_future = None

    def _reset_protocol_state(self) -> None:
        """Reset per-connection protocol state."""
        self._aes_key = None
        self._handshake_ready = False
        self._frame_sequence = 0
        self._command_sequence = 0
        self._notification_buffer.clear()
        self._fragment_assembler.reset()
        self._status_event.clear()

    @callback
    def _notification_handler(self, _sender: int, data: bytearray) -> None:
        """Handle raw BLE notification data."""
        self.hass.async_create_task(self._async_handle_notification(bytes(data)))

    async def _async_handle_notification(self, data: bytes) -> None:
        """Buffer notification bytes and decode complete BluFi frames."""
        try:
            _LOGGER.debug(
                "Received raw Typhur BLE notification from %s: len=%s data=%s",
                self.address,
                len(data),
                data.hex(),
            )
            self._notification_buffer.extend(data)
            _LOGGER.debug(
                "Typhur %s: buffer size=%s, first 4 bytes=%s",
                self.address,
                len(self._notification_buffer),
                self._notification_buffer[:4].hex() if len(self._notification_buffer) >= 4 else "N/A",
            )
            while len(self._notification_buffer) >= 4:
                frame_length = 4 + self._notification_buffer[3]
                _LOGGER.debug(
                    "Typhur %s: checking frame: buffer=%s frame_length=%s control=0x%02x",
                    self.address,
                    len(self._notification_buffer),
                    frame_length,
                    self._notification_buffer[1],
                )
                if len(self._notification_buffer) < frame_length:
                    _LOGGER.debug(
                        "Typhur %s: buffer too small, waiting for more data",
                        self.address,
                    )
                    break
                raw_frame = bytes(self._notification_buffer[:frame_length])
                del self._notification_buffer[:frame_length]
                _LOGGER.debug(
                    "Decoding Typhur frame from %s: len=%s control=0x%02x",
                    self.address,
                    len(raw_frame),
                    raw_frame[1],
                )
                frame = decode_frame(raw_frame, self._aes_key)
                self._handle_frame(frame, raw_frame)
        except (json.JSONDecodeError, TyphurError, TyphurParseError) as err:
            _LOGGER.warning("Failed to process Typhur notification: %s", err)

    def _handle_frame(self, frame: BluFiFrame, raw_frame: bytes) -> None:
        """Handle a decoded BluFi frame."""
        _LOGGER.debug(
            "Handling Typhur frame from %s: type=0x%02x control=0x%02x len=%s",
            self.address,
            frame.frame_type,
            frame.control,
            len(frame.payload),
        )
        try:
            if frame.frame_type == FRAME_TYPE_DH_PUBLIC_KEY:
                payload = (
                    self._fragment_assembler.add(frame)
                    if frame.fragmented or self._fragment_assembler.active
                    else frame.payload
                )
                if payload is None:
                    return
                _LOGGER.debug(
                    "Received Typhur DH public key frame from %s; header=%s payload_len=%s",
                    self.address,
                    raw_frame[:4].hex(),
                    len(payload),
                )
                self._handle_dh_public_key(payload)
                return
            if frame.frame_type in (FRAME_TYPE_DEVICE_READY, FRAME_TYPE_DEVICE_READY_LEGACY):
                _LOGGER.debug(
                    "Received Typhur ready/init frame from %s; header=%s payload=%s",
                    self.address,
                    raw_frame[:4].hex(),
                    frame.payload.hex(),
                )
                self._handle_device_ready()
                return
            if frame.frame_type == FRAME_TYPE_ERROR:
                _LOGGER.debug(
                    "Typhur error frame received from %s: %s",
                    self.address,
                    frame.payload.hex(),
                )
                return
            if frame.frame_type != FRAME_TYPE_APP_PAYLOAD:
                _LOGGER.debug(
                    "Ignoring unknown Typhur frame type 0x%02x from %s",
                    frame.frame_type,
                    self.address,
                )
                return
            payload = (
                self._fragment_assembler.add(frame)
                if frame.fragmented or self._fragment_assembler.active
                else frame.payload
            )
            if payload is None:
                return
            payload = decode_application_payload(payload)
            message = json.loads(payload.decode())
            if not isinstance(message, dict):
                raise TyphurProtocolError("Decoded notification is not a JSON object")
            self._handle_message(message)
        except (json.JSONDecodeError, TyphurError, TyphurParseError) as err:
            _LOGGER.warning("Failed to process Typhur frame: %s", err)

    def _handle_dh_public_key(self, payload: bytes) -> None:
        """Handle the device's DH public key payload."""
        if self._dh_public_key_future is None:
            _LOGGER.debug("Ignoring unexpected Typhur DH public key from %s", self.address)
            return
        if not self._dh_public_key_future.done():
            self._dh_public_key_future.set_result(payload)

    def _handle_device_ready(self) -> None:
        """Handle a ready/init frame during the handshake."""
        if self._dh_public_key_future is not None and not self._dh_public_key_future.done():
            self._dh_public_key_future.set_result(None)

    def _handle_message(self, message: dict[str, Any]) -> None:
        """Route a decoded message to pending commands and listeners."""
        import json as _json
        try:
            msg_str = _json.dumps(message, indent=2)
            _LOGGER.debug(
                "Typhur %s: handling message cmdType=%s\n%s",
                self.address,
                message.get("cmdType"),
                msg_str[:2000],
            )
        except Exception:
            _LOGGER.debug(
                "Typhur %s: handling message cmdType=%s (could not serialize)",
                self.address,
                message.get("cmdType"),
            )
        matched_future: asyncio.Future[dict[str, Any]] | None = None
        cmd_id = message.get("cmdId")
        if isinstance(cmd_id, str):
            matched_future = self._pending.get(cmd_id)

        cmd_data = message.get("cmdData", {})
        if matched_future is None and isinstance(cmd_data, dict):
            original_cmd_id = cmd_data.get("cmdId")
            if isinstance(original_cmd_id, str):
                matched_future = self._pending.get(original_cmd_id)
            if matched_future is None and isinstance(original_cmd_id, str):
                for pending_id, future in self._pending.items():
                    if original_cmd_id.startswith(pending_id[:20]):
                        matched_future = future
                        break

        if matched_future is not None and not matched_future.done():
            matched_future.set_result(message)

        user_id = message.get("userId")
        if isinstance(user_id, str) and user_id and user_id != self._last_user_id:
            _LOGGER.debug(
                "Typhur %s: discovered user ID from receipt: %s",
                self.address,
                user_id,
            )
            self._last_user_id = user_id

        cmd_type = message.get("cmdType")
        if isinstance(cmd_type, str) and "status" in cmd_type:
            _LOGGER.debug(
                "Typhur %s: received status cmdType=%s", self.address, cmd_type
            )
            try:
                status = parse_status_response(message)
            except TyphurParseError as err:
                _LOGGER.warning("Ignoring invalid Typhur status response: %s", err)
            else:
                _LOGGER.debug(
                    "Typhur %s: status decoded successfully, probes=%s",
                    self.address,
                    status.discovered_probe_count,
                )
                self._last_status = status
                self._status_event.set()
                if self._status_callback is not None:
                    self._status_callback(status)

    def _disconnected(self, _client: BleakClient) -> None:
        """Handle a BLE disconnect."""
        self._client = None
        self._reset_protocol_state()
        self._cancel_pending(TyphurConnectionError("BLE device disconnected"))
        if self._unavailable_callback is not None:
            self._unavailable_callback()
