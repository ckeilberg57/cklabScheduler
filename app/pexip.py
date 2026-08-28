import requests

from app.config import Settings


class PexipAPI:
    def __init__(self):
        self.status_base = f"https://{Settings.REG_STATUS_HOST}"
        self.client_base = f"https://{Settings.COMMAND_HOST}"
        self.auth = (Settings.API_USER, Settings.API_PASS)
        self.status_verify = Settings.REG_VERIFY_TLS
        self.client_verify = Settings.COMMAND_VERIFY_TLS

    def _status_request(self, method, path, **kwargs):
        url = f"{self.status_base}{path}"
        resp = requests.request(
            method, url, auth=self.auth, verify=self.status_verify, timeout=20, **kwargs
        )
        resp.raise_for_status()
        if resp.text:
            try:
                return resp.json()
            except Exception:
                return {"text": resp.text}
        return {}

    def _client_request(self, method, path, **kwargs):
        url = f"{self.client_base}{path}"
        resp = requests.request(method, url, verify=self.client_verify, timeout=20, **kwargs)
        resp.raise_for_status()
        if resp.text:
            try:
                return resp.json()
            except Exception:
                return {"text": resp.text}
        return {}

    def list_registered_endpoints(self):
        data = self._status_request("GET", "/api/admin/status/v1/registration_alias/?limit=1000")
        items = data.get("objects", data if isinstance(data, list) else [])
        results = []

        for item in items:
            alias = (
                item.get("alias")
                or item.get("name")
                or item.get("registration_alias")
                or item.get("local_alias")
                or ""
            )
            if not alias:
                continue

            display_name = (
                item.get("display_name")
                or item.get("description")
                or item.get("device_name")
                or alias
            )

            is_registered = item.get("is_registered")
            if is_registered is None:
                is_registered = item.get("registered")
            if is_registered is None:
                # Older Pexip firmware: the status endpoint only emits currently-
                # registered aliases, so no is_registered field means registered.
                is_registered = True

            if not is_registered:
                continue

            results.append({
                "alias": alias,
                "display_name": display_name,
                "protocol": item.get("protocol", ""),
                "is_registered": is_registered,
                "node": item.get("conference_node") or item.get("node") or "",
            })

        results.sort(key=lambda x: x["display_name"].lower())
        return results

    def request_control_token(self, meeting_alias):
        headers = {"Content-Type": "application/json", "pin": Settings.HOST_PIN}
        payload = {"display_name": Settings.CONTROL_DISPLAY_NAME}
        data = self._client_request(
            "POST",
            f"/api/client/v2/conferences/{meeting_alias}/request_token",
            headers=headers,
            json=payload,
        )
        result = data.get("result", {})
        token = result.get("token")
        if not token:
            raise RuntimeError(f"No control token returned for {meeting_alias}: {data}")
        return token

    def start_conference(self, meeting_alias, token):
        headers = {"Content-Type": "application/json", "token": token}
        return self._client_request(
            "POST",
            f"/api/client/v2/conferences/{meeting_alias}/start_conference",
            headers=headers,
            json={},
        )

    def dial_endpoint_to_meeting(self, meeting_alias, endpoint_alias, token, role="host"):
        payload = {
            "destination": endpoint_alias,
            "protocol": Settings.DIAL_PROTOCOL,
            "role": role.upper(),
        }
        headers = {"Content-Type": "application/json", "token": token}
        return self._client_request(
            "POST",
            f"/api/client/v2/conferences/{meeting_alias}/dial",
            headers=headers,
            json=payload,
        )

    def disconnect_conference(self, meeting_alias, token):
        headers = {"Content-Type": "application/json", "token": token}
        return self._client_request(
            "POST",
            f"/api/client/v2/conferences/{meeting_alias}/disconnect",
            headers=headers,
            json={},
        )

    def release_control_token(self, meeting_alias, token):
        headers = {"Content-Type": "application/json", "token": token}
        try:
            return self._client_request(
                "POST",
                f"/api/client/v2/conferences/{meeting_alias}/release_token",
                headers=headers,
                json={},
            )
        except Exception:
            return {}

    def get_live_participants(self, meeting_alias, token):
        """Fetch live participants using an already-acquired token."""
        headers = {"Content-Type": "application/json", "token": token}
        data = self._client_request(
            "GET",
            f"/api/client/v2/conferences/{meeting_alias}/participants",
            headers=headers,
        )
        result = data.get("result", data)
        if isinstance(result, dict):
            result = result.get("participants", [])
        return result if isinstance(result, list) else []

    def get_live_participants_via_edges(self, meeting_alias):
        """Fetch live participants by acquiring and releasing a fresh token."""
        token = None
        try:
            token = self.request_control_token(meeting_alias)
            return self.get_live_participants(meeting_alias, token)
        finally:
            if token:
                self.release_control_token(meeting_alias, token)
