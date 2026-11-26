// O firmware físico usa HX711 e célula de carga por padrão.
// O gerador do Wokwi sobrescreve esta configuração para usar o potenciômetro.
#ifndef STOCKWEIGHT_PHYSICAL
#define STOCKWEIGHT_PHYSICAL 1
#endif

// Integração opcional com o dashboard via MQTT.
// Substitua os dois campos Wi-Fi antes de gravar no ESP32.
#ifndef STOCKWEIGHT_MQTT
#define STOCKWEIGHT_MQTT 1
#endif
#ifndef SW_MQTT_HOST
#define SW_MQTT_HOST "test.mosquitto.org"
#endif
#ifndef SW_MQTT_PORT
#define SW_MQTT_PORT 1883
#endif
#ifndef SW_MQTT_PREFIX
#define SW_MQTT_PREFIX "stockweight/c7e3c689dfd36f59"
#endif
#ifndef SW_DEVICE_ID
#define SW_DEVICE_ID "stockweight-wokwi-01"
#endif
#ifndef SW_WIFI_SSID
#define SW_WIFI_SSID "JUNINHO 2.4"
#endif
#ifndef SW_WIFI_PASSWORD
#define SW_WIFI_PASSWORD "ab301004"
#endif

// Firmware principal do StockWeight.
// O programa lê a massa, processa a contagem e publica telemetria.
#include "mass_source.h"
#include "stock_engine.h"
#include <esp_random.h>

#ifdef STOCKWEIGHT_MQTT
#include "mqtt_transport.h"
StockMqttTransport network;  // Gerencia Wi-Fi, MQTT e as filas de comunicação.
constexpr const char* kDeviceId = SW_DEVICE_ID;
#else
constexpr const char* kDeviceId = "stockweight-wokwi-01";
#endif

// Definição dos pinos utilizados no protótipo simulado.
// GPIO34 pertence ao ADC1 e continua disponível durante o uso do Wi-Fi.
constexpr int kMassPin = 34;   // Entrada analógica da massa simulada.
constexpr int kAlertPin = 26;  // LED vermelho de alerta de estoque baixo.
constexpr int kFaultPin = 27;  // Botão que simula uma falha de leitura.
#if STOCKWEIGHT_PHYSICAL
constexpr uint32_t kSamplePeriodMs = 1000;  // O HX711 de 10 Hz precisa de tempo entre leituras.
#else
constexpr uint32_t kSamplePeriodMs = 100;
#endif
#if STOCKWEIGHT_PHYSICAL
constexpr int kHx711DoutPin = 18;  // DT/DOUT do HX711.
constexpr int kHx711SckPin = 19;   // SCK do HX711.
constexpr double kPhysicalGramsPerRaw = 0.0025977;  // Calibração obtida com 182 g.
#endif
// Fonte de massa atual: potenciômetro no Wokwi.
// No protótipo físico, esta camada será substituída pela leitura do HX711.
#if STOCKWEIGHT_PHYSICAL
Hx711MassSource source(kHx711DoutPin, kHx711SckPin);
#else
PotentiometerMassSource source(kMassPin);  // Fonte atual de massa.
#endif
stockweight::StockEngine engine;           // Regras de tara, calibração e estoque.
stockweight::CommandProcessor commands(engine);  // Interpreta comandos recebidos.
String serialLine;                         // Buffer do comando serial atual.
bool serialOverflow = false;               // Impede comandos maiores que o limite.
std::string session;                       // Identificador da execução atual.
uint32_t sequence = 0;                     // Sequência das mensagens de telemetria.
uint32_t lastSample = 0;                   // Instante da última amostragem.
uint32_t lastPublish = 0;                  // Instante da última publicação periódica.

// Monta e envia o estado atual para o monitor serial e, quando habilitado, MQTT.
void publishTelemetry() {
  const std::string json = engine.telemetry(kDeviceId, session, ++sequence);
  Serial.println(json.c_str());
#ifdef STOCKWEIGHT_MQTT
  network.publishTelemetry(json);
#endif
}

// Executa um comando recebido do dashboard ou da porta serial.
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

// Executado uma vez no início do ESP32.
void setup() {
  // Inicializa a comunicação serial usada para depuração e integração local.
  Serial.begin(115200);
  // Inicializa o sensor de massa e os pinos de sinalização/falha.
  source.begin();
  pinMode(kAlertPin, OUTPUT);
  pinMode(kFaultPin, INPUT_PULLUP);
  char id[24];
  snprintf(id, sizeof(id), "boot-%08lx", static_cast<unsigned long>(esp_random()));
  session = id;
  // O dispositivo inicia em preparação para evitar movimentações falsas.
  engine.prepare("boot-preparation");
#if STOCKWEIGHT_PHYSICAL
  // Aplica o fator medido antes de começar a receber as amostras físicas.
  engine.setInitialCalibration(kPhysicalGramsPerRaw);
#endif
  serialLine.reserve(192);
#ifdef STOCKWEIGHT_MQTT
  network.begin(session);
#endif
  publishTelemetry();
}

// Laço principal: amostra o sensor, processa comandos e publica telemetria.
void loop() {
  const uint32_t now = millis();
  // Faz uma amostragem a cada 100 ms e atualiza o LED de alerta.
  if (now - lastSample >= kSamplePeriodMs) {
    lastSample = now;
    const MassReading reading = source.read();
    // O botão de falha só existe no circuito do Wokwi.
#if STOCKWEIGHT_PHYSICAL
    engine.sample(reading.raw, reading.valid, now);
#else
    engine.sample(reading.raw, reading.valid && digitalRead(kFaultPin) == HIGH, now);
#endif
    digitalWrite(kAlertPin, engine.alert() ? HIGH : LOW);
  }
  // Lê comandos pela serial, uma linha por vez, com proteção contra excesso.
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
  // Processa comandos recebidos pelo broker MQTT.
#ifdef STOCKWEIGHT_MQTT
  std::string remoteCommand;
  while (network.nextCommand(remoteCommand)) executeCommand(remoteCommand);
#endif
  // Publica uma fotografia do estado a cada 500 ms.
  if (now - lastPublish >= 500) {
    lastPublish = now;
    publishTelemetry();
  }
  delay(1);
}
