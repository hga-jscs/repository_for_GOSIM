"""Optional account-balance guard for explicitly authorized DeepSeek local runs."""

import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen
from file_records import replace_record
from model_config import OFFICIAL_ENDPOINTS, canonical_model


class BudgetStop(RuntimeError):
    pass


class BalanceBudget:
    PRICES = {"deepseek-flash": (2, 0.04, 8), "deepseek-v4-pro": (9, 0.30, 27)}
    PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
    RESERVATION_BASIS = "Official CNY peak prices per million tokens, ignoring off-peak discounts; conservative reservation, not an invoice"

    def __init__(self, path: Path, key: str, endpoint: str, model: str = "deepseek-flash"):
        if endpoint.rstrip("/") not in OFFICIAL_ENDPOINTS:
            raise ValueError("The local balance guard requires the official DeepSeek endpoint")
        self.path = path
        self.key = key
        model = canonical_model(model)
        if model not in self.PRICES:
            raise ValueError("Configure an explicit conservative price before using this model with the local budget")
        self.model = model

    @classmethod
    def from_environment(cls, key: str, endpoint: str, model: str = "deepseek-flash"):
        path = os.environ.get("AGENT_BUDGET_FILE")
        return cls(Path(path).resolve(), key, endpoint, model) if path else None

    @classmethod
    def reservation(cls, record: dict) -> float:
        usage = record.get("usage_by_model", {"deepseek-flash": record})
        total = 0
        for model, counts in usage.items():
            miss, hit, output = cls.PRICES[canonical_model(model)]
            total += (counts.get("input_tokens", 0) - counts.get("cached_tokens", 0)) * miss + counts.get("cached_tokens", 0) * hit + counts.get("output_tokens", 0) * output
        return total / 1_000_000

    @staticmethod
    def remaining(record: dict, balance: float) -> float:
        reservation = BalanceBudget.reservation(record)
        spent = max(0, record["initial_balance_cny"] - balance, reservation)
        return min(record["effective_current_limit_cny"] - spent, balance - record["protected_balance_cny"])

    def next_request_reserve(self, input_bytes: int, max_output_tokens: int) -> float:
        miss, _, output = self.PRICES[self.model]
        return max(5 if self.model == "deepseek-v4-pro" else 2,
                   (min(input_bytes, 1_000_000) * miss + max_output_tokens * output) / 1_000_000)

    def check(self, input_bytes: int = 0, max_output_tokens: int = 0) -> None:
        request = Request("https://api.deepseek.com/user/balance", headers={"Authorization": "Bearer " + self.key})
        with urlopen(request, timeout=20) as response:
            account = json.load(response)
        balances = [entry for entry in account["balance_infos"] if entry["currency"] == "CNY"]
        if not account["is_available"] or len(balances) != 1:
            raise BudgetStop("No available CNY balance; model calls stopped")
        balance = float(balances[0]["total_balance"])
        record = json.loads(self.path.read_text(encoding="utf-8"))
        record.update(last_balance_cny=balance, spent_cny=round(max(0, record["initial_balance_cny"] - balance), 6), checked_at=time.time())
        record["cost_basis"] = "Observed account balance decrease; may include other account activity"
        self.save(record)
        # Reserve enough for the bounded next request, including its possible billed failure.
        if self.remaining(record, balance) < self.next_request_reserve(input_bytes, max_output_tokens):
            raise BudgetStop("Local spending or protected-balance limit reached; files preserved")

    def record(self, usage) -> None:
        record = json.loads(self.path.read_text(encoding="utf-8"))
        if "usage_by_model" not in record:
            record["usage_by_model"] = {"deepseek-flash": {name: record.get(name, 0) for name in ("input_tokens", "output_tokens", "cached_tokens")}}
        counts = record["usage_by_model"].setdefault(self.model, {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0})
        record["model_calls"] = record.get("model_calls", 0) + 1
        record["source"] = "Official balance snapshots and actual response usage; billing may settle later"
        for name, value in (("input_tokens", getattr(usage, "prompt_tokens", 0)), ("output_tokens", getattr(usage, "completion_tokens", 0))):
            record[name] = record.get(name, 0) + (value or 0)
            counts[name] += value or 0
        cached = getattr(usage, "prompt_cache_hit_tokens", None) or getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        record["cached_tokens"] = record.get("cached_tokens", 0) + cached
        counts["cached_tokens"] += cached
        record["model"] = self.model
        record["reservation_basis"] = self.RESERVATION_BASIS
        record["pricing_source"] = self.PRICE_SOURCE
        self.save(record)

    def save(self, record: dict) -> None:
        pending = self.path.with_suffix(".tmp")
        pending.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_record(pending, self.path)
