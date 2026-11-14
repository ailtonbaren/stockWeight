#pragma once

#include <cmath>
#include <cstdint>
#include <iomanip>
#include <sstream>
#include <string>

namespace stockweight {

// The potentiometer represents 0–5000 g with a 12-bit ESP32 ADC.
constexpr double kNominalGramsPerRaw = 5000.0 / 4095.0;
constexpr unsigned kWindowSize = 10;

struct Settings {
  double unit_g = 5.0;
  int minimum = 50;
  double grams_per_raw = kNominalGramsPerRaw;
  double tare_raw = 0.0;
  bool tare_set = false;
  bool calibrated = false;
};

struct Result {
  bool ok;
  const char* message;
};

class StockEngine {
 public:
  const Settings& settings() const { return settings_; }

  Result restore(const Settings& value) {
    if (!validUnit(value.unit_g) || value.minimum < 0 || value.minimum > 1000000 ||
        !std::isfinite(value.grams_per_raw) || value.grams_per_raw <= 0 ||
        value.grams_per_raw > 10000 || !std::isfinite(value.tare_raw) ||
        value.tare_raw < 0 || value.tare_raw > 4095) {
      return {false, "Parâmetros persistidos inválidos."};
    }
    settings_ = value;
    resetBaseline();
    return {true, "Configuração restaurada."};
  }

  void sample(double raw, bool valid, uint32_t now_ms) {
    if (static_cast<uint32_t>(now_ms - now_ms_) > 500) clearWindow();
    now_ms_ = now_ms;
    valid_ = valid && std::isfinite(raw) && raw >= 0 && raw <= 4095;
    if (!valid_) {
      clearWindow();
      return;
    }
    samples_[cursor_] = raw;
    cursor_ = (cursor_ + 1) % kWindowSize;
    if (count_ < kWindowSize) ++count_;
    double total = 0, low = samples_[0], high = samples_[0];
    for (unsigned i = 0; i < count_; ++i) {
      total += samples_[i];
      if (samples_[i] < low) low = samples_[i];
      if (samples_[i] > high) high = samples_[i];
    }
    raw_ = total / count_;
    const double tolerance_g = std::fmax(0.25, std::fmin(1.5, settings_.unit_g * 0.25));
    // A removed container must not become a withdrawal of the entire stock.
    container_present_ = (raw - settings_.tare_raw) * settings_.grams_per_raw >= -tolerance_g;
    stable_ = container_present_ && count_ == kWindowSize &&
              (high - low) * settings_.grams_per_raw <= tolerance_g;
    if (!stable_) return;
    last_stable_raw_ = raw_;
    const double net = std::fmax(0.0, netGrams());
    const double estimate = std::floor(net / settings_.unit_g + 0.5);
    if (estimate > 1000000) {
      valid_ = false;
      clearWindow();
      return;
    }
    const int quantity = static_cast<int>(estimate);
    if (!monitoring_) {
      has_quantity_ = true;
      quantity_ = quantity;
      accepted_net_g_ = net;
      return;
    }
    if (!has_baseline_) {
      addEvent(ever_baseline_ ? "adjustment" : "initial", 0, quantity);
      has_baseline_ = ever_baseline_ = true;
      has_quantity_ = true;
      quantity_ = quantity;
      accepted_net_g_ = net;
    } else if (quantity != quantity_ &&
               std::fabs(net - accepted_net_g_) >= settings_.unit_g * 0.65) {
      addEvent(quantity < quantity_ ? "withdrawal" : "restock", quantity - quantity_, quantity);
      quantity_ = quantity;
      accepted_net_g_ = net;
    }
  }

  Result tare() {
    if (!valid_ || !stable_) return {false, "Aguarde uma leitura estável antes de tarar."};
    settings_.tare_raw = last_stable_raw_;
    settings_.tare_set = true;
    resetBaseline();
    return {true, "Tara aplicada. Aguarde a estabilização."};
  }

  Result prepare(const std::string& measurement_id) {
    workflow_ = true;
    monitoring_ = false;
    measurement_id_ = measurement_id;
    settings_.tare_raw = 0;
    settings_.tare_set = false;
    ever_baseline_ = false;
    resetBaseline();
    return {true, "Preparação iniciada. Tare o recipiente vazio e defina a massa por peça."};
  }

  Result startMonitoring(const std::string& measurement_id, const std::string& expected_preparation = "") {
    if (!expected_preparation.empty() && expected_preparation != measurement_id_)
      return {false, "A preparação mudou. Confira o material e inicie novamente pelo painel."};
    if (monitoring_) return {false, "O monitoramento já está ativo. Prepare uma nova contagem para reiniciar."};
    if (!settings_.tare_set || !valid_ || !stable_ || !container_present_ || !has_quantity_)
      return {false, "Tare o recipiente e aguarde o lote estabilizar antes de iniciar."};
    monitoring_ = true;
    measurement_id_ = measurement_id;
    has_baseline_ = ever_baseline_ = true;
    accepted_net_g_ = std::fmax(0.0, netGrams());
    addEvent("initial", 0, quantity_);
    return {true, "Monitoramento iniciado com o estoque atual."};
  }

  Result resetTare() {
    // Recovery must work even when an excessive tare hides the container.
    if (!settings_.tare_set && settings_.tare_raw == 0)
      return {true, "A tara já está zerada."};
    settings_.tare_raw = 0;
    settings_.tare_set = false;
    resetBaseline();
    return {true, "Tara resetada. Aguarde estabilizar e tare o recipiente vazio."};
  }

  Result calibrate(double reference_g) {
    if (!std::isfinite(reference_g) || reference_g <= 0 || reference_g > 5000)
      return {false, "Informe uma massa de referência maior que zero e até 5000 g."};
    if (!settings_.tare_set) return {false, "Tare o recipiente vazio antes da calibração."};
    if (!valid_ || !stable_) return {false, "Aguarde uma leitura estável antes de calibrar."};
    const double span = last_stable_raw_ - settings_.tare_raw;
    if (span < 1.0) return {false, "Coloque a massa de referência sobre o recipiente tarado."};
    const double factor = reference_g / span;
    if (!std::isfinite(factor) || factor <= 0 || factor > 10000)
      return {false, "Fator de calibração inválido."};
    settings_.grams_per_raw = factor;
    settings_.calibrated = true;
    resetBaseline();
    return {true, "Calibração aplicada à escala simulada."};
  }

  Result setUnit(double grams) {
    if (!validUnit(grams)) return {false, "A massa unitária deve estar entre 0,01 e 5000 g."};
    if (settings_.unit_g != grams) {
      settings_.unit_g = grams;
      resetBaseline();
    }
    return {true, "Massa unitária atualizada."};
  }

  Result unitFromSample(int pieces) {
    if (pieces <= 0 || pieces > 1000000) return {false, "Informe uma quantidade inteira positiva."};
    if (!settings_.tare_set || !valid_ || !stable_ || netGrams() <= 0)
      return {false, "Tare o recipiente e pese uma amostra estável de peças."};
    return setUnit(netGrams() / pieces);
  }

  Result setMinimum(int minimum) {
    if (minimum < 0 || minimum > 1000000) return {false, "O estoque mínimo deve ser um inteiro de 0 a 1000000."};
    settings_.minimum = minimum;
    return {true, "Estoque mínimo atualizado."};
  }

  bool alert() const {
    return monitoring_ && valid_ && container_present_ && has_quantity_ && quantity_ <= settings_.minimum;
  }

  std::string telemetry(const std::string& device, const std::string& session, uint32_t sequence) const {
    std::ostringstream out;
    out << std::fixed << std::setprecision(5)
        << "{\"type\":\"telemetry\",\"device_id\":\"" << device
        << "\",\"session_id\":\"" << session << "\",\"sequence\":" << sequence
        << ",\"uptime_ms\":" << now_ms_ << ",\"valid\":" << boolean(valid_)
        << ",\"monitoring\":" << boolean(monitoring_)
        << ",\"measurement_id\":\"" << measurement_id_ << "\""
        << ",\"stable\":" << boolean(stable_)
        << ",\"container_present\":" << boolean(valid_ && container_present_)
        << ",\"status\":\"" << (!valid_ ? "invalid" : !container_present_ ? "container_removed" : stable_ ? "ready" : "settling")
        << "\",\"raw\":" << (valid_ ? number(raw_) : "null")
        << ",\"gross_g\":" << (valid_ ? number(raw_ * settings_.grams_per_raw) : "null")
        << ",\"tare_g\":" << settings_.tare_raw * settings_.grams_per_raw
        << ",\"net_g\":" << (valid_ ? number(netGrams()) : "null")
        << ",\"quantity\":" << (valid_ && container_present_ && has_quantity_ ? std::to_string(quantity_) : "null")
        << ",\"alert\":" << (valid_ && container_present_ && has_quantity_ ? boolean(alert()) : "null")
        << ",\"settings\":{\"unit_g\":" << std::setprecision(10) << settings_.unit_g << std::setprecision(5) << ",\"minimum\":" << settings_.minimum
        << ",\"grams_per_raw\":" << std::setprecision(10) << settings_.grams_per_raw << std::setprecision(5)
        << ",\"tare_raw\":" << settings_.tare_raw
        << ",\"tare_set\":" << boolean(settings_.tare_set) << ",\"calibrated\":" << boolean(settings_.calibrated) << "},\"event\":";
    if (!monitoring_ || !event_seq_) out << "null";
    else out << "{\"sequence\":" << event_seq_ << ",\"kind\":\"" << event_kind_
             << "\",\"delta\":" << event_delta_ << ",\"quantity\":" << event_quantity_ << "}";
    out << "}";
    return out.str();
  }

 private:
  Settings settings_;
  // Standalone core clients retain the original behavior; the application and
  // ESP32 explicitly enter preparation on boot before accepting stock changes.
  bool monitoring_ = true;
  bool workflow_ = false;
  std::string measurement_id_ = "legacy";
  double samples_[kWindowSize] = {};
  unsigned count_ = 0, cursor_ = 0;
  double raw_ = 0, last_stable_raw_ = 0, accepted_net_g_ = 0;
  bool valid_ = false, stable_ = false, container_present_ = true;
  bool has_baseline_ = false, ever_baseline_ = false, has_quantity_ = false;
  int quantity_ = 0;
  uint32_t now_ms_ = 0, event_seq_ = 0;
  const char* event_kind_ = "initial";
  int event_delta_ = 0, event_quantity_ = 0;

  static bool validUnit(double value) { return std::isfinite(value) && value >= 0.01 && value <= 5000; }
  static const char* boolean(bool value) { return value ? "true" : "false"; }
  static std::string number(double value) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(5) << value;
    return out.str();
  }
  double netGrams() const { return (raw_ - settings_.tare_raw) * settings_.grams_per_raw; }
  void clearWindow() { count_ = cursor_ = 0; stable_ = false; }
  void resetBaseline() {
    if (workflow_) monitoring_ = false;
    has_baseline_ = has_quantity_ = false;
    clearWindow();
  }
  void addEvent(const char* kind, int delta, int quantity) {
    ++event_seq_;
    event_kind_ = kind;
    event_delta_ = delta;
    event_quantity_ = quantity;
  }
};

// Shared command protocol for Serial and MQTT.
// Commands: prepare ID | start_monitoring ID [PREPARATION] | tare ID | reset_tare ID
//           calibrate ID GRAMS | unit ID GRAMS | minimum ID INTEGER
//           sample_unit ID INTEGER
inline std::string command(StockEngine& engine, const std::string& line) {
  std::istringstream input(line);
  std::string action, id, extra;
  input >> action >> id;
  const bool id_ok = !id.empty() && id.size() <= 64 &&
      id.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") == std::string::npos;
  Result result = {false, "Comando inválido."};
  if (!id_ok) return "{\"type\":\"ack\",\"command_id\":null,\"ok\":false,\"message\":\"Identificador inválido.\"}";
  if (action == "prepare") {
    if (!(input >> extra)) result = engine.prepare(id);
  } else if (action == "start_monitoring") {
    std::string expected;
    if (!(input >> expected)) result = engine.startMonitoring(id);
    else if (!(input >> extra)) result = engine.startMonitoring(id, expected);
  } else if (action == "tare" || action == "reset_tare") {
    if (!(input >> extra)) result = action == "tare" ? engine.tare() : engine.resetTare();
  } else if (action == "unit" || action == "calibrate") {
    double value;
    if ((input >> value) && !(input >> extra)) result = action == "unit" ? engine.setUnit(value) : engine.calibrate(value);
  } else if (action == "minimum" || action == "sample_unit") {
    int value;
    if ((input >> value) && !(input >> extra)) result = action == "minimum" ? engine.setMinimum(value) : engine.unitFromSample(value);
  }
  std::ostringstream out;
  out << std::fixed << std::setprecision(10)
      << "{\"type\":\"ack\",\"command_id\":\"" << id << "\",\"ok\":"
      << (result.ok ? "true" : "false") << ",\"message\":\"" << result.message
      << "\",\"unit_g\":" << engine.settings().unit_g << ",\"minimum\":" << engine.settings().minimum << "}";
  return out.str();
}

class CommandProcessor {
 public:
  explicit CommandProcessor(StockEngine& engine) : engine_(engine) {}
  std::string execute(const std::string& line) {
    std::istringstream input(line);
    std::string action, id;
    input >> action >> id;
    for (const auto& cached : cache_) {
      if (!id.empty() && cached.id == id) {
        if (cached.line == line) return cached.response;
        return "{\"type\":\"ack\",\"command_id\":null,\"ok\":false,\"message\":\"Identificador já usado para outro comando.\"}";
      }
    }
    const std::string response = command(engine_, line);
    cache_[cursor_] = {id, line, response};
    cursor_ = (cursor_ + 1) % 8;
    return response;
  }
 private:
  struct Cached { std::string id, line, response; };
  StockEngine& engine_;
  Cached cache_[8];
  unsigned cursor_ = 0;
};

}  // namespace stockweight
