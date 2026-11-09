#include "stock_engine.h"
#include <iostream>

int main(int argc, char** argv) {
  stockweight::StockEngine engine;
  stockweight::CommandProcessor commands(engine);
  const std::string session = argc > 1 ? argv[1] : "native-session";
  if (session.empty() || session.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") != std::string::npos) return 2;
  uint32_t sequence = 0;
  std::string line;
  while (std::getline(std::cin, line)) {
    if (line.compare(0, 7, "sample ") == 0) {
      std::istringstream input(line.substr(7));
      uint32_t milliseconds;
      double raw;
      int valid;
      if (!(input >> milliseconds >> raw >> valid)) return 3;
      engine.sample(raw, valid != 0, milliseconds);
    } else if (line.compare(0, 8, "restore ") == 0) {
      std::istringstream input(line.substr(8));
      stockweight::Settings value;
      if (!(input >> value.unit_g >> value.minimum >> value.grams_per_raw >> value.tare_raw >> value.tare_set >> value.calibrated)) return 3;
      const auto result = engine.restore(value);
      std::cout << "{\"ok\":" << (result.ok ? "true" : "false") << "}" << std::endl;
      continue;
    } else if (line != "snapshot") {
      std::cout << commands.execute(line) << std::endl;
      continue;
    }
    std::cout << engine.telemetry("stockweight-local-01", session, ++sequence) << std::endl;
  }
}
