"""Money calculations with explicit VAT terms, rounded to kopecks."""
from decimal import Decimal,ROUND_HALF_UP
RATES={Decimal(v) for v in ('0','5','7','10','20','22')}
def calculate(amount,rate='22',mode='add'):
 amount=Decimal(str(amount));rate=Decimal(str(rate))
 if not amount.is_finite() or amount<0 or amount>Decimal('1000000000000'):raise ValueError('Проверьте сумму')
 if rate not in RATES or mode not in ('add','extract','without'):raise ValueError('Проверьте режим и ставку')
 rounded=lambda n:n.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
 amount=rounded(amount)
 if mode=='without':net=gross=amount;tax=Decimal('0.00')
 elif mode=='add':net=amount;tax=rounded(net*rate/100);gross=net+tax
 else:gross=amount;tax=rounded(gross*rate/(100+rate));net=gross-tax
 return dict(net=str(net),vat=str(tax),gross=str(gross),rate=str(rate),mode=mode)
