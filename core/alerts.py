import logging
import subprocess
import sys
import threading
import time
from typing import Dict

log = logging.getLogger(__name__)


class Alerts:
    def __init__(self, enabled: bool = True, sound: bool = True):
        self.enabled = enabled
        self.sound = sound
        self._last: Dict[str, float] = {}
        self.pending_beep = threading.Event()

    def notify(self, title: str, message: str) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if now - self._last.get(title, 0) < 5:
            return
        self._last[title] = now
        if sys.platform == "darwin":
            try:
                script = 'display notification "{}" with title "{}"'.format(
                    message.replace('"', ""), title.replace('"', "")
                )
                if self.sound:
                    script = 'display notification "{}" with title "{}" sound name "Glass"'.format(
                        message.replace('"', ""), title.replace('"', "")
                    )
                subprocess.Popen(
                    ["osascript", "-e", script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                log.debug("notification failed", exc_info=True)
        self.pending_beep.set()
