"""Validated HTTP request contracts shared with the web and Android clients."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class WatchAdd(RequestModel):
    keyword: str = Field(min_length=1, max_length=200)
    max_price: float | None = Field(default=None, ge=0)
    min_price: float | None = Field(default=None, ge=0)


class WatchRun(RequestModel):
    watch_id: int | None = Field(default=None, ge=1)
    all: bool = False
    limit: int = Field(default=20, ge=1, le=50)
    enrich_sellers: bool = False

    @model_validator(mode="after")
    def choose_target(self):
        if self.all == (self.watch_id is not None):
            raise ValueError("请选择 watch_id 或 all=true，不能同时指定")
        return self


class RuleAdd(RequestModel):
    kind: Literal["item_id", "title_keyword", "location", "no_badge", "price_drop", "seller_nick", "price_anomaly"]
    value: str = Field(min_length=1, max_length=500)
    note: str = ""


class RuleRemove(RequestModel):
    rule_id: int = Field(ge=1)


class SellerUnban(RequestModel):
    seller_nick: str = Field(min_length=1)
