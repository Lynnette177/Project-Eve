import json
import os
import threading
from pathlib import Path

from dotenv import load_dotenv

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

from ai.tools.georelated import gps_to_amap_and_reverse, latlon_to_amap_coord


_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class NullTeslaMateMQTT:
    def __init__(self, reason: str = "disabled"):
        self.reason = reason

    def get_location(self, getAddress=True):
        return {
            "latitude": "未知",
            "longitude": "未知",
            "address": "未处理" if getAddress else "未处理",
            "geofence": "未知",
        }


class TeslaMateMQTT:
    def __init__(self, broker="127.0.0.1", port=18883, car_id="1", user=None, password=None):
        if mqtt is None:
            raise RuntimeError("paho-mqtt is not installed")

        self.broker = broker
        self.port = int(port)
        self.car_id = car_id
        self.topic = f"teslamate/cars/{car_id}/#"
        self.car_data = {}
        self.lock = threading.Lock()

        self.client = mqtt.Client()
        if user:
            self.client.username_pw_set(user, password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.broker, self.port, 60)
        threading.Thread(target=self.client.loop_forever, daemon=True).start()

    def _on_connect(self, client, userdata, flags, rc):
        print(f"✅ 已连接 MQTT {self.broker}:{self.port}")
        client.subscribe(self.topic)

    def _on_message(self, client, userdata, msg):
        key = msg.topic.split("/")[-1]
        with self.lock:
            self.car_data[key] = msg.payload.decode("utf-8")

    def get_location(self, getAddress=True):
        """
        获取车辆最新地理位置，数据来自 MQTT 实时推送。
        返回字典包含 latitude、longitude、address（可选）、geofence。
        """
        with self.lock:
            raw = self.car_data.copy()

        lat = "未知"
        lon = "未知"
        amap_lat = "未知"
        amap_lon = "未知"
        try:
            location = json.loads(raw.get("location", "{}"))
            lat = location.get("latitude", "未知")
            lon = location.get("longitude", "未知")
            if lat != "未知" and lon != "未知":
                amap_lon, amap_lat = latlon_to_amap_coord(lat, lon)
        except Exception:
            pass

        address = "未处理"
        if getAddress and lat != "未知" and lon != "未知":
            try:
                address = gps_to_amap_and_reverse(lat, lon)
            except Exception:
                address = "未知"

        return {
            "latitude": amap_lat,
            "longitude": amap_lon,
            "address": address,
            "geofence": raw.get("geofence", "未知"),
        }


def create_tesla_client():
    if not _env_flag("TESLA_MQTT_ENABLED", default=False):
        print("[TeslaMateMQTT] disabled by TESLA_MQTT_ENABLED")
        return NullTeslaMateMQTT("disabled")

    broker = os.getenv("TESLA_MQTT_BROKER", "127.0.0.1")
    port = os.getenv("TESLA_MQTT_PORT", "18883")
    car_id = os.getenv("TESLA_MQTT_CAR_ID", "1")
    user = os.getenv("TESLA_MQTT_USERNAME") or None
    password = os.getenv("TESLA_MQTT_PASSWORD") or None

    try:
        return TeslaMateMQTT(
            broker=broker,
            port=port,
            car_id=car_id,
            user=user,
            password=password,
        )
    except Exception as exc:
        print(f"[TeslaMateMQTT] unavailable, fallback to disabled mode: {exc}")
        return NullTeslaMateMQTT(str(exc))


tesla = create_tesla_client()
