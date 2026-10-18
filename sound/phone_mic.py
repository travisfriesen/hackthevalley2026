"""Phone microphone bridge for the Pi.

    python phone_mic.py                 # on the Pi, next to audio_receiver.py

Open https://<pi-address>:8443 in Chrome on the phone, accept the certificate
warning once (self-signed), tap "Start microphone". The phone streams 16-bit
mono audio over a websocket and this forwards it to audio_receiver.py on
localhost, so the rolling WAV and fall_detector.py work unchanged.

The receiver takes one sender at a time: stop audio_sender.py on the laptop first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import ssl
import subprocess
from pathlib import Path

from aiohttp import WSMsgType, web

from audio_link import DEFAULT_PORT, pack_header

log = logging.getLogger("phone_mic")

PAGE = r"""<!doctype html>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DownLink phone mic</title>
<style>
  body { font-family: system-ui, sans-serif; background: #f3f5f9; color: #0b0d17; margin: 0; padding: 24px; }
  button { font-size: 1.4rem; padding: 18px 28px; border: 0; border-radius: 12px; background: #2ea360; color: #fff; width: 100%; }
  button.stop { background: #d64545; }
  #bar { height: 14px; background: #dbe2ed; border-radius: 7px; margin: 22px 0 10px; overflow: hidden; }
  #lvl { height: 100%; width: 0; background: #2ea360; }
  #msg { color: #5d6878; min-height: 3em; }
</style>
<h2>DownLink phone microphone</h2>
<button id="go">Start microphone</button>
<div id="bar"><div id="lvl"></div></div>
<div id="msg">Keep this screen on and this tab in front.</div>
<script>
const go = document.getElementById('go'), msg = document.getElementById('msg'), lvl = document.getElementById('lvl');
let ctx, ws, stream, wake, running = false;

let awakeNote = '';
async function keepAwake() {
  try {
    wake = await navigator.wakeLock.request('screen');
    wake.addEventListener('release', () => { wake = null; });
    awakeNote = '';
  } catch (e) {
    awakeNote = ' WARNING: could not keep the screen awake (' + (e.name || e) + '): turn off battery saver and set the screen timeout to the maximum.';
  }
  if (running) msg.textContent = `Streaming at ${ctx.sampleRate} Hz. Keep this screen on.` + awakeNote;
}
// the browser drops the lock whenever the page is hidden: take it again when the tab is back in front
document.addEventListener('visibilitychange', () => { if (running && document.visibilityState === 'visible' && !wake) keepAwake(); });

async function start() {
  stream = await navigator.mediaDevices.getUserMedia({audio: {
    echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1}});
  ctx = new AudioContext({sampleRate: 16000});
  ws = new WebSocket(`wss://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';
  ws.onmessage = e => { msg.textContent = e.data; };
  ws.onclose = () => { if (running) msg.textContent = 'Disconnected from the Pi.'; };
  await new Promise(r => ws.onopen = r);
  ws.send(JSON.stringify({rate: ctx.sampleRate}));

  const code = `class P extends AudioWorkletProcessor {
    process(inputs) { const c = inputs[0][0]; if (c) this.port.postMessage(c.slice(0)); return true; }
  } registerProcessor('p', P);`;
  await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([code], {type: 'text/javascript'})));
  const node = new AudioWorkletNode(ctx, 'p');
  let acc = [], n = 0;
  node.port.onmessage = e => {
    const f = e.data; acc.push(f); n += f.length;
    let peak = 0; for (const v of f) peak = Math.max(peak, Math.abs(v));
    lvl.style.width = Math.min(100, peak * 300) + '%';
    if (n >= 2048 && ws.readyState === 1) {          // ~128 ms per message
      const out = new Int16Array(n); let k = 0;
      for (const a of acc) for (const v of a) out[k++] = Math.max(-1, Math.min(1, v)) * 32767;
      ws.send(out.buffer); acc = []; n = 0;
    }
  };
  ctx.createMediaStreamSource(stream).connect(node);
  await keepAwake();
  running = true; go.textContent = 'Stop'; go.className = 'stop';
  msg.textContent = `Streaming at ${ctx.sampleRate} Hz. Keep this screen on.` + awakeNote;
}

function stop() {
  running = false; stream.getTracks().forEach(t => t.stop()); ctx.close(); ws.close();
  if (wake) wake.release(); go.textContent = 'Start microphone'; go.className = ''; lvl.style.width = '0'; msg.textContent = 'Stopped.';
}

go.onclick = () => (running ? stop() : start()).catch(e => { msg.textContent = 'Error: ' + e; });
</script>
"""


async def index(request: web.Request) -> web.Response:
    return web.Response(text=PAGE, content_type="text/html")


async def stream(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=1 << 20)
    await ws.prepare(request)
    token, port = request.app["token"], request.app["receiver_port"]
    writer = None
    try:
        first = await ws.receive()
        rate = int(json.loads(first.data)["rate"])
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(pack_header(rate, token))
        await writer.drain()
        if await reader.readexactly(1) != b"K":
            await ws.send_str("The receiver rejected this stream (it is already running at a "
                              "different sample rate, or has another sender). Restart it.")
            return ws
        log.info("Phone connected from %s at %d Hz", request.remote, rate)
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                writer.write(msg.data)
                await writer.drain()
    except (ConnectionError, OSError, asyncio.IncompleteReadError, ValueError, KeyError) as exc:
        log.warning("Stream ended: %s", exc)
        if not ws.closed:
            await ws.send_str(f"Cannot reach audio_receiver.py ({exc}). Is it running?")
    finally:
        if writer is not None:
            writer.close()
        log.info("Phone disconnected")
    return ws


def make_cert(directory: Path, hosts: list[str]) -> tuple[Path, Path]:
    """Self-signed certificate (browsers only allow microphone access on https)."""
    directory.mkdir(parents=True, exist_ok=True)
    cert, key = directory / "phone_mic.crt", directory / "phone_mic.key"
    if not cert.exists():
        san = ",".join(f"IP:{h}" for h in hosts)
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "90",
             "-keyout", str(key), "-out", str(cert), "-subj", "/CN=kaze-pi",
             "-addext", f"subjectAltName={san}"],
            check=True, capture_output=True)
    return cert, key


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--receiver-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--token", help="Shared secret; must match audio_receiver.py")
    parser.add_argument("--host", action="append", default=[],
                        help="Pi address(es) the phone will use (for the certificate)")
    args = parser.parse_args()

    hosts = args.host or ["127.0.0.1"]
    cert, key = make_cert(Path.home() / ".cache" / "kaze", hosts)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)

    app = web.Application()
    app["token"], app["receiver_port"] = args.token, args.receiver_port
    app.add_routes([web.get("/", index), web.get("/ws", stream)])
    log.info("Open https://<pi-address>:%d on the phone", args.port)
    web.run_app(app, host="0.0.0.0", port=args.port, ssl_context=tls, print=None)


if __name__ == "__main__":
    main()
