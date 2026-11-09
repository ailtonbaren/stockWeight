# StockWeight

Controle de estoque por pesagem para pequenos almoxarifados, oficinas e laboratórios. O objetivo é reduzir contagens manuais e antecipar a reposição de materiais padronizados, começando com parafusos.

O dashboard tem dois modos: **simulador local**, sem broker, e **MQTT**, para receber leituras e enviar comandos ao ESP32 no Wokwi. A integração MQTT está implementada e testada com um cliente em memória; a conexão real com o broker e a execução do novo firmware MQTT no Wokwi ainda precisam ser verificadas. O usuário já confirmou o funcionamento da versão offline no Wokwi, incluindo leitura e tara, e do dashboard local.

## Conectar Wokwi e dashboard por MQTT

A aplicação continua no notebook. Não é necessário instalar um broker Mosquitto local. O ESP32 e o dashboard conectam ao broker na internet.

### 1. Preparar o notebook e o firmware

Pare o servidor anterior com `Ctrl+C`. Na pasta do projeto:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-mqtt.txt
python3 scripts/prepare_wokwi.py --mqtt
```

A dependência adicional do backend é **paho-mqtt 2.1.0**. O modo local continua funcionando sem ela. O preparador cria `data/mqtt.json` com broker `test.mosquitto.org:1883`, identificação do dispositivo e um prefixo aleatório próprio deste projeto. Esse arquivo é a referência compartilhada pelo dashboard e pelo firmware gerado. Ele não contém senhas nem tokens de autenticação.

### 2. Atualizar o projeto no Wokwi

No projeto **stockWeight** já criado, pare a simulação e substitua os conteúdos:

| Aba no Wokwi | Arquivo local |
| --- | --- |
| `sketch.ino` | `build/wokwi/sketch.ino` |
| `diagram.json` | `build/wokwi/diagram.json` |
| `libraries.txt` | `build/wokwi/libraries.txt` |

Crie a aba `libraries.txt` se ela não existir. Seu conteúdo é `PubSubClient@2.8`. Também é possível adicionar PubSubClient pelo Library Manager e selecionar essa versão. O Wi-Fi da simulação é `Wokwi-GUEST`, sem senha; não coloque as credenciais reais do seu roteador no projeto público. A documentação explica [as bibliotecas do Wokwi](https://docs.wokwi.com/guides/libraries) e [o Wi-Fi simulado](https://docs.wokwi.com/guides/esp32-wifi).

### 3. Iniciar o sistema

No notebook:

```bash
.venv/bin/python -m app.server --mode mqtt
```

Abra **http://localhost:8765**, inicie o Wokwi e aguarde **ESP32 conectado** no painel. **Broker conectado** indica somente que o notebook assinou os tópicos; uma leitura recente do ESP32 também é necessária para habilitar os comandos. O monitor serial imprime `MQTT conectado. Aguardando comandos do dashboard.` quando o ESP32 consegue conectar e enviar sua assinatura.

No modo MQTT, altere a massa **pelo potenciômetro do Wokwi**. Os controles de massa do simulador local ficam ocultos. Faça tara, configure massa unitária e mínimo pelos botões do dashboard. Sucesso só aparece após um `ack` correspondente emitido pelo ESP32. O prazo de espera é de oito segundos. Se expirar, o painel informa que o comando pode ter sido executado; confira a leitura antes de tentar de novo. O servidor não repete tara automaticamente.

O histórico MQTT fica em **`data/stockweight-mqtt.sqlite3`**, separado do banco dos testes locais. O último estado confirmado do dispositivo é armazenado, mas não é reaplicado automaticamente quando o ESP32 reinicia. Nessa situação, faça a tara e confira os parâmetros novamente.

### 4. Se o broker não conectar

Teste no terminal do próprio notebook, sem depender de pip:

```bash
python3 scripts/check_mqtt.py
```

O teste verifica DNS, conexões IPv4/IPv6 e a confirmação MQTT. Uma página web não abrir não prova que a porta MQTT esteja fora do ar. Se o Mosquitto continuar inacessível, teste o broker público alternativo:

```bash
python3 scripts/check_mqtt.py --broker broker.emqx.io
```

Se o alternativo responder `MQTT OK`, prepare e salve a troca:

```bash
python3 scripts/prepare_wokwi.py --mqtt --broker broker.emqx.io
```

Copie o novo `sketch.ino` para o Wokwi e reinicie **a simulação e o servidor Python**. Ambos precisam usar o mesmo broker e prefixo; não há troca automática silenciosa de broker. O endpoint alternativo está na [documentação do EMQX](https://www.emqx.com/en/mqtt/public-mqtt5-broker).

Os dois brokers são públicos de teste, sem garantia de disponibilidade e sem privacidade dos tópicos. Envie apenas dados de demonstração. O transporte desta etapa usa TCP sem TLS na porta 1883, sem credenciais. Os parâmetros de rede e tópicos ficam em `data/mqtt.json`; não coloque senhas ou tokens reais no repositório ou no Wokwi público.

Se houver várias movimentações enquanto o sistema estiver desconectado, o histórico pode ficar incompleto. A telemetria contém o último evento, não uma fila durável de todos os movimentos. O dashboard mostra um aviso quando detecta salto na sequência de eventos. O alerta local e o monitor serial continuam operando durante as tentativas de reconexão.

### Cenários da integração

1. Com a massa estável, tare o recipiente vazio **pelo dashboard** e observe a confirmação no painel e no monitor serial.
2. Aumente o potenciômetro até ficar acima do estoque mínimo; confira quantidade e LED.
3. Reduza até atingir ou ficar abaixo do mínimo; confira o LED e o alerta no dashboard após estabilização.
4. Reponha massa; confira que o alerta se apaga e o histórico registra reposição.
5. Altere o mínimo pelo dashboard e confira a reação do LED no Wokwi e a configuração retornada na telemetria.
6. Pare o Wokwi: o dashboard deve ficar desconectado após cerca de cinco segundos sem telemetria, ou antes ao receber aviso de indisponibilidade. Inicie novamente e confira a nova sessão. O modo MQTT não muda para dados locais quando a conexão falha.

## Executar o simulador local sem MQTT

Requisitos: Python **3.10 ou superior**, `g++` com C++11 e um navegador. Não é necessário instalar pacotes com pip ou npm, baixar Mosquitto ou ter internet para o dashboard local.

Na pasta do projeto:

```bash
python3 -m app.server
```

Abra **http://localhost:8765**. O primeiro início compila automaticamente o simulador em `build/`. Use `Ctrl+C` para encerrar. Se a porta estiver ocupada:

```bash
python3 -m app.server --port 8766
```

O histórico, os parâmetros e a última massa de teste ficam em `data/stockweight.sqlite3`. Ao reiniciar, uma nova sessão de medição começa; a configuração e o histórico anterior são preservados. Uma diferença ocorrida enquanto o programa estava desligado não é apresentada como consumo medido.

Para um ensaio com banco separado, preservando os dados anteriores:

```bash
python3 -m app.server --database /tmp/stockweight-ensaio.sqlite3
```

O servidor escuta somente no próprio notebook (`127.0.0.1`). Nesta etapa não há acesso de outros computadores.

## Primeiro teste pelo dashboard

1. Clique em **100 g — recipiente vazio**. Aguarde a leitura estável e clique em **Tarar balança**. Confirme que a massa líquida fica próxima de zero.
2. Use massa unitária **5 g** e estoque mínimo **50 peças**.
3. Clique em **1.100 g — exemplo normal**. Após aproximadamente um segundo, a estimativa será de **200 peças**, sem alerta.
4. Clique em **275 g — exemplo baixo**. A estimativa será de **35 peças**; o indicador de LED acende e aparece o alerta de reposição. O histórico registra uma retirada de **165 peças**.
5. Clique em **1.200 g — exemplo reposição**. A estimativa será de **220 peças**; o alerta se apaga e o histórico registra uma reposição de **185 peças**.
6. Altere o estoque mínimo para **220**: o alerta deve acender também quando a quantidade for exatamente igual ao mínimo.

Os exemplos pressupõem tara próxima de 100 g e escala nominal. Os valores podem diferir ligeiramente devido à quantização simulada do ADC. Se calibrar com uma referência incompatível com a massa de teste, a relação entre massa de entrada e leitura também muda.

### Descobrir a massa de um parafuso por amostra

1. Deixe o recipiente vazio a 100 g e tare a balança.
2. Altere a massa total para **150 g**, representando o recipiente mais **10 parafusos** de aproximadamente 5 g.
3. Aguarde estabilizar, abra **Descobrir a massa por uma amostra**, informe **10** e clique em **Calcular e aplicar**.
4. O núcleo calcula `massa_líquida / quantidade_da_amostra` e aplica o resultado como massa unitária. A alteração é um ajuste de medição, não consumo.

Usar mais peças na amostra reduz o impacto da resolução da leitura. A estimativa pressupõe peças de massas semelhantes.

### Calibração simulada

Tare o recipiente vazio a 100 g. Coloque uma massa conhecida de 500 g, ajustando a massa total para 600 g. Aguarde estabilizar, abra **Calibrar com uma massa conhecida**, informe 500 g e confirme.

O firmware calcula `gramas_por_unidade_bruta = massa_de_referência / (leitura_bruta_atual - leitura_bruta_da_tara)`. A tara permanece armazenada em unidades brutas e é convertida com o mesmo fator; a aplicação não desconta a tara novamente. A simulação parte de um zero ideal e escala nominal de 0–5.000 g. A montagem física precisará de procedimento próprio para zero, fator e capacidade do sensor.

### Falhas e manipulação

- **Simular falha do sensor:** publica leituras inválidas e suspende a contagem de movimentos até recuperar uma janela estável.
- **Pausar conexão:** interrompe a telemetria e bloqueia comandos; **Reconectar simulador** retoma a leitura.
- **Massa abaixo da tara:** mostra recipiente ausente e preserva o último estoque confirmado. Ao recolocar o recipiente, registra apenas a diferença líquida entre estados estáveis. É uma proteção inicial; a regra definitiva para retirada do recipiente ainda será acordada.
- **Alterações rápidas de massa:** só produzem eventos após uma janela estável de dez amostras. Uma pausa longa com massa estável pode ser interpretada como uma movimentação concluída.

## ESP32 no Wokwi, sem MQTT

Prepare uma versão com apenas dois arquivos para facilitar a cópia:

```bash
python3 scripts/prepare_wokwi.py
```

1. No [Wokwi](https://wokwi.com/), crie um projeto ESP32 Arduino.
2. Substitua todo o conteúdo da aba `sketch.ino` por `build/wokwi/sketch.ino`.
3. Substitua todo o conteúdo de `diagram.json` por `build/wokwi/diagram.json`.
4. Salve o projeto como **StockWeight**. Não são necessárias bibliotecas adicionais nesta etapa.
5. Inicie a simulação e acompanhe o monitor serial a **115200 baud**. Mova o potenciômetro para variar a massa. O botão vermelho simula uma leitura inválida enquanto estiver pressionado.

Os arquivos em `build/wokwi/` são gerados. Faça alterações nos arquivos originais em `firmware/stockweight/` e execute novamente o preparador antes de copiar ao Wokwi. Como alternativa, você pode copiar o `.ino` original para `sketch.ino`, criar abas para os dois cabeçalhos `.h` e usar o `diagram.json` original.

| Componente | Ligação |
| --- | --- |
| Potenciômetro VCC / GND | 3V3 / GND |
| Potenciômetro SIG | GPIO34, ADC1 |
| LED vermelho | GPIO26 → resistor de 220 Ω → ânodo; cátodo → GND |
| Botão de falha | GPIO27 → botão → GND; pull-up interno |

O potenciômetro representa uma massa nominal de até 5 kg. Não mede parafusos reais. A documentação oficial descreve a [simulação do ESP32](https://docs.wokwi.com/guides/esp32) e o [potenciômetro](https://docs.wokwi.com/parts/wokwi-potentiometer).

Envie estes comandos no monitor serial, **um por linha**, terminando com Enter:

```text
tare demo-tara-1
unit demo-unidade-1 5
minimum demo-minimo-1 50
calibrate demo-calibracao-1 500
sample_unit demo-amostra-1 10
```

Cada comando deve ter um identificador diferente. `calibrate` exige uma massa de referência colocada após tarar; `sample_unit` exige uma amostra conhecida após tarar. A resposta JSON tem `type: "ack"`, `command_id`, `ok` e `message`. O monitor também recebe telemetria JSON a cada 500 ms. Os oito comandos mais recentes são deduplicados no núcleo por identificador.

Os parâmetros do ESP32 ficam em RAM e voltam ao padrão ao reiniciar a simulação. A persistência em NVS será acrescentada em outra etapa. No modo offline, salvar parâmetros no SQLite do dashboard não configura o ESP32, pois são instâncias separadas.

## Estrutura

```text
firmware/stockweight/
  stockweight.ino       ESP32: amostragem, LED e comandos
  stock_engine.h       regras compartilhadas, calibração e comandos
  mass_source.h        interface de leitura e adaptador do potenciômetro
  mqtt_transport.h     tarefa de rede Wi-Fi/MQTT e filas entre tarefas
  diagram.json         circuito para o Wokwi
simulator/main.cpp     adaptação nativa do mesmo núcleo C++
app/server.py          API local, processo simulado e SQLite
app/mqtt_transport.py  cliente Paho, assinaturas e validação da telemetria
app/mqtt_config.py     configuração compartilhada do broker e tópicos
app/static/            dashboard HTML/CSS/JS, sem CDN
scripts/               compilação, preparação do Wokwi e diagnóstico MQTT
tests/                 testes do núcleo, persistência e contrato HTTP
docs/                  plano original e decisões da arquitetura
```

## Verificação

```bash
python3 -m unittest discover -v
```

Os testes usam bancos temporários e cobrem estabilização, limite de alerta, retiradas e reposições, ruído no limite de arredondamento, tara/calibração, amostra conhecida, falhas, recipiente ausente, comandos duplicados e persistência após reiniciar. O contrato HTTP é exercitado com o parser real e streams em memória, sem abrir portas. Os testes MQTT usam o adaptador da aplicação, um cliente em memória e o núcleo C++ real para verificar envio de comandos, confirmação assíncrona, timeout, sessão, deduplicação, mensagens fora de ordem, dados retidos, erro de publicação e separação dos históricos. Não comprovam disponibilidade de broker público ou funcionamento da biblioteca Paho em uma conexão real.

O núcleo compila com `-Wall -Wextra -Werror`. O usuário validou a execução da versão offline no Wokwi, incluindo leitura e tara, e o histórico do dashboard local. **A nova versão com Wi-Fi/MQTT precisa de compilação e execução no Wokwi e de conexão real com o backend.** O ambiente do agente bloqueia sockets e não conseguiu instalar Paho; por isso não foi possível fazer esse teste de rede nem inspeção visual da nova tela. Não houve teste físico com HX711.

## Próximas etapas

1. Validar a conexão MQTT implementada com um broker acessível no notebook e no Wokwi.
2. Executar os cenários de tara, configuração, retirada, reposição e perda de conexão usando o dashboard e o ESP32 conectados.
3. Avaliar persistência de parâmetros no ESP32 e uma fila durável de eventos durante desconexões.
4. Definir regras de observação do consumo e calcular autonomia. Hoje, ela permanece como **Sem consumo registrado** ou **Dados insuficientes**, sem gerar previsão artificial.
5. Validar todos os cenários da entrega de **16/11** com a comunicação real.
6. Preparar relatório e orientação escrita para o pitch por último, conforme combinado.
7. Na etapa física, implementar e testar o adaptador HX711, a persistência do ESP32 e a calibração real.

As decisões estão em [docs/architecture.md](docs/architecture.md). O [plano original](docs/stockWeight.md) permanece preservado.
