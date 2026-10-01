"""Keep bot.py running: restart it whenever it stops. Output goes to bot.log.

Started by Windows Task Scheduler at log on (see install_autostart.ps1 and SETUP.md §6).
"""
import datetime
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "bot.log")


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [run_forever] {msg}\n")


while True:
    log("starting bot.py")
    with open(LOG, "a", encoding="utf-8") as out:
        code = subprocess.call([sys.executable, "bot.py"], cwd=HERE, stdout=out, stderr=out)
    log(f"bot.py stopped (exit code {code}); restarting in 15 seconds")
    time.sleep(15)
