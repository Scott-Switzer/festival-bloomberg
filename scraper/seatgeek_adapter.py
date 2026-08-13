from __future__ import annotations
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
from typing import Any,Mapping
import hashlib,json

def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'true', '1', 'yes', 'y', 'on'}:
            return True
        if normalized in {'false', '0', 'no', 'n', 'off', ''}:
            return False
    raise ValueError(f'invalid boolean value: {value!r}')

@dataclass(frozen=True)
class SeatGeekSnapshot:
    external_event_id:str; external_listing_id:str; listing_url:str; title:str; ticket_type:str|None; section:str|None; row:str|None; quantity:int|None; price_minor:int|None; currency:str|None; fee_components_minor:int|None; total_buyer_price_minor:int|None; is_active:bool; retrieved_at:datetime; content_hash:str; provenance:str; quality_flags:tuple[str,...]

class SeatGeekAdapter:
    source='seatgeek'
    def snapshot(self,raw:Mapping[str,Any],retrieved_at=None):
        def p(*ks):return next((raw[k] for k in ks if raw.get(k) is not None),None)
        vals={'event':p('event_id','external_event_id'),'listing':p('listing_id','external_listing_id'),'url':p('listing_url','url'),'title':p('title')}; flags=tuple('MISSING_'+k.upper() for k,v in vals.items() if v in (None,'')); ts=retrieved_at or datetime.now(timezone.utc); active=p('is_active')
        digest=hashlib.sha256(json.dumps(raw,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
        return SeatGeekSnapshot(str(vals['event'] or ''),str(vals['listing'] or ''),str(vals['url'] or ''),str(vals['title'] or ''),p('ticket_type'),p('section'),p('row'),p('quantity'),p('price_minor','price'),p('currency'),p('fee_components_minor'),p('total_buyer_price_minor','total_price_minor'),_parse_bool(active, default=True),ts,digest,'seatgeek:'+str(vals['listing'] or 'unknown'),flags)
    def to_row(self,snapshot,edition_id):
        row=asdict(snapshot); row.update(edition_id=edition_id,source=self.source); row['quality_flags']=list(row['quality_flags']); return row
