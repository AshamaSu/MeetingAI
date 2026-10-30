"""speakr API v1 客户端（异步 httpx）。"""
import httpx


class SpeakrClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json", "Accept": "application/json"}
        self._timeout = timeout

    def _client(self):
        return httpx.AsyncClient(base_url=self.base, headers=self._headers, timeout=self._timeout)

    async def _req(self, method: str, path: str, body=None):
        async with self._client() as c:
            r = await c.request(method, path, json=body)
            r.raise_for_status()
            return r.json() if r.content else {}

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

    async def tags(self, name: str) -> list:
        d = await self._req("GET", f"/api/v1/tags?name={name}")
        return d.get("tags") or []

    async def create_tag(self, name: str, color: str) -> dict:
        return await self._req("POST", "/api/v1/tags", {"name": name, "color": color})

    async def attach_tag(self, rid: int, tag_id: int):
        return await self._req("POST", f"/api/v1/recordings/{rid}/tags", {"tag_ids": [tag_id]})

    async def detach_tag(self, rid: int, tag_id: int):
        return await self._req("DELETE", f"/api/v1/recordings/{rid}/tags/{tag_id}")

    async def patch_recording(self, rid: int, **fields):
        return await self._req("PATCH", f"/api/v1/recordings/{rid}", fields)
