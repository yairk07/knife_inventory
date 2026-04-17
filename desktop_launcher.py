import os
import threading
import webbrowser

from app import app


class DesktopLauncher:
    def __init__(self):
        self.host = os.getenv("FLASK_HOST", "127.0.0.1")
        self.port = int(os.getenv("FLASK_PORT", "5000"))
        self.debug = os.getenv("FLASK_DEBUG", "0").strip() == "1"

    def app_url(self):
        return f"http://{self.host}:{self.port}"

    def _open_browser(self):
        webbrowser.open(self.app_url())

    def run(self):
        threading.Timer(1.0, self._open_browser).start()
        app.run(host=self.host, port=self.port, debug=self.debug, use_reloader=self.debug)


if __name__ == "__main__":
    DesktopLauncher().run()
