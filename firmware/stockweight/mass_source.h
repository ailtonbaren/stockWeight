#pragma once

#include <Arduino.h>
#include <algorithm>

#if STOCKWEIGHT_PHYSICAL
#include <HX711.h>
#endif

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

#if STOCKWEIGHT_PHYSICAL
// Fonte física baseada na célula de carga conectada ao amplificador HX711.
class Hx711MassSource : public MassSource {
 public:
  Hx711MassSource(int dout_pin, int sck_pin) : dout_pin_(dout_pin), sck_pin_(sck_pin) {}

  void begin() override {
    // O HX711 usa dois pinos digitais: dados e relógio.
    scale_.begin(dout_pin_, sck_pin_);
    // Aguarda o módulo concluir a inicialização após receber alimentação.
    delay(1000);
  }

  MassReading read() override {
    // Rejeita os extremos para que uma conversão espúria não bloqueie a tara.
    long samples[7];
    for (unsigned i = 0; i < 7; ++i) samples[i] = scale_.read();
    std::sort(samples, samples + 7);
    long total = 0;
    for (unsigned i = 1; i < 6; ++i) total += samples[i];
    return {static_cast<double>(total) / 5.0, true};
  }

 private:
  int dout_pin_;
  int sck_pin_;
  HX711 scale_;
};
#endif
