import os
import tempfile
import unittest
from pathlib import Path

# Testes de integracao em um banco isolado (sem alterar restaurante.db).
import database
arquivo = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
arquivo.close()
database.ARQUIVO = Path(arquivo.name)
from main import app, tokens
from fastapi.testclient import TestClient


class TesteFluxo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cliente = TestClient(app)
        r = cls.cliente.post('/unidades', json={'nome':'Centro','endereco':'Rua A'})
        assert r.status_code == 201
        r = cls.cliente.post('/usuarios', json={'nome':'Admin','email':'admin@teste.com','senha':'senha123','perfil':'ADMINISTRADOR','unidade_id':1})
        assert r.status_code == 201
        r = cls.cliente.post('/auth/login', json={'email':'admin@teste.com','senha':'senha123'})
        cls.admin = {'authorization':'Bearer ' + r.json()['access_token']}
        r = cls.cliente.post('/produtos', json={'unidade_id':1,'nome':'Baiao','preco':25,'estoque':10},headers=cls.admin)
        assert r.status_code == 201
        r = cls.cliente.post('/usuarios',json={'nome':'Cliente','email':'cliente@teste.com','senha':'senha123','perfil':'CLIENTE','unidade_id':1})
        assert r.status_code == 201
        r = cls.cliente.post('/auth/login',json={'email':'cliente@teste.com','senha':'senha123'})
        cls.auth = {'authorization':'Bearer ' + r.json()['access_token']}

    def test_01_login(self):
        self.assertEqual(self.cliente.post('/auth/login',json={'email':'cliente@teste.com','senha':'senha123'}).status_code,200)

    def test_02_sem_token(self):
        self.assertEqual(self.cliente.get('/pedidos').status_code,401)

    def test_03_sem_permissao(self):
        self.assertEqual(self.cliente.post('/produtos',json={'unidade_id':1,'nome':'X','preco':1,'estoque':1},headers=self.auth).status_code,403)

    def test_04_campo_obrigatorio(self):
        self.assertEqual(self.cliente.post('/pedidos',json={'itens':[{'produto_id':1,'quantidade':1}]},headers=self.auth).status_code,422)

    def test_05_quantidade_invalida(self):
        self.assertEqual(self.cliente.post('/pedidos',json={'canalPedido':'APP','itens':[{'produto_id':1,'quantidade':-1}]},headers=self.auth).status_code,422)

    def test_06_produto_inexistente(self):
        self.assertEqual(self.cliente.post('/pedidos',json={'canalPedido':'APP','itens':[{'produto_id':999,'quantidade':1}]},headers=self.auth).status_code,404)

    def test_07_estoque_insuficiente(self):
        self.assertEqual(self.cliente.post('/pedidos',json={'canalPedido':'APP','itens':[{'produto_id':1,'quantidade':999}]},headers=self.auth).status_code,409)

    def test_08_fluxo_completo(self):
        r=self.cliente.post('/pedidos',json={'canalPedido':'APP','itens':[{'produto_id':1,'quantidade':2}]},headers=self.auth)
        self.assertEqual(r.status_code,201)
        pid=r.json()['id']
        self.assertEqual(self.cliente.get('/pedidos',params={'canalPedido':'APP'},headers=self.auth).status_code,200)
        self.assertEqual(self.cliente.get('/pedidos/'+str(pid),headers=self.auth).status_code,200)
        r=self.cliente.post('/pagamentos',json={'pedido_id':pid,'aprovado':True},headers=self.auth)
        self.assertEqual(r.status_code,201)
        self.assertEqual(r.json()['status_pedido'],'PAGO')
        for status in ['EM_PREPARACAO','PRONTO','ENTREGUE']:
            self.assertEqual(self.cliente.patch(f'/pedidos/{pid}/status',json={'status':status},headers=self.admin).status_code,200)
        self.assertEqual(self.cliente.get('/estoque/1',headers=self.admin).json()['fisico'],8)
        self.assertTrue(self.cliente.get('/auditoria',headers=self.admin).json()['registros'])

    def test_09_pagamento_recusado(self):
        r=self.cliente.post('/pedidos',json={'canalPedido':'BALCAO','itens':[{'produto_id':1,'quantidade':1}]},headers=self.auth)
        pid=r.json()['id']
        r=self.cliente.post('/pagamentos',json={'pedido_id':pid,'aprovado':False},headers=self.auth)
        self.assertEqual(r.status_code,201)
        self.assertEqual(r.json()['status_pedido'],'CANCELADO')

    def test_10_pagamento_duplicado(self):
        r=self.cliente.post('/pedidos',json={'canalPedido':'APP','itens':[{'produto_id':1,'quantidade':1}]},headers=self.auth)
        pid=r.json()['id']
        self.assertEqual(self.cliente.post('/pagamentos',json={'pedido_id':pid,'aprovado':True},headers=self.auth).status_code,201)
        self.assertEqual(self.cliente.post('/pagamentos',json={'pedido_id':pid,'aprovado':True},headers=self.auth).status_code,409)
        
    def test_11_consultar_produtos(self):
        r = self.cliente.get(
            '/produtos',
            params={'unidade_id': 1},
            headers=self.auth
        )
        self.assertEqual(r.status_code, 200)

    def test_12_consultar_pedidos(self):
        r = self.cliente.get(
            '/pedidos',
            headers=self.auth
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), (list, dict))

    def test_13_consultar_estoque(self):
        r = self.cliente.get(
            '/estoque/1',
            headers=self.admin
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn('fisico', r.json())
        self.assertIn('reservado', r.json())
        self.assertIn('disponivel', r.json())

if __name__ == '__main__':
    unittest.main()
