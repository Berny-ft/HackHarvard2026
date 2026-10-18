"""Photon channel. spectrum-ts is TypeScript-only, so sends go through photon-bridge/ (a small Node process
that holds the Photon connection and forwards inbound texts to /photon/inbound)."""
import requests


class PhotonChannel:
    name = "photon"

    def __init__(self, bridge_url, bridge_secret):
        self.url = bridge_url.rstrip("/") + "/send"
        self.headers = {"Authorization": f"Bearer {bridge_secret}"}

    def send_text(self, to, body):
        r = requests.post(self.url, json={"to": to, "body": body}, headers=self.headers, timeout=20)
        try:
            data = r.json()
        except ValueError:
            data = {}
        return {"ok": r.ok and data.get("ok", False), "status": data.get("status", r.status_code)}


class PhotonRestChannel:
    """Photon's REST API straight from Python: what photon-bridge/send does, without the Node process (for hosts
    that run Python only, like the Chalk web sandbox). Sends only; inbound texts still need the bridge.
    A shared line can't start a conversation, so `to` must have texted the line before; the reply goes into that
    conversation (its space id and the line's own sender id, from their latest inbound text)."""
    name = "photon"
    API = "https://api.photon.codes"

    def __init__(self, project_id, api_key):
        self.base = f"{self.API}/v1/projects/{project_id}"
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def _context(self, to):
        want = "".join(c for c in to if c.isdigit())
        r = requests.get(f"{self.base}/messages", headers=self.headers, timeout=20, params={
            "direction": "received", "platform": "imessage", "order": "desc", "pageSize": "100"})
        r.raise_for_status()
        for m in r.json().get("items", []):
            if "".join(c for c in (m.get("sentBy") or {}).get("handle", "") if c.isdigit()) == want:
                space, line = (m.get("space") or {}).get("id"), (m.get("receivedBy") or {}).get("id")
                if space and line:
                    return space, line
        return None

    def send_text(self, to, body):
        import uuid
        try:
            ctx = self._context(to)
        except requests.RequestException as e:
            return {"ok": False, "status": f"photon unreachable: {e}"}
        if ctx is None:
            return {"ok": False, "status": "no conversation yet: text the Photon line from this phone first"}
        r = requests.post(f"{self.base}/messages", timeout=20,
                          headers={**self.headers, "idempotency-key": uuid.uuid4().hex},
                          json={"spaceId": ctx[0], "senderId": ctx[1],
                                "content": {"type": "text", "format": "plain", "text": body}})
        return {"ok": r.ok, "status": "sent" if r.ok else f"{r.status_code} {r.text[:200]}"}
