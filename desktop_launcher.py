import os
import threading
import webbrowser

from app import app, flask_listen_port_resolver


class DesktopLauncher:
    def __init__(self):
        self.host = os.getenv("FLASK_HOST", "127.0.0.1")
        self.preferred_port = int(os.getenv("FLASK_PORT", "5000"))
        self.debug = os.getenv("FLASK_DEBUG", "0").strip() == "1"
        self._listen_port = self.preferred_port

    def app_url(self):
        return f"http://{self.host}:{self._listen_port}"

    def _open_browser(self):
        webbrowser.open(self.app_url())

    def run(self):
        self._listen_port = flask_listen_port_resolver.resolve(self.host, self.preferred_port)
        if self._listen_port != self.preferred_port:
            print(f"Port {self.preferred_port} was not available; opened {self.app_url()}")
        threading.Timer(1.0, self._open_browser).start()
        use_reload = self.debug and os.getenv("FLASK_USE_RELOADER", "0").strip() == "1"
        app.run(host=self.host, port=self._listen_port, debug=self.debug, use_reloader=use_reload)


if __name__ == "__main__":
    DesktopLauncher().run()
