from models.Apartamento import Apartamento
print(Apartamento)

x = Apartamento(titulo="Teste", preco=100000, area=80, localizacao="Lisboa", link="http://exemplo.com", origem="Sapo")
print(x.to_dict())


# correr  python -m models.teste e não através do Run Python File