"use strict";

const $ = (id) => document.getElementById(id);
const format = (value, digits = 0) => value == null ? "—" : Number(value).toLocaleString("pt-BR", {minimumFractionDigits: digits, maximumFractionDigits: digits});
const gram = (value) => value == null ? "—" : `${format(value, 1)} g`;
const time = (value) => value ? new Date(value).toLocaleTimeString("pt-BR") : "—";
let current = null;
let refreshing = false;
let busy = false;
let initialized = false;
let chosenMaterial = null;
let historyMaterial = "";
let materialOptionsKey = "";
let feedbackTimer;
let sliderTimer;

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {cache: "no-store"} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || result.message || "Não foi possível concluir a operação.");
  return result;
}

function feedback(message, failure = false, persistent = false) {
  clearTimeout(feedbackTimer);
  $("feedback").textContent = message;
  $("feedback").classList.toggle("failure", failure);
  $("feedback").hidden = false;
  if (!persistent) feedbackTimer = setTimeout(() => { $("feedback").hidden = true; }, 5500);
}

function controls() {
  const choiceMatches = !chosenMaterial || chosenMaterial === current?.workflow?.material?.id;
  const online = Boolean(current?.connected) && !busy && Boolean(current?.workflow?.ready) && choiceMatches;
  const stable = online && Boolean(current?.telemetry?.stable);
  const preparing = current?.workflow?.phase === "preparing";
  $("tare-button").disabled = !stable || !preparing;
  $("reset-tare-button").disabled = !online || !current?.telemetry?.settings?.tare_set;
  $("prepare-button").disabled = !current?.connected || busy || !current?.workflow?.supported;
  $("start-button").disabled = busy || !current?.workflow?.can_start || !choiceMatches;
  $("material-select").disabled = busy;
  $("material-form").querySelector("button").disabled = busy;
  document.querySelectorAll("[data-command-control]").forEach((button) => { button.disabled = !online; });
  document.querySelectorAll("[data-preparation-control]").forEach((button) => { button.disabled = !online || !preparing; });
  document.querySelectorAll("[data-stable-control]").forEach((button) => { button.disabled = !stable || !preparing || !current?.telemetry?.settings?.tare_set; });
}

async function sendCommand(command, value, extra = {}) {
  if (busy) return;
  busy = true;
  controls();
  try {
    if (current?.mode === "mqtt") feedback("Enviando comando. Aguardando confirmação do ESP32…", false, true);
    const result = await request("/api/command", {command, value, command_id: crypto.randomUUID(), ...extra});
    feedback(result.message);
    // Reflect the acknowledged configuration, including a unit derived from a sample.
    if (command === "sample_unit") $("unit").dataset.dirty = "false";
    if (command === "unit") $("unit").dataset.dirty = "false";
    if (command === "minimum") $("minimum").dataset.dirty = "false";
    if (command === "prepare") {
      chosenMaterial = null;
      historyMaterial = "";
      $("unit").dataset.dirty = $("minimum").dataset.dirty = "false";
    }
    await refresh();
  } catch (error) {
    feedback(error.message, true);
    await refresh();
  } finally {
    busy = false;
    controls();
  }
}

async function simulation(change) {
  if (current?.mode === "mqtt") return;
  try {
    await request("/api/simulation", change);
    await refresh();
  } catch (error) {
    feedback(error.message, true);
    await refresh();
  }
}

function inputValue(id, value) {
  const input = $(id);
  if (document.activeElement !== input && input.dataset.dirty !== "true") input.value = value;
}

function render(data) {
  current = data;
  const reading = data.telemetry;
  const online = data.connected;
  const mqttMode = data.mode === "mqtt";
  const flow = data.workflow;
  renderMaterials(data.materials, flow);
  $("connection").textContent = mqttMode ? (online ? "ESP32 conectado" : data.mqtt_connected ? "Aguardando ESP32" : "Broker desconectado") : (online ? "Simulador local ativo" : "Simulador desconectado");
  $("connection").className = `pill ${online ? "normal" : "neutral"}`;
  $("last-reading").textContent = time(data.last_reading_at);
  $("server-error").hidden = !data.error;
  $("server-error").textContent = data.error || "";
  $("history-warning").hidden = !data.history_warning;
  $("history-warning").textContent = data.history_warning || "";
  $("mode-title").textContent = mqttMode ? "ESP32 via MQTT." : "Modo de teste local.";
  $("mode-description").textContent = mqttMode ? "As leituras vêm do ESP32 no Wokwi. Aguarde a confirmação ao enviar comandos." : "Os dados são simulados neste computador. Use os controles de massa para testar.";
  $("mode-tag").textContent = mqttMode ? (data.mqtt_connected ? "Broker conectado" : "Aguardando broker") : "Teste sem broker";
  $("local-controls").hidden = mqttMode;
  $("mqtt-controls").hidden = !mqttMode;
  $("source-eyebrow").textContent = mqttMode ? "Leituras recebidas por MQTT" : "Simulador local";
  $("simulation-title").textContent = mqttMode ? "ESP32 no Wokwi" : "Entrada de peso";
  if (mqttMode) {
    $("mqtt-host").textContent = `${data.mqtt.host}:${data.mqtt.port}`;
    $("mqtt-topic").textContent = data.mqtt.topic;
    $("device-state").textContent = online ? "Recebendo leituras do ESP32" : "Aguardando uma leitura recente do ESP32";
  }
  $("pause").textContent = data.simulation.connected ? "Pausar conexão" : "Reconectar simulador";
  $("fault").checked = data.simulation.fault;
  if (!initialized && !mqttMode) {
    $("mass").value = data.simulation.mass_g;
    $("mass-slider").value = data.simulation.mass_g;
    initialized = true;
  }
  if (reading && flow.ready) {
    const settings = reading.settings;
    const quantity = online && flow.unit_ready && settings.tare_set ? reading.quantity : null;
    const stable = online && reading.stable;
    let status = "Estoque normal", style = "normal", hint = "Quantidade confirmada após estabilização da leitura.";
    if (!online) {
      status = "Sem conexão"; style = "neutral"; hint = "A última leitura foi preservada. Reconecte para atualizar o estoque.";
    } else if (!reading.valid) {
      status = "Falha de leitura"; style = "alert"; hint = "Leitura inválida. O histórico não registra movimentações durante a falha.";
    } else if (!reading.container_present) {
      status = "Recipiente ausente"; style = "neutral"; hint = "Massa abaixo da tara. Aguardando o retorno do recipiente.";
    } else if (!stable) {
      status = "Estabilizando"; style = "neutral"; hint = "Aguarde cerca de 1 segundo. Oscilações não geram movimentações.";
    } else if (flow.phase === "preparing") {
      status = "Estimativa de teste"; style = "neutral"; hint = "Confira o lote e clique em Iniciar monitoramento. Nenhuma movimentação é registrada nesta etapa.";
      if (!settings.tare_set) { status = "Tare o recipiente"; hint = flow.message; }
      else if (!flow.unit_ready) { status = "Pese uma amostra"; hint = flow.message; }
    } else if (reading.alert) {
      status = "Repor estoque"; style = "alert"; hint = "A quantidade está igual ou abaixo do estoque mínimo.";
    }
    $("quantity").textContent = format(quantity);
    $("stock-status").textContent = status;
    $("stock-status").className = `pill ${style}`;
    $("stock-hint").textContent = hint;
    $("stability").textContent = stable ? "Leitura estável" : "Leitura não confirmada";
    $("minimum-label").textContent = `${format(settings.minimum)} peças`;
    $("unit-label").textContent = flow.unit_ready ? `${format(settings.unit_g, 3)} g` : "A definir";
    $("net").textContent = online ? format(reading.net_g, 1) : "—";
    $("gross").textContent = online ? gram(reading.gross_g) : "—";
    $("tare").textContent = gram(reading.tare_g);
    $("stock-meter").max = Math.max(settings.minimum * 2, quantity || 0, 1);
    $("stock-meter").value = quantity || 0;
    $("virtual-led").classList.toggle("on", online && reading.alert === true);
    $("virtual-led").setAttribute("aria-label", online && reading.alert ? "LED de alerta aceso" : "LED de alerta desligado");
    inputValue("unit", flow.material.unit_g == null ? "" : Number(flow.material.unit_g.toFixed(6)));
    inputValue("minimum", settings.minimum);
    $("calibration-status").textContent = settings.calibrated ? "Escala calibrada com massa de referência." : "Escala nominal, sem calibração de referência.";
  } else {
    for (const id of ["quantity", "net", "gross", "tare", "unit-label", "minimum-label"]) $(id).textContent = "—";
    $("stock-status").textContent = "Aguardando leitura";
    $("stock-status").className = "pill neutral";
    $("stability").textContent = "Leitura não confirmada";
    $("stock-hint").textContent = mqttMode ? "Inicie a simulação no Wokwi e aguarde uma leitura recente." : "Aguarde uma leitura estável para confirmar o estoque.";
    $("stock-meter").value = 0;
    $("virtual-led").classList.remove("on");
    $("virtual-led").setAttribute("aria-label", "LED sem leitura disponível");
  }
  renderAutonomy(data.autonomy);
  renderHistory(data.events);
  drawChart(data.readings, reading?.settings.minimum || 0);
  controls();
}

function renderMaterials(materials, flow) {
  const key = JSON.stringify(materials.map(({id, name}) => [id, name]));
  if (key !== materialOptionsKey) {
    materialOptionsKey = key;
    $("material-select").replaceChildren();
    $("history-material").replaceChildren(new Option("Material atual", ""), new Option("Todos os materiais", "all"));
    for (const material of materials) {
      $("material-select").add(new Option(material.name, material.id));
      $("history-material").add(new Option(material.name, material.id));
    }
  }
  $("material-select").value = chosenMaterial || flow.material.id;
  $("history-material").value = historyMaterial;
  $("material-title").textContent = flow.material.name;
  $("workflow-phase").textContent = flow.phase === "monitoring" ? "Monitorando" : "Preparação";
  $("workflow-phase").className = `pill ${flow.phase === "monitoring" ? "normal" : "neutral"}`;
  $("preparation-title").textContent = flow.phase === "monitoring" ? "Material monitorado" : "Preparar contagem";
  $("workflow-message").textContent = chosenMaterial && chosenMaterial !== flow.material.id
    ? "Clique em Preparar contagem para usar o material selecionado. Troque o conteúdo do recipiente após a confirmação."
    : flow.message;
  $("preparation-note").hidden = flow.phase === "monitoring";
  $("start-button").hidden = flow.phase === "monitoring";
  $("prepare-button").textContent = flow.ready && (!chosenMaterial || chosenMaterial === flow.material.id) ? "Preparar nova contagem" : "Preparar contagem";
}

function renderAutonomy(autonomy) {
  const hours = format(autonomy.observed_seconds / 3600, 1);
  const observed = `${hours} h acompanhadas nos últimos ${autonomy.window_days} dias corridos.`;
  const messages = {
    forecast: `${format(autonomy.days, 1)} dias`,
    no_consumption: "Sem consumo registrado",
    insufficient_data: "Dados insuficientes",
    unavailable: "Aguardando leitura válida",
    preparing: "Preparação da contagem",
  };
  $("autonomy").textContent = messages[autonomy.status] || "Dados insuficientes";
  $("consumed").textContent = format(autonomy.consumed_pieces);
  let detail = `Mínimo de ${autonomy.minimum_hours} h acompanhadas. ${observed}`;
  if (autonomy.status === "forecast") detail = `${format(autonomy.daily_consumption, 1)} peças/dia. ${observed} Estimativa se o consumo médio se mantiver.`;
  if (autonomy.status === "no_consumption") detail = `${observed} Ainda não há retiradas para calcular a previsão.`;
  if (autonomy.status === "unavailable") detail = `A previsão fica suspensa até receber uma leitura conectada e estável com o recipiente. ${observed}`;
  if (autonomy.status === "preparing") detail = "O acompanhamento do consumo começa ao iniciar o monitoramento do lote.";
  $("autonomy-detail").textContent = detail;
}

function renderHistory(events, target = "events", example = false) {
  const body = $(target);
  body.replaceChildren();
  if (!events.length) {
    const row = body.insertRow();
    const cell = row.insertCell();
    cell.colSpan = example ? 4 : 5; cell.className = "empty";
    cell.textContent = "O histórico começa ao iniciar o monitoramento. Não há movimentos para este material.";
    return;
  }
  const labels = {initial: "Estoque inicial", adjustment: "Ajuste de medição", withdrawal: "Retirada", restock: "Reposição"};
  for (const event of events) {
    const row = body.insertRow();
    row.insertCell().textContent = example ? `${format(event.elapsed_hours)} h` : new Date(event.received_at).toLocaleString("pt-BR");
    if (!example) row.insertCell().textContent = event.material_name;
    const tag = document.createElement("span");
    tag.className = `event-tag ${event.kind}`;
    tag.textContent = labels[event.kind] || event.kind;
    row.insertCell().append(tag);
    const delta = row.insertCell();
    delta.textContent = ["initial", "adjustment"].includes(event.kind) ? "—" : `${event.delta > 0 ? "+" : ""}${format(event.delta)}`;
    delta.className = event.delta < 0 ? "delta-negative" : event.delta > 0 ? "delta-positive" : "";
    row.insertCell().textContent = `${format(event.quantity)} peças`;
  }
}

let exampleLoaded = false;
let exampleLoading = false;
$("autonomy-example").addEventListener("toggle", async () => {
  if (!$("autonomy-example").open || exampleLoaded || exampleLoading) return;
  exampleLoading = true;
  $("example-loading").hidden = false;
  $("example-error").hidden = true;
  try {
    const data = await request("/api/autonomy/example");
    const autonomy = data.autonomy;
    $("example-stock").textContent = `${format(data.quantity)} peças`;
    $("example-period").textContent = `${format(autonomy.observed_days)} dias`;
    $("example-consumed").textContent = `${format(autonomy.consumed_pieces)} peças`;
    $("example-rate").textContent = `${format(autonomy.daily_consumption, 1)} peças/dia`;
    $("example-days").textContent = `${format(autonomy.days, 1)} dias`;
    $("example-formula").textContent = `${format(autonomy.consumed_pieces)} peças ÷ ${format(autonomy.observed_days)} dias = ${format(autonomy.daily_consumption, 1)} peças/dia. ${format(data.quantity)} peças ÷ ${format(autonomy.daily_consumption, 1)} peças/dia = ${format(autonomy.days, 1)} dias de autonomia.`;
    renderHistory(data.events, "example-events", true);
    $("example-content").hidden = false;
    exampleLoaded = true;
  } catch (error) {
    $("example-error").textContent = "Não foi possível carregar o exemplo. Feche e abra esta seção para tentar novamente.";
    $("example-error").hidden = false;
  } finally {
    $("example-loading").hidden = true;
    exampleLoading = false;
  }
});

function drawChart(readings, minimum) {
  const canvas = $("stock-chart");
  const rectangle = canvas.getBoundingClientRect();
  const width = rectangle.width, height = rectangle.height;
  if (!width || !height) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const context = canvas.getContext("2d");
  const colors = getComputedStyle(document.documentElement);
  const chartText = colors.getPropertyValue("--chart-text").trim();
  context.scale(ratio, ratio);
  const stable = readings.filter((point) => point.stable && point.quantity != null);
  $("chart-empty").hidden = stable.length < 2;
  const left = 40, right = width - 16, top = 12, bottom = height - 24;
  const maximum = Math.max(20, Math.ceil(Math.max(minimum, ...stable.map((p) => p.quantity)) / 20) * 20);
  const y = (value) => bottom - value / maximum * (bottom - top);
  context.font = "11px system-ui";
  for (let step = 0; step <= 2; step++) {
    const value = Math.round(maximum * step / 2);
    context.strokeStyle = colors.getPropertyValue("--chart-grid").trim();
    context.beginPath(); context.moveTo(left, y(value)); context.lineTo(right, y(value)); context.stroke();
    context.fillStyle = chartText; context.fillText(format(value), 0, y(value) + 3);
  }
  if (stable.length < 2) return;
  const firstTime = Date.parse(readings[0].received_at);
  const duration = Math.max(1000, Date.parse(readings.at(-1).received_at) - firstTime);
  const x = (point) => left + (Date.parse(point.received_at) - firstTime) / duration * (right - left);
  context.setLineDash([4, 4]); context.strokeStyle = colors.getPropertyValue("--chart-limit").trim();
  context.beginPath(); context.moveTo(left, y(minimum)); context.lineTo(right, y(minimum)); context.stroke();
  context.setLineDash([]); context.strokeStyle = colors.getPropertyValue("--chart-line").trim(); context.lineWidth = 2;
  context.beginPath();
  let drawing = false;
  for (const point of readings) {
    if (!point.stable || point.quantity == null) { drawing = false; continue; }
    if (!drawing) context.moveTo(x(point), y(point.quantity));
    else context.lineTo(x(point), y(point.quantity));
    drawing = true;
  }
  context.stroke();
  context.fillStyle = chartText;
  context.fillText(time(readings[0].received_at), left, height - 3);
  const lastLabel = time(readings.at(-1).received_at);
  context.fillText(lastLabel, right - context.measureText(lastLabel).width, height - 3);
  canvas.setAttribute("aria-label", `Histórico recente. Última quantidade estável: ${stable.at(-1).quantity} peças. Estoque mínimo: ${minimum} peças.`);
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const query = historyMaterial ? `?history_material=${encodeURIComponent(historyMaterial)}` : "";
    render(await request("/api/state" + query));
  } catch (error) {
    $("connection").textContent = "Servidor local indisponível";
    $("connection").className = "pill alert";
    $("server-error").hidden = false;
    $("server-error").textContent = "Não foi possível atualizar o painel. Verifique se o servidor está em execução no terminal.";
    if (current) current.connected = false;
    for (const id of ["quantity", "net", "gross"]) $(id).textContent = "—";
    $("autonomy").textContent = "Servidor indisponível";
    $("autonomy-detail").textContent = "Reconecte ao servidor para atualizar a previsão.";
    $("stock-status").textContent = "Sem conexão";
    $("stock-status").className = "pill neutral";
    $("virtual-led").classList.remove("on");
    controls();
  } finally {
    refreshing = false;
  }
}

$("mass-form").addEventListener("submit", (event) => {
  event.preventDefault();
  clearTimeout(sliderTimer);
  $("mass-slider").value = $("mass").value;
  simulation({mass_g: Number($("mass").value)});
});
$("mass-slider").addEventListener("input", (event) => {
  $("mass").value = event.target.value;
  clearTimeout(sliderTimer);
  sliderTimer = setTimeout(() => simulation({mass_g: Number(event.target.value)}), 180);
});
document.querySelectorAll("[data-mass]").forEach((button) => button.addEventListener("click", () => {
  clearTimeout(sliderTimer);
  const mass = Number(button.dataset.mass);
  $("mass").value = $("mass-slider").value = mass;
  simulation({mass_g: mass});
}));
$("fault").addEventListener("change", (event) => simulation({fault: event.target.checked}));
$("pause").addEventListener("click", () => current && simulation({connected: !current.simulation.connected}));
$("tare-button").addEventListener("click", () => sendCommand("tare"));
$("reset-tare-button").addEventListener("click", () => sendCommand("reset_tare"));
$("material-select").addEventListener("change", (event) => {
  chosenMaterial = event.target.value;
  if (current) renderMaterials(current.materials, current.workflow);
  controls();
});
$("prepare-button").addEventListener("click", () => sendCommand("prepare", undefined, {material_id: $("material-select").value}));
$("start-button").addEventListener("click", () => sendCommand("start_monitoring"));
$("history-material").addEventListener("change", async (event) => { historyMaterial = event.target.value; await refresh(); });
$("material-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  controls();
  try {
    const result = await request("/api/materials", {name: $("material-name").value});
    chosenMaterial = result.material.id;
    $("material-name").value = "";
    document.querySelector(".new-material").open = false;
    feedback(result.message);
    await refresh();
  } catch (error) {
    feedback(error.message, true);
  } finally {
    busy = false;
    controls();
  }
});
for (const [form, input, command] of [
  ["unit-form", "unit", "unit"], ["minimum-form", "minimum", "minimum"],
  ["sample-form", "sample-count", "sample_unit"], ["calibration-form", "reference", "calibrate"],
]) {
  $(form).addEventListener("submit", (event) => { event.preventDefault(); sendCommand(command, Number($(input).value)); });
}
for (const id of ["unit", "minimum"]) $(id).addEventListener("input", () => { $(id).dataset.dirty = "true"; });
window.addEventListener("themechange", () => current && drawChart(current.readings, current.telemetry?.settings.minimum || 0));
window.addEventListener("resize", () => current && drawChart(current.readings, current.telemetry?.settings.minimum || 0));
(async function poll() {
  await refresh();
  setTimeout(poll, 500);
})();
