#pragma once

#include <Arduino.h>

struct MassReading {
  double raw;
  bool valid;
};

// Hardware adapters only supply raw readings. Stock rules stay in StockEngine.
class MassSource {
 public:
  virtual void begin() = 0;
  virtual MassReading read() = 0;
  virtual ~MassSource() = default;
};

class PotentiometerMassSource : public MassSource {
 public:
  explicit PotentiometerMassSource(int pin) : pin_(pin) {}
  void begin() override {
    pinMode(pin_, INPUT);
    analogReadResolution(12);
    analogSetPinAttenuation(pin_, ADC_11db);
  }
  MassReading read() override {
    const int raw = analogRead(pin_);
    return {static_cast<double>(raw), raw >= 0 && raw <= 4095};
  }
 private:
  int pin_;
};
