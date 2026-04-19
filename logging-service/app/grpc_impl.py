from __future__ import annotations

import logging_pb2
import logging_pb2_grpc


class LoggingService(logging_pb2_grpc.LoggingServiceServicer):
    def __init__(self, store):
        self.store = store

    def StoreTransaction(self, request, _context):
        tx = {
            "transaction_id": request.transaction_id,
            "timestamp": request.timestamp,
            "user_id": request.user_id,
            "amount": request.amount,
            "message": request.message or None,
        }
        self.store.save(tx)
        return logging_pb2.TransactionReply(ok=True, instance=self.store.instance_name)

    def GetTransactionsByUser(self, request, _context):
        transactions = self.store.get_by_user(request.user_id)
        return logging_pb2.TransactionsReply(
            transactions=[
                logging_pb2.TransactionRequest(
                    transaction_id=tx["transaction_id"],
                    timestamp=tx["timestamp"],
                    user_id=tx["user_id"],
                    amount=tx["amount"],
                    message=tx.get("message", "") or "",
                )
                for tx in transactions
            ]
        )
