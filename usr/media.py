import audio
import G711
from usr import logging

LOGGER = logging.getLogger("media")

media = None


def singleton_media(name: str, media_type: int):
    global media
    if media is None:
        media = Media(name, media_type)
        return media

    if not media.is_idle():
        LOGGER.warn("{} is using media".format(media.name))
        return None

    media.set_media_config(name, media_type)
    return media


def release_singleton_media() -> None:
    global media
    if media is None:
        return
    if not media.is_idle():
        media.stop()
    media = None


class Media:
    MEDIA_TYPE_AUDIO = 1
    MEDIA_TYPE_PCM = 2
    MEDIA_TYPE_RECORD = 3
    MEDIA_TYPE_PCMA = 4

    def __init__(self, name: str, media_type: int) -> None:
        self.name = name
        self.type = media_type
        self.pcm = None
        self.pcma = None
        self.audio = audio.Audio(0)
        self.audio.set_pa(29)

    def set_media_config(self, name: str, media_type: int) -> None:
        self.name = name
        self.type = media_type

    def is_idle(self) -> bool:
        return bool(self.pcma)

    def start(self) -> None:
        if self.type == self.MEDIA_TYPE_PCMA:
            self.pcm = audio.Audio.PCM(1, 1, 8000, 2, 1, 5)
            self.pcma = G711(self.pcm)
        else:
            raise ValueError("unknown audio type")

    def stop(self) -> None:
        if self.type == self.MEDIA_TYPE_PCMA:
            if self.pcma:
                del self.pcma
                self.pcma = None
                self.pcm.close()
                del self.pcm
                self.pcm = None
        else:
            raise ValueError("wrong audio type")
        self.name = None
        self.type = None

    def pcma_read(self) -> bytes:
        if not self.pcma:
            return b""
        return self.pcma.read(0)

    def pcma_write(self, payload: bytes) -> int:
        if not self.pcma:
            return 0
        return self.pcma.write(payload, 0)

    def set_volume(self, value: int) -> int:
        if not self.pcm:
            return 0
        if self.type == self.MEDIA_TYPE_PCMA:
            return self.pcm.setVolume(value)
        else:
            raise ValueError("wrong audio type")

    def get_volume(self) -> int:
        if not self.pcm:
            return 0
        if self.type == self.MEDIA_TYPE_PCMA:
            return self.pcm.getVolume()
        else:
            raise ValueError("wrong audio type")
