#pragma once

#include <Arduino.h>

// Estrutura que representa uma leitura bruta do sensor e sua validade.
struct MassReading {
  double raw;
  bool valid;
};

// Adaptadores de hardware fornecem apenas a leitura bruta.
// As regras de estoque permanecem concentradas no StockEngine.
class MassSource {
 public:
  // Configura o hardware da fonte de massa.
  virtual void begin() = 0;
  // Retorna uma leitura bruta e informa se ela é válida.
  virtual MassReading read() = 0;
  // Permite destruir a fonte por meio da classe-base.
  virtual ~MassSource() = default;
};

class PotentiometerMassSource : public MassSource {
 public:
  // Guarda o GPIO usado como entrada analógica da massa simulada.
  explicit PotentiometerMassSource(int pin) : pin_(pin) {}
  void begin() override {
    // Configura o pino e a resolução do ADC do ESP32.
    pinMode(pin_, INPUT);
    analogReadResolution(12);
    analogSetPinAttenuation(pin_, ADC_11db);
  }
  MassReading read() override {
    // Lê o potenciômetro e valida o intervalo de 12 bits do ADC.
    const int raw = analogRead(pin_);
    return {static_cast<double>(raw), raw >= 0 && raw <= 4095};
  }
 private:
  // GPIO conectado ao potenciômetro ou, no hardware real, substituído pelo HX711.
  int pin_;
};
