import asyncio
import json
import httpx


class ModelError(Exception):
    def __init__(self, code, message, audit=None):
        self.code, self.message = code, message
        self.audit = audit
        super().__init__(message)


class DeepSeek:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(settings.model_timeout, connect=15))
        self.semaphore = asyncio.Semaphore(settings.max_model_concurrency)

    async def close(self):
        await self.client.aclose()

    async def json(self, system, user, max_tokens=1800):
        key = self.settings.deepseek_api_key.get_secret_value()
        if not key:
            raise ModelError('unconfigured', '模型尚未配置。请在后端设置测试密钥后重试。')
        body = dict(model=self.settings.deepseek_model,
                    messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                    response_format={'type': 'json_object'}, thinking={'type': 'disabled'},
                    max_tokens=max_tokens, temperature=0)
        async with self.semaphore:
            for attempt in range(2):
                try:
                    response = await self.client.post(self.settings.deepseek_base_url.rstrip('/') + '/chat/completions',
                        headers={'Authorization': 'Bearer ' + key}, json=body)
                except httpx.TimeoutException:
                    raise ModelError('timeout', '模型响应超时，请稍后重试。') from None
                except httpx.RequestError:
                    if attempt == 0:
                        await asyncio.sleep(1)
                        continue
                    raise ModelError('network', '暂时无法连接模型服务，请稍后重试。') from None
                if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    await asyncio.sleep(1)
                    continue
                if response.status_code in (401, 403):
                    raise ModelError('credentials', '测试密钥已失效或没有访问权限。更换后端密钥后可继续使用。')
                if response.status_code == 402:
                    raise ModelError('balance', '模型账户余额不足，请检查测试账户。')
                if response.status_code != 200:
                    raise ModelError('upstream', '模型服务暂不可用，请稍后重试。')
                try:
                    data = response.json()
                    choice = data['choices'][0]
                    if choice.get('finish_reason') != 'stop':
                        raise ValueError('incomplete')
                    content = choice['message']['content']
                    if not content or not content.strip():
                        raise ValueError('empty')
                    parsed = json.loads(content)
                    if not isinstance(parsed, dict):
                        raise ValueError('not object')
                    return parsed, {'model': data.get('model', self.settings.deepseek_model), 'usage': data.get('usage', {})}
                except (KeyError, IndexError, TypeError, ValueError):
                    if attempt == 0:
                        continue
                    raise ModelError('format', '模型未返回完整的回答，请重试。') from None
