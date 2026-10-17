"""Messaging channels. Anything with `name` and send_text(to, body) -> {"ok", "status"} works."""
from .. import config


class ConsoleChannel:
    name = "console"

    def __init__(self):
        self.sent = []

    def send_text(self, to, body):
        self.sent.append((to, body))
        print(f"[text -> {to}] {body}")
        return {"ok": True, "status": "console"}


_channel = None


def get_channel():
    global _channel
    if _channel is None:
        if config.PHOTON_PROJECT_ID:
            from .photon import PhotonChannel
            _channel = PhotonChannel(config.PHOTON_BRIDGE_URL, config.PHOTON_BRIDGE_SECRET)
        else:
            _channel = ConsoleChannel()
    return _channel


def set_channel(ch):
    global _channel
    _channel = ch
