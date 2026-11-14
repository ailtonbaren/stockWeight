# stockWeight

Controle de estoque por pesagem para pequenos almoxarifados, oficinas e laboratórios. O objetivo é reduzir contagens manuais e antecipar a reposição de materiais padronizados, começando com parafusos.

O dashboard tem dois modos: **simulador local**, sem broker, e **MQTT**, para receber leituras e enviar comandos ao ESP32 no Wokwi. O usuário confirmou o funcionamento da integração completa usando a internet compartilhada do celular, após dificuldades de conexão na rede da faculdade. O painel também calcula a autonomia do estoque e oferece um histórico fictício separado para demonstrar essa previsão.

## Contar por amostra e monitorar um material

1. Selecione um material, ou use **Cadastrar material** para criar, por exemplo, “Prego” ou “Parafuso M4”. Clique em **Preparar contagem**. Ao trocar o material, aguarde a confirmação antes de substituir o conteúdo do recipiente.
2. Coloque somente o recipiente vazio, aguarde estabilizar e clique em **Tarar balança**.
3. Coloque uma amostra conhecida, por exemplo **10 peças**, e aguarde estabilizar. Em **Aprender peso por amostra**, informe 10 e clique em **Aprender peso por peça**. Também é possível informar a massa unitária manualmente.
4. Coloque o lote completo que deseja contar. O painel mostra a estimativa; durante a preparação, as variações de peso não geram retiradas/reposições e o LED de estoque fica desligado.
5. Confira o lote e clique em **Iniciar monitoramento**. A quantidade atual vira um único **Estoque inicial**. Só as mudanças estáveis seguintes geram movimentações e alerta.

Cada material salva sua massa por peça e estoque mínimo no SQLite. A tara é refeita ao preparar outra contagem. Use o filtro **Material** no histórico para consultar um item ou todos os materiais; isso não muda o material da leitura atual. O recipiente contém um tipo por vez, escolhido manualmente no dashboard.

Exemplo nominal: recipiente de 100 g, amostra com massa total de 150 g e 10 peças → cerca de 5 g por peça. Um lote com massa total de 500 g → cerca de 80 peças. Pequenas diferenças de massa decorrem da quantização do ADC simulado.

O banco antigo é migrado automaticamente, preservando seus registros de parafusos. Ao reiniciar o simulador local ou o ESP32, o dispositivo entra em preparação; perfis e históricos continuam no notebook. Reiniciar somente o servidor MQTT pode retomar uma contagem conhecida se o ESP32 continuar na mesma sessão.

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

Para corrigir uma tara aplicada com peso errado, clique em **Resetar tara**, ao lado de **Tarar balança**. O reset remove o desconto mesmo quando a leitura está abaixo da tara anterior e o recipiente aparece como retirado. Ele volta à preparação, preservando calibração, massa unitária e mínimo, sem criar movimentações falsas. Aguarde estabilizar, tare o recipiente vazio e confira o lote antes de iniciar novamente. O botão exige conexão com o dispositivo, mas não exige leitura estável. Gere o firmware com `python3 scripts/prepare_wokwi.py --mqtt` e atualize `sketch.ino` no Wokwi para usar essas funções.

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

1. Selecione o material e prepare a contagem. Com a massa estável, tare o recipiente vazio **pelo dashboard**, aprenda ou informe a massa unitária e observe as confirmações.
2. Aumente o potenciômetro até o lote completo, confira a estimativa e clique em **Iniciar monitoramento**. Verifique o estoque inicial e o LED.
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

1. Selecione o material e clique em **Preparar contagem**, se necessário. Clique em **100 g**, aguarde estabilizar e clique em **Tarar balança**.
2. Salve massa unitária **5 g** e estoque mínimo **50 peças**.
3. Clique em **1.100 g**. A estimativa será de **200 peças**, ainda sem registrar eventos. Clique em **Iniciar monitoramento** para registrar o estoque inicial.
4. Clique em **275 g**. A estimativa será de **35 peças**, com LED e alerta de reposição. O histórico registra retirada de **165 peças**.
5. Clique em **1.200 g**. A estimativa será de **220 peças**, com reposição de **185 peças** e alerta desligado.
6. Altere o estoque mínimo para **220**: o alerta deve acender também quando a quantidade for exatamente igual ao mínimo.

Os exemplos pressupõem tara próxima de 100 g e escala nominal. Os valores podem diferir ligeiramente devido à quantização simulada do ADC. Se calibrar com uma referência incompatível com a massa de teste, a relação entre massa de entrada e leitura também muda.

### Descobrir a massa de um parafuso por amostra

1. Deixe o recipiente vazio a 100 g e tare a balança.
2. Altere a massa total para **150 g**, representando o recipiente mais **10 parafusos** de aproximadamente 5 g.
3. Aguarde estabilizar; em **Aprender peso por amostra**, informe **10** e clique em **Aprender peso por peça**.
4. O núcleo calcula `massa_líquida / quantidade_da_amostra`; o perfil salva o valor confirmado. Coloque o lote completo e só então inicie o monitoramento. A preparação não gera consumo.

Usar mais peças na amostra reduz o impacto da resolução da leitura. A estimativa pressupõe peças de massas semelhantes.

### Calibração simulada

Durante a preparação, tare o recipiente vazio a 100 g. Coloque uma massa conhecida de 500 g, ajustando a massa total para 600 g. Aguarde estabilizar, abra **Calibração com peso conhecido**, informe 500 g e confirme.

O firmware calcula `gramas_por_unidade_bruta = massa_de_referência / (leitura_bruta_atual - leitura_bruta_da_tara)`. A tara permanece armazenada em unidades brutas e é convertida com o mesmo fator; a aplicação não desconta a tara novamente. A simulação parte de um zero ideal e escala nominal de 0–5.000 g. A montagem física precisará de procedimento próprio para zero, fator e capacidade do sensor.

### Falhas e manipulação

- **Simular falha do sensor:** publica leituras inválidas e suspende a contagem de movimentos até recuperar uma janela estável.
- **Pausar conexão:** interrompe a telemetria e bloqueia comandos; **Reconectar simulador** retoma a leitura.
- **Massa abaixo da tara:** mostra recipiente ausente e preserva o último estoque confirmado. Ao recolocar o recipiente, registra apenas a diferença líquida entre estados estáveis. É uma proteção inicial; a regra definitiva para retirada do recipiente ainda será acordada.
- **Alterações rápidas de massa:** só produzem eventos após uma janela estável de dez amostras. Uma pausa longa com massa estável pode ser interpretada como uma movimentação concluída.

## Autonomia do estoque

A previsão considera uma janela móvel dos **últimos 7 dias corridos** (7 × 24 horas, incluindo fins de semana) e exige pelo menos **24 horas de acompanhamento confiável** dentro dessa janela. As horas acompanhadas podem ser acumuladas em vários intervalos; períodos sem comunicação ou com leitura inválida são excluídos, sem serem tratados como consumo zero. Horas acompanhadas sem retiradas entram no cálculo normalmente.

```text
consumo médio diário = peças retiradas / (horas acompanhadas / 24)
autonomia em dias = quantidade atual / consumo médio diário
```

Somente eventos de **retirada** entram no consumo. Reposições aumentam o estoque atual; estoque inicial, tara, calibração e ajustes não contam como retiradas. A previsão pressupõe que o consumo médio observado se mantenha; não é uma garantia de duração do estoque.

- **Dados insuficientes:** ainda não há 24 horas acompanhadas na referência atual, dentro dos últimos 7 dias.
- **Sem consumo registrado:** já há tempo suficiente, mas nenhuma retirada; não se apresenta autonomia infinita.
- **Aguardando leitura válida:** previsão suspensa durante desconexão, falha, instabilidade ou ausência do recipiente. Os intervalos já acompanhados permanecem salvos.

Uma nova referência começa quando o dispositivo reinicia, muda a tara, a massa unitária ou a calibração, registra um ajuste, ou quando é detectada perda de eventos. Alterar apenas o estoque mínimo preserva a referência. Reiniciar somente o servidor preserva os intervalos conhecidos do mesmo dispositivo e sessão, mas exclui o período desligado e a movimentação recebida na recuperação. Retiradas cuja atribuição ao período acompanhado não seja confiável ficam fora da média. O histórico de movimentações continua preservado mesmo quando a referência da previsão muda.

A contagem começa a partir das leituras recebidas por esta versão; o histórico antigo não permite reconstruir períodos de acompanhamento confiável. Os registros para a previsão ficam no SQLite, separados da lista de até 100 eventos exibida no painel. O cálculo roda no Python; esta funcionalidade não exige atualizar o firmware no Wokwi.

### Exemplo para demonstrar a previsão

Abra **Histórico de exemplo · demonstração da autonomia** no dashboard. Essa seção usa o arquivo `examples/autonomy-history.json` e a mesma função de cálculo do estoque monitorado. Seus dados são fictícios, não alteram o banco, não enviam comandos e não substituem os indicadores do dispositivo.

O exemplo começa com 160 peças. Ao longo de 3 dias acompanhados, ocorrem retiradas de 20, 30 e 10 peças e uma reposição de 100 peças. O estoque final é 200 peças; o consumo médio é `60 / 3 = 20 peças/dia` e a autonomia é `200 / 20 = 10 dias`. Assim é possível demonstrar a previsão sem aguardar dias reais nem acelerar o relógio da aplicação.

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
prepare demo-preparacao-1
tare demo-tara-1
reset_tare demo-reset-tara-1
unit demo-unidade-1 5
minimum demo-minimo-1 50
calibrate demo-calibracao-1 500
sample_unit demo-amostra-1 10
start_monitoring demo-inicio-1
```

Cada comando deve ter um identificador diferente. `calibrate` exige uma massa de referência colocada após tarar; `sample_unit` exige uma amostra conhecida após tarar. A resposta JSON tem `type: "ack"`, `command_id`, `ok` e `message`. O monitor também recebe telemetria JSON a cada 500 ms. Os oito comandos mais recentes são deduplicados no núcleo por identificador.

`reset_tare` remove a tara sem exigir estabilidade; use para recuperar uma tara incorreta e depois execute uma nova tara com o recipiente vazio. Os exemplos acima têm pré-condições diferentes e não formam uma sequência automática.

`prepare` inicia a preparação e remove a tara anterior; `start_monitoring` registra o estoque inicial após tara e estabilização. No modo conectado ao dashboard, use os botões do painel para preparar/iniciar: eles associam a contagem ao material e verificam a preparação de destino. Os exemplos de monitor serial são úteis para teste direto do dispositivo, sem cadastro de material no banco.

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
app/autonomy.py        períodos acompanhados e previsão de autonomia
app/materials.py       perfis, identificação de contagens e migração do histórico
app/static/            dashboard HTML/CSS/JS, sem CDN
examples/              histórico fictício isolado para demonstrar a previsão
scripts/               compilação, preparação do Wokwi e diagnóstico MQTT
tests/                 testes do núcleo, persistência e contrato HTTP
docs/                  plano original e decisões da arquitetura
```

## Verificação

```bash
python3 -m unittest discover -v
```

Os testes usam bancos temporários e cobrem estabilização, limite de alerta, retiradas e reposições, ruído no limite de arredondamento, tara/calibração, amostra conhecida, falhas, recipiente ausente, comandos duplicados e persistência após reiniciar. O contrato HTTP é exercitado com o parser real e streams em memória, sem abrir portas. Os testes MQTT usam o adaptador da aplicação, um cliente em memória e o núcleo C++ real para verificar envio de comandos, confirmação assíncrona, timeout, sessão, deduplicação, mensagens fora de ordem, dados retidos, erro de publicação e separação dos históricos. Não comprovam disponibilidade de broker público ou funcionamento da biblioteca Paho em uma conexão real.

Os testes de autonomia usam relógio controlado para verificar 24 horas mínimas, janela de 7 dias, dias sem consumo, interrupções, persistência, deduplicação e mudanças de referência. O contrato HTTP verifica também que consultar o exemplo não altera nenhuma tabela do banco nem o estado monitorado.

O núcleo compila com `-Wall -Wextra -Werror`. A suíte atual passou em **75 testes**, incluindo preparação sem histórico/LED, amostra de 10 e lote de 80, perfis e filtros, migração preservando registros, retomada MQTT e comandos atrasados. O fluxo pelos botões foi conferido em Firefox com dois materiais, retirada após iniciar e reset de tara durante monitoramento; o layout foi inspecionado em desktop e celular, com os dois temas e sem transbordamento horizontal no celular.

O usuário validou anteriormente a integração MQTT real com internet do celular. Os testes desta alteração usam o transporte MQTT em memória e não substituem uma nova execução no Wokwi; copiar o sketch atualizado antes de conferir a integração. A previsão é testada com relógio controlado, sem aguardar 24 horas reais. Ainda não houve teste físico com HX711.
