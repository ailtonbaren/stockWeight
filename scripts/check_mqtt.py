#!/usr/bin/env python3
"""Probe DNS, each IP route and MQTT 3.1.1 CONNECT without extra packages."""
import argparse
from pathlib import Path
import secrets
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.mqtt_config import DEFAULT_CONFIG, MqttConfig, load_config


def probe(host, port, timeout=3):
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        print(f"DNS de {host}: {error}")
        return False
    client_id = ("sw-check-" + secrets.token_hex(4)).encode("ascii")
    body = b"\x00\x04MQTT\x04\x02\x00\x0a" + len(client_id).to_bytes(2, "big") + client_id
    packet = b"\x10" + bytes([len(body)]) + body
    visited = set()
    success = False
    for family, kind, protocol, _, address in addresses:
        if (family, address) in visited:
            continue
        visited.add((family, address))
        label = "IPv6" if family == socket.AF_INET6 else "IPv4"
        try:
            with socket.socket(family, kind, protocol) as connection:
                connection.settimeout(timeout)
                connection.connect(address)
                connection.sendall(packet)
                reply = b""
                while len(reply) < 4:
                    chunk = connection.recv(4 - len(reply))
                    if not chunk:
                        raise OSError("O servidor encerrou a conexão sem confirmar MQTT.")
                    reply += chunk
                if reply != b"\x20\x02\x00\x00":
                    raise OSError(f"MQTT não aceitou a conexão: resposta {reply.hex()}.")
                connection.sendall(b"\xe0\x00")
                print(f"{host}:{port} · {label} {address[0]}: MQTT OK")
                success = True
        except OSError as error:
            print(f"{host}:{port} · {label} {address[0]}: {error}")
    return success


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Testar conexão MQTT sem instalar bibliotecas")
    parser.add_argument("--mqtt-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--broker", help="Testar outro broker sem alterar a configuração")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    config = load_config(args.mqtt_config)
    selected = MqttConfig(host=args.broker or config.host, port=args.port if args.port is not None else config.port)
    raise SystemExit(0 if probe(selected.host, selected.port) else 1)
