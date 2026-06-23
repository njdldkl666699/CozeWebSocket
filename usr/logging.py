import _thread
import sys

import utime


class Level:
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3
    CRITICAL = 4


DEBUG = Level.DEBUG
INFO = Level.INFO
WARN = Level.WARN
WARNING = Level.WARN
ERROR = Level.ERROR
CRITICAL = Level.CRITICAL

_levelToName = {
    Level.CRITICAL: "CRITICAL",
    Level.ERROR: "ERROR",
    Level.WARN: "WARN",
    Level.INFO: "INFO",
    Level.DEBUG: "DEBUG",
}

_nameToLevel = {
    "CRITICAL": Level.CRITICAL,
    "ERROR": Level.ERROR,
    "WARN": Level.WARN,
    "WARNING": Level.WARN,
    "INFO": Level.INFO,
    "DEBUG": Level.DEBUG,
}


def getLevelName(level: int) -> str:
    if level not in _levelToName:
        raise ValueError('unknown level "{}", choose from <class Level>.'.format(level))
    return _levelToName[level]


def getNameLevel(name: str) -> int:
    temp = name.upper()
    if temp not in _nameToLevel:
        raise ValueError('"{}" is not valid.'.format(name))
    return _nameToLevel[temp]


class BasicConfig:
    logger_register_table = {}
    basic_configure = {"level": Level.DEBUG, "debug": True, "stream": sys.stdout}

    @classmethod
    def getLogger(cls, name: str):
        if name not in cls.logger_register_table:
            logger = Logger(name)
            cls.logger_register_table[name] = logger
        else:
            logger = cls.logger_register_table[name]
        return logger

    @classmethod
    def update(cls, **kwargs) -> None:
        level = kwargs.pop("level", None)
        if level is not None:
            if isinstance(level, str):
                level = getNameLevel(level)
            kwargs["level"] = level
        return cls.basic_configure.update(kwargs)

    @classmethod
    def get(cls, key: str):
        return cls.basic_configure[key]

    @classmethod
    def set(cls, key: str, value) -> None:
        if key == "level":
            if isinstance(value, str):
                value = getNameLevel(value)
        cls.basic_configure[key] = value


class Logger:
    lock = _thread.allocate_lock()

    def __init__(self, name: str) -> None:
        self.name = name

    @staticmethod
    def __getFormattedTime() -> str:
        cur_time_tuple = utime.localtime()
        return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
            cur_time_tuple[0],
            cur_time_tuple[1],
            cur_time_tuple[2],
            cur_time_tuple[3],
            cur_time_tuple[4],
            cur_time_tuple[5],
        )

    def log(self, level: int, *message) -> None:
        if BasicConfig.get("debug") is False and level < BasicConfig.get("level"):
            return

        stream = BasicConfig.get("stream")
        prefix = "[{}][{}][{}]".format(
            self.__getFormattedTime(),
            getLevelName(level),
            self.name,
        )
        text = "{} {}\n".format(prefix, " ".join([str(item) for item in message]))
        with self.lock:
            stream.write(text)
            flush = getattr(stream, "flush", None)
            if flush:
                flush()

    def debug(self, *message) -> None:
        self.log(Level.DEBUG, *message)

    def info(self, *message) -> None:
        self.log(Level.INFO, *message)

    def warn(self, *message) -> None:
        self.log(Level.WARN, *message)

    def warning(self, *message) -> None:
        self.log(Level.WARN, *message)

    def error(self, *message) -> None:
        self.log(Level.ERROR, *message)

    def critical(self, *message) -> None:
        self.log(Level.CRITICAL, *message)


def getLogger(name: str):
    return BasicConfig.getLogger(name)


def basicConfig(level: int = INFO) -> None:
    BasicConfig.set("level", level)


def set_output(out) -> None:
    BasicConfig.set("stream", out)
