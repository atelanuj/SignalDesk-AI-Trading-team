import re
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from .config import DEFAULT_SYMBOLS, US_SYMBOLS
from .strategies import STRATEGIES


class ScanRequest(BaseModel):
    market: Literal['IN', 'US'] = 'IN'
    universe: Literal['watchlist', 'nifty50', 'nifty100', 'nifty_next50', 'nifty200', 'nifty_bank'] = 'watchlist'
    universe_source: str | None = None
    universe_fetched_at: str | None = None
    symbols: list[str] = Field(default_factory=lambda: DEFAULT_SYMBOLS.copy(), min_length=1, max_length=200)
    strategies: list[str] = Field(default_factory=lambda: list(STRATEGIES), min_length=1, max_length=5)
    refresh: bool = False

    @model_validator(mode='before')
    @classmethod
    def market_defaults(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            if value.get('universe', 'watchlist') == 'watchlist' and len(value.get('symbols', [])) > 20:
                raise ValueError('Custom watchlists are limited to 20 symbols.')
            # Keep old stored jobs and API clients compatible; explicit market is strict.
            if 'market' not in value and value.get('symbols'):
                value['market'] = 'IN' if any(s.strip().upper().endswith(('.NS', '.BO')) for s in value['symbols']) else 'US'
            if 'symbols' not in value and value.get('market') == 'US':
                value['symbols'] = US_SYMBOLS.copy()
        return value

    @model_validator(mode='after')
    def market_symbols(self):
        if self.universe != 'watchlist' and self.market != 'IN':
            raise ValueError('NIFTY index screens are available in India mode only.')
        if self.universe == 'watchlist' and len(self.symbols) > 20:
            raise ValueError('Custom watchlists are limited to 20 symbols. Select a NIFTY index for a broader screen.')
        if any(s.endswith(('.NS', '.BO')) != (self.market == 'IN') for s in self.symbols):
            raise ValueError('Indian symbols require .NS (NSE) or .BO (BSE); US symbols must not use these suffixes.')
        return self

    @field_validator('symbols')
    @classmethod
    def clean_symbols(cls, symbols):
        cleaned = list(dict.fromkeys(s.strip().upper() for s in symbols))
        if any(not re.fullmatch(r'[A-Z0-9^][A-Z0-9.&^=-]{0,23}', s) for s in cleaned):
            raise ValueError('Use ticker symbols such as NVDA, BRK-B or RELIANCE.NS.')
        return cleaned

    @field_validator('strategies')
    @classmethod
    def clean_strategies(cls, strategies):
        if any(s not in STRATEGIES for s in strategies):
            raise ValueError('Unknown strategy.')
        return list(dict.fromkeys(strategies))


class ChatRequest(BaseModel):
    market: Literal['IN', 'US'] = 'IN'
    conversation_id: str = Field(min_length=36, max_length=36)
    message: str = Field(min_length=1, max_length=4000)
    symbols: list[str] = Field(default_factory=lambda: DEFAULT_SYMBOLS.copy(), min_length=1, max_length=20)
    strategies: list[str] = Field(default_factory=lambda: list(STRATEGIES), min_length=1, max_length=5)
    scan_id: str | None = None

    @model_validator(mode='after')
    def market_symbols(self):
        ScanRequest(market=self.market, symbols=self.symbols, strategies=self.strategies)
        return self

    @field_validator('symbols')
    @classmethod
    def clean_symbols(cls, symbols):
        return ScanRequest(symbols=symbols).symbols

    @field_validator('strategies')
    @classmethod
    def clean_strategies(cls, strategies):
        return ScanRequest(strategies=strategies).strategies

    @field_validator('message')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Message cannot be blank.')
        return value.strip()


class WatchlistRequest(BaseModel):
    market: Literal['IN', 'US'] = 'IN'
    symbols: list[str]

    @model_validator(mode='after')
    def market_symbols(self):
        ScanRequest(market=self.market, symbols=self.symbols)
        return self

    @field_validator('symbols')
    @classmethod
    def validate_symbols(cls, value):
        return ScanRequest(symbols=value).symbols
