from __future__ import annotations
from typing import Any, Mapping
class TicketRepository:
    _tables={'tier':'core.festival_ticket_tiers','observation':'core.secondary_ticket_observations','spread':'metrics.ticket_price_spreads'}
    def __init__(self,connection): self.connection=connection
    def _insert(self,kind:str,row:Mapping[str,Any])->None:
        if kind not in self._tables or not row: raise ValueError('invalid record')
        cols=list(row); names=', '.join('"'+c+'"' for c in cols); marks=', '.join('?' for _ in cols)
        self.connection.execute(f'INSERT INTO {self._tables[kind]} ({names}) VALUES ({marks})',[row[c] for c in cols])
    def insert_primary_tier(self,row): self._insert('tier',row); self.connection.commit()
    def insert_secondary_observation(self,row): self._insert('observation',row); self.connection.commit()
    def insert_price_spread(self,row): self._insert('spread',row); self.connection.commit()
    def insert_all(self,*,tier=None,observation=None,spread=None):
        try:
            if tier is not None:self._insert('tier',tier)
            if observation is not None:self._insert('observation',observation)
            if spread is not None:self._insert('spread',spread)
            self.connection.commit()
        except Exception:
            self.connection.rollback(); raise
