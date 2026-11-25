"""Publish local HA entities through the broker advertised by Supervisor."""
import json
import logging
import os
from urllib.request import HTTPRedirectHandler, Request, build_opener

import paho.mqtt.client as mqtt


class LocalOnly(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def discovery(observer, names, removed):
    prefix = "sitevakt_" + observer.replace("-", "_")
    device_id = "sitevakt_" + observer
    state_topic = f"sitevakt/{observer}/state"

    def problem_sensor(key, label):
        return {
            "platform": "binary_sensor", "device_class": "problem", "name": label,
            "unique_id": f"{device_id}_{key}",
            "default_entity_id": f"binary_sensor.{prefix}_{key.replace('-', '_')}",
            "expire_after": 180,
            "value_template": "{{ value_json['" + key + "'] }}",
        }

    components = {"tenestefeil": problem_sensor("tenestefeil", "Tenestefeil")}
    for name in names:
        key = "maal_" + name
        components[key] = problem_sensor(key, name)
    for name in removed:
        components["maal_" + name] = {"platform": "binary_sensor"}
    return {
        "device": {"identifiers": [device_id], "name": f"Sitevakt {observer}"},
        "origin": {"name": "Sitevakt"}, "components": components,
        "state_topic": state_topic, "qos": 1,
    }


class Dashboard:
    def __init__(self, observer, names, removed):
        self.config = json.dumps(discovery(observer, names, removed))
        self.observer = observer
        self.last_error = None
        self.client = None
        self.announced = False

    def connect(self):
        request = Request("http://supervisor/services/mqtt", headers={
            "Authorization": "Bearer " + os.environ["SUPERVISOR_TOKEN"]})
        with build_opener(LocalOnly).open(request, timeout=4) as response:
            service = json.load(response)["data"]
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, reconnect_on_failure=False)
        client.connect_timeout = 4
        if service.get("ssl"):
            client.tls_set()
        if service.get("username"):
            client.username_pw_set(service["username"], service.get("password"))
        client.connect(service["host"], service["port"])
        client.loop_start()
        self.client = client
        self.announced = False

    def send(self, topic, payload, retain=False):
        info = self.client.publish(topic, payload, qos=1, retain=retain)
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            raise TimeoutError("MQTT PUBACK")

    def publish(self, states):
        try:
            if self.client is None:
                self.connect()
            if not self.announced:
                topic = f"homeassistant/device/sitevakt_{self.observer}/config"
                self.send(topic, self.config, retain=True)
                self.announced = True
            self.send(f"sitevakt/{self.observer}/state", json.dumps(states))
            if self.last_error:
                logging.info("HA-sensorane får målingar att")
            self.last_error = None
        except Exception as error:
            # Optional output: monitoring and durable Sprøyt delivery must continue.
            category = type(error).__name__
            if category != self.last_error:
                logging.warning("HA-sensorane ventar på MQTT (%s)", category)
            self.last_error = category
            if self.client:
                self.client.disconnect()
                self.client.loop_stop()
                self.client = None
