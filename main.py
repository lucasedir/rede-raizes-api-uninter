from datetime import datetime, timedelta, timezone
from hashlib import pbkdf2_hmac
import hashlib
import sqlite3
from enum import Enum
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import hmac
import secrets

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from database import conectar, criar_tabelas

app = FastAPI(title='Rede Raízes do Nordeste', version='1.0.0',
              description='Projeto acadêmico: API de gerenciamento de pedidos')
criar_tabelas()

# Tokens ficam na memória. Ao reiniciar a API, é necessário fazer login novamente.
tokens = {}
FUSO = timezone(timedelta(hours=-3))


def agora():
    return datetime.now(FUSO)


def data_texto(data):
    return data.isoformat(timespec='seconds')


def erro(codigo, mensagem):
    raise HTTPException(status_code=codigo, detail=mensagem)


def buscar(banco, tabela, identificador):
    return banco.execute(f'SELECT * FROM {tabela} WHERE id = ?', (identificador,)).fetchone()


def senha_hash(senha):
    salt = secrets.token_bytes(16)
    resultado = pbkdf2_hmac('sha256', senha.encode(), salt, 120000)
    return salt.hex() + ':' + resultado.hex()


def conferir_senha(senha, armazenada):
    try:
        salt, resultado = armazenada.split(':')
        tentativa = pbkdf2_hmac('sha256', senha.encode(), bytes.fromhex(salt), 120000)
        return hmac.compare_digest(tentativa, bytes.fromhex(resultado))
    except (ValueError, AttributeError):
        return False


@app.exception_handler(HTTPException)
async def tratar_erro(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={'erro': {'codigo': exc.status_code, 'mensagem': exc.detail}})


@app.exception_handler(RequestValidationError)
async def tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={'erro': {'codigo': 422, 'mensagem': 'Dados inválidos ou incompletos'}})


def auditar(banco, usuario_id, acao, detalhe):
    banco.execute('INSERT INTO auditoria (usuario_id, acao, detalhe, criado_em) VALUES (?, ?, ?, ?)',
                  (usuario_id, acao, detalhe, data_texto(agora())))


def usuario_atual(authorization: str | None):
    if not authorization or not authorization.startswith('Bearer '):
        erro(401, 'Faça login e informe o token no cabeçalho Authorization')
    token = authorization[7:]
    usuario_id = tokens.get(token)
    if not usuario_id:
        erro(401, 'Token inválido. Faça login novamente')
    with conectar() as banco:
        usuario = buscar(banco, 'usuarios', usuario_id)
    if not usuario:
        erro(401, 'Usuário não encontrado')
    return dict(usuario)


def exigir(authorization, perfis=None):
    usuario = usuario_atual(authorization)
    if perfis and usuario['perfil'] not in perfis:
        erro(403, 'Seu perfil não permite esta operação')
    return usuario


def mesma_unidade(usuario, unidade_id):
    if usuario['unidade_id'] != unidade_id:
        erro(403, 'Acesso permitido apenas à unidade vinculada ao usuário')


def liberar_expirados(banco):
    # Pedidos não pagos expiram automaticamente após 15 minutos.
    banco.execute('''UPDATE pedidos SET status = 'EXPIRADO'
                     WHERE status = 'AGUARDANDO_PAGAMENTO' AND expira_em <= ?''',
                  (data_texto(agora()),))


def reservado(banco, produto_id):
    resultado = banco.execute('''
        SELECT COALESCE(SUM(i.quantidade), 0) AS quantidade
        FROM itens_pedido i JOIN pedidos p ON p.id = i.pedido_id
        WHERE i.produto_id = ? AND p.status = 'AGUARDANDO_PAGAMENTO'
    ''', (produto_id,)).fetchone()
    return resultado['quantidade']


class Unidade(BaseModel):
    nome: str = Field(min_length=1)
    endereco: str = Field(min_length=1)
    telefone: str | None = None


class Usuario(BaseModel):
    nome: str = Field(min_length=1)
    email: str
    senha: str = Field(min_length=4)
    perfil: str
    unidade_id: int


class Login(BaseModel):
    email: str
    senha: str


class Produto(BaseModel):
    unidade_id: int
    nome: str = Field(min_length=1)
    preco: float = Field(ge=0)
    estoque: int = Field(ge=0)


class MovimentoEstoque(BaseModel):
    produto_id: int
    quantidade: int  # Positivo = entrada; negativo = saída.


class Item(BaseModel):
    produto_id: int
    quantidade: int = Field(ge=1)


class CanalPedido(str, Enum):
    APP = 'APP'
    TOTEM = 'TOTEM'
    BALCAO = 'BALCAO'
    PICKUP = 'PICKUP'
    WEB = 'WEB'
    TELEFONE = 'TELEFONE'


class NovoPedido(BaseModel):
    canalPedido: CanalPedido
    itens: list[Item] = Field(min_length=1)


class Pagamento(BaseModel):
    pedido_id: int
    aprovado: bool


class NovoStatus(BaseModel):
    status: str


@app.get('/')
def inicio():
    return {'mensagem': 'API da Rede Raízes do Nordeste funcionando!'}


@app.post('/unidades', status_code=201)
def cadastrar_unidade(dados: Unidade):
    # Cadastro livre apenas para facilitar a configuração inicial do projeto.
    with conectar() as banco:
        cursor = banco.execute('INSERT INTO unidades (nome, endereco, telefone) VALUES (?, ?, ?)',
                               (dados.nome, dados.endereco, dados.telefone))
        return {'id': cursor.lastrowid, **dados.model_dump()}


@app.get('/unidades')
def listar_unidades():
    with conectar() as banco:
        return {'unidades': [dict(x) for x in banco.execute('SELECT * FROM unidades')]}


@app.post('/usuarios', status_code=201)
def cadastrar_usuario(dados: Usuario, authorization: str | None = Header(default=None)):
    perfis = ('CLIENTE', 'ATENDENTE', 'COZINHA', 'GERENTE', 'ADMINISTRADOR')
    perfil = dados.perfil.upper()
    if perfil not in perfis:
        erro(400, 'Perfil inválido')
    # Clientes podem se cadastrar; funcionários exigem gerente ou administrador.
    if perfil != 'CLIENTE':
        with conectar() as banco:
            total = banco.execute('SELECT COUNT(*) AS total FROM usuarios').fetchone()['total']
        if total > 0:
            responsavel = exigir(authorization, ('GERENTE', 'ADMINISTRADOR'))
            mesma_unidade(responsavel, dados.unidade_id)
        elif perfil != 'ADMINISTRADOR':
            erro(403, 'O primeiro usuário deve ser ADMINISTRADOR')
    with conectar() as banco:
        if not buscar(banco, 'unidades', dados.unidade_id):
            erro(404, 'Unidade não encontrada')
        try:
            cursor = banco.execute('''INSERT INTO usuarios (nome, email, senha, perfil, unidade_id)
                                      VALUES (?, ?, ?, ?, ?)''',
                                   (dados.nome, dados.email.lower(), senha_hash(dados.senha),
                                    perfil, dados.unidade_id))
        except sqlite3.IntegrityError:
            erro(409, 'Este e-mail já está cadastrado')
        return {'id': cursor.lastrowid, 'nome': dados.nome, 'email': dados.email.lower(),
                'perfil': perfil, 'unidade_id': dados.unidade_id}


@app.post('/auth/login')
def login(dados: Login):
    with conectar() as banco:
        usuario = banco.execute('SELECT * FROM usuarios WHERE email = ?',
                                (dados.email.lower(),)).fetchone()
    if not usuario or not conferir_senha(dados.senha, usuario['senha']):
        erro(401, 'E-mail ou senha incorretos')
    token = secrets.token_urlsafe(24)
    tokens[token] = usuario['id']
    return {'access_token': token, 'token_type': 'bearer', 'perfil': usuario['perfil']}


@app.post('/auth/logout')
def logout(authorization: str | None = Header(default=None)):
    exigir(authorization)
    tokens.pop(authorization[7:], None)
    return {'mensagem': 'Sessão encerrada'}


@app.get('/usuarios/{usuario_id}')
def consultar_usuario(usuario_id: int, authorization: str | None = Header(default=None)):
    atual = exigir(authorization)
    with conectar() as banco:
        usuario = buscar(banco, 'usuarios', usuario_id)
    if not usuario:
        erro(404, 'Usuário não encontrado')
    if atual['id'] != usuario_id:
        exigir(authorization, ('GERENTE', 'ADMINISTRADOR'))
        mesma_unidade(atual, usuario['unidade_id'])
    return {k: usuario[k] for k in ('id', 'nome', 'email', 'perfil', 'unidade_id')}


@app.post('/produtos', status_code=201)
def cadastrar_produto(dados: Produto, authorization: str | None = Header(default=None)):
    usuario = exigir(authorization, ('GERENTE', 'ADMINISTRADOR'))
    mesma_unidade(usuario, dados.unidade_id)
    with conectar() as banco:
        if not buscar(banco, 'unidades', dados.unidade_id):
            erro(404, 'Unidade não encontrada')
        cursor = banco.execute('''INSERT INTO produtos (unidade_id, nome, preco, estoque)
                                  VALUES (?, ?, ?, ?)''',
                               (dados.unidade_id, dados.nome, dados.preco, dados.estoque))
        return {'id': cursor.lastrowid, **dados.model_dump()}


@app.get('/produtos')
def listar_produtos(unidade_id: int = Query(...)):
    with conectar() as banco:
        if not buscar(banco, 'unidades', unidade_id):
            erro(404, 'Unidade não encontrada')
        liberar_expirados(banco)
        produtos = []
        for linha in banco.execute('SELECT * FROM produtos WHERE unidade_id = ?', (unidade_id,)).fetchall():
            produto = dict(linha)
            produto['disponivel'] = produto['estoque'] - reservado(banco, produto['id'])
            produtos.append(produto)
        return {'produtos': produtos}


@app.get('/estoque/{produto_id}')
def consultar_estoque(produto_id: int, authorization: str | None = Header(default=None)):
    usuario = exigir(authorization)
    with conectar() as banco:
        liberar_expirados(banco)
        produto = buscar(banco, 'produtos', produto_id)
        if not produto:
            erro(404, 'Produto não encontrado')
        mesma_unidade(usuario, produto['unidade_id'])
        reserva = reservado(banco, produto_id)
        return {'produto_id': produto_id, 'fisico': produto['estoque'],
                'reservado': reserva, 'disponivel': produto['estoque'] - reserva}


@app.post('/estoque/movimentacoes')
def movimentar_estoque(dados: MovimentoEstoque,
                      authorization: str | None = Header(default=None)):
    usuario = exigir(authorization, ('GERENTE', 'ADMINISTRADOR'))
    if dados.quantidade == 0:
        erro(400, 'A quantidade não pode ser zero')
    with conectar() as banco:
        liberar_expirados(banco)
        produto = buscar(banco, 'produtos', dados.produto_id)
        if not produto:
            erro(404, 'Produto não encontrado')
        mesma_unidade(usuario, produto['unidade_id'])
        novo = produto['estoque'] + dados.quantidade
        if novo < reservado(banco, dados.produto_id):
            erro(409, 'Movimentação deixaria o estoque abaixo das reservas')
        banco.execute('UPDATE produtos SET estoque = ? WHERE id = ?', (novo, dados.produto_id))
        return {'produto_id': dados.produto_id, 'estoque': novo}


@app.post('/pedidos', status_code=201)
def criar_pedido(dados: NovoPedido, authorization: str | None = Header(default=None)):
    usuario = exigir(authorization, ('CLIENTE', 'ATENDENTE'))
    canal = dados.canalPedido.value
    # Não permitir o mesmo produto duas vezes no mesmo pedido.
    ids = [item.produto_id for item in dados.itens]
    if len(ids) != len(set(ids)):
        erro(400, 'Produto repetido no pedido')
    with conectar() as banco:
        banco.execute('BEGIN IMMEDIATE')
        liberar_expirados(banco)
        total = 0
        itens = []
        for item in dados.itens:
            produto = buscar(banco, 'produtos', item.produto_id)
            if not produto or produto['unidade_id'] != usuario['unidade_id']:
                erro(404, 'Produto não encontrado na unidade do usuário')
            if produto['estoque'] - reservado(banco, produto['id']) < item.quantidade:
                erro(409, f'Estoque insuficiente para {produto["nome"]}')
            total += produto['preco'] * item.quantidade
            itens.append((item.produto_id, item.quantidade, produto['preco']))
        inicio = agora()
        fim = inicio + timedelta(minutes=15)
        cursor = banco.execute('''INSERT INTO pedidos
            (usuario_id, unidade_id, canal, status, criado_em, expira_em, total)
            VALUES (?, ?, ?, 'AGUARDANDO_PAGAMENTO', ?, ?, ?)''',
            (usuario['id'], usuario['unidade_id'], canal, data_texto(inicio), data_texto(fim), round(total, 2)))
        pedido_id = cursor.lastrowid
        for produto_id, quantidade, preco in itens:
            banco.execute('''INSERT INTO itens_pedido (pedido_id, produto_id, quantidade, preco_unitario)
                             VALUES (?, ?, ?, ?)''', (pedido_id, produto_id, quantidade, preco))
        auditar(banco, usuario['id'], 'CRIAR_PEDIDO', str(pedido_id))
        return {'id': pedido_id, 'status': 'AGUARDANDO_PAGAMENTO', 'canalPedido': canal,
                'total': round(total, 2), 'expira_em': data_texto(fim)}


def consultar_pedido_interno(banco, pedido_id, usuario):
    pedido = buscar(banco, 'pedidos', pedido_id)
    if not pedido:
        erro(404, 'Pedido não encontrado')
    mesma_unidade(usuario, pedido['unidade_id'])
    if usuario['perfil'] == 'CLIENTE' and pedido['usuario_id'] != usuario['id']:
        erro(403, 'Você só pode consultar seus pedidos')
    return pedido


@app.get('/pedidos')
def listar_pedidos(canalPedido: CanalPedido | None = None, status: str | None = None, authorization: str | None = Header(default=None)):
    usuario = exigir(authorization)
    with conectar() as banco:
        liberar_expirados(banco)
        sql = 'SELECT * FROM pedidos WHERE unidade_id = ?'
        parametros = [usuario['unidade_id']]
        if usuario['perfil'] == 'CLIENTE':
            sql += ' AND usuario_id = ?'
            parametros.append(usuario['id'])
        if canalPedido:
            sql += ' AND canal = ?'
            parametros.append(canalPedido.value)
        if status:
            sql += ' AND status = ?'
            parametros.append(status.upper())
        sql += ' ORDER BY id DESC'
        return {'pedidos': [dict(x) for x in banco.execute(sql, parametros).fetchall()]}


@app.get('/pedidos/{pedido_id}')
def consultar_pedido(pedido_id: int, authorization: str | None = Header(default=None)):
    usuario = exigir(authorization)
    with conectar() as banco:
        liberar_expirados(banco)
        pedido = consultar_pedido_interno(banco, pedido_id, usuario)
        itens = [dict(x) for x in banco.execute('SELECT produto_id, quantidade, preco_unitario FROM itens_pedido WHERE pedido_id = ?', (pedido_id,))]
        return {'pedido': dict(pedido), 'itens': itens}


@app.post('/pagamentos', status_code=201)
def registrar_pagamento(dados: Pagamento, authorization: str | None = Header(default=None)):
    usuario = exigir(authorization)
    with conectar() as banco:
        banco.execute('BEGIN IMMEDIATE')
        liberar_expirados(banco)
        pedido = consultar_pedido_interno(banco, dados.pedido_id, usuario)
        if pedido['status'] != 'AGUARDANDO_PAGAMENTO':
            erro(409, 'Este pedido não está aguardando pagamento')
        resultado = 'APROVADO' if dados.aprovado else 'RECUSADO'
        if dados.aprovado:
            itens = banco.execute('SELECT * FROM itens_pedido WHERE pedido_id = ?', (dados.pedido_id,)).fetchall()
            for item in itens:
                banco.execute('UPDATE produtos SET estoque = estoque - ? WHERE id = ?',
                              (item['quantidade'], item['produto_id']))
            status = 'PAGO'
        else:
            status = 'CANCELADO'
        banco.execute('UPDATE pedidos SET status = ? WHERE id = ?', (status, dados.pedido_id))
        cursor = banco.execute('INSERT INTO pagamentos (pedido_id, resultado, registrado_em) VALUES (?, ?, ?)',
                               (dados.pedido_id, resultado, data_texto(agora())))
        auditar(banco, usuario['id'], 'PAGAMENTO_MOCK', str(dados.pedido_id) + ':' + resultado)
        return {'id': cursor.lastrowid, 'pedido_id': dados.pedido_id,
                'resultado': resultado, 'status_pedido': status}


@app.get('/pagamentos/{pagamento_id}')
def consultar_pagamento(pagamento_id: int, authorization: str | None = Header(default=None)):
    usuario = exigir(authorization)
    with conectar() as banco:
        pagamento = buscar(banco, 'pagamentos', pagamento_id)
        if not pagamento:
            erro(404, 'Pagamento não encontrado')
        consultar_pedido_interno(banco, pagamento['pedido_id'], usuario)
        return dict(pagamento)


@app.patch('/pedidos/{pedido_id}/status')
def atualizar_status(pedido_id: int, dados: NovoStatus,
                    authorization: str | None = Header(default=None)):
    usuario = exigir(authorization)
    with conectar() as banco:
        liberar_expirados(banco)
        pedido = consultar_pedido_interno(banco, pedido_id, usuario)
        atual = pedido['status']
        proximo = dados.status.upper()
        etapas = {'PAGO': 'EM_PREPARACAO', 'EM_PREPARACAO': 'PRONTO', 'PRONTO': 'ENTREGUE'}
        if proximo == 'CANCELADO' and atual == 'AGUARDANDO_PAGAMENTO':
            if usuario['perfil'] not in ('CLIENTE', 'ATENDENTE', 'GERENTE', 'ADMINISTRADOR'):
                erro(403, 'Perfil sem permissão para cancelar')
        elif etapas.get(atual) == proximo:
            if usuario['perfil'] not in ('COZINHA', 'ATENDENTE', 'GERENTE', 'ADMINISTRADOR'):
                erro(403, 'Perfil sem permissão para atualizar o preparo')
        else:
            erro(409, 'Mudança de status não permitida')
        banco.execute('UPDATE pedidos SET status = ? WHERE id = ?', (proximo, pedido_id))
        auditar(banco, usuario['id'], 'ALTERAR_STATUS', str(pedido_id) + ':' + proximo)
        return {'pedido_id': pedido_id, 'status': proximo}


@app.get('/auditoria')
def consultar_auditoria(authorization: str | None = Header(default=None)):
    usuario = exigir(authorization, ('GERENTE', 'ADMINISTRADOR'))
    with conectar() as banco:
        linhas = banco.execute("""SELECT a.id, a.acao, a.detalhe, a.criado_em
                                  FROM auditoria a JOIN usuarios u ON a.usuario_id = u.id
                                  WHERE u.unidade_id = ? ORDER BY a.id DESC LIMIT 50""",
                               (usuario['unidade_id'],)).fetchall()
        return {'registros': [dict(x) for x in linhas]}
