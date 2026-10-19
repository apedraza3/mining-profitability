"""Adapter contract.

poll() returns {"machines": [machine dicts], "accounts": {account_key: data}} and raises
on failure. Machine dicts use the machine_state columns (see fleet/store.py).
"""

import requests


class SourceError(Exception):
    pass


class Adapter:
    name = "base"
    interval_s = 60

    def is_configured(self) -> bool:
        return True

    def poll(self) -> dict:
        raise NotImplementedError

    @staticmethod
    def get_json(url, *, headers=None, params=None, timeout=15):
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        if resp.status_code != 200:
            raise SourceError(f"HTTP {resp.status_code} from {url.split('?')[0]}")
        return resp.json()
