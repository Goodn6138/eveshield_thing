#!/usr/bin/env python3
"""
ZX303 Tracker Backend - Render-ready
Receives TCP data from ZX303 GPS tracker, serves web dashboard.
"""

from flask import Flask, jsonify, render_template_string, request
import socket
import struct
import threading
import datetime
import json
import os

# ==================== CONFIG ====================
TCP_HOST = "0.0.0.0"
TCP_PORT = int(os.environ.get("TCP_PORT", 5001))
FLASK_PORT = int(os.environ.get("PORT", 10000))

# ==================== DATA STORE ====================
devices = {}
raw_packets = []

# ==================== PACKET PARSER ====================

def decode_imei(data):
    imei_bytes = data[4:12]
    imei = "".join(f"{b:02x}" for b in imei_bytes)
    return imei.lstrip("0") or "UNKNOWN"

def decode_location(data):
    idx = 4
    date = data[idx]; idx += 1
    month = data[idx]; idx += 1
    year = data[idx]; idx += 1
    hour = data[idx]; idx += 1
    minute = data[idx]; idx += 1
    second = data[idx]; idx += 1

    lat_raw = struct.unpack(">I", bytes(data[idx:idx+4]))[0]
    idx += 4
    lon_raw = struct.unpack(">I", bytes(data[idx:idx+4]))[0]
    idx += 4

    speed = data[idx]; idx += 1
    course_status = struct.unpack(">H", bytes(data[idx:idx+2]))[0]
    idx += 2

    latitude = lat_raw / 30000.0 / 60.0
    longitude = lon_raw / 30000.0 / 60.0
    course = course_status & 0x03FF

    timestamp = f"20{year:02d}-{month:02d}-{date:02d} {hour:02d}:{minute:02d}:{second:02d}"

    return {
        "timestamp": timestamp,
        "lat": round(latitude, 6),
        "lon": round(longitude, 6),
        "speed_kmh": speed,
        "course": course
    }

def parse_packet(raw_data):
    if len(raw_data) < 5:
        return None, "TOO_SHORT", None

    if raw_data[:2] not in (b'\x78\x78', b'\x79\x79'):
        return None, "BAD_HEADER", {"raw": raw_data.hex()}

    protocol = raw_data[3]

    if protocol == 0x01:
        imei = decode_imei(raw_data)
        return imei, "LOGIN", {"imei": imei}

    elif protocol in (0x12, 0x22):
        loc = decode_location(raw_data)
        return None, "LOCATION", loc

    elif protocol == 0x13:
        return None, "HEARTBEAT", {
            "status": f"0x{raw_data[4]:02x}",
            "voltage": raw_data[5] if len(raw_data) > 5 else None,
            "gsm_signal": raw_data[6] if len(raw_data) > 6 else None
        }

    elif protocol == 0x15:
        try:
            msg = raw_data[4:-2].decode("ascii", errors="replace")
            return None, "STRING", {"message": msg}
        except:
            return None, "STRING", {"raw": raw_data[4:-2].hex()}

    else:
        return None, f"UNKNOWN_0x{protocol:02x}", {"raw": raw_data.hex()}

def store_event(imei, event, data):
    now = datetime.datetime.now().isoformat()

    if imei not in devices:
        devices[imei] = {
            "imei": imei,
            "first_seen": now,
            "last_seen": now,
            "events": [],
            "latest": {}
        }

    devices[imei]["last_seen"] = now
    devices[imei]["events"].append({
        "time": now,
        "type": event,
        "data": data
    })

    devices[imei]["events"] = devices[imei]["events"][-100:]

    if event == "LOCATION":
        devices[imei]["latest"] = data

    print(f"[STORED] IMEI:{imei} | {event} | {json.dumps(data)}")

# ==================== TCP SERVER ====================

def tcp_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((TCP_HOST, TCP_PORT))
        s.listen(5)
        print(f"[TCP] Listening on {TCP_HOST}:{TCP_PORT}")

        while True:
            conn, addr = s.accept()
            print(f"[TCP] Connection from {addr}")
            handler = threading.Thread(target=handle_client, args=(conn, addr))
            handler.daemon = True
            handler.start()

def handle_client(conn, addr):
    current_imei = None
    buffer = b""

    with conn:
        while True:
            try:
                chunk = conn.recv(1024)
                if not chunk:
                    break

                buffer += chunk
                raw_packets.append({
                    "time": datetime.datetime.now().isoformat(),
                    "source": f"tcp:{addr[0]}",
                    "hex": chunk.hex()
                })

                print(f"[RAW TCP] {chunk.hex()}")

                while len(buffer) >= 5:
                    header_idx = buffer.find(b'\x78\x78')
                    if header_idx == -1:
                        header_idx = buffer.find(b'\x79\x79')

                    if header_idx == -1:
                        buffer = b""
                        break

                    if header_idx > 0:
                        buffer = buffer[header_idx:]

                    if len(buffer) < 3:
                        break

                    pkt_len = buffer[2] + 5

                    if len(buffer) < pkt_len:
                        break

                    packet = buffer[:pkt_len]
                    buffer = buffer[pkt_len:]

                    imei, event, data = parse_packet(packet)

                    if imei:
                        current_imei = imei

                    if current_imei:
                        store_event(current_imei, event, data)
                    else:
                        print(f"[PARSED] NO_IMEI | {event} | {json.dumps(data)}")

            except Exception as e:
                print(f"[ERROR] {e}")
                break

    print(f"[TCP] Disconnected from {addr}")

# ==================== FLASK APP ====================

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>ZX303 Tracker</title>
    <meta http-equiv="refresh" content="10">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace; background: #0a0a0a; color: #00ff88; padding: 20px; min-height: 100vh; }
        h1 { color: #00d4ff; margin-bottom: 10px; font-size: 24px; }
        .subtitle { color: #666; margin-bottom: 20px; font-size: 14px; }
        .device { background: #111; border: 1px solid #222; border-radius: 8px; padding: 20px; margin-bottom: 15px; }
        .device h2 { color: #ffaa00; font-size: 18px; margin-bottom: 10px; }
        .meta { color: #888; font-size: 13px; margin-bottom: 15px; }
        .meta span { margin-right: 20px; }
        .location { background: #0d1f0d; border: 1px solid #1a3a1a; border-radius: 6px; padding: 15px; margin-bottom: 15px; }
        .location h3 { color: #00ff88; font-size: 14px; margin-bottom: 10px; }
        .coords { font-size: 20px; font-weight: bold; margin: 10px 0; }
        .coords span { color: #888; font-size: 14px; font-weight: normal; }
        .stats { display: flex; gap: 20px; margin: 10px 0; font-size: 14px; }
        .stats div { color: #ccc; }
        .stats div strong { color: #00d4ff; }
        .map-btn { display: inline-block; background: #0066cc; color: white; text-decoration: none; padding: 8px 16px; border-radius: 4px; font-size: 13px; margin-top: 10px; }
        .map-btn:hover { background: #0088ff; }
        .events { margin-top: 15px; }
        .events h3 { color: #ff66cc; font-size: 14px; margin-bottom: 10px; }
        .event { border-bottom: 1px solid #1a1a1a; padding: 8px 0; font-size: 13px; }
        .event-type { color: #ffaa00; font-weight: bold; margin-right: 10px; }
        .event-time { color: #555; margin-right: 10px; }
        .event-data { color: #aaa; }
        .no-data { color: #ff4444; text-align: center; padding: 40px; }
        .api-info { margin-top: 30px; padding: 15px; background: #111; border-radius: 6px; font-size: 12px; }
        .api-info a { color: #00d4ff; }
        .status-online { color: #00ff88; }
        .status-offline { color: #ff4444; }
        .setup-box { background: #1a1a0d; border: 1px solid #333300; border-radius: 6px; padding: 15px; margin: 20px 0; }
        .setup-box code { background: #222; padding: 2px 6px; border-radius: 3px; color: #ffaa00; }
        .setup-box h3 { color: #ffaa00; margin-bottom: 10px; }
    </style>
</head>
<body>
    <h1>🛰️ ZX303 Tracker Dashboard</h1>
    <p class="subtitle">Auto-refresh every 10s | Server: {{ server_time }}</p>

    {% if not devices %}
        <div class="no-data">
            <h2>Waiting for ZX303 connection...</h2>
        </div>

        <div class="setup-box">
            <h3>⚙️ Setup Instructions</h3>
            <p><strong>Via Serial AT:</strong></p>
            <p><code>AT+SERVER=1,"YOUR_SERVER_IP",5001,0</code></p>
            <p>Then restart tracker</p>
            <br>
            <p><strong>Via SMS:</strong></p>
            <p><code>adminip123456 YOUR_SERVER_IP 5001</code></p>
            <p><code>reset123456</code></p>
        </div>
    {% endif %}

    {% for imei, dev in devices.items() %}
    <div class="device">
        <h2>📟 {{ imei }}</h2>
        <div class="meta">
            <span>📅 First: {{ dev.first_seen[:19] }}</span>
            <span>🔄 Last: {{ dev.last_seen[:19] }}</span>
            <span class="{% if dev.last_seen > five_min_ago %}status-online{% else %}status-offline{% endif %}">
                ● {{ "ONLINE" if dev.last_seen > five_min_ago else "OFFLINE" }}
            </span>
        </div>

        {% if dev.latest %}
        <div class="location">
            <h3>📍 Latest Location</h3>
            <div class="coords">
                {{ dev.latest.lat }}°N, {{ dev.latest.lon }}°E
                <span>({{ dev.latest.timestamp }})</span>
            </div>
            <div class="stats">
                <div>🚀 <strong>{{ dev.latest.speed_kmh }}</strong> km/h</div>
                <div>🧭 <strong>{{ dev.latest.course }}</strong>°</div>
            </div>
            <a class="map-btn" href="https://www.google.com/maps?q={{ dev.latest.lat }},{{ dev.latest.lon }}" target="_blank">
                🗺️ Open in Google Maps
            </a>
        </div>
        {% else %}
        <p class="no-data">No GPS fix yet</p>
        {% endif %}

        <div class="events">
            <h3>📜 Recent Events</h3>
            {% for event in dev.events[-10:]|reverse %}
            <div class="event">
                <span class="event-type">[{{ event.type }}]</span>
                <span class="event-time">{{ event.time[11:19] }}</span>
                <span class="event-data">{{ event.data | tojson }}</span>
            </div>
            {% endfor %}
        </div>
    </div>
    {% endfor %}

    <div class="api-info">
        <h3>🔗 API Endpoints</h3>
        <p><a href="/api/devices">GET /api/devices</a> — All devices JSON</p>
        <p><a href="/api/raw">GET /api/raw</a> — Recent raw hex packets</p>
    </div>
</body>
</html>
"""

@app.route("/")
def dashboard():
    five_min = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat()
    return render_template_string(
        HTML_TEMPLATE,
        devices=devices,
        server_time=datetime.datetime.now().isoformat()[:19],
        five_min_ago=five_min
    )

@app.route("/api/devices")
def api_all():
    return jsonify(devices)

@app.route("/api/devices/<imei>")
def api_device(imei):
    if imei in devices:
        return jsonify(devices[imei])
    return jsonify({"error": "not found"}), 404

@app.route("/api/raw")
def api_raw():
    return jsonify(raw_packets[-50:])

# ==================== MAIN ====================

if __name__ == "__main__":
    tcp_thread = threading.Thread(target=tcp_server)
    tcp_thread.daemon = True
    tcp_thread.start()

    print(f"[FLASK] Starting on port {FLASK_PORT}")
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
