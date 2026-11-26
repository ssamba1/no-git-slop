import pytest

SAMPLE = """invoice,customer,description,quantity,unit_price,tax_rate
INV-1,Acme,Widgets,10,2.50,0.20
INV-1,Acme,Bolts,100,0.10,0.20
INV-2,Globex,Consulting,3,150.00,0.00
INV-3,Acme,Widgets,4,2.50,0.20
"""


@pytest.fixture
def sample():
    return SAMPLE
