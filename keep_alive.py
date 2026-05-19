from flask import Flask
import os
from threading import Thread
import logging

app = Flask('')
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


@app.route('/')
def home():
    return "✅ Naruto Companion Bot is running!"


def run():
    try:
        port = int(os.environ.get("PORT", "8080"))
        app.run(host='0.0.0.0', port=port, use_reloader=False)
    except OSError:
        pass


def keep_alive():
    thread = Thread(target=run)
    thread.daemon = True
    thread.start()
