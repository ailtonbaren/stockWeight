#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <esp_arduino_version.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <cstring>
#include <string>

// This task is the sole owner of WiFi/PubSubClient. It never accesses StockEngine:
// inbound commands and outbound snapshots cross bounded FreeRTOS queues instead.
class StockMqttTransport {
 public:
  bool begin(const std::string& session) {
    session_ = session;
    root_ = std::string(SW_MQTT_PREFIX) + "/" + SW_DEVICE_ID;
    telemetryTopic_ = root_ + "/telemetry";
    commandTopic_ = root_ + "/command";
    ackTopic_ = root_ + "/ack";
    availabilityTopic_ = root_ + "/availability";
    telemetry_ = xQueueCreate(1, sizeof(Telemetry));
    acks_ = xQueueCreate(8, sizeof(Acknowledgement));
    commands_ = xQueueCreate(8, sizeof(Command));
    if (!telemetry_ || !acks_ || !commands_) {
      log("Sem memória para as filas MQTT. O monitor serial continua disponível.");
      return false;
    }
    if (xTaskCreate(taskEntry, "stockweight-mqtt", 8192, this, 1, nullptr) != pdPASS) {
      log("Não foi possível iniciar a tarefa MQTT.");
      return false;
    }
    return true;
  }

  void publishTelemetry(const std::string& json) {
    if (!telemetry_ || json.size() >= sizeof(Telemetry::text)) return;
    Telemetry packet{};
    std::memcpy(packet.text, json.c_str(), json.size() + 1);
    // Only the most recent snapshot is needed; the last event has its own ID.
    xQueueOverwrite(telemetry_, &packet);
  }

  void publishAck(const std::string& json) {
    if (!acks_ || json.size() >= sizeof(Acknowledgement::text)) return;
    Acknowledgement packet{};
    std::memcpy(packet.text, json.c_str(), json.size() + 1);
    if (xQueueSend(acks_, &packet, 0) != pdTRUE) log("Fila de confirmações MQTT cheia.");
  }

  bool nextCommand(std::string& line) {
    if (!commands_) return false;
    Command packet{};
    if (xQueueReceive(commands_, &packet, 0) != pdTRUE) return false;
    line = packet.text;
    return true;
  }

 private:
  struct Telemetry { char text[1536]; };
  struct Acknowledgement { char text[768]; };
  struct Command { char text[193]; };
  WiFiClient socket_;
  PubSubClient client_{socket_};
  QueueHandle_t telemetry_ = nullptr, acks_ = nullptr, commands_ = nullptr;
  std::string session_, root_, telemetryTopic_, commandTopic_, ackTopic_, availabilityTopic_;

  static void log(const char* message) {
    Serial.printf("{\"type\":\"network\",\"message\":\"%s\"}\n", message);
  }

  std::string availability(bool online) const {
    return std::string("{\"device_id\":\"") + SW_DEVICE_ID + "\",\"session_id\":\"" + session_ +
           "\",\"online\":" + (online ? "true" : "false") + "}";
  }

  void receive(char* topic, uint8_t* payload, unsigned int length) {
    if (commandTopic_ != topic || length > 270) return;
    const std::string frame(reinterpret_cast<char*>(payload), length);
    const auto separator = frame.find('\t');
    if (separator == std::string::npos || frame.substr(0, separator) != session_) {
      log("Comando de outra sessão ignorado.");
      return;
    }
    const std::string line = frame.substr(separator + 1);
    if (line.empty() || line.size() >= sizeof(Command::text) ||
        line.find_first_of("\r\n\t") != std::string::npos || line.find('\0') != std::string::npos) return;
    Command packet{};
    std::memcpy(packet.text, line.c_str(), line.size() + 1);
    if (xQueueSend(commands_, &packet, 0) != pdTRUE) log("Fila de comandos MQTT cheia.");
  }

  static void taskEntry(void* context) {
    static_cast<StockMqttTransport*>(context)->run();
  }

  void run() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    socket_.setConnectionTimeout(1000);
#else
    socket_.setTimeout(1);
#endif
    client_.setServer(SW_MQTT_HOST, SW_MQTT_PORT);
    if (!client_.setBufferSize(2048)) {
      log("Sem memória para o pacote MQTT.");
      vTaskDelete(nullptr);
      return;
    }
    client_.setKeepAlive(15);
    client_.setSocketTimeout(1);
    client_.setCallback([this](char* topic, uint8_t* payload, unsigned int length) { receive(topic, payload, length); });
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin("Wokwi-GUEST", "", 6);
    log("Conectando ao Wi-Fi Wokwi-GUEST.");
    uint32_t lastAttempt = millis() - 5000;
    const std::string clientId = std::string("sw-") + session_;
    const std::string offline = availability(false);
    for (;;) {
      const uint32_t now = millis();
      if (WiFi.status() == WL_CONNECTED) {
        if (!client_.connected() && now - lastAttempt >= 5000) {
          lastAttempt = now;
          if (client_.connect(clientId.c_str(), availabilityTopic_.c_str(), 0, true, offline.c_str())) {
            if (client_.subscribe(commandTopic_.c_str(), 0)) {
              client_.publish(availabilityTopic_.c_str(), availability(true).c_str(), true);
              log("MQTT conectado. Aguardando comandos do dashboard.");
            } else {
              log("Falha ao assinar o tópico de comandos.");
              client_.disconnect();
            }
          } else {
            Serial.printf("{\"type\":\"network\",\"message\":\"Falha ao conectar ao broker; nova tentativa em 5 s.\",\"code\":%d}\n", client_.state());
          }
        }
        if (client_.connected()) {
          client_.loop();
          Acknowledgement ack{};
          if (xQueuePeek(acks_, &ack, 0) == pdTRUE && client_.publish(ackTopic_.c_str(), ack.text, false))
            xQueueReceive(acks_, &ack, 0);
          Telemetry telemetry{};
          if (xQueueReceive(telemetry_, &telemetry, 0) == pdTRUE)
            client_.publish(telemetryTopic_.c_str(), telemetry.text, false);
        }
      }
      vTaskDelay(pdMS_TO_TICKS(10));
    }
  }
};
