from __future__ import annotations

import hazelcast


class HazelcastStore:
    def __init__(self, instance_name: str, cluster_name: str, cluster_members: list[str]):
        self.instance_name = instance_name
        self.client = hazelcast.HazelcastClient(cluster_name=cluster_name, cluster_members=cluster_members)
        self.transactions_map = self.client.get_map("tx-map").blocking()
        self.user_map = self.client.get_map("user-tx-map").blocking()

    def save(self, tx: dict) -> None:
        self.transactions_map.put(tx["transaction_id"], tx)
        existing = self.user_map.get(tx["user_id"]) or []
        existing.append(tx["transaction_id"])
        self.user_map.put(tx["user_id"], existing)
        print(f"[{self.instance_name}] stored {tx['transaction_id']} for {tx['user_id']}", flush=True)

    def get_by_user(self, user_id: str) -> list[dict]:
        ids = self.user_map.get(user_id) or []
        rows = []
        for tx_id in ids:
            row = self.transactions_map.get(tx_id)
            if row is not None:
                rows.append(row)
        print(f"[{self.instance_name}] read {len(rows)} tx for {user_id}", flush=True)
        return rows

    def reset(self) -> None:
        self.transactions_map.clear()
        self.user_map.clear()

    def close(self) -> None:
        self.client.shutdown()
