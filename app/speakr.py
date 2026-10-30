"""speakr API 客户端(异步 httpx,多账号)。

多账号设计:每个 speakr 用户各自拥有录音,token 只能看到自己的录音。
SPEAKR_TOKENS 配置多个 token(逗号分隔),请求遇 401/403 自动换下一个,
成功后按录音 id 记住可用 token,后续该录音的请求直接用它。
"""
import json
import re

import httpx

_RID = re.compile(r"/recordings/(\d+)")


class SpeakrClient:
    def __init__(self, base_url, tokens, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self._tokens = [t for t in (tokens or []) if t]
        self._timeout = timeout
        self._tok_for_rid = {}  # recording_id -> token 下标

    @staticmethod
    def _headers(token: str) -> dict:
        return {"Authorization": f"Bearer {token}",
                "Content-Type": "application/json", "Accept": "application/json"}

    def _order(self, path: str):
        """优先使用该录音上次成功的 token,其余按配置顺序兜底。"""
        m = _RID.search(path)
        if m and int(m.group(1)) in self._tok_for_rid:
            i = self._tok_for_rid[int(m.group(1))]
            return [i] + [j for j in range(len(self._tokens)) if j != i]
        return list(range(len(self._tokens)))

    def token_for(self, rid: int):
        """返回该录音最近一次成功使用的 token;未知返回 None。"""
        i = self._tok_for_rid.get(rid)
        return self._tokens[i] if i is not None and i < len(self._tokens) else None

    async def _req(self, method: str, path: str, body=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        last_err = "no tokens configured"
        for i in self._order(path):
            async with httpx.AsyncClient(base_url=self.base,
                                         headers=self._headers(self._tokens[i]),
                                         timeout=self._timeout) as c:
                r = await c.request(method, path, content=data)
            if r.status_code in (401, 403):
                last_err = f"{method} {path} -> HTTP {r.status_code} with token#{i}"
                continue  # 换下一个账号
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:200]}")
            m = _RID.search(path)
            if m:
                self._tok_for_rid[int(m.group(1))] = i
            return r.json() if r.content else {}
        raise RuntimeError(last_err)

    async def me(self, token: str = None) -> dict:
        tok = token or (self._tokens[0] if self._tokens else "")
        async with httpx.AsyncClient(base_url=self.base, headers=self._headers(tok),
                                     timeout=self._timeout) as c:
            r = await c.get("/api/v1/users/me")
            r.raise_for_status()
            return r.json()

    async def transcript(self, rid: int) -> dict:
        return await self._req("GET", f"/api/v1/recordings/{rid}/transcript?format=json")

    async def recording(self, rid: int) -> dict:
        return await self._req("GET", f"/api/v1/recordings/{rid}")

    async def summary(self, rid: int) -> str:
        d = await self._req("GET", f"/api/v1/recordings/{rid}/summary")
        return d.get("summary") or ""

    async def put_summary(self, rid: int, summary: str):
        return await self._req("PUT", f"/api/v1/recordings/{rid}/summary", {"summary": summary})

    async def notes(self, rid: int) -> str:
        d = await self._req("GET", f"/api/v1/recordings/{rid}/notes")
        return d.get("notes") or ""

    async def put_notes(self, rid: int, notes: str):
        return await self._req("PUT", f"/api/v1/recordings/{rid}/notes", {"notes": notes})

    async def tags(self, name: str, token: str = None) -> list:
        """tag 列表按 token 视角返回(/tags 路径不含录音 id,无法自动轮换,
        必须显式指定用哪个账号看——同名 tag 在不同账号下是不同的)。"""
        if token:
            async with httpx.AsyncClient(base_url=self.base, headers=self._headers(token),
                                         timeout=self._timeout) as c:
                r = await c.get(f"/api/v1/tags?name={name}")
                if r.status_code >= 400:
                    raise RuntimeError(f"GET /tags -> HTTP {r.status_code}: {r.text[:200]}")
                return (r.json() if r.content else {}).get("tags") or []
        d = await self._req("GET", f"/api/v1/tags?name={name}")
        return d.get("tags") or []

    async def create_tag(self, name: str, color: str, token: str = None) -> dict:
        if token:
            async with httpx.AsyncClient(base_url=self.base, headers=self._headers(token),
                                         timeout=self._timeout) as c:
                r = await c.post("/api/v1/tags", content=json.dumps({"name": name, "color": color}).encode("utf-8"))
                if r.status_code >= 400:
                    raise RuntimeError(f"POST /tags -> HTTP {r.status_code}: {r.text[:200]}")
                return r.json() if r.content else {}
        return await self._req("POST", "/api/v1/tags", {"name": name, "color": color})

    async def attach_tag(self, rid: int, tag_id: int):
        return await self._req("POST", f"/api/v1/recordings/{rid}/tags", {"tag_ids": [tag_id]})

    async def detach_tag(self, rid: int, tag_id: int):
        return await self._req("DELETE", f"/api/v1/recordings/{rid}/tags/{tag_id}")

    async def patch_recording(self, rid: int, **fields):
        return await self._req("PATCH", f"/api/v1/recordings/{rid}", fields)
