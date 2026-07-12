#exemplo de classe para representar um apartamento
class Apartamento:
    def __init__(self, titulo=None, preco=None, area=None, localizacao=None, link=None, origem=None):
        self.titulo = titulo
        self.preco = preco
        self.area = area
        self.localizacao = localizacao
        self.link = link
        self.origem = origem  # Sapo, Idealista, OLX, Imovirtual, etc.

    def to_dict(self):
        return {
            "titulo": self.titulo,
            "preco": self.preco,
            "area": self.area,
            "localizacao": self.localizacao,
            "link": self.link,
            "origem": self.origem
        }