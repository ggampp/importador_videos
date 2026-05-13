# Importador automático de vídeos do YouTube para o LingQ

Automatiza a importação diária dos vídeos mais recentes dos seus canais
favoritos do YouTube (organizados por idioma) para a plataforma
[LingQ](https://www.lingq.com), eliminando a etapa manual de abrir cada vídeo
no Edge e clicar na extensão.

## Como funciona

1. `yt-dlp` consulta cada canal cadastrado em `config.json` e devolve a lista
   dos vídeos mais recentes (sem precisar de API key do YouTube).
2. Vídeos já importados em execuções anteriores são filtrados via
   `history.json`.
3. `Playwright` abre o Microsoft Edge usando um **perfil dedicado** (isolado
   do seu perfil pessoal). Nesse perfil ficam guardados: sua sessão logada
   no LingQ + a extensão LingQ Importer já instalada.
4. Para cada vídeo novo, o script abre a página no YouTube e clica no botão
   "Import to LingQ" injetado pela extensão.
5. Tudo é logado em `logs/import_AAAA-MM-DD.log` e o histórico é gravado a
   cada vídeo (à prova de queda no meio do processo).

## Setup inicial (faça uma única vez)

### Pré-requisitos
- Windows 10/11
- Python 3.10 ou superior — verifique com `python --version` no PowerShell.
  Se não tiver, instale por [python.org](https://www.python.org/downloads/)
  marcando a opção "Add Python to PATH".
- Microsoft Edge instalado.

### Passo a passo
Abra o PowerShell na pasta do projeto e execute:

```powershell
.\setup.bat
```

Esse script vai:
1. Instalar `playwright` e `yt-dlp` via pip.
2. Instalar o canal Edge no Playwright.
3. Abrir uma janela do Edge isolada para você:
   - **Instalar a extensão LingQ Importer** — vá na Chrome Web Store
     (`https://chromewebstore.google.com/`), pesquise "LingQ Importer" e
     clique em "Add to Chrome" / "Add to Edge".
   - **Fazer login em lingq.com** com sua conta normal.
   - Quando terminar, feche o browser. A sessão fica salva no perfil isolado.

## Uso diário

### Manual
Dê duplo-clique em `run.bat` (ou execute `python import_videos.py` no
PowerShell).

### Automático (recomendado)
Já está configurado como uma **tarefa agendada do Cowork** rodando todo dia
às 07:00 — você não precisa fazer nada, basta deixar o computador ligado.

## Configuração

Tudo é editável em `config.json`:

| Campo | O que faz |
|---|---|
| `videos_por_canal` | Quantos vídeos mais recentes pegar por canal (1, 3, 5…) |
| `headless` | `true` para rodar sem janela visível (mais rápido) |
| `timeout_extensao_segundos` | Quanto tempo esperar a extensão LingQ aparecer |
| `delay_entre_videos_segundos` | Pausa entre importações |
| `youtube_cookies_file` | Arquivo `cookies.txt` do YouTube para o `yt-dlp` usar em VPS quando o YouTube pedir login |
| `transcrever_sem_legenda` | Tenta gerar uma legenda VTT por IA quando o YouTube não fornecer legenda |
| `transcricao_modelo` | Modelo da API OpenAI usado no fallback de transcrição (padrão: `whisper-1`) |
| `canais` | Lista de canais com idioma e URL — adicione/remova à vontade |

### Adicionar um novo canal
Edite o array `canais` no `config.json`:
```json
{ "idioma": "alemao", "lang_code": "de", "url": "https://www.youtube.com/@SeuCanal", "nome": "Seu Canal" }
```

## Estrutura de arquivos

```
Importar vídeos na plataforma Lingq.com/
├── config.json              ← configuração editável (canais, opções)
├── import_videos.py         ← script principal (execução diária)
├── setup_profile.py         ← setup interativo único do perfil Edge
├── requirements.txt         ← dependências Python
├── setup.bat                ← instala tudo (Windows)
├── run.bat                  ← roda manualmente uma importação
├── history.json             ← gerado automaticamente (vídeos já importados)
├── edge_profile/            ← gerado automaticamente (perfil dedicado do Edge)
├── logs/                    ← gerado automaticamente (logs diários)
└── README.md
```

## Troubleshooting

### "Edge não abre" / "executable not found"
- Confirme o caminho em `config.json` → `edge_executable`. Normalmente é
  `C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`. Se o seu
  Edge estiver em outro lugar, ajuste.

### "Botão da extensão LingQ não encontrado"
- A extensão pode estar desativada nesse perfil. Rode `python setup_profile.py`
  e confirme que a extensão está visível na barra de ferramentas do Edge.
- Os seletores CSS da extensão podem mudar entre versões. Veja a função
  `import_via_extension` em `import_videos.py` — a lista `selectors` é fácil
  de estender. Abra um vídeo manualmente, inspecione o botão da extensão
  (F12) e adicione o seletor à lista.

### "Vídeos não estão sendo importados, mas não dá erro"
- Aumente `headless: false` para acompanhar visualmente o que acontece.
- Aumente `timeout_extensao_segundos` (ex.: 40) — a extensão LingQ pode
  demorar a injetar o botão em vídeos longos.

### "Sem legendas disponíveis" só na VPS
- O YouTube pode bloquear IPs de datacenter com a mensagem "Sign in to confirm
  you're not a bot". Exporte cookies do YouTube em formato Netscape para
  `youtube_cookies.txt` e configure `youtube_cookies_file`.
- Se `transcrever_sem_legenda` estiver `true`, o script tenta baixar o áudio e
  gerar um `.vtt` pela API da OpenAI. Configure `OPENAI_API_KEY` no ambiente da
  VPS, ou `openai_api_key` no `config.json`.

### "Quero reimportar um vídeo"
- Abra `history.json`, apague a entrada do vídeo (a chave é o ID do
  YouTube, ex.: `"dQw4w9WgXcQ"`) e rode novamente.

### "Quero parar a execução automática diária"
- No app Cowork, vá em Scheduled Tasks e desative a tarefa
  "import-lingq-diario".

## Notas técnicas

- A extensão LingQ Importer só funciona em modo **não-headless** (limitação
  do Chromium para extensões). O `headless: true` no config força o uso do
  modo headless do Playwright, que **desativa extensões** — só use se quiser
  testar buscar vídeos sem importar.
- O perfil dedicado em `edge_profile/` é completamente separado do seu Edge
  pessoal — pode usar o Edge normalmente enquanto o script roda, desde que
  seja em outro perfil.
- O script é idempotente: rodar duas vezes no mesmo dia não cria duplicatas
  porque o `history.json` filtra IDs já vistos.
