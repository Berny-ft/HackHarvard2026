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
