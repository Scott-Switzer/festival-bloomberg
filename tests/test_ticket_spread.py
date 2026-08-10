from datetime import datetime,timezone,timedelta
from decimal import Decimal as D
from metrics.spread_calculator import FXTable,calculate_spread
from scraper.seatgeek_adapter import SeatGeekAdapter
P={'currency':'USD','total_primary_price_minor':10000,'fee_components_minor':1000,'created_at':datetime(2026,8,10,tzinfo=timezone.utc)}
def s(**x):return {'currency':'USD','total_buyer_price_minor':14000,'fee_components_minor':2000,'retrieved_at':datetime(2026,8,10,1,tzinfo=timezone.utc),**x}
def test_same_currency():
 r=calculate_spread(P,s(),fx=FXTable({})); assert r.absolute_spread_minor==4000 and r.percentage_spread==D('.4')
def test_fx_and_fallback():
 r=calculate_spread(P,s(currency='EUR'),fx=FXTable({('2026-08-10','EUR','USD'):D('1.1')})); assert r.absolute_spread_minor==5400
 r=calculate_spread(P,s(currency='EUR'),fx=FXTable({})); assert 'FX_FALLBACK' in r.quality_flags and not r.arbitrage_candidate
def test_limits_missing_and_unknown_fees():
 assert 'TIMESTAMP_OUT_OF_TOLERANCE' in calculate_spread(P,s(retrieved_at=P['created_at']+timedelta(hours=25)),fx=FXTable({}),mode='historical').quality_flags
 assert 'MISSING_PRICE' in calculate_spread({**P,'total_primary_price_minor':None},s(),fx=FXTable({})).quality_flags
 assert not calculate_spread(P,s(fee_components_minor=None),fx=FXTable({})).arbitrage_candidate
def test_immutable_changed_listing():
 a=SeatGeekAdapter(); raw={'event_id':'e','listing_id':'l','listing_url':'u','title':'VIP','total_buyer_price_minor':1}; x=a.snapshot(raw); y=a.snapshot({**raw,'total_buyer_price_minor':2}); assert x.content_hash!=y.content_hash and x.total_buyer_price_minor==1
