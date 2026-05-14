# Interface web local

Esta primeira versão transforma o importador em uma aplicação web local.

## Como iniciar

No PowerShell, dentro da pasta do projeto:

```powershell
.\run_web.bat
```

Depois abra:

```text
http://127.0.0.1:8000
```

No Windows, a interface roda sem `--reload` porque o Playwright precisa abrir
um processo auxiliar para ler os cookies do perfil Edge.

## O que já dá para fazer

- Listar os canais configurados.
- Adicionar, editar e remover canais.
- Ativar ou desativar canais sem apagar a configuração.
- Definir quantos vídeos cada canal deve recuperar por execução.
- Rodar a importação manualmente pela tela.
- Acompanhar logs e status da execução.
- Ver os últimos itens do histórico.
- Salvar `wwwlingqcomsa` e `csrftoken` do LingQ para importar direto pela API,
  sem abrir o Edge durante a execução.

## Como os dados são salvos

A aplicação continua usando `config.json` e `history.json`.

Cada canal agora possui estes campos extras:

```json
{
  "id": "identificador-estavel",
  "ativo": true,
  "videos_por_execucao": 1
}
```

O script de linha de comando continua funcionando com:

```powershell
python import_videos.py
```
