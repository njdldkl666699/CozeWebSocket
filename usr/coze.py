import _thread
import sys
from queue import Queue

import request
import ubinascii
import ujson
import utime
from usr import logging, packet, uwebsocket
from usr.media import Media, release_singleton_media, singleton_media

LOGGER = logging.getLogger("coze")

DEFAULT_DEVICE_ID = "quecpython_device"
DEFAULT_MAX_UPLOAD_AUDIO_BYTES = 262144


def _now_ms() -> int:
    return utime.ticks_ms()


def _decode_if_needed(data):
    if isinstance(data, bytes):
        return data.decode("utf-8")
    return data


def _print_exception(exc: Exception) -> None:
    printer = getattr(sys, "print_exception", None)
    if printer:
        printer(exc)
    else:
        LOGGER.error(repr(exc))


def _b64encode(data: bytes) -> str:
    encoded = ubinascii.b2a_base64(data).strip()
    if isinstance(encoded, bytes):
        return encoded.decode("utf-8")
    return encoded


def _get_first(data: dict, keys: tuple, default=None):
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return default


def _get_message_id(data: dict):
    message = data.get("message") or {}
    return _get_first(
        data,
        ("id", "message_id", "item_id", "content_id"),
        _get_first(message, ("id", "message_id")),
    )


class AudioTurnRecorder:
    def __init__(self, device_id: str, max_bytes: int) -> None:
        self.device_id = device_id
        self.max_bytes = max_bytes
        self.lock = _thread.allocate_lock()
        self.turn_seq = 0
        self.reset()

    def reset(self) -> None:
        self.turn_id = None
        self.capture_started_ms = None
        self.capture_finished_ms = None
        self.chunks = []
        self.size = 0
        self.dropped = 0
        self.capturing = False

    def start(self) -> str:
        self.lock.acquire()
        try:
            self.turn_seq += 1
            self.reset()
            self.turn_id = "{}-{}".format(self.device_id, self.turn_seq)
            self.capture_started_ms = _now_ms()
            self.capturing = True
            return self.turn_id
        finally:
            self.lock.release()

    def stop(self) -> None:
        self.lock.acquire()
        try:
            if self.capturing:
                self.capture_finished_ms = _now_ms()
            self.capturing = False
        finally:
            self.lock.release()

    def append(self, data: bytes) -> None:
        if not data:
            return
        self.lock.acquire()
        try:
            if not self.capturing:
                return
            remain = self.max_bytes - self.size
            if remain <= 0:
                self.dropped += len(data)
                return
            if len(data) > remain:
                self.chunks.append(data[:remain])
                self.size += remain
                self.dropped += len(data) - remain
            else:
                self.chunks.append(data)
                self.size += len(data)
        finally:
            self.lock.release()

    def snapshot(self):
        self.lock.acquire()
        try:
            if self.turn_id is None or self.size <= 0:
                return None
            audio = b"".join(self.chunks)
            return {
                "audio_turn_id": self.turn_id,
                "audio": audio,
                "capture_started_ms": self.capture_started_ms,
                "capture_finished_ms": self.capture_finished_ms or _now_ms(),
                "dropped_audio_bytes": self.dropped,
            }
        finally:
            self.lock.release()


class CozeWebSocket:
    def __init__(
        self,
        url: str,
        auth: str,
        callback=None,
        audio_upload_url: str | None = None,
        audio_upload_token: str | None = None,
        device_id: str = DEFAULT_DEVICE_ID,
        max_upload_audio_bytes: int = DEFAULT_MAX_UPLOAD_AUDIO_BYTES,
    ) -> None:
        self.media = None

        self.url = url
        self.headers = {"Authorization": "Bearer " + auth}
        self.callback = callback
        self.volume = 8
        self.client = None
        self.is_active = False
        self.running = False

        self.audio_queue = Queue()
        self.event_queue = Queue()
        self.upload_queue = Queue()

        self.ws_recv_task_id = None
        self.ws_audio_uplink_handler_id = None
        self.ws_audio_downlink_handler_id = None
        self.ws_callback_event_id = None
        self.ws_upload_handler_id = None

        self.audio_upload_url = audio_upload_url
        self.audio_upload_token = audio_upload_token
        self.device_id = device_id or DEFAULT_DEVICE_ID
        self.turn_recorder = AudioTurnRecorder(self.device_id, max_upload_audio_bytes)
        self.coze_ids = {
            "conversation_id": None,
            "section_id": None,
            "chat_id": None,
            "message_id": None,
            "event_id": None,
        }

    def start(self) -> None:
        media = singleton_media("pcma", Media.MEDIA_TYPE_PCMA)
        if media is None:
            LOGGER.warn("media is busy, please stop it first")
            return
        self.media = media

        self.client = uwebsocket.Client.connect(self.url, self.headers)
        self.running = True
        self.client.send(ujson.dumps(packet.build_chat_update()))
        self.ws_recv_task_id = _thread.start_new_thread(self.ws_recv_task, ())

        if self.callback:
            self.ws_callback_event_id = _thread.start_new_thread(self.ws_server_event_handler, ())
        if self.audio_upload_url:
            self.ws_upload_handler_id = _thread.start_new_thread(self.ws_audio_upload_handler, ())

    def stop(self) -> None:
        self.running = False
        try:
            self.stop_audio_stream()
        finally:
            if self.ws_recv_task_id:
                _thread.stop_thread(self.ws_recv_task_id)
                self.ws_recv_task_id = None
            if self.ws_callback_event_id:
                _thread.stop_thread(self.ws_callback_event_id)
                self.ws_callback_event_id = None
            if self.ws_upload_handler_id:
                _thread.stop_thread(self.ws_upload_handler_id)
                self.ws_upload_handler_id = None
            if self.client:
                self.client.close()
                self.client = None
            release_singleton_media()
            self.is_active = False

    def ws_audio_uplink_handler(self) -> None:
        if not self.media or not self.client:
            LOGGER.warn("media or client is not ready, cannot start audio uplink")
            return

        while self.running and self.is_active:
            try:
                frames = []
                for _ in range(5):
                    frame = self.media.pcma_read()
                    if frame:
                        frames.append(frame)
                data = b"".join(frames)
                if data:
                    self.turn_recorder.append(data)
                    payload = packet.build_audio_append(_b64encode(data))
                    self.client.send(ujson.dumps(payload))
                utime.sleep_ms(1)
            except Exception as e:
                LOGGER.error("audio uplink error")
                _print_exception(e)

    def ws_audio_downlink_handler(self) -> None:
        if not self.media:
            LOGGER.warn("media is not ready, cannot start audio downlink")
            return

        while self.running and self.is_active:
            event = self.audio_queue.get()
            try:
                data = event.get("data") or {}
                content = _get_first(data, ("content", "delta"))
                if content:
                    self.media.pcma_write(ubinascii.a2b_base64(content))
            except Exception as e:
                LOGGER.error("audio downlink error")
                _print_exception(e)
            utime.sleep_ms(1)

    def ws_server_event_handler(self) -> None:
        if not self.callback:
            LOGGER.warn("callback is not set, cannot start server event handler")
            return

        while self.running:
            event = self.event_queue.get()
            try:
                self.callback(self, event)
            except Exception as e:
                LOGGER.error("callback error")
                _print_exception(e)
            utime.sleep_ms(1)

    def ws_audio_upload_handler(self) -> None:
        while self.running:
            payload = self.upload_queue.get()
            self.upload_user_audio(payload)
            utime.sleep_ms(1)

    def start_audio_stream(self) -> None:
        if not self.media:
            LOGGER.warn("media is not ready, cannot start audio stream")
            return
        if self.is_active:
            return

        self.media.start()
        self.media.set_volume(self.volume)
        self.is_active = True
        self.ws_audio_uplink_handler_id = _thread.start_new_thread(self.ws_audio_uplink_handler, ())
        self.ws_audio_downlink_handler_id = _thread.start_new_thread(
            self.ws_audio_downlink_handler, ()
        )

    def stop_audio_stream(self) -> None:
        if not self.media:
            LOGGER.warn("media is not ready, cannot stop audio stream")
            return

        if self.ws_audio_uplink_handler_id:
            _thread.stop_thread(self.ws_audio_uplink_handler_id)
            self.ws_audio_uplink_handler_id = None
        if self.ws_audio_downlink_handler_id:
            _thread.stop_thread(self.ws_audio_downlink_handler_id)
            self.ws_audio_downlink_handler_id = None
        if not self.media.is_idle():
            self.media.stop()
        self.is_active = False

    def ws_recv_task(self) -> None:
        if not self.client:
            LOGGER.warn("client is not ready, cannot start ws_recv_task")
            return

        while self.running:
            recv_data = None
            try:
                recv_data = self.client.recv()
                if not recv_data:
                    LOGGER.warn("illegal data {}".format(recv_data))
                    continue

                event = self.parse_event(recv_data)
                if event is None:
                    continue

                if event.get("event_type") == packet.EventType.CONVERSATION_AUDIO_DELTA:
                    self.audio_queue.put(event)
                else:
                    self.handle_server_event(event)
                    if self.callback:
                        self.event_queue.put(event)
            except Exception as e:
                if "EIO" in str(e):
                    _print_exception(e)
                    self.handle_disconnect()
                    break
                if recv_data is not None:
                    LOGGER.error("recv error[{}] |{}|".format(len(recv_data), recv_data))
                LOGGER.error("ws error")
                _print_exception(e)
            utime.sleep_ms(1)

    def parse_event(self, raw):
        try:
            return ujson.loads(_decode_if_needed(raw))
        except Exception as e:
            LOGGER.error("json parse error")
            LOGGER.error("raw data length: {}".format(len(raw)))
            _print_exception(e)
            return None

    def handle_disconnect(self) -> None:
        if self.is_active:
            self.stop_audio_stream()
        if self.client:
            self.client.close()
        self.running = False
        if self.callback:
            self.event_queue.put(packet.disconnected)

    def handle_server_event(self, event: dict) -> None:
        event_type: str = event.get("event_type", "")
        data = event.get("data") or {}

        self.coze_ids["event_id"] = event.get("id") or self.coze_ids["event_id"]
        self._update_coze_ids(event_type, data)

        if event_type == packet.EventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            self.turn_recorder.start()
        elif event_type == packet.EventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
            self.turn_recorder.stop()
        elif event_type == packet.EventType.CONVERSATION_AUDIO_TRANSCRIPT_COMPLETED:
            self.coze_ids["message_id"] = _get_message_id(data) or self.coze_ids["message_id"]
            self.enqueue_audio_upload(event)
        elif event_type in (
            packet.EventType.CONVERSATION_CHAT_COMPLETED,
            packet.EventType.CONVERSATION_CHAT_FAILED,
            packet.EventType.CONVERSATION_CHAT_CANCELED,
        ):
            self.turn_recorder.stop()

    def _update_coze_ids(self, event_type: str, data: dict) -> None:
        conversation_id = data.get("conversation_id")
        if conversation_id:
            self.coze_ids["conversation_id"] = conversation_id

        section_id = _get_first(data, ("section_id", "last_section_id"))
        if section_id:
            self.coze_ids["section_id"] = section_id

        chat_id = data.get("chat_id")
        if not chat_id and event_type and event_type.startswith("conversation.chat."):
            chat_id = data.get("id")
        if chat_id:
            self.coze_ids["chat_id"] = chat_id

    def enqueue_audio_upload(self, transcript_event: dict) -> None:
        if not self.audio_upload_url:
            return
        snapshot = self.turn_recorder.snapshot()
        if snapshot is None:
            LOGGER.warn("skip audio upload: no captured user audio")
            return

        data = transcript_event.get("data") or {}
        transcript = _get_first(data, ("content", "text", "transcript"), "")
        payload = {
            "schema_version": "1.0",
            "audio_turn_id": snapshot["audio_turn_id"],
            "coze_conversation_id": self.coze_ids["conversation_id"],
            "coze_section_id": self.coze_ids["section_id"],
            "coze_chat_id": self.coze_ids["chat_id"],
            "coze_message_id": self.coze_ids["message_id"],
            "coze_event_id": transcript_event.get("id") or self.coze_ids["event_id"],
            "device_id": self.device_id,
            "transcript": transcript,
            "audio": {
                "encoding": "base64",
                "codec": "g711a",
                "sample_rate": 8000,
                "channels": 1,
                "data": _b64encode(snapshot["audio"]),
            },
            "timestamps": {
                "capture_started_ms": snapshot["capture_started_ms"],
                "capture_finished_ms": snapshot["capture_finished_ms"],
            },
        }
        if snapshot["dropped_audio_bytes"] > 0:
            payload["dropped_audio_bytes"] = snapshot["dropped_audio_bytes"]
        self.upload_queue.put(payload)

    def upload_user_audio(self, payload: dict) -> None:
        headers = {"Content-Type": "application/json"}
        if self.audio_upload_token:
            headers["Authorization"] = "Bearer " + self.audio_upload_token
        response = None
        try:
            response = request.post(
                self.audio_upload_url,
                data=ujson.dumps(payload),
                headers=headers,
                timeout=20,
            )
            status_code = response.status_code
            if status_code >= 200 and status_code < 300:
                LOGGER.info("audio upload success: {}".format(status_code))
            else:
                LOGGER.error("audio upload failed: {}".format(status_code))
        except Exception as e:
            LOGGER.error("audio upload error")
            _print_exception(e)
        finally:
            if response:
                try:
                    response.close()
                except Exception as e:
                    LOGGER.error("audio upload response close error")
                    _print_exception(e)

    def active(self) -> bool:
        return self.is_active

    def get_config(self, arg: str):
        if arg == "volume":
            return self.volume
        return None

    def config(self, **kwargs):
        if not self.media:
            LOGGER.warn("media is not ready, cannot configure")
            return

        for key, value in kwargs.items():
            if key == "volume":
                self.volume = value
                if self.is_active:
                    self.media.set_volume(value)

    def interrupted(self) -> None:
        if not self.client:
            LOGGER.warn("client is not ready, cannot send interrupt")
            return
        if not self.is_active:
            return
        self.client.send(ujson.dumps(packet.build_cancel()))
