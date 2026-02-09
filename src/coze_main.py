from usr.coze import cozews
from usr import packet
import ujson

def callback(coze, msg):
    start,end = ujson.search(msg, 'event_type')
    event = msg[start:end]
    if event == packet.EventType.CHAT_CREATED:
        coze.start_audio_stream()
        print('connect server success...')
    elif event == packet.EventType.DISCONNECTED:
        print('server disconnected...')
    elif event == packet.EventType.CONVERSATION_AUDIO_TRANSCRIPT_COMPLETED:
        start,end = ujson.search(msg, 'content')
        print('ASR {}'.format(msg[start:end]))
    elif event == packet.EventType.CONVERSATION_MESSAGE_COMPLETED:
        start,end = ujson.search(msg, 'content_type')
        content_type = msg[start:end]
        start,end = ujson.search(msg, 'type')
        type = msg[start:end]
        if content_type == 'text' and type == 'answer':
            start,end = ujson.search(msg, 'content')
            print('TTS {}'.format(msg[start:end]))
    elif event == packet.EventType.CONVERSATION_CHAT_FAILED:
        start,end = ujson.search(msg, 'last_error')
        print('failed {}'.format(msg[start:end]))
    elif event == packet.EventType.SERVER_ERROR:
        start,end = ujson.search(msg, 'msg')
        print('error {}'.format(msg[start:end]))
    else:
        print('unkown event_type: {}'.format(msg['event_type']))

#url = "ws://183.201.115.203/v1/chat?bot_id=7511922148273831962"
url = "wss://ws.coze.cn/v1/chat?bot_id=7595096935447724032"

#auth = "pat_eSuCmnooG6PLDildBu9ghH0OapGEkTg4wxTKNekj9AgXKAajIb0YQpgQ464k5J5x"  # Replace with your actual auth token
auth = "pat_bgE5pSWNDM7XnfLi0TGEEyMXX9BcqUmJU3lEFXHWgaWpFbqjgvrh48HjqRoPwj9y"  # Replace with your actual auth token

coze = cozews(url, auth, callback)

coze.config(volume=11)
coze.start()

print('config done')
