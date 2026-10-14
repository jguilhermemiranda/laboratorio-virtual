# G.U.Y.

Ecossistema local de controle de acesso e desenvolvimento remoto.

| Módulo | Arquivo | Onde roda | Função |
|---|---|---|---|
| **GUY ACCESS** | `app.py` | PC Windows | Pessoas, cartões RFID, digitais, auditoria. **Decide** o acesso a partir dos eventos do ESP32 (serial USB). |
| **Reconhecimento facial** | `guy_face.py` | PC Windows | Identifica rostos pela webcam. **Só identifica; não libera acesso.** |
| **GUYLAB cliente** | `guylab_client.py` | PC Windows | Editor + terminal remoto. |
| **GUYLAB servidor** | `server.py` | Raspberry Pi (ou PC, para teste) | Contas, workspaces, execução de comandos. |
| **Firmware ESP32** | `firmware/guy_access_esp32/` | ESP32 | Leitor RFID + sensor biométrico; obedece `ACCESS_DECISION` do PC. |
| **Launcher** | `main.py` + `guy_launcher.py` | PC | Instala o que falta e abre tudo. |
| **Comum** | `guy_common.py` | todos | Caminhos, paleta, marca, configurações. |

## Uso rápido (Windows)

Dê dois cliques em **`Iniciar GUY.bat`** (ou `python main.py`).

Na primeira vez ele cria `.venv`, instala `requirements.txt` e baixa os modelos faciais (precisa de internet). Nas seguintes só verifica e abre o launcher.

```
python main.py                      launcher
python main.py check                diagnóstico sem janelas
python main.py run access|face|lab  abre um módulo direto
python main.py --reinstall          força reinstalar dependências
```

## Raspberry Pi (servidor GUYLAB)

Não precisa de pip nem de venv (só biblioteca padrão):

```
python3 main.py server --create-admin     # cria o 1º administrador (interativo)
python3 main.py server --host 0.0.0.0     # escuta na rede (padrão do server.py)
```

No PC, o launcher também sobe um servidor **só local** (127.0.0.1) para testes.

## Como os módulos conversam

- **GUY ACCESS é a fonte das identidades.** O módulo facial lê (somente leitura) a tabela `people` e só associa um rosto a uma pessoa existente. Em *Pessoas → Cadastrar rosto* o cadastro já abre para a pessoa selecionada.
- Cada módulo roda em **processo separado**: webcam travada não derruba o controle de acesso. A saída de cada um fica em `logs/<modulo>.log`.
- Todos os caminhos vêm de `guy_common.py` e são relativos à pasta do projeto (nunca ao diretório de onde você abriu o programa).
- IP/porta do GUYLAB e a última COM usada ficam em `guy_settings.json` (nunca senhas).

## Dados locais (não versionar!)

`guy_access.db`, `guy_face.db`, `guy_identity.db` (hashes de senha), `workspaces/`, `logs/`, `models/`, `guy_settings.json`. Tudo está no `.gitignore`. Se esses `.db` já foram commitados: `git rm --cached *.db`. **Faça backup copiando esses arquivos.**

## Hardware (comprovado pelo código do firmware, sem alterações)

RFID MFRC522: SS=5, RST=22, SPI SCK=18 / MISO=19 / MOSI=23. Sensor biométrico: UART2, RX=16, TX=17, 57600 baud. Relé: GPIO 26, **desabilitado** (`ENABLE_RELAY=false`). Serial com o PC: 115200 baud, JSON por linha.
Bibliotecas: MFRC522, Adafruit Fingerprint Sensor, ArduinoJson (o código usa `StaticJsonDocument`).

## Testes

`python -m unittest discover -s tests -v` — protocolo do servidor, bancos, pareamento cartão+digital, roteamento do `main.py`. Janelas, webcam, serial e firmware só se validam na máquina real.

## Segurança — leia antes de expor na rede

- A conexão do GUYLAB **não é criptografada**: a senha trafega em texto puro.
- Conta com permissão `execute` roda **qualquer comando** como o usuário que executa o servidor; o isolamento por workspace não vale para ela.
- Não exponha a porta 8765 fora de rede confiável.

## Solução de problemas

**"Can't read ONNX file" / modelos faciais não carregam** — rode `python main.py check` e veja a linha *Modelos faciais*: ela mostra o erro real do OpenCV.
- Caminho do projeto com acento (ex.: `autenticação`): o OpenCV no Windows não abre o arquivo. O G.U.Y. detecta isso e guarda os modelos em `%LOCALAPPDATA%\GUY\models` (a linha *Pasta de modelos* mostra onde).
- Modelo corrompido (página de erro salva no lugar do `.onnx`): apague a pasta de modelos e rode `main.py` de novo; ele baixa outra vez.
- OpenCV incompatível: `python main.py --reinstall`.
