from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,timezone
from decimal import Decimal,ROUND_HALF_UP
from typing import Mapping
D=Decimal
@dataclass(frozen=True)
class FXTable:
    rates:Mapping[tuple[str,str,str],Decimal]; fallback_rate:Decimal=D('1')
    def convert(self,amount:int,source:str,target:str,day:str):
        source=source.upper(); target=target.upper()
        if source==target:
            return amount,False
        rate=self.rates.get((day,source,target))
        if rate is not None:
            converted=int((D(amount)*rate).quantize(D('1'),rounding=ROUND_HALF_UP))
            return converted,False
        converted=int((D(amount)*self.fallback_rate).quantize(D('1'),rounding=ROUND_HALF_UP))
        return converted,True
@dataclass(frozen=True)
class SpreadResult:
    absolute_spread_minor:int|None; percentage_spread:Decimal|None; buyer_margin:Decimal|None; currency:str; timestamp_delta_seconds:int; quality_flags:tuple[str,...]; arbitrage_candidate:bool
def _a(x):return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
def calculate_spread(primary,secondary,*,fx:FXTable,mode='active',safety_buffer=D('.10'),min_margin=D('.15')):
    flags=[]; pc=str(primary.get('currency') or '').upper(); sc=str(secondary.get('currency') or '').upper(); cur=pc or sc
    pp=primary.get('total_primary_price_minor'); sp=secondary.get('total_buyer_price_minor')
    if pp is None or sp is None:return SpreadResult(None,None,None,cur,0,('MISSING_PRICE',),False)
    if not pc or not sc:flags.append('MISSING_CURRENCY')
    day=_a(secondary['retrieved_at']).date().isoformat() if secondary.get('retrieved_at') else ''
    sp,fb=fx.convert(int(sp),sc or cur,cur,day)
    if fb:flags.append('FX_FALLBACK')
    if primary.get('created_at') and secondary.get('retrieved_at'):
        delta=abs(int((_a(secondary['retrieved_at'])-_a(primary['created_at'])).total_seconds())); limit={'historical':86400,'active':21600,'real_time':3600}.get(mode,21600)
        if delta>limit:flags.append('TIMESTAMP_OUT_OF_TOLERANCE')
    else:delta=0;flags.append('MISSING_TIMESTAMP')
    if primary.get('fee_components_minor') is None or secondary.get('fee_components_minor') is None:flags.append('UNKNOWN_FEES')
    absolute=sp-int(pp); pct=D(absolute)/D(pp) if pp else None; margin=D(absolute)/D(sp) if sp else None
    blocked={'MISSING_PRICE','MISSING_CURRENCY','FX_FALLBACK','UNKNOWN_FEES','TIMESTAMP_OUT_OF_TOLERANCE','MISSING_TIMESTAMP'}
    candidate=not blocked.intersection(flags) and absolute>0 and margin is not None and margin>=min_margin and D(absolute)>=D(sp)*safety_buffer
    return SpreadResult(absolute,pct,margin,cur,delta,tuple(flags),candidate)
