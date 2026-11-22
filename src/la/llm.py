"""A small client for an OpenAI-compatible /chat/completions API (urllib only).

Settings come from the environment, which compose.yaml's settings block fills in:
  LA_LLM_URL, LA_LLM_MODEL, LA_LLM_REASONING (off|low|high|empty), LA_LLM_MAX_TOKENS,
  LA_LLM_TIMEOUT, LA_LLM_VERIFY_TLS (true|false), LA_LLM_CA_FILE, LLM_API_KEY (.env).
"""
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

THINK = re.compile(r"<think>.*?</think>", re.S)


class LLMError(Exception):
    pass


class Settings:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.url = env.get("LA_LLM_URL", "").rstrip("/")
        self.model = env.get("LA_LLM_MODEL", "")
        self.reasoning = env.get("LA_LLM_REASONING", "off").strip().lower()
        self.max_tokens = int(env.get("LA_LLM_MAX_TOKENS", "2000") or 2000)
        self.timeout = int(env.get("LA_LLM_TIMEOUT", "90") or 90)
        self.verify_tls = env.get("LA_LLM_VERIFY_TLS", "true").strip().lower() != "false"
        self.ca_file = env.get("LA_LLM_CA_FILE", "").strip()
        self.api_key = env.get("LLM_API_KEY", "").strip()

    @property
    def gpt(self):
        return bool(re.search(r"gpt-5|\bo[134]\b", self.model))

    def problem(self):
        if not self.url or not self.model:
            return ("No model is set. Fill in llm-url and llm-model in the settings block "
                    "at the top of compose.yaml.")
        if "<" in self.url:
            return f"The model URL still has a placeholder: {self.url}. Fill in the host in compose.yaml."
        if not re.match(r"^https?://", self.url):
            return f"The model URL must start with http:// or https:// (it is {self.url})."
        return None


def body(s, messages):
    b = {"model": s.model, "messages": messages, "temperature": 0, "stream": False}
    b["max_completion_tokens" if s.gpt else "max_tokens"] = s.max_tokens
    if s.reasoning in ("off", "low", "high"):
        if s.gpt:
            # gpt-5 calls "off" minimal; gpt-5.1 and later call it none.
            off = "none" if re.search(r"gpt-5\.\d", s.model) else "minimal"
            b["reasoning_effort"] = {"off": off}.get(s.reasoning, s.reasoning)
            if s.reasoning != "off":
                b.pop("temperature")      # reasoning GPT models take only the default
        else:
            b["chat_template_kwargs"] = {"enable_thinking": s.reasoning != "off"}
    return b


def _ssl_context(s):
    if not s.url.startswith("https://"):
        return None
    if not s.verify_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=s.ca_file or None)


def _server_said(detail, key=""):
    """The server's own words, which usually say what is wrong. Never the key."""
    try:
        d = json.loads(detail)
        err = d.get("error")
        text = err.get("message") if isinstance(err, dict) else err
        text = text or d.get("detail") or d.get("title") or d.get("message")
    except (ValueError, AttributeError):
        text = None
    text = re.sub(r"\s+", " ", str(text or detail)).strip()
    if key and len(key) >= 8:
        text = text.replace(key, "<key>")
    text = re.sub(r"\b(nvapi-|sk-)[\w-]{8,}", r"\1<key>", text)
    return text[:200] or "nothing"


def chat(s, messages):
    """(reply text, seconds, completion tokens). Raises LLMError with a plain-English reason."""
    headers = {"Content-Type": "application/json"}
    if s.api_key:
        headers["Authorization"] = f"Bearer {s.api_key}"
    data = json.dumps(body(s, messages)).encode()
    req = urllib.request.Request(s.url + "/chat/completions", data=data, headers=headers)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=s.timeout, context=_ssl_context(s)) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        said = _server_said(e.read(2000).decode("utf-8", "replace"), s.api_key)
        if e.code in (401, 403):
            raise LLMError(f"The model server refused access (HTTP {e.code}). Check LLM_API_KEY "
                           f"in .env. Server said: {said}") from None
        if e.code == 404:
            raise LLMError(f"Not found (HTTP 404) at {s.url}. Check llm-url (usually ends in /v1) "
                           f"and llm-model ({s.model}). Server said: {said}") from None
        if e.code == 400 and re.search(r"reasoning|thinking|chat_template", said, re.I):
            raise LLMError(f"The model server refused the thinking setting (llm-reasoning: "
                           f"{s.reasoning or 'empty'}). Set llm-reasoning to empty. Server said: {said}") from None
        if e.code == 429:
            raise LLMError("The model server is rate-limiting (HTTP 429). Try again in a minute.") from None
        raise LLMError(f"The model server returned HTTP {e.code}. Server said: {said}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise LLMError(f"The model server's certificate failed the check: {reason.verify_message}. "
                           "Set llm-ca-file to its CA, or llm-verify-tls to false.") from None
        if isinstance(reason, TimeoutError) or isinstance(e, TimeoutError):
            raise LLMError(f"The model server gave no reply within {s.timeout} seconds.") from None
        raise LLMError(f"Cannot reach the model server at {s.url}: {reason}") from None
    seconds = time.monotonic() - started
    try:
        d = json.loads(raw)
        choice = d["choices"][0]
        msg = choice.get("message") or {}
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("The model server sent a reply that is not an OpenAI-style answer. "
                       "Is llm-url the API address (ending in /v1)?") from None
    text = THINK.sub("", msg.get("content") or "")
    if "</think>" in text:
        text = text.split("</think>")[-1]
    text = text.strip()
    tokens = (d.get("usage") or {}).get("completion_tokens") or 0
    if not text:
        if choice.get("finish_reason") == "length":
            raise LLMError(f"The model used all {s.max_tokens} reply tokens thinking and gave no answer. "
                           "Set llm-reasoning to off, or raise llm-max-tokens.")
        raise LLMError("The model gave an empty answer.")
    return text, seconds, tokens


def parse_json(text):
    """The JSON object in a reply, also when wrapped in ``` fences or extra words."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(t)
    except ValueError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except ValueError:
            pass
    raise LLMError("The model's answer was not the JSON it was asked for.")
