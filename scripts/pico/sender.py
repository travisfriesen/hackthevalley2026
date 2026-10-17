# Pico 2 W fixed-rate UDP transmitter. The Pi captures these frames' CSI by transmitter MAC.
# Needs config.py on the board (NOT in git): SSID = "..."; PASSWORD = "..."; RATE_HZ = 100
# Never exits: on any error it logs to errors.txt on the board and reconnects.
import network, socket, time, machine
from config import SSID, PASSWORD, RATE_HZ

led = machine.Pin("LED", machine.Pin.OUT)
w = network.WLAN(network.STA_IF)


def log(msg):
    print(msg)
    try:
        with open("errors.txt", "a") as f:
            f.write("%d %s\n" % (time.ticks_ms() // 1000, msg))
    except OSError:
        pass


def connect():
    w.active(True)
    w.config(pm=network.WLAN.PM_NONE)  # no power save: keeps the TX rate steady
    if not w.isconnected():
        w.connect(SSID, PASSWORD)
    t0 = time.ticks_ms()
    while not w.isconnected():
        led.toggle(); time.sleep_ms(100)  # fast blink = connecting
        if time.ticks_diff(time.ticks_ms(), t0) > 20000:
            raise OSError("connect timeout, status %d" % w.status())
    gw = w.ifconfig()[2]
    log("joined ip %s gw %s ch %s" % (w.ifconfig()[0], gw, w.config("channel")))
    return gw


def run(gw):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    period = 1_000_000 // RATE_HZ
    nxt = time.ticks_us()
    n, t0 = 0, time.ticks_ms()
    while True:
        s.sendto(n.to_bytes(4, "little"), (gw, 9999))
        n += 1
        nxt = time.ticks_add(nxt, period)
        d = time.ticks_diff(nxt, time.ticks_us())
        if d > 0:
            time.sleep_us(d)
        if n % RATE_HZ == 0:
            led.toggle()  # slow blink = sending
            if not w.isconnected():
                raise OSError("link lost after %d pkts" % n)
        if n % (RATE_HZ * 10) == 0:
            print("sent", n, "avg Hz", n * 1000 // time.ticks_diff(time.ticks_ms(), t0))


while True:
    try:
        run(connect())
    except Exception as e:  # noqa: BLE001 - keep transmitting no matter what
        log("error: %r" % e)
        try:
            w.disconnect()
        except Exception:
            pass
        time.sleep(2)
