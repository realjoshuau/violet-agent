"""Queue metrics and observability tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict, field
from typing import Any


@dataclass
class MessageSnapshot:
    """Snapshot of a queued or processing message."""

    author: str
    author_id: str
    author_avatar_url: str | None
    content: str
    channel: str
    message_id: str
    created_at: float
    enqueued_at: float


@dataclass
class QueueMetrics:
    """Metrics for a single server queue."""

    context_key: str
    queue_size: int
    active_worker: bool
    currently_processing: MessageSnapshot | None
    processing_start_time: float | None
    queued_messages: list[MessageSnapshot] = field(default_factory=list)
    total_processed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_key": self.context_key,
            "queue_size": self.queue_size,
            "active_worker": self.active_worker,
            "currently_processing": (
                {
                    **asdict(self.currently_processing),
                    "processing_time_seconds": time.time() - self.processing_start_time,
                }
                if self.currently_processing
                else None
            ),
            "queued_messages": [asdict(m) for m in self.queued_messages],
            "total_processed": self.total_processed,
        }


class MetricsCollector:
    """Collect and track queue metrics."""

    def __init__(self):
        self.metrics: dict[str, QueueMetrics] = {}
        self.total_messages_processed = 0

    def get_or_create(self, context_key: str) -> QueueMetrics:
        if context_key not in self.metrics:
            self.metrics[context_key] = QueueMetrics(
                context_key=context_key,
                queue_size=0,
                active_worker=False,
                currently_processing=None,
                processing_start_time=None,
                queued_messages=[],
                total_processed=0,
            )
        return self.metrics[context_key]

    def enqueue_message(self, context_key: str, message_snapshot: MessageSnapshot) -> None:
        """Add a message to the queued list."""
        m = self.get_or_create(context_key)
        m.queued_messages.append(message_snapshot)

    def dequeue_message(self, context_key: str) -> None:
        """Remove the next message from queued list."""
        m = self.get_or_create(context_key)
        if m.queued_messages:
            m.queued_messages.pop(0)

    def update_queue_size(self, context_key: str, size: int) -> None:
        m = self.get_or_create(context_key)
        m.queue_size = size

    def start_processing(
        self, context_key: str, message_snapshot: MessageSnapshot
    ) -> None:
        m = self.get_or_create(context_key)
        m.currently_processing = message_snapshot
        m.processing_start_time = time.time()
        # Remove from queued list if it's there
        self.dequeue_message(context_key)

    def finish_processing(self, context_key: str) -> None:
        m = self.get_or_create(context_key)
        m.currently_processing = None
        m.processing_start_time = None
        m.total_processed += 1
        self.total_messages_processed += 1

    def set_worker_active(self, context_key: str, active: bool) -> None:
        m = self.get_or_create(context_key)
        m.active_worker = active

    def get_all_metrics(self) -> dict[str, dict[str, Any]]:
        return {
            key: metrics.to_dict() for key, metrics in self.metrics.items()
        }

    def get_summary(self) -> dict[str, Any]:
        return {
            "total_contexts": len(self.metrics),
            "active_workers": sum(1 for m in self.metrics.values() if m.active_worker),
            "total_queue_depth": sum(m.queue_size for m in self.metrics.values()),
            "total_messages_processed": self.total_messages_processed,
            "contexts": self.get_all_metrics(),
        }
