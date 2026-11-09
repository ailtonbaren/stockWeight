"""Shared MQTT settings for the dashboard and the generated Wokwi sketch."""
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import secrets

from scripts.build_simulator import ROOT

DEFAULT_CONFIG = ROOT / "data/mqtt.json"
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True)
class MqttConfig:
    host: str = "test.mosquitto.org"
    port: int = 1883
    prefix: str = "stockweight/example"
    device_id: str = "stockweight-wokwi-01"

    def __post_init__(self):
        if not isinstance(self.host, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", self.host):
            raise ValueError("Informe o hostname do broker, sem http:// ou mqtt://.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Porta MQTT inválida.")
        if not isinstance(self.prefix, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", self.prefix) or len(self.prefix) > 80:
            raise ValueError("Prefixo MQTT inválido: use letras, números, /, - ou _.")
        if not isinstance(self.device_id, str) or not IDENTIFIER.fullmatch(self.device_id):
            raise ValueError("Identificador de dispositivo inválido.")

    @property
    def topic(self):
        return f"{self.prefix}/{self.device_id}"


def load_config(path: Path = DEFAULT_CONFIG) -> MqttConfig:
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        config = MqttConfig(prefix="stockweight/" + secrets.token_hex(8))
        try:
            with path.open("x", encoding="utf-8") as output:
                json.dump(asdict(config), output, indent=2)
                output.write("\n")
        except FileExistsError:
            pass
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {"host", "port", "prefix", "device_id"}:
            raise ValueError("A configuração precisa de host, port, prefix e device_id.")
        return MqttConfig(**value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Arquivo de configuração MQTT inválido.") from error
