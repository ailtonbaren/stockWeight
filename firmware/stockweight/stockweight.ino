#include "mass_source.h"
#include "stock_engine.h"
#include <esp_random.h>
#ifdef STOCKWEIGHT_MQTT
#include "mqtt_transport.h"
StockMqttTransport network;
constexpr const char* kDeviceId = SW_DEVICE_ID;
#else
constexpr const char* kDeviceId = "stockweight-wokwi-01";
#endif

// GPIO34 is ADC1: it remains usable when Wi-Fi is added later.
constexpr int kMassPin = 34;
constexpr int kAlertPin = 26;
constexpr int kFaultPin = 27;
PotentiometerMassSource source(kMassPin);
stockweight::StockEngine engine;
stockweight::CommandProcessor commands(engine);
String serialLine;
bool serialOverflow = false;
std::string session;
uint32_t sequence = 0;
uint32_t lastSample = 0;
uint32_t lastPublish = 0;

void publishTelemetry() {
  const std::string json = engine.telemetry(kDeviceId, session, ++sequence);
  Serial.println(json.c_str());
#ifdef STOCKWEIGHT_MQTT
  network.publishTelemetry(json);
#endif
}

void executeCommand(const std::string& line) {
  std::string acknowledgement = commands.execute(line);
#ifdef STOCKWEIGHT_MQTT
  // Core acknowledgements are JSON objects; append the source/session identity.
  acknowledgement.pop_back();
  acknowledgement += std::string(",\"device_id\":\"") + kDeviceId + "\",\"session_id\":\"" + session + "\"}";
  network.publishAck(acknowledgement);
#endif
  Serial.println(acknowledgement.c_str());
  digitalWrite(kAlertPin, engine.alert() ? HIGH : LOW);
  publishTelemetry();
}

void setup() {
  Serial.begin(115200);
  source.begin();
  pinMode(kAlertPin, OUTPUT);
  pinMode(kFaultPin, INPUT_PULLUP);
  char id[24];
  snprintf(id, sizeof(id), "boot-%08lx", static_cast<unsigned long>(esp_random()));
  session = id;
  engine.prepare("boot-preparation");
  serialLine.reserve(192);
#ifdef STOCKWEIGHT_MQTT
  network.begin(session);
#endif
  publishTelemetry();
}

void loop() {
  const uint32_t now = millis();
  if (now - lastSample >= 100) {
    lastSample = now;
    const MassReading reading = source.read();
    engine.sample(reading.raw, reading.valid && digitalRead(kFaultPin) == HIGH, now);
    digitalWrite(kAlertPin, engine.alert() ? HIGH : LOW);
  }
  while (Serial.available()) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n') {
      if (serialOverflow) {
        Serial.println("{\"type\":\"ack\",\"command_id\":null,\"ok\":false,\"message\":\"Comando muito longo.\"}");
      } else if (serialLine.length()) {
        executeCommand(serialLine.c_str());
      }
      serialLine = "";
      serialOverflow = false;
    } else if (c != '\r') {
      if (!serialOverflow && serialLine.length() < 192) serialLine += c;
      else serialOverflow = true;
    }
  }
#ifdef STOCKWEIGHT_MQTT
  std::string remoteCommand;
  while (network.nextCommand(remoteCommand)) executeCommand(remoteCommand);
#endif
  if (now - lastPublish >= 500) {
    lastPublish = now;
    publishTelemetry();
  }
  delay(1);
}
