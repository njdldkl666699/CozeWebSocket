import sys

import ujson
import utime
from usr import logging, packet
from usr.coze import DEFAULT_DEVICE_ID, DEFAULT_MAX_UPLOAD_AUDIO_BYTES, CozeWebSocket

LOGGER = logging.getLogger("coze_main")
MAIN_LOOP_SLEEP_MS = 200

SECRET_PATHS = (
    "usr/secret.json",
    "/usr/secret.json",
    "secret.json",
)


def print_exception(exc: Exception) -> None:
    printer = getattr(sys, "print_exception", None)
    if printer:
        printer(exc)
    else:
        LOGGER.error(repr(exc))


def load_secret() -> dict:
    for path in SECRET_PATHS:
        try:
            with open(path, "r") as f:
                return ujson.loads(f.read())
        except Exception as e:
            LOGGER.warn("load secret failed: {}".format(path))
            print_exception(e)
    return {}


def get_ws_url(secret: dict):
    url = secret.get("coze_ws_url") or secret.get("url")
    if url:
        return url

    bot_id = secret.get("bot_id")
    if bot_id:
        return "wss://ws.coze.cn/v1/chat?bot_id={}".format(bot_id)
    return None


def callback(coze: CozeWebSocket, msg: dict) -> None:
    event = msg.get("event_type")
    LOGGER.debug("event_type: {}".format(event))
    data = msg.get("data") or {}

    if event in (packet.EventType.CHAT_CREATED, packet.EventType.CONVERSATION_CHAT_CREATED):
        coze.start_audio_stream()
        LOGGER.info("connect server success...")
    elif event == packet.EventType.DISCONNECTED:
        LOGGER.info("server disconnected...")
    elif event == packet.EventType.CONVERSATION_AUDIO_TRANSCRIPT_COMPLETED:
        LOGGER.info("ASR {}".format(data.get("content") or data.get("text") or ""))
    elif event == packet.EventType.CONVERSATION_MESSAGE_COMPLETED:
        content_type = data.get("content_type")
        message_type = data.get("type")
        if content_type == "text" and message_type == "answer":
            LOGGER.info("TTS {}".format(data.get("content") or ""))
    elif event == packet.EventType.CONVERSATION_CHAT_FAILED:
        LOGGER.error("failed {}".format(data.get("last_error") or ""))
    elif event == packet.EventType.SERVER_ERROR:
        LOGGER.error("error {}".format(data.get("msg") or data.get("message") or ""))
    elif event == packet.EventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
        LOGGER.info("speech started")
    elif event == packet.EventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
        LOGGER.info("speech stopped")
    else:
        LOGGER.debug("unknown event_type: {}".format(event))


def main() -> None:
    secret = load_secret()
    url = get_ws_url(secret)
    auth = secret.get("coze_token") or secret.get("auth")
    if not url or not auth:
        LOGGER.error("please configure usr/secret.json: coze_token and bot_id/coze_ws_url")
        return

    coze = CozeWebSocket(
        url,
        auth,
        callback,
        audio_upload_url=secret.get("audio_upload_url"),
        audio_upload_token=secret.get("audio_upload_token"),
        device_id=secret.get("device_id", DEFAULT_DEVICE_ID),
        max_upload_audio_bytes=secret.get("max_upload_audio_bytes", DEFAULT_MAX_UPLOAD_AUDIO_BYTES),
    )
    coze.config(volume=secret.get("volume") or 11)
    try:
        coze.start()
        LOGGER.info("config done")
        while coze.running:
            utime.sleep_ms(MAIN_LOOP_SLEEP_MS)
    except KeyboardInterrupt:
        LOGGER.info("interrupted by Ctrl-C")
    except Exception as e:
        LOGGER.error("main loop error")
        print_exception(e)
    finally:
        LOGGER.info("stopping CozeWebSocket")
        coze.stop()
        LOGGER.info("CozeWebSocket stopped")


if __name__ == "__main__":
    main()
