# Rede Raízes do Nordeste — API acadêmica

Projeto didático em **Python 3.13 + FastAPI + SQLite**. Fluxo principal: criar pedido, reservar estoque por 15 minutos, registrar pagamento simulado e atualizar status.

## Instalação no Windows (PowerShell)

Abra a pasta `rede-raizes-api-uninter` no VS Code. Tenha Python 3.13 instalado.

```powershell
& "C:\Users\lucas\AppData\Local\Programs\Python\Python313\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Se o Python estiver no PATH, pode usar `python -m venv .venv` em vez do caminho completo.

Swagger: http://127.0.0.1:8000/docs. O banco `restaurante.db` é criado automaticamente na primeira inicialização (criação das tabelas em `database.py`). Para reiniciar um banco de testes, pare o servidor e exclua apenas `restaurante.db`.

## Ordem para testar no Swagger

1. `POST /unidades`: `{"nome":"Centro","endereco":"Rua A, 10","telefone":"41999999999"}`.
2. `POST /usuarios`: primeiro usuário `{"nome":"Admin","email":"admin@exemplo.com","senha":"senha123","perfil":"ADMINISTRADOR","unidade_id":1}`.
3. `POST /auth/login`: `{"email":"admin@exemplo.com","senha":"senha123"}`. Copie o token.
4. Nas rotas protegidas, no campo `authorization`, informe `Bearer TOKEN_COPIADO`.
5. `POST /produtos`: `{"unidade_id":1,"nome":"Baiao de Dois","preco":25,"estoque":20}`.
6. `POST /usuarios`: cadastre cliente `{"nome":"Cliente","email":"cliente@exemplo.com","senha":"senha123","perfil":"CLIENTE","unidade_id":1}`; faça login com ele.
7. `POST /pedidos` com token do cliente: `{"canalPedido":"APP","itens":[{"produto_id":1,"quantidade":2}]}`.
8. `POST /pagamentos` com token do cliente: `{"pedido_id":1,"aprovado":true}`.
9. Faça login como administrador e use `PATCH /pedidos/1/status` para mudar sucessivamente para `EM_PREPARACAO`, `PRONTO`, `ENTREGUE`.
10. Confira `GET /estoque/1`, `GET /pedidos?canalPedido=APP` e `GET /auditoria`.

Os IDs são exemplos: use os valores efetivamente retornados. O token é mantido na memória e precisa ser renovado após reiniciar o servidor.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

A pasta `postman` inclui uma coleção de exemplos. Importe o arquivo JSON no Postman, crie uma variável `baseUrl` com `http://127.0.0.1:8000` e uma variável `token` com o valor do login.

## Arquitetura simplificada

- `main.py`: rotas, validações e regras de negócio do MVP.
- `database.py`: persistência SQLite e criação inicial de tabelas.
- `tests/`: testes automatizados.
- `postman/`: coleção de requisições.

Para uma arquitetura por camadas mais rigorosa, as regras de negócio podem ser movidas para um módulo de serviços. Nesta versão, a separação é **parcial**; não se deve afirmar que implementa integralmente Domain/Application/Infrastructure/API.

## Limites do MVP

- O pagamento é **simulado**, sem conexão com instituição financeira.
- A unidade do cliente é indicada no cadastro, não calculada por GPS.
- A reserva de 15 minutos é liberada nas consultas/operações da API, não por tarefa agendada.
- Tokens de sessão são temporários em memória (não JWT).
- Fidelidade, promoções e consentimento LGPD ainda precisam ser tratados na documentação como propostas; não foram implementados.
- Não existe sistema de migrations versionadas; a inicialização cria as tabelas se não existirem.
- Este é um protótipo didático, não uma aplicação para produção.

## GitHub

Crie um repositório público e envie os arquivos do projeto, excluindo `.venv` e o banco local conforme `.gitignore`. Faça commits ao longo das etapas e registre o link do repositório no documento acadêmico.
