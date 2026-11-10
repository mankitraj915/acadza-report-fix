"""Provider-agnostic chat wrapper (OpenAI-compatible endpoints), stdlib only.

  LLM_PROVIDER=groq|gemini           primary   (default: config.llm.provider)
  LLM_FALLBACK=gemini|groq|none      used only when the primary is rate-capped
  GROQ_API_KEY / GEMINI_API_KEY      from the environment or .env
  LLM_MODEL_<ROLE>=...               optional per-role override on the primary

Every response is cached on disk by a hash of (provider, model, messages,
params), so a re-run costs nothing and reproduces without a key.
"""
import hashlib
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request

PROVIDERS = {
    "groq": {"url": "https://api.groq.com/openai/v1/chat/completions", "key": "GROQ_API_KEY"},
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "key": "GEMINI_API_KEY"},
}
DAILY = re.compile(r"per day|\(TPD\)|\(RPD\)|PerDay|daily", re.I)


class LLMUnavailable(RuntimeError):
    pass


class RateCapped(RuntimeError):
    pass


def load_env(path):
    p = pathlib.Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _parse_json(text):
    t = (text or "").strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("no JSON object in model output")
    return json.loads(t[i: j + 1])


class LLM:
    def __init__(self, cfg, root, offline=False):
        self.cfg = cfg["llm"]
        self.provider = os.environ.get("LLM_PROVIDER") or self.cfg["provider"]
        if self.provider not in PROVIDERS:
            raise LLMUnavailable(f"unknown LLM_PROVIDER '{self.provider}'")
        fb = os.environ.get("LLM_FALLBACK") or self.cfg.get("fallback_provider") or "none"
        self.fallback = fb if fb in PROVIDERS and fb != self.provider else None
        self.cache_dir = pathlib.Path(root) / self.cfg["cache_dir"]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.usage = []
        self.min_gap = self.cfg.get("min_seconds_between_calls", {})
        self._last = {}
        self.capped = set()  # (provider, model) pairs that hit a cap in this run

    def model_for(self, role, provider=None):
        provider = provider or self.provider
        env = os.environ.get(f"LLM_MODEL_{role.upper()}")
        if provider == self.provider and env:
            return env
        return self.candidates(role, provider)[0]

    def candidates(self, role, provider):
        """Models to try for a role, in order. Config may give one id or a list (next one is used when capped)."""
        m = self.cfg["models"][provider][role]
        return m if isinstance(m, list) else [m]

    def _payload(self, provider, role, system, user, model):
        return {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.cfg.get("temperature", 0),
            "response_format": {"type": "json_object"},
        }

    def _cache_path(self, provider, payload):
        h = hashlib.sha256(json.dumps([provider, payload], sort_keys=True).encode()).hexdigest()
        return self.cache_dir / f"{h[:32]}.json"

    def chat_json(self, role, system, user, tag=""):
        """Primary first; the fallback only when the primary is rate-capped.
        A cached answer from either provider is reused before touching the network."""
        plan = []
        for p in [self.provider, self.fallback]:
            if p:
                models = self.candidates(role, p)
                if p == self.provider and os.environ.get(f"LLM_MODEL_{role.upper()}"):
                    models = [os.environ[f"LLM_MODEL_{role.upper()}"]]
                plan += [(p, self._payload(p, role, system, user, m)) for m in models]
        for p, payload in plan:
            path = self._cache_path(p, payload)
            if path.exists():
                rec = json.loads(path.read_text(encoding="utf-8"))
                self._log(p, role, payload["model"], tag, rec.get("usage") or {}, True)
                return _parse_json(rec["content"])
        if self.offline:
            raise LLMUnavailable(f"cache miss for {role}/{tag} in offline mode")
        last = None
        for p, payload in plan:
            if (p, payload["model"]) in self.capped:
                continue
            key = os.environ.get(PROVIDERS[p]["key"])
            if not key:
                last = f"{PROVIDERS[p]['key']} is not set (put it in .env)"
                continue
            try:
                content, usage = self._post(p, payload, key)
            except RateCapped as e:
                self.capped.add((p, payload["model"]))
                last = f"{p}/{payload['model']}: {e}"
                print(f"    [llm] {last}; trying the next model")
                continue
            _parse_json(content)  # raises ValueError before garbage is cached
            self._cache_path(p, payload).write_text(json.dumps(
                {"provider": p, "model": payload["model"], "role": role, "tag": tag,
                 "messages": payload["messages"], "content": content, "usage": usage},
                ensure_ascii=False, indent=1), encoding="utf-8")
            self._log(p, role, payload["model"], tag, usage, False)
            return _parse_json(content)
        raise LLMUnavailable(last or "no provider available")

    def _post(self, provider, payload, key):
        req_body = json.dumps(payload).encode()
        gap = self.min_gap.get(provider, 0) if isinstance(self.min_gap, dict) else self.min_gap
        last_err, n429 = None, 0
        for attempt in range(10):
            wait = gap - (time.time() - self._last.get(provider, 0))
            if wait > 0:
                time.sleep(wait)
            self._last[provider] = time.time()
            req = urllib.request.Request(PROVIDERS[provider]["url"], data=req_body, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                "User-Agent": "report-fix-engine/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.loads(r.read().decode())
                return data["choices"][0]["message"]["content"], data.get("usage") or {}
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:600]
                last_err = f"HTTP {e.code}: {detail[:200]}"
                if e.code == 429:
                    delay = float(e.headers.get("retry-after") or 0) or 20
                    n429 += 1
                    if delay > 120 or DAILY.search(detail) or n429 > 6:
                        raise RateCapped("daily cap" if (delay > 120 or DAILY.search(detail)) else "repeated 429")
                    time.sleep(delay + 1)
                    continue
                if e.code >= 500:
                    time.sleep(min(60, 4 * 2 ** attempt))
                    continue
                if e.code == 400 and "json" in detail.lower() and attempt < 2:
                    continue  # provider-side JSON validation failure; try again
                raise LLMUnavailable(last_err)
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = str(e)
                time.sleep(3 * (attempt + 1))
        raise LLMUnavailable(f"gave up after retries: {last_err}")

    def _log(self, provider, role, model, tag, usage, cached):
        self.usage.append({"provider": provider, "role": role, "model": model, "tag": tag, "cached": cached,
                           "prompt_tokens": usage.get("prompt_tokens", 0),
                           "completion_tokens": usage.get("completion_tokens", 0)})

    def summary(self):
        """Token totals per model. Cost uses list prices from config; None when a model has no price."""
        prices = self.cfg.get("prices_usd_per_mtok", {})
        by = {}
        for u in self.usage:
            m = by.setdefault(u["model"], {"provider": u["provider"], "calls": 0, "cached": 0,
                                           "prompt_tokens": 0, "completion_tokens": 0})
            m["calls"] += 1
            m["cached"] += int(u["cached"])
            m["prompt_tokens"] += u["prompt_tokens"]
            m["completion_tokens"] += u["completion_tokens"]
        total, priced = 0.0, True
        for model, m in by.items():
            p = prices.get(model)
            if p:
                m["list_cost_usd"] = round((m["prompt_tokens"] * p["in"] + m["completion_tokens"] * p["out"]) / 1e6, 5)
                total += m["list_cost_usd"]
            else:
                m["list_cost_usd"] = None
                priced = False
        return {"provider": self.provider, "fallback": self.fallback, "calls": len(self.usage),
                "cached_calls": sum(u["cached"] for u in self.usage),
                "fallback_calls": sum(u["provider"] != self.provider for u in self.usage),
                "prompt_tokens": sum(u["prompt_tokens"] for u in self.usage),
                "completion_tokens": sum(u["completion_tokens"] for u in self.usage),
                "by_model": by, "list_cost_usd": round(total, 5) if priced else None,
                "note": "Tokens include cached calls, i.e. what a cold run uses. list_cost is at paid list price; "
                        "the actual run used free tiers."}
