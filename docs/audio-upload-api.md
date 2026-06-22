# User Audio Upload API

This API lets the device upload the user's audio for one Coze turn. The device
starts capturing at `input_audio_buffer.speech_started`, stops capturing at
`input_audio_buffer.speech_stopped`, and uploads after
`conversation.audio_transcript.completed` so the upload can include Coze ids and
transcript text.

## Endpoint

`POST /v1/coze/user-audio`

Use HTTPS in production.

## Headers

- `Content-Type: application/json`
- `Authorization: Bearer <token>` optional, when configured on the device

## Request Body

```json
{
  "schema_version": "1.0",
  "audio_turn_id": "quec-123456-1",
  "coze_conversation_id": "conv_abc",
  "coze_section_id": "section_abc",
  "coze_chat_id": "chat_abc",
  "coze_message_id": "msg_abc",
  "coze_event_id": "event_abc",
  "device_id": "quecpython_device",
  "transcript": "你好",
  "audio": {
    "encoding": "base64",
    "codec": "g711a",
    "sample_rate": 8000,
    "channels": 1,
    "data": "base64..."
  },
  "timestamps": {
    "capture_started_ms": 123456,
    "capture_finished_ms": 125000
  }
}
```

## Binding Rules

- `coze_conversation_id` identifies the Coze conversation/session when Coze
  provides it in downlink event `data.conversation_id`. It corresponds to
  `ConversationData.id` in Coze's conversation list API.
- `coze_section_id` is optional. Coze's conversation list API exposes
  `last_section_id` for the latest context section in a conversation. If a
  WebSocket event carries `data.section_id` or `data.last_section_id`, the
  device forwards it here; otherwise this field is `null`.
- `coze_chat_id` identifies one chat/run inside a Coze conversation. In a
  conversation with multiple user turns, this is the primary turn-level binding
  key. The device tracks it from `conversation.chat.*` events using
  `data.id` or `data.chat_id`, and also updates it from later events that carry
  `data.chat_id`.
- `coze_message_id` identifies the user message/audio transcript when Coze
  provides it. The device extracts it from
  `conversation.audio_transcript.completed` using the first available field:
  `data.id`, `data.message_id`, `data.item_id`, `data.content_id`,
  `data.message.id`, `data.message.message_id`.
- If Coze does not provide a message id, the server should bind by
  `coze_chat_id` first, then use `audio_turn_id` plus `transcript` as a
  fallback join key against Coze history.
- `audio_turn_id` is generated locally and is unique for the device runtime.

## Device Configuration

Add these optional fields to `usr/secret.json`:

```json
{
  "audio_upload_url": "https://example.com/v1/coze/user-audio",
  "audio_upload_token": "server-token",
  "device_id": "device-001",
  "max_upload_audio_bytes": 262144
}
```

`max_upload_audio_bytes` limits in-memory audio captured for one user turn.
When the limit is reached, extra audio bytes are dropped for the third-party
upload only; Coze streaming continues normally.

## Response

Success:

```json
{
  "ok": true,
  "audio_id": "aud_abc"
}
```

Any 2xx status code is treated as success by the device.

Failure:

```json
{
  "ok": false,
  "error": "reason"
}
```
